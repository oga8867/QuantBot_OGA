"""
=============================================================================
run_bot.py - 퀀트봇 자동매매 모드 실행기
=============================================================================

스케줄러를 시작하여 정해진 시간에 분석 → 신호 생성 → 주문 실행을 자동화합니다.

실행 흐름:
1. 설정 로드 (자본금, 리스크, 전략 파라미터)
2. 브로커 연결 (Alpaca/KIS/Paper)
3. 스케줄러 시작
4. 정해진 시간에 자동으로:
   - 데이터 수집 → 분석 → 신호 생성
   - 리스크 체크 → 포지션 사이징
   - 주문 실행 → 알림 전송

사용법:
    python run_bot.py                         # 모의매매, 기본 설정
    python run_bot.py --capital 50000000      # ���본금 5천만원
    python run_bot.py --live                  # 실거래 모드 (주의!)
    python run_bot.py --broker alpaca         # Alpaca 사용
    python run_bot.py --broker kis            # 한국투자증권 사용
=============================================================================
"""

import argparse
import sys
import os
import time
import signal
import threading
from datetime import datetime
from typing import Dict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


# ─── 청산 사유 한국어 변환 ───
# 거래 이력 모달에서 사용자 친화적으로 표시
def _exit_reason_kr(reason: str) -> str:
    return {
        "stop_loss": "🔴 하드 스탑 (손절선 도달)",
        "take_profit_1": "🟢 1차 익절 (50% 매도, 손절선 본전 상향)",
        "take_profit_2": "🟢 2차 익절 (전량 매도)",
        "trailing_stop": "📉 트레일링 스탑 (Chandelier Exit)",
        "time_stop": "⏰ 보유기간 초과",
        "signal_sell": "📊 앙상블 매도 신호",
        "close_position": "📊 일반 청산",
        "close_partial": "📊 부분 청산",
        "ensemble": "📊 앙상블 매수 신호",
    }.get(reason, reason)

from config.settings import Settings, CapitalConfig, RiskConfig
from collectors.price_us import PriceCollectorUS
from collectors.price_kr import PriceCollectorKR
from analyzers.technical import TechnicalAnalyzer
from strategy.ensemble import EnsembleStrategy
from risk.position_sizer import PositionSizer
from risk.stop_loss import StopLossManager
from executor.paper_executor import PaperExecutor
from notifier.telegram_bot import TelegramNotifier
from notifier.discord_webhook import DiscordNotifier
from scheduler.job_manager import JobManager
from database.cache import DatabaseManager
from executor.safety_guard import SafetyGuard, SafetyConfig
from utils.logger import setup_logger
from utils.market import to_krw


class QuantBot:
    """
    퀀트봇 메인 오케스트레이터

    모든 모듈을 연결하고 전체 매매 프로세스를 관리합니다.
    """

    def __init__(self, settings: Settings, broker: str = "paper", live: bool = False):
        """
        Parameters:
            settings: 설정 객체
            broker: "paper", "alpaca", "kis"
            live: True=실거래 (주의!)
        """
        self.settings = settings
        self.logger = setup_logger(level="INFO", log_file=True)
        # ★ threading.Event: 스레드 안전한 stop 시그널
        self._stop_event = threading.Event()

        # 모듈 초기화
        self.analyzer = TechnicalAnalyzer(settings.technical)
        # ensemble_config 전달하여 모듈 enable/disable 자동 반영
        self.ensemble = EnsembleStrategy(ensemble_config=settings.ensemble)

        # ── 모의매매 모드에서 앙상블 임계값 완화 ──
        if not live:
            self.ensemble.buy_threshold = 0.10
            self.ensemble.sell_threshold = -0.10
            self.logger.info(
                "[앙상블] 모의매매 모드: 임계값 완화 "
                f"(매수>{self.ensemble.buy_threshold}, "
                f"매도<{self.ensemble.sell_threshold})"
            )
        self.position_sizer = PositionSizer(
            capital=settings.capital.total_capital,
            risk_per_trade=settings.risk.risk_per_trade,
            max_position_pct=settings.risk.max_position_size,
            stop_loss_atr_mult=settings.risk.stop_loss_atr_multiplier,
            kelly_fraction=settings.risk.kelly_fraction
        )
        self.stop_manager = StopLossManager(
            atr_multiplier=settings.risk.stop_loss_atr_multiplier,
            risk_reward_ratio=settings.risk.risk_reward_ratio
        )
        # 알림: 텔레그램 + 디스코드 (설정된 것만 활성화됨)
        self.notifier = TelegramNotifier()
        self.discord = DiscordNotifier()
        self.db = DatabaseManager()
        self.db.initialize()

        # 브로커 실행기 선택
        self.executor = self._create_executor(broker, live)

        # 안전장치 (실거래 시 특히 중요)
        capital = settings.capital.total_capital
        # ★ 최대 주문 금액: 설정값 우선, 0이면 자동(자본의 20%)
        # _max_order_is_auto 플래그 — 자본 동기화 시 자동 재계산 여부 판단용
        _configured_max_order = getattr(settings.risk, "max_order_value", 0) or 0
        self._max_order_is_auto = (_configured_max_order <= 0)
        max_order_val = _configured_max_order if not self._max_order_is_auto else capital * 0.20
        min_order_val = capital * 0.001

        self.safety = SafetyGuard(
            capital=capital,
            paper=(not live),
            config=SafetyConfig(
                max_daily_loss_pct=settings.risk.max_daily_loss if hasattr(settings.risk, 'max_daily_loss') else 0.03,
                max_order_pct=settings.risk.max_position_size if hasattr(settings.risk, 'max_position_size') else 0.10,
                max_positions=10,
                max_position_weight=settings.risk.max_position_size if hasattr(settings.risk, 'max_position_size') else 0.20,
                max_daily_trades=50,
                consecutive_loss_limit=5,
                order_delay_sec=3 if live else 0,
                max_order_value=max_order_val,
                min_order_value=min_order_val,
            )
        )
        self.logger.info(
            f"[SafetyGuard] 주문 한도: "
            f"최소 {min_order_val:,.0f} / 최대 {max_order_val:,.0f} "
            f"(자본금 {capital:,.0f} 기준)"
        )

        # 스케줄러
        self.scheduler = JobManager()

        # ── 매도 후 쿨다운 (반복매매 방지) ──
        # 같은 종목을 매도 직후 다시 매수하면 수수료만 먹는 무의미한 거래가 됨
        # 매도 시점을 기록하고, 일정 시간(쿨다운) 동안 재매수를 차단
        self._sell_cooldowns: Dict[str, float] = {}  # {symbol: 매도 시각(timestamp)}
        self._cooldown_seconds = 3600  # 기본 1시간 쿨다운 (초)
        self._restore_sell_cooldowns()  # DB에서 최근 매도 기록 기반 쿨다운 복원

        # ── ExitManager: 손절/익절/트레일링 스탑 자동 청산 ──
        # 진입가 기반 자동 청산 의사결정. 앙상블 SELL 신호와 별개로
        # 매 분석 사이클에서 보유 포지션의 청산 조건을 체크합니다.
        # 학술적 근거:
        # - Le Beau Chandelier Exit: 22일 ATR × 3 (드로우다운 22% 감소)
        # - Half-Kelly + ATR 스탑: 약 75% 성장률, 변동성 대폭 감소
        from executor.exit_manager import ExitManager
        self.exit_manager = ExitManager(
            atr_stop_multiplier=getattr(settings.risk, 'stop_loss_atr_multiplier', 2.0),
            rr_ratio=getattr(settings.risk, 'risk_reward_ratio', 2.0),
            trailing_atr_multiplier=3.0,  # Chandelier Exit 표준
            enable_partial=True,
            enable_time_stop=False,
        )
        self._restore_exit_states()  # DB에서 ExitManager 상태 복원

        # ── 적응형 임계값: VIX/체제 기반 동적 매수/매도 임계값 ──
        # 변동성 ↑ → 임계값 ↑ (강한 신호만), 위기장 → 포지션 ↓
        # 5분마다 갱신, 분석 사이클 시작 시 자동 적용
        from strategy.adaptive_threshold import AdaptiveThresholdManager
        self.adaptive_thresholds = AdaptiveThresholdManager(paper=(not live))
        self._current_thresholds = None  # 분석 사이클에서 갱신

        # ── 시장 정지 감지: 서킷브레이커/사이드카/VI ──
        # 실거래 필수 안전장치. 분석 사이클 시작 시 체크하여
        # CB 발동 시 즉시 모든 매매 차단, VI 발동 종목은 매매 보류.
        # 모의투자에서도 활성화하여 실거래 전환 시 검증 완료 상태로 시작.
        from strategy.market_halt_detector import MarketHaltDetector
        self.halt_detector = MarketHaltDetector(kis_client=None)
        # KIS 클라이언트는 _setup_kis_for_halt_detector()에서 lazy init
        self._halt_check_result = None  # 분석 사이클에서 갱신

        # ── 종목 자동 발굴 시스템 ──
        # 워치리스트 외 유망 종목을 섹터/시장에서 탐색하여 분석 대상에 추가
        from config.settings import DiscoveryConfig
        self._discovery_config = getattr(settings, 'discovery', DiscoveryConfig())
        self._discovery_cycle_count = 0    # 발굴 주기 카운터
        self._discovered_us: list = []     # 자동 발굴된 미국 종목 리스트
        self._discovered_kr: list = []     # 자동 발굴된 한국 종목 리스트
        self._discovery_hold_counts: dict = {}  # {symbol: 연속 HOLD 횟수}
        self._load_discovered_from_db()    # DB에서 이전 발굴 결과 복원

    @property
    def running(self) -> bool:
        """하위호환: self.running 접근 시 Event 상태 반환"""
        return not self._stop_event.is_set()

    @running.setter
    def running(self, value: bool):
        """하위호환: self.running = True/False 지원"""
        if value:
            self._stop_event.clear()
        else:
            self._stop_event.set()

    def _create_executor(self, broker: str, live: bool):
        """
        브로커 실행기 생성

        지원 브로커:
        - "paper": 모의매매 (기본)
        - "alpaca": 미국 주식 (Alpaca Markets)
        - "kis": 한국 주식 (한국투자증권)
        - "dual": 듀얼 마켓 (KIS + Alpaca 동시 운용)
        """
        if broker == "dual":
            # ── 듀얼 모드: KIS(한국) + Alpaca(미국) 동시 운용 ──
            from executor.dual_executor import DualExecutor
            self.logger.info(
                "[실행기] 듀얼 마켓 모드 — KIS(한국) + Alpaca(미국) 동시 운용"
            )
            return DualExecutor(paper=not live, db=self.db)
        elif broker == "alpaca":
            from executor.alpaca_executor import AlpacaExecutor
            return AlpacaExecutor(paper=not live)
        elif broker == "kis":
            from executor.kis_executor import KISExecutor
            # ★ Phase 10: DB 주입 → KIS 체결 시 자동으로 trades 테이블에 기록
            # 그러지 않으면 live 거래가 대시보드 PnL/이력에 안 보임
            return KISExecutor(paper=not live, db=self.db)
        else:
            # DB를 PaperExecutor에 주입 → 거래 이력/포지션이 DB에 영속 저장됨
            # 봇 재시작 시 connect()에서 자동으로 이전 상태 복원
            return PaperExecutor(
                initial_capital=self.settings.capital.total_capital,
                currency=self.settings.capital.currency,
                db=self.db
            )

    def start(self):
        """봇 시작"""
        self.logger.info("=" * 50)
        self.logger.info("  퀀트봇 시작")
        self.logger.info("=" * 50)
        self.logger.info(self.settings.summary())

        # 브로커 연결
        if not self.executor.connect():
            self.logger.error("브로커 연결 실패! 종료합니다.")
            return

        # ── ★ 실거래 자본 동기화 (CRITICAL) ──
        # 설정 파일의 capital과 실제 계좌 잔고가 다르면 포지션 크기가 잘못 계산됨.
        # 실거래 모드에서는 반드시 실제 계좌 잔고를 진실의 원천으로 사용.
        self._sync_capital_from_broker()

        # ── ★ Phase 6B: 브로커 ↔ DB 포지션 reconciliation ──
        # 봇 크래시 후 재시작 시 DB 불일치를 잡아 이중 매수를 방지합니다.
        # 브로커가 진실의 원천 → DB를 브로커에 맞춰 정렬 + 사용자 알림
        self._reconcile_with_broker()

        # 스케줄 설정
        self.scheduler.setup_default_schedule(
            analyze_kr_func=self._analyze_kr_market,
            analyze_us_func=self._analyze_us_market,
            risk_check_func=self._check_risk,
        )

        # 시작 알림 (텔레그램 + 디스코드)
        self.notifier.send_message(
            f"🤖 <b>퀀트봇 시작</b>\n"
            f"모드: {'실거래' if not self.executor.paper else '모의매매'}\n"
            f"자본금: {self.settings.capital.total_capital:,.0f} "
            f"{self.settings.capital.currency}"
        )
        self.discord.send_bot_status(
            running=True,
            mode="live" if not self.executor.paper else "paper",
            equity=self.settings.capital.total_capital,
            pnl_pct=0.0
        )

        # 스케줄러 시작
        self.scheduler.start()
        self.running = True

        self.logger.info("봇이 실행 중입니다. Ctrl+C로 종료...")

        # 메인 루프 (에러 자동 복구 포함)
        # [에러 복구 전략]
        # - 일반 예외: 로그 후 계속 실행 (재시도 카운터 사용)
        # - 연속 5회 에러: 알림 전송 후 30초 대기
        # - KeyboardInterrupt/SystemExit: 정상 종료
        # - DB 연결 끊김: 자동 재연결
        error_count = 0
        MAX_ERRORS = 5

        try:
            while self.running:
                try:
                    # ★ Phase 7B: Event 기반 대기 — stop() 호출 시 즉시 반응
                    if self._stop_event.wait(1):
                        break
                    error_count = 0  # 정상 루프 → 에러 카운터 초기화
                except KeyboardInterrupt:
                    break
                except Exception as e:
                    error_count += 1
                    self.logger.error(f"[에러 복구] 메인 루프 에러 #{error_count}: {e}")

                    if error_count >= MAX_ERRORS:
                        self.logger.critical(f"[에러 복구] 연속 {MAX_ERRORS}회 에러 발생!")
                        self.notifier.send_risk_alert(
                            f"봇 연속 에러 {MAX_ERRORS}회!\n"
                            f"마지막 에러: {str(e)[:100]}\n"
                            f"30초 후 재시도합니다.",
                            level="CRITICAL"
                        )
                        error_count = 0
                        # 30초 대기 — 도중 stop 신호 오면 즉시 종료
                        if self._stop_event.wait(30):
                            break

                    # DB 재연결 시도
                    self._try_reconnect_db()
        except (KeyboardInterrupt, SystemExit):
            pass
        finally:
            self.stop()

    def stop(self):
        """봇 중지"""
        self.running = False
        self.scheduler.stop()

        # ★ Phase 11 BUG-6 FIX: 종료 직전 모든 ExitManager state를 DB에 flush
        # 트레일링 스탑 최신값이 디스크에 영속화되어 재시작 시 손익 보호 유지
        try:
            for sym in list(self.exit_manager.states.keys()):
                try:
                    self._save_exit_state(sym)
                except Exception as e:
                    self.logger.debug(f"[종료 flush] {sym}: {e}")
        except Exception as e:
            self.logger.warning(f"[종료] ExitManager flush 실패: {e}")

        self.notifier.send_message("🛑 <b>퀀트봇 종료</b>")
        # 디스코드 종료 알림
        account = self.executor.get_account()
        pnl_pct = (account.total_equity / self.settings.capital.total_capital - 1) * 100
        self.discord.send_bot_status(
            running=False,
            mode="live" if not self.executor.paper else "paper",
            equity=account.total_equity,
            pnl_pct=pnl_pct
        )
        self.db.close()
        self.logger.info("퀀트봇 종료됨")

    def _try_reconnect_db(self):
        """
        DB 연결 상태 확인 및 재연결

        SQLite 연결이 끊어지면 (파일 잠금, 디스크 오류 등)
        자동으로 재연결을 시도합니다.
        """
        try:
            # 간단한 쿼리로 연결 확인
            self.db.conn.execute("SELECT 1").fetchone()
        except Exception:
            self.logger.warning("[에러 복구] DB 연결 끊김, 재연결 시도...")
            try:
                self.db.initialize()
                self.logger.info("[에러 복구] DB 재연결 성공")
            except Exception as e:
                self.logger.error(f"[에러 복구] DB 재연결 실패: {e}")

    # ═══════════════════════════════════════════════════════════════════════
    # 시장 영업시간 체크 + 매도 쿨다운
    # ═══════════════════════════════════════════════════════════════════════

    def _is_market_open(self, market: str) -> bool:
        """
        시장이 현재 열려 있는지 확인

        한국 (KR): 평일 09:00~15:30 KST
        미국 (US): 평일 09:30~16:00 ET (한국시간 23:30~06:00, 서머타임 22:30~05:00)

        ★ 모의매매에서도 실제 시장 시간에만 거래해야 의미 있는 시뮬레이션이 됩니다.
          장 마감 중에 분석하면 가격이 안 변하므로 같은 신호가 반복 → 무의미한 매매

        Parameters:
            market: "KR" 또는 "US"

        Returns:
            True이면 거래 가능, False이면 장 마감 중
        """
        # ★ CRITICAL FIX: zoneinfo로 DST 자동 처리
        # 이전 버그: ET = UTC-4 하드코딩 → 11~3월(EST)에 미국장 1시간 어긋남
        try:
            from utils.timezones import now_kst, now_et
            kst = now_kst()
            et = now_et()
        except ImportError:
            # zoneinfo 없는 환경 fallback (DST 부정확)
            from datetime import timezone, timedelta as _td
            now_utc = datetime.now(timezone.utc)
            kst = now_utc + _td(hours=9)
            et = now_utc - _td(hours=4)  # EDT 가정 (DST)

        if market == "KR":
            if kst.weekday() >= 5:  # 주말
                return False
            from datetime import time as _time
            t = kst.time()
            # 09:00:00 <= t <= 15:30:00 (등호 포함, 15:30 종가도 거래 가능)
            return _time(9, 0) <= t <= _time(15, 30)

        elif market == "US":
            if et.weekday() >= 5:  # 주말
                return False
            from datetime import time as _time
            t = et.time()
            # 09:30:00 <= t < 16:00:00 ET (NYSE/NASDAQ 정규장)
            return _time(9, 30) <= t < _time(16, 0)

        return True  # 알 수 없는 시장은 허용

    def _restore_sell_cooldowns(self):
        """
        DB의 최근 매도 기록에서 쿨다운 상태 복원 (서버 재시작 대응)

        서버를 재시작하면 메모리의 _sell_cooldowns가 사라져서
        최근에 매도한 종목을 바로 재매수하는 문제가 발생합니다.
        DB의 trades 테이블에서 최근 매도 기록을 읽어
        아직 쿨다운 기간 내인 종목을 복원합니다.
        """
        try:
            # ★ Phase 5: 현재 모드 거래만 (paper/live 격리)
            trades = self.db.get_trades(
                limit=200,
                mode=getattr(self.executor, "mode", "paper")
            )
            now = time.time()
            restored = 0

            for trade in trades:
                if trade.get("side", "").upper() != "SELL":
                    continue

                symbol = trade.get("symbol", "")
                if not symbol:
                    continue

                # 이미 쿨다운에 등록된 종목은 건너뜀 (가장 최근 매도만 유효)
                if symbol in self._sell_cooldowns:
                    continue

                # 매도 시각 파싱
                try:
                    ts_str = trade.get("timestamp", "")
                    sell_time = datetime.fromisoformat(ts_str).timestamp()
                except (ValueError, TypeError):
                    continue

                # 아직 쿨다운 기간 내인지 확인
                elapsed = now - sell_time
                if elapsed < self._cooldown_seconds:
                    self._sell_cooldowns[symbol] = sell_time
                    remaining = int(self._cooldown_seconds - elapsed)
                    self.logger.info(
                        f"[쿨다운 복원] {symbol}: 매도 후 {int(elapsed)}초 경과, "
                        f"{remaining}초 남음"
                    )
                    restored += 1

            if restored > 0:
                self.logger.info(f"[쿨다운 복원] {restored}개 종목 쿨다운 상태 DB에서 복원 완료")

        except Exception as e:
            self.logger.warning(f"[쿨다운 복원] DB에서 복원 실패 (무시): {e}")

    def _sync_capital_from_broker(self):
        """
        실거래 모드: 실제 브로커 계좌 잔고로 자본금 동기화 (CRITICAL)

        ⚠️ 왜 필요한가:
          설정 파일(user_settings.json)의 capital이 실제 계좌 잔고와 다르면
          PositionSizer와 SafetyGuard가 잘못된 자본 기준으로 매매 크기를 계산.
          예: 설정 ₩1,000만 / 실제 ₩400만 → 모든 리스크 한도가 2.5배 헐거워짐
              → 단일 종목에 실제 자본의 25%가 들어가는데 10%로 오인

        정책:
          - paper 모드: 설정된 가상 자본 유지 (실제 계좌 없음)
          - live 모드: 브로커의 total_equity(총평가금액)를 진실의 원천으로 사용
          - 계좌 조회 실패 시: 설정값 유지 + 경고 (자본 0으로 만들지 않음)
        """
        # paper 모드는 가상 자본 사용 — 동기화 불필요
        if getattr(self.executor, "paper", True):
            return

        try:
            account = self.executor.get_account()
            # 계좌 조회 자체가 실패했으면 설정값 유지 (0으로 덮어쓰면 매매 불가)
            if hasattr(self.executor, "account_query_succeeded"):
                if not self.executor.account_query_succeeded():
                    self.logger.warning(
                        "[자본 동기화] 계좌 조회 실패 → 설정값 유지 "
                        f"(₩{self.settings.capital.total_capital:,.0f})"
                    )
                    return

            real_capital = float(account.total_equity)
            if real_capital <= 0:
                self.logger.warning(
                    f"[자본 동기화] 계좌 평가액이 {real_capital} → 설정값 유지. "
                    f"실거래 API 신청/입금 여부 확인 필요"
                )
                return

            configured = float(self.settings.capital.total_capital)
            diff_pct = abs(real_capital - configured) / max(configured, 1.0) * 100

            if diff_pct > 1.0:
                self.logger.warning(
                    f"[자본 동기화] ⚠️ 설정 자본 ₩{configured:,.0f} ≠ "
                    f"실제 계좌 ₩{real_capital:,.0f} (차이 {diff_pct:.0f}%) "
                    f"→ 실제 계좌 값으로 매매 크기 계산"
                )
                try:
                    self.notifier.send_message(
                        f"💰 자본 동기화\n"
                        f"설정: ₩{configured:,.0f}\n"
                        f"실제: ₩{real_capital:,.0f}\n"
                        f"→ 실제 잔고 기준으로 매매합니다."
                    )
                except Exception:
                    pass
            else:
                self.logger.info(
                    f"[자본 동기화] 실제 계좌 ₩{real_capital:,.0f} 확인"
                )

            # 실제 자본으로 갱신
            self.settings.capital.total_capital = real_capital
            self.position_sizer.capital = real_capital
            self.safety.capital = real_capital

            # ★ 주문 한도도 갱신:
            #   - max_order_value가 '자동'(설정 0)이면 새 자본의 20%로 재계산
            #     (이전 버그: capital만 갱신하고 config.max_order_value는 stale)
            #   - 사용자가 절대값을 명시했으면 그 값 유지 (자본과 무관)
            #   - min_order_value는 항상 자본 비례 재계산
            try:
                if getattr(self, "_max_order_is_auto", True):
                    self.safety.config.max_order_value = real_capital * 0.20
                self.safety.config.min_order_value = real_capital * 0.001
                self.logger.info(
                    f"[자본 동기화] 주문 한도 갱신 — "
                    f"최대 ₩{self.safety.config.max_order_value:,.0f} / "
                    f"최소 ₩{self.safety.config.min_order_value:,.0f}"
                )
            except Exception as e:
                self.logger.warning(f"[자본 동기화] 주문 한도 갱신 실패: {e}")

        except Exception as e:
            self.logger.error(
                f"[자본 동기화] 실패 (설정값 유지): {e}"
            )

    def _reconcile_with_broker(self):
        """
        브로커 잔고와 DB 포지션을 비교하여 불일치 감지 + 자동 정렬

        호출 시점: 봇 시작 시 connect() 직후, 분석 루프 진입 전.

        ⚠️ CRITICAL — 이중 매수 방지의 핵심:
          시나리오: 봇이 KIS에 매수 주문 → KIS 200 SUBMITTED → 봇이 DB 기록 직전 크래시
          재시작 시 DB에는 포지션 없음 → 분석이 같은 신호로 또 매수 → 이중 매수
          이를 감지하려면 KIS의 실제 잔고를 진실의 원천으로 삼아 reconcile.

        정책:
          - 브로커 = ground truth (실제 보유 종목)
          - DB의 포지션이 브로커에 없으면 → DB에서 삭제 (orphan 정리)
          - 브로커에 있는데 DB에 없으면 → DB에 추가 (잃어버린 포지션 복원)
          - 수량이 다르면 → 브로커 수량으로 정렬
          - 모든 불일치는 텔레그램/디스코드로 알림

        PaperExecutor는 자체 메모리가 진실이므로 reconcile 스킵.
        """
        # PaperExecutor는 reconcile 불필요 (in-memory 상태가 진실)
        if self.executor.name == "paper":
            return

        try:
            mode = getattr(self.executor, "mode", "paper")
            broker_positions = self.executor.get_positions()

            # ★ CRITICAL: API 호출이 실패했으면 reconcile 중단
            # 이전 버그: get_positions가 API 실패 시 [] 반환 → 모든 DB 포지션이 broker-only로
            # 판단되어 DELETE FROM positions → 실제 포지션 기록 전부 소실
            if not self.executor.positions_query_succeeded():
                self.logger.error(
                    "[Reconcile] 브로커 API 실패 — DB 보호를 위해 reconcile 중단. "
                    "다음 분석 사이클에서 재시도."
                )
                return

            db_positions = self.db.load_positions(mode=mode)

            broker_map = {p.symbol: p for p in broker_positions}
            db_map = {p["symbol"]: p for p in db_positions}

            broker_only = set(broker_map.keys()) - set(db_map.keys())
            db_only = set(db_map.keys()) - set(broker_map.keys())
            both = set(broker_map.keys()) & set(db_map.keys())
            qty_diff = []
            for sym in both:
                broker_qty = int(broker_map[sym].quantity)
                db_qty = int(db_map[sym]["quantity"])
                if broker_qty != db_qty:
                    qty_diff.append((sym, db_qty, broker_qty))

            if not (broker_only or db_only or qty_diff):
                self.logger.info(
                    f"[Reconcile] ✓ 브로커({len(broker_map)}개) ↔ "
                    f"DB({len(db_map)}개) 일치"
                )
                return

            # ── 불일치 감지 → 로깅 + 자동 정렬 ──
            alert_lines = ["⚠️ 봇 시작 시 브로커-DB 포지션 불일치 감지:"]

            # 1) DB에만 있는 포지션 (브로커에는 없음) → 삭제
            for sym in db_only:
                self.logger.warning(
                    f"[Reconcile] DB-only: {sym} (DB: {db_map[sym]['quantity']}주, "
                    f"브로커: 없음) → DB에서 삭제"
                )
                alert_lines.append(
                    f"• {sym}: DB에는 {db_map[sym]['quantity']}주, 브로커엔 없음 → 삭제"
                )
                try:
                    self.db.delete_position(sym, mode=mode)
                    # ExitManager에서도 제거
                    if hasattr(self.exit_manager, "states") and sym in self.exit_manager.states:
                        del self.exit_manager.states[sym]
                except Exception as e:
                    self.logger.error(f"[Reconcile] {sym} DB 삭제 실패: {e}")

            # 2) 브로커에만 있는 포지션 (DB 누락) → DB에 추가 + ExitManager 등록
            for sym in broker_only:
                bp = broker_map[sym]
                self.logger.warning(
                    f"[Reconcile] 브로커-only: {sym} (브로커: {bp.quantity}주 @ "
                    f"₩{bp.avg_price:,.0f}, DB: 없음) → DB에 추가 + 자동청산 등록"
                )
                alert_lines.append(
                    f"• {sym}: 브로커 {bp.quantity}주 @ ₩{bp.avg_price:,.0f}, "
                    f"DB 없음 → 복원"
                )
                # ── ATR 추정 (DB에 없으므로 진입가 기반 보수적 추정) ──
                # 정확한 ATR이 없으면 진입가의 2.5%를 ATR로 가정 (한국 주식 평균 변동성)
                est_atr = float(bp.avg_price) * 0.025
                try:
                    self.db.update_position(
                        symbol=sym,
                        quantity=int(bp.quantity),
                        avg_price=float(bp.avg_price),
                        current_price=float(bp.current_price),
                        position_type="스윙",
                        position_type_en="swing",
                        target_price=round(float(bp.avg_price) + est_atr * 2.0 * 2.0, 2),
                        stop_price=round(float(bp.avg_price) - est_atr * 2.0, 2),
                        reasons_json="[]",
                        holding_period="복원됨",
                        bought_at="",
                        mode=mode,
                    )
                except Exception as e:
                    self.logger.error(f"[Reconcile] {sym} DB 추가 실패: {e}")

                # ── ★ Phase 12 FIX: ExitManager에도 등록 (자동 손절/익절 보장) ──
                # 이전 버그: DB만 추가하고 ExitManager 미등록 → 복원된 포지션이
                # 영원히 자동 청산 안 됨 (손절/익절/트레일링 전부 미작동)
                try:
                    if sym not in self.exit_manager.states:
                        self.exit_manager.register_entry(
                            symbol=sym,
                            entry_price=float(bp.avg_price),
                            atr=est_atr,
                            atr_stop_mult=2.0,
                            rr_ratio=2.0,
                            holding_days_max=30,
                        )
                        self.logger.info(
                            f"[Reconcile] {sym} ExitManager 등록 완료 "
                            f"(추정 ATR ₩{est_atr:,.0f}, 손절 ₩{float(bp.avg_price) - est_atr*2.0:,.0f})"
                        )
                        self._save_exit_state(sym)
                except Exception as e:
                    self.logger.error(f"[Reconcile] {sym} ExitManager 등록 실패: {e}")

            # 3) 수량 불일치 → 브로커 기준으로 정렬
            for sym, db_qty, broker_qty in qty_diff:
                self.logger.warning(
                    f"[Reconcile] 수량 불일치 {sym}: DB={db_qty}, 브로커={broker_qty} "
                    f"→ DB를 브로커 값으로 갱신"
                )
                alert_lines.append(
                    f"• {sym}: DB {db_qty}주 ↔ 브로커 {broker_qty}주 → "
                    f"브로커 기준 정렬"
                )
                try:
                    bp = broker_map[sym]
                    # 기존 메타데이터 보존하면서 수량만 갱신
                    db_meta = db_map[sym]
                    self.db.update_position(
                        symbol=sym,
                        quantity=int(broker_qty),
                        avg_price=float(bp.avg_price),
                        current_price=float(bp.current_price),
                        position_type=db_meta.get("position_type", ""),
                        position_type_en=db_meta.get("position_type_en", ""),
                        target_price=db_meta.get("target_price", 0),
                        stop_price=db_meta.get("stop_price", 0),
                        reasons_json=db_meta.get("reasons_json", "[]"),
                        holding_period=db_meta.get("holding_period", ""),
                        bought_at=db_meta.get("bought_at", ""),
                        mode=mode,
                    )
                except Exception as e:
                    self.logger.error(f"[Reconcile] {sym} 수량 갱신 실패: {e}")

            # 사용자에게 알림 (텔레그램 + 디스코드)
            try:
                alert_msg = "\n".join(alert_lines)
                self.logger.warning(alert_msg)
                if hasattr(self, "notifier"):
                    self.notifier.send_message(alert_msg)
                if hasattr(self, "discord"):
                    self.discord.send_message(alert_msg)
            except Exception:
                pass
        except Exception as e:
            # 절대 봇 시작을 막지 않음 — 경고만 남기고 계속 진행
            self.logger.error(f"[Reconcile] 실패 (계속 진행): {e}")

    def _restore_exit_states(self):
        """
        DB에서 ExitManager 상태 복원 (서버 재시작 대응)

        positions 테이블에서 진입 시점 ATR, 현재 손절선, 최고가, 분할 매도 비중을
        읽어와 ExitManager 메모리에 등록합니다.
        DB에 entry_atr=0인 구버전 포지션은 복원 시 ATR을 재추정합니다.
        """
        try:
            # ★ Phase 5: 현재 모드 포지션만 복원 (paper/live 격리)
            db_positions = self.db.load_positions(
                mode=getattr(self.executor, "mode", "paper")
            )
            restored = 0

            for pos_data in db_positions:
                symbol = pos_data.get("symbol", "")
                if not symbol:
                    continue
                quantity = pos_data.get("quantity", 0)
                if isinstance(quantity, bytes):
                    import struct
                    quantity = struct.unpack('<q', quantity)[0]
                if quantity <= 0:
                    continue

                avg_price = float(pos_data.get("avg_price", 0))
                if avg_price <= 0:
                    continue

                # ExitManager 상태 필드들 (구버전 포지션은 0)
                entry_atr = float(pos_data.get("entry_atr", 0) or 0)
                current_stop = float(pos_data.get("current_stop", 0) or 0)
                highest = float(pos_data.get("highest_since_entry", 0) or 0)
                partial_sold = float(pos_data.get("partial_sold_pct", 0) or 0)
                target_1 = float(pos_data.get("target_1", 0) or 0)
                target_2 = float(pos_data.get("target_2", 0) or 0)
                stop_price = float(pos_data.get("stop_price", 0) or 0)
                bought_at = pos_data.get("bought_at", "")

                # ── 신규 포지션이거나 ATR 데이터가 없으면 추정 ──
                # 표준 가정: ATR = avg_price × 2% (대다수 종목의 일평균 변동폭)
                if entry_atr <= 0:
                    entry_atr = avg_price * 0.02
                if current_stop <= 0:
                    current_stop = stop_price if stop_price > 0 else (avg_price - entry_atr * 2.0)
                if target_1 <= 0:
                    target_1 = avg_price + entry_atr * 2.0 * 1.0  # 2× ATR (1R)
                if target_2 <= 0:
                    target_2 = avg_price + entry_atr * 2.0 * 2.0  # 2× ATR × 2 (2R)
                if highest <= 0:
                    highest = avg_price

                # 진입 시각 파싱
                from executor.exit_manager import PositionExitState
                try:
                    entry_time = datetime.fromisoformat(bought_at) if bought_at else datetime.now()
                except (ValueError, TypeError):
                    entry_time = datetime.now()

                state = PositionExitState(
                    symbol=symbol,
                    entry_price=avg_price,
                    entry_atr=entry_atr,
                    entry_time=entry_time,
                    initial_stop=current_stop,
                    target_1=target_1,
                    target_2=target_2,
                    current_stop=current_stop,
                    highest_price_since_entry=highest,
                    partial_sold_pct=partial_sold,
                )
                self.exit_manager.restore_state(state)
                restored += 1

            if restored > 0:
                self.logger.info(
                    f"[ExitManager 복원] {restored}개 포지션의 청산 상태 DB에서 복원"
                )

        except Exception as e:
            self.logger.warning(f"[ExitManager 복원] 실패 (무시, 신규로 시작): {e}")

    def _save_exit_state(self, symbol: str):
        """
        ExitManager 상태를 DB positions 테이블에 동기화

        ★ 수정: mode 필터 + DB 락 적용 (이전엔 raw conn + symbol-only WHERE)
        positions 테이블은 UNIQUE(symbol, mode)이므로 mode 없이 UPDATE하면
        다른 모드(paper↔live)의 같은 종목 행을 잘못 덮어쓸 수 있었음.
        """
        if not self.db:
            return
        state_dict = self.exit_manager.get_state_dict(symbol)
        if not state_dict:
            return
        try:
            self.db.update_exit_state(
                symbol=symbol,
                mode=getattr(self.executor, "mode", "paper"),
                entry_atr=state_dict["entry_atr"],
                current_stop=state_dict["current_stop"],
                highest_since_entry=state_dict["highest_since_entry"],
                partial_sold_pct=state_dict["partial_sold_pct"],
                target_1=state_dict["target_1"],
                target_2=state_dict["target_2"],
            )
        except Exception as e:
            self.logger.debug(f"[ExitManager 동기화] {symbol} 실패 (무시): {e}")

    def _is_in_cooldown(self, symbol: str) -> bool:
        """
        매도 후 쿨다운 중인지 확인

        같은 종목을 매도 직후 재매수하면 수수료만 낭비하는 무의미한 거래입니다.
        매도 후 일정 시간(기본 1시간) 동안 해당 종목 재매수를 차단합니다.

        Returns:
            True이면 쿨다운 중 (매수 차단), False이면 매수 허용
        """
        if symbol not in self._sell_cooldowns:
            return False

        elapsed = time.time() - self._sell_cooldowns[symbol]
        if elapsed < self._cooldown_seconds:
            remaining = int(self._cooldown_seconds - elapsed)
            self.logger.debug(
                f"[쿨다운] {symbol}: 매도 후 {int(elapsed)}초 경과, "
                f"{remaining}초 남음 (재매수 차단)"
            )
            return True

        # 쿨다운 만료 → 정리
        del self._sell_cooldowns[symbol]
        return False

    def _analyze_us_market(self):
        """
        미국 시장 분석 (스케줄 작업)

        통합 워치리스트 = 사용자 관심종목 + 자동 발굴 종목 + 보유 종목

        [v2.1] 발굴 시스템 주기적 호출 추가
        """
        # ── 종목 발굴 주기 체크 ──
        # cycle_multiplier회 분석마다 1번 발굴 실행
        self._discovery_cycle_count += 1
        if (self._discovery_config.enabled and
                self._discovery_cycle_count % self._discovery_config.cycle_multiplier == 0):
            try:
                self._run_discovery()
            except Exception as e:
                self.logger.error(f"[발굴] 실행 중 오류 (분석은 계속 진행): {e}")

        from config.settings import US_WATCHLIST
        watchlist = self._build_merged_watchlist("us", US_WATCHLIST)
        self.logger.info(f"[스케줄] 미국 시장 분석 시작... ({len(watchlist)}종목)")
        self._run_analysis(watchlist, market="US")
        # 분석 후 자동 발굴 종목 순환 (연속 HOLD 종목 제거)
        self._rotate_discovered("US")

    def _analyze_kr_market(self):
        """
        한국 시장 분석 (스케줄 작업)

        통합 워치리스트 = 사용자 관심종목 + 자동 발굴 종목 + 보유 종목
        """
        from config.settings import KR_WATCHLIST
        watchlist = self._build_merged_watchlist("kr", KR_WATCHLIST)
        self.logger.info(f"[스케줄] 한국 시장 분석 시작... ({len(watchlist)}종목)")
        self._run_analysis(watchlist, market="KR")
        self._rotate_discovered("KR")

    def _load_user_watchlist(self, market: str) -> list:
        """
        대시보드 user_settings.json에서 관심종목을 로드합니다.

        Args:
            market: "us" 또는 "kr"

        Returns:
            관심종목 리스트 (없으면 빈 리스트 → 호출측에서 기본값 사용)
        """
        import json
        settings_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "config", "user_settings.json"
        )
        try:
            if os.path.exists(settings_path):
                with open(settings_path, "r", encoding="utf-8") as f:
                    settings = json.load(f)

                key = f"{market}_watchlist"  # "us_watchlist" 또는 "kr_watchlist"
                user_list = settings.get(key, [])

                if user_list:
                    # 한국 종목은 .KS/.KQ 접미사 필요
                    if market == "kr":
                        formatted = []
                        for code in user_list:
                            code = str(code).strip()
                            # 이미 .KS/.KQ가 있으면 그대로, 없으면 .KS 추가
                            if not code.endswith((".KS", ".KQ")):
                                formatted.append(f"{code}.KS")
                            else:
                                formatted.append(code)
                        return formatted
                    return user_list
        except Exception as e:
            self.logger.debug(f"사용자 워치리스트 로드 실패: {e}")

        return []  # 빈 리스트 → 호출측에서 기본 리스트 사용

    # ═══════════════════════════════════════════════════════════════════════
    # 종목 자동 발굴 시스템
    # ═══════════════════════════════════════════════════════════════════════
    #
    # 워치리스트 고정 종목만 보는 한계를 벗어나,
    # 섹터 유니버스(~100종목) + 시장 상위 종목에서 유망 종목을 자동 탐색합니다.
    #
    # 흐름:
    # 1. _run_discovery()  — N번째 분석 사이클마다 발굴 실행
    # 2. _build_merged_watchlist() — 사용자 종목 + 발굴 종목 + 보유 종목 통합
    # 3. _rotate_discovered() — 연속 HOLD 종목 자동 제거
    # ═══════════════════════════════════════════════════════════════════════

    def _build_merged_watchlist(self, market: str, default_list: list) -> list:
        """
        통합 워치리스트 구성

        일반 모드:
            = 사용자 관심종목 (없으면 default fallback)
            + 자동 발굴 종목 (discovery_enabled 시)
            + 현재 보유 종목 (매도 신호 감지)

        ★ 엄격 화이트리스트 모드 (settings.watchlist.strict_mode=True):
            = 사용자 관심종목만 (비어있으면 빈 리스트 — fallback 없음)
            + 현재 보유 종목 (매도/홀드 분석은 필요)
            자동 발굴 종목 제외 (분석/매수 대상 아님)

        Returns:
            중복 제거된 종목 리스트 (최대 max_total_watchlist개)
        """
        from utils.market import is_us_stock, is_kr_stock

        strict = getattr(self.settings.watchlist, "strict_mode", False)

        # 1. 사용자 관심종목
        user_list = self._load_user_watchlist(market.lower())
        if not user_list and not strict:
            # 일반 모드: 비어있으면 기본값 fallback
            user_list = default_list
        # 엄격 모드: 비어있어도 fallback 안 함 (의도적)
        merged = list(user_list)  # 복사

        # 2. 자동 발굴 종목 추가 (엄격 모드에서는 제외)
        if self._discovery_config.enabled and not strict:
            discovered = self._discovered_us if market == "US" else self._discovered_kr
            for sym in discovered:
                if sym not in merged:
                    merged.append(sym)

        # 3. 현재 보유 종목 추가 (매도 누락 방지)
        # 엄격 모드에서도 보유 종목 분석은 필요 (매도 / 손절 / 익절)
        try:
            positions = self.executor.get_positions()
            for pos in positions:
                sym = pos.symbol
                if sym not in merged:
                    is_target = (
                        (market == "US" and is_us_stock(sym)) or
                        (market == "KR" and is_kr_stock(sym))
                    )
                    if is_target:
                        merged.append(sym)
                        if strict:
                            self.logger.debug(
                                f"[엄격모드] 보유종목 {sym} 추가 (매도 신호 감지용, 신규 매수는 차단)"
                            )
                        else:
                            self.logger.debug(f"[워치리스트] 보유종목 추가: {sym}")
        except Exception as e:
            self.logger.debug(f"보유종목 워치리스트 추가 실패: {e}")

        # 4. 최대 크기 제한
        max_size = self._discovery_config.max_total_watchlist
        if len(merged) > max_size:
            # 사용자 종목 + 보유 종목은 유지, 발굴 종목에서 자름
            merged = merged[:max_size]

        # 엄격 모드 로깅
        if strict:
            self.logger.info(
                f"[엄격 화이트리스트] {market} 모드 활성 — "
                f"사용자 지정 {len(user_list)}개 + 보유 {len(merged) - len(user_list)}개 "
                f"= {len(merged)}개 분석 (자동발굴 제외)"
            )

        return merged

    def _run_discovery(self):
        """
        종목 자동 발굴 실행

        섹터 유니버스(~100종목) + 시장 거래량 상위종목을 빠르게 스크리닝하여
        유망 종목을 찾아 _discovered_us / _discovered_kr에 저장합니다.

        빠른 기술적 스크리닝만 사용 (뉴스/팩터 분석은 정기 분석에서 수행)
        """
        if not self._discovery_config.enabled:
            return

        self.logger.info("[발굴] ═══ 종목 자동 발굴 시작 ═══")
        start_time = time.time()

        from config.settings import SECTOR_UNIVERSE, TechnicalConfig
        from collectors.scanner import MarketScanner
        from utils.market import detect_market, is_us_stock, is_kr_stock

        scanner = MarketScanner()
        analyzer = TechnicalAnalyzer(TechnicalConfig())

        # ── 1. 스캔 대상 종목 수집 ──
        # 사용자 관심 섹터의 종목 + 선택적으로 시장 상위 종목
        scan_targets = set()

        # 1a. 섹터 유니버스에서 수집
        user_sectors = self._load_user_interest_sectors()
        if user_sectors:
            for sector_key in user_sectors:
                sector = SECTOR_UNIVERSE.get(sector_key, {})
                for sym in sector.get("stocks", []):
                    scan_targets.add(sym)
        else:
            # 관심 섹터 미설정 시 전체 유니버스 스캔
            for sector in SECTOR_UNIVERSE.values():
                for sym in sector.get("stocks", []):
                    scan_targets.add(sym)

        # 1b. 시장 거래량 상위 종목 (선택)
        if self._discovery_config.include_market_movers:
            movers = self._fetch_market_movers()
            scan_targets.update(movers)

        # 기존 사용자 워치리스트 제외 (이미 분석 대상)
        from config.settings import US_WATCHLIST, KR_WATCHLIST
        user_us = set(self._load_user_watchlist("us") or US_WATCHLIST)
        user_kr = set(self._load_user_watchlist("kr") or KR_WATCHLIST)
        scan_targets -= user_us
        scan_targets -= user_kr

        self.logger.info(f"[발굴] 스캔 대상: {len(scan_targets)}개 종목")

        # ── 2. 빠른 기술적 스크리닝 ──
        us_candidates = []
        kr_candidates = []
        scanned = 0
        failed = 0

        for symbol in scan_targets:
            try:
                market = detect_market(symbol)
                if market == "KR":
                    collector = PriceCollectorKR()
                else:
                    collector = PriceCollectorUS()

                df = collector.safe_collect(symbol, period="3mo")
                if df is None or df.empty or len(df) < 20:
                    failed += 1
                    continue

                # 기술적 지표 계산 + 스캐너 스크리닝
                df_analyzed = analyzer.calculate_all(df)
                scan_result = scanner.scan_symbol(symbol, df_analyzed)
                scanned += 1

                # 최소 우선순위 필터
                if scan_result.priority >= self._discovery_config.min_priority_score:
                    entry = {
                        "symbol": symbol,
                        "priority": scan_result.priority,
                        "signals": scan_result.signals[:3],  # 상위 3개 신호
                        "price": scan_result.latest_price,
                        "change_pct": scan_result.change_pct,
                    }
                    if market == "US":
                        us_candidates.append(entry)
                    else:
                        kr_candidates.append(entry)

            except Exception as e:
                self.logger.debug(f"[발굴] {symbol} 스크리닝 실패: {e}")
                failed += 1
                continue

        # ── 3. 우선순위 정렬 → 상위 N개 선택 ──
        max_per_market = self._discovery_config.max_discovered_per_market

        us_candidates.sort(key=lambda x: x["priority"], reverse=True)
        kr_candidates.sort(key=lambda x: x["priority"], reverse=True)

        self._discovered_us = [c["symbol"] for c in us_candidates[:max_per_market]]
        self._discovered_kr = [c["symbol"] for c in kr_candidates[:max_per_market]]

        # 새로 발굴된 종목의 HOLD 카운터 초기화
        all_discovered = set(self._discovered_us + self._discovered_kr)
        for sym in list(self._discovery_hold_counts.keys()):
            if sym not in all_discovered:
                del self._discovery_hold_counts[sym]
        for sym in all_discovered:
            if sym not in self._discovery_hold_counts:
                self._discovery_hold_counts[sym] = 0

        # ── 4. DB에 저장 (봇 재시작 시 복원용) ──
        self._save_discovered_to_db()

        elapsed = time.time() - start_time
        self.logger.info(
            f"[발굴] ═══ 발굴 완료 ({elapsed:.0f}초) ═══\n"
            f"  스캔: {scanned}개 성공 / {failed}개 실패\n"
            f"  발굴 US: {len(self._discovered_us)}개 {self._discovered_us[:5]}\n"
            f"  발굴 KR: {len(self._discovered_kr)}개 {self._discovered_kr[:5]}"
        )

        # 발굴 결과 상세 로그
        for c in us_candidates[:max_per_market]:
            self.logger.info(
                f"  [US] {c['symbol']}: 우선순위 {c['priority']:.1f} "
                f"({', '.join(c['signals'][:2])})"
            )
        for c in kr_candidates[:max_per_market]:
            self.logger.info(
                f"  [KR] {c['symbol']}: 우선순위 {c['priority']:.1f} "
                f"({', '.join(c['signals'][:2])})"
            )

    def _rotate_discovered(self, market: str):
        """
        자동 발굴 종목 순환 — 연속 HOLD 종목 자동 제거

        정기 분석 결과를 기반으로, 연속으로 HOLD 판정된 종목은
        발굴 리스트에서 제거하여 새 종목에 자리를 양보합니다.
        """
        if not self._discovery_config.enabled:
            return

        discovered = self._discovered_us if market == "US" else self._discovered_kr
        if not discovered:
            return

        limit = self._discovery_config.rotation_hold_limit
        removed = []

        for sym in list(discovered):
            # DB에서 최근 신호 조회 (★ mode 필터 — paper/live 신호 섞임 방지)
            try:
                _sig_mode = getattr(self.executor, "mode", "paper")
                recent_rows = self.db.get_signals(symbol=sym, limit=limit, mode=_sig_mode)
                recent_signals = [r.get("signal_type", "") for r in recent_rows]

                # 최근 N개가 전부 HOLD면 제거
                if len(recent_signals) >= limit and all(s == "HOLD" for s in recent_signals):
                    discovered.remove(sym)
                    self._discovery_hold_counts.pop(sym, None)
                    removed.append(sym)
            except Exception:
                pass

        if removed:
            self.logger.info(
                f"[발굴 순환] {market} 연속 HOLD 제거: {removed}"
            )
            self._save_discovered_to_db()

    def _fetch_market_movers(self) -> set:
        """
        시장 거래량 상위 종목 조회

        한국: pykrx에서 KOSPI/KOSDAQ 거래량 상위 N개
        미국: 사전 정의된 확장 유니버스에서 선택
        """
        movers = set()
        count = self._discovery_config.market_movers_count

        # ── 한국: pykrx 거래량 상위 ──
        try:
            from pykrx import stock as pykrx_stock
            from datetime import datetime as _dt
            today_str = _dt.now().strftime("%Y%m%d")

            for mkt in ("KOSPI", "KOSDAQ"):
                try:
                    df = pykrx_stock.get_market_ohlcv_by_ticker(today_str, market=mkt)
                    if df is not None and not df.empty and "거래량" in df.columns:
                        # 거래량 상위 N개
                        top = df.nlargest(count, "거래량")
                        suffix = ".KS" if mkt == "KOSPI" else ".KQ"
                        for ticker in top.index:
                            movers.add(f"{ticker}{suffix}")
                except Exception as e:
                    self.logger.debug(f"[발굴] pykrx {mkt} 상위종목 실패: {e}")
        except ImportError:
            self.logger.debug("[발굴] pykrx 미설치 → 한국 상위종목 스킵")

        # ── 미국: 확장 유니버스 (S&P 500 주요 대형주) ──
        # yfinance에는 "전체 종목 거래량 순위" API가 없으므로
        # 주요 대형주 중 섹터 유니버스에 없는 종목을 추가 스캔 대상으로
        _US_EXPANDED = [
            "PANW", "NOW", "SHOP", "XYZ", "UBER", "ABNB", "DDOG", "ZS",
            "CRWD", "NET", "FTNT", "MELI", "SE", "SNAP", "PINS",
            "ROKU", "TTD", "BILL", "HUBS", "TWLO", "OKTA", "ZM",
            "DASH", "RBLX", "U", "HOOD", "SOFI", "AFRM", "UPST",
            "CELH", "AXON", "DECK", "ON", "GFS", "WOLF",
        ]
        movers.update(_US_EXPANDED[:count])

        self.logger.debug(f"[발굴] 시장 상위종목: {len(movers)}개 수집")
        return movers

    def _load_user_interest_sectors(self) -> list:
        """대시보드 설정에서 사용자 관심 섹터 로드"""
        import json as _json
        settings_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "config", "user_settings.json"
        )
        try:
            if os.path.exists(settings_path):
                with open(settings_path, "r", encoding="utf-8") as f:
                    settings = _json.load(f)
                return settings.get("interest_sectors", [])
        except Exception:
            pass
        return []

    def _save_discovered_to_db(self):
        """발굴 결과를 DB 캐시에 저장 (봇 재시작 시 복원용)"""
        try:
            import json as _json
            data = {
                "us": self._discovered_us,
                "kr": self._discovered_kr,
                "hold_counts": self._discovery_hold_counts,
                "updated_at": datetime.now().isoformat(),
            }
            self.db.set_cache("discovery_results", data, ttl=86400)  # 24시간
        except Exception as e:
            self.logger.debug(f"[발굴] DB 저장 실패: {e}")

    def _load_discovered_from_db(self):
        """DB에서 이전 발굴 결과 복원"""
        try:
            data = self.db.get_cache("discovery_results")
            if data:
                self._discovered_us = data.get("us", [])
                self._discovered_kr = data.get("kr", [])
                self._discovery_hold_counts = data.get("hold_counts", {})
                self.logger.info(
                    f"[발굴] DB에서 복원: US {len(self._discovered_us)}개, "
                    f"KR {len(self._discovered_kr)}개"
                )
        except Exception as e:
            self.logger.debug(f"[발굴] DB 복원 실패: {e}")

    def get_discovery_status(self) -> dict:
        """발굴 시스템 현재 상태 (대시보드 API용)"""
        return {
            "enabled": self._discovery_config.enabled,
            "discovered_us": self._discovered_us,
            "discovered_kr": self._discovered_kr,
            "total_discovered": len(self._discovered_us) + len(self._discovered_kr),
            "cycle_count": self._discovery_cycle_count,
            "cycle_multiplier": self._discovery_config.cycle_multiplier,
            "next_discovery_in": max(0, self._discovery_config.cycle_multiplier - (self._discovery_cycle_count % self._discovery_config.cycle_multiplier)),
            "hold_counts": self._discovery_hold_counts,
            "config": {
                "max_per_market": self._discovery_config.max_discovered_per_market,
                "max_watchlist": self._discovery_config.max_total_watchlist,
                "min_priority": self._discovery_config.min_priority_score,
                "include_movers": self._discovery_config.include_market_movers,
                "rotation_limit": self._discovery_config.rotation_hold_limit,
            }
        }

    def _run_analysis(self, watchlist: list, market: str):
        """
        감시 종목 분석 + 신호에 따른 주문

        [v2.0] 앙상블 통합 분석:
        1. 기술적 분석 → ModuleScore (technical)
        2. 뉴스 감성 분석 → ModuleScore (sentiment)
        3. ensemble.combine() → 최종 BUY/SELL/HOLD

        [v2.2] 시장 영업시간 체크 + 매도 쿨다운:
        - 장 마감 중에는 분석만 하고 매매 실행은 건너뜀
        - 매도 후 1시간 쿨다운 동안 같은 종목 재매수 차단
        """
        from strategy.ensemble import ModuleScore
        from collectors.news import NewsCollector
        from strategy.factor import FactorAnalyzer

        # ── 시장 영업시간 체크 ──
        # 장 마감 중에 분석은 하되, 매매 실행은 건너뛰어 무의미한 거래 방지
        market_open = self._is_market_open(market)
        if not market_open:
            self.logger.info(
                f"[{market}] 장 마감 중 → 분석만 실행, 매매 주문은 건너뜀 "
                f"(수수료 낭비 방지)"
            )

        # ── 적응형 임계값 갱신 (분석 사이클당 1회) ──
        # VIX/시장체제에 따라 매수 임계값/신뢰도/포지션 크기 자동 조정
        try:
            self._current_thresholds = self.adaptive_thresholds.compute()
            self.logger.info(f"[적응형 임계값] {self._current_thresholds.detail}")
        except Exception as e:
            self.logger.debug(f"[적응형 임계값] 계산 실패 (기본값 유지): {e}")
            self._current_thresholds = None

        # ── 시장 정지 감지 (서킷브레이커/사이드카/VI) ──
        # 실거래 필수 안전장치. CB 발동 시 모든 매매 차단,
        # VI 발동 종목은 그 종목만 매매 보류.
        try:
            # KIS 클라이언트 lazy bind (모의/실거래 모두 활용)
            if self.halt_detector.kis_client is None and hasattr(self.executor, 'access_token'):
                # KISExecutor 자체를 halt_detector에 연결
                self.halt_detector.kis_client = self.executor

            # 보유 종목 추출 (VI 체크용)
            holding_symbols = []
            try:
                for pos in self.executor.get_positions():
                    if pos.quantity > 0:
                        holding_symbols.append(pos.symbol)
            except Exception:
                pass

            self._halt_check_result = self.halt_detector.check(
                holding_symbols=holding_symbols
            )

            r = self._halt_check_result
            if not r.can_trade_new and not r.can_trade_exit:
                self.logger.error(
                    f"[🚨 시장 정지] 모든 매매 차단: {r.detail}"
                )
                self.notifier.send_risk_alert(f"🚨 시장 정지 발동: {r.detail}")
                self.discord.send_risk_alert(f"🚨 시장 정지 발동: {r.detail}")
                # 매매 차단된 사이클은 분석만 하고 매매 스킵
            elif not r.can_trade_new:
                self.logger.warning(
                    f"[⚠️ 신규 매수 차단] {r.detail}"
                )
            elif r.warnings:
                self.logger.info(f"[시장 상태] {r.detail}")
        except Exception as e:
            self.logger.debug(f"[시장 정지 감지] 체크 실패 (정상으로 진행): {e}")
            self._halt_check_result = None

        news_collector = NewsCollector()
        factor_analyzer = FactorAnalyzer()
        results = []

        # [FIX] Collector를 루프 밖에서 1회만 생성 (매 종목마다 생성하면 낭비)
        collector_us = PriceCollectorUS()
        collector_kr = PriceCollectorKR()

        skipped = 0  # 데이터 수집 실패한 종목 수 추적

        for symbol in watchlist:
            try:
                # ── 1. 가격 데이터 수집 ──
                collector = collector_us if market == "US" else collector_kr

                df = collector.safe_collect(symbol, period="6mo")
                if df is None or df.empty:
                    self.logger.warning(
                        f"[{market}] {symbol} 데이터 수집 실패 → 건너뜀"
                    )
                    skipped += 1
                    continue

                # ── 2. 기술적 분석 → ModuleScore ──
                df_analyzed = self.analyzer.calculate_all(df)
                tech_signal = self.analyzer.generate_signal(df_analyzed)

                # 기술적 분석 점수를 -1~+1 범위로 변환
                # BUY: +strength, SELL: -strength, HOLD: 0
                if tech_signal.signal == "BUY":
                    tech_score = tech_signal.strength
                elif tech_signal.signal == "SELL":
                    tech_score = -tech_signal.strength
                else:
                    tech_score = 0.0

                module_scores = [
                    ModuleScore(
                        name="technical",
                        score=tech_score,
                        confidence=min(tech_signal.strength + 0.3, 1.0),
                        reasons=tech_signal.reasons
                    )
                ]

                # ── 3. 팩터 분석 → ModuleScore ──
                try:
                    factor_scores = factor_analyzer.analyze(symbol, df=df_analyzed)
                    if abs(factor_scores.combined) > 0.01:
                        module_scores.append(
                            ModuleScore(
                                name="factor",
                                score=factor_scores.combined,
                                confidence=0.7,
                                reasons=factor_scores.reasons
                            )
                        )
                except Exception as e:
                    self.logger.debug(f"팩터 분석 실패 ({symbol}): {e}")

                # ── 4. 뉴스 감성 분석 → ModuleScore ──
                try:
                    sentiment = news_collector.get_sentiment_summary(symbol)
                    avg_sent = sentiment.get("avg_sentiment", 0.0)
                    news_count = sentiment.get("news_count", 0)

                    # 뉴스가 있을 때만 감성 모듈 추가
                    # 신뢰도: 뉴스 수가 많을수록 높음 (최소 3개는 있어야 의미)
                    if news_count > 0:
                        sent_confidence = min(news_count / 10, 1.0)
                        sent_reasons = []
                        if avg_sent > 0.2:
                            sent_reasons.append(f"뉴스 긍정적 ({avg_sent:.2f})")
                        elif avg_sent < -0.2:
                            sent_reasons.append(f"뉴스 부정적 ({avg_sent:.2f})")
                        else:
                            sent_reasons.append(f"뉴스 중립 ({avg_sent:.2f})")

                        module_scores.append(
                            ModuleScore(
                                name="sentiment",
                                score=avg_sent,  # -1~+1
                                confidence=sent_confidence,
                                reasons=sent_reasons
                            )
                        )
                except Exception as e:
                    self.logger.debug(f"뉴스 감성 수집 실패 ({symbol}): {e}")

                # ── 5. 앙상블 결합 ──
                ensemble_signal = self.ensemble.combine(module_scores)

                # 신호 로그 DB 저장 (앙상블 결과)
                # [안전장치] DB 저장 실패해도 매매 실행은 계속 진행
                # DB가 손상되면 log_signal에서 exception이 발생하는데,
                # 이것이 매매 실행까지 차단하면 안 되므로 별도 try-except 처리
                try:
                    self.db.log_signal(
                        symbol=symbol,
                        signal_type=ensemble_signal.action,
                        confidence=ensemble_signal.confidence,
                        score=ensemble_signal.score,
                        components=ensemble_signal.components,
                        reasons=ensemble_signal.reasons,
                        mode=getattr(self.executor, "mode", "paper"),  # ★ Phase 5
                    )
                except Exception as db_err:
                    self.logger.warning(
                        f"[DB] 신호 로그 저장 실패 ({symbol}): {db_err} "
                        f"→ 매매 실행은 계속 진행"
                    )

                results.append({
                    "symbol": symbol,
                    "signal": ensemble_signal.action,
                    "strength": abs(ensemble_signal.score),
                    "reasons": ensemble_signal.reasons,
                    "price": float(df_analyzed["Close"].iloc[-1]),
                    "atr": float(df_analyzed["ATR"].iloc[-1]) if "ATR" in df_analyzed.columns else 0,
                    "status": "success",
                    "components": ensemble_signal.components,
                })

                # ── 6. 매매 신호 시 처리 ──
                # 상세 로그: 각 종목의 앙상블 판단 과정을 투명하게 기록
                # → 매매가 왜 안 되는지 사용자가 파악할 수 있도록
                self.logger.info(
                    f"[신호] {symbol}: {ensemble_signal.action} "
                    f"(점수={ensemble_signal.score:+.3f}, "
                    f"신뢰도={ensemble_signal.confidence:.2f}, "
                    f"임계값=±0.2) "
                    f"{'→ 매수 시도!' if ensemble_signal.action == 'BUY' and ensemble_signal.confidence > (0.03 if self.executor.paper else 0.15) else ''}"
                    f"{'→ 매도 시도!' if ensemble_signal.action == 'SELL' else ''}"
                    f"{'→ HOLD (관망)' if ensemble_signal.action == 'HOLD' else ''}"
                )
                # 모듈별 기여도도 디버그 로그로 기록
                for comp_name, comp_val in ensemble_signal.components.items():
                    self.logger.debug(f"  └ {comp_name}: {comp_val:+.3f}")

                # ── ★ 시장 정지 / VI 차단 체크 ──
                # 서킷브레이커 발동 시 모든 매매 차단,
                # VI 발동 종목은 개별 매매 보류
                halt_block_this_symbol = False
                if self._halt_check_result:
                    if symbol in self._halt_check_result.block_symbols:
                        self.logger.warning(
                            f"[VI 차단] {symbol}: 변동성 완화장치 발동 중 → 매매 보류"
                        )
                        halt_block_this_symbol = True

                # ── ★ ExitManager 자동 청산 체크 (앙상블 신호 이전) ──
                # 보유 종목이라면 손절/익절/트레일링 스탑을 우선 체크
                # 청산 조건 발동 시 즉시 매도하고 다음 종목으로
                # 단, CB 발동 시 매도도 차단 (can_trade_exit=False)
                # VI 발동 종목은 단일가 매매라 슬리피지 크므로 보류
                can_exit_now = (
                    market_open
                    and not halt_block_this_symbol
                    and (self._halt_check_result is None
                         or self._halt_check_result.can_trade_exit)
                )
                if can_exit_now:
                    # ★ 실시간 시세 우선 — 손절/익절을 현재가에 즉시 반응시킴.
                    #   보유(ExitManager 등록) 종목만 조회해 불필요한 API 호출 방지.
                    #   실패 시 일봉 종가로 자동 fallback (봇 중단 방지).
                    rt_price = None
                    if self.exit_manager.get_state_dict(symbol):
                        rt_price = self._get_realtime_price(symbol)
                    current_price = (rt_price if rt_price
                                     else float(df_analyzed["Close"].iloc[-1]))
                    exit_decision = self.exit_manager.evaluate(symbol, current_price)

                    # ★ Phase 11 BUG-5 FIX: 매 evaluate마다 trailing/highest를 DB에 영속화
                    # 이전 버그: should_exit=True일 때만 저장 → 청산 안 한 사이의 트레일링 진행이
                    # 봇 재시작 시 사라져서 멀티데이 추세에서 손익 보호 무력화
                    # 수정: 매 분석마다 저장 (가벼운 UPSERT)
                    try:
                        self._save_exit_state(symbol)
                    except Exception as save_err:
                        self.logger.debug(f"[ExitState 저장] {symbol}: {save_err}")

                    if exit_decision.should_exit:
                        self.logger.info(
                            f"[자동 청산] {symbol}: {exit_decision.reason.value} | "
                            f"{exit_decision.detail}"
                        )
                        if exit_decision.sell_ratio >= 1.0:
                            # 전량 매도
                            self._execute_sell(
                                symbol, exit_reason=exit_decision.reason.value
                            )
                        else:
                            # 분할 매도 (★ decision 전달로 atomic state 변경)
                            self._execute_partial_sell(
                                symbol,
                                ratio=exit_decision.sell_ratio,
                                exit_reason=exit_decision.reason.value,
                                decision=exit_decision,
                            )
                        # 청산 처리 후 이번 사이클은 더 이상 신호 처리 안 함
                        continue

                # ── 매수/매도 실행 조건 ──
                # [v2.2] 장 마감 중에는 매매 실행을 건너뜀
                if not market_open:
                    if ensemble_signal.action in ("BUY", "SELL"):
                        self.logger.info(
                            f"[{market} 장외] {symbol}: {ensemble_signal.action} "
                            f"신호 감지 → 장 마감 중이므로 매매 보류"
                        )
                    # 분석 결과는 기록하되, 매매는 실행하지 않음
                elif ensemble_signal.action == "BUY":
                    # ── ★ 시장 정지 체크: CB/사이드카 발동 시 매수 차단 ──
                    if (self._halt_check_result
                            and not self._halt_check_result.can_trade_new):
                        self.logger.warning(
                            f"[시장 정지] {symbol}: 신규 매수 차단 "
                            f"({self._halt_check_result.detail})"
                        )
                        continue
                    # VI 발동 종목 매수 차단
                    if halt_block_this_symbol:
                        continue

                    # ── 적응형 임계값 적용 ──
                    # VIX/시장체제에 따라 매수 임계값 + 신뢰도 + 포지션 크기 조절
                    if self._current_thresholds is None:
                        self._current_thresholds = self.adaptive_thresholds.compute()
                    th = self._current_thresholds
                    min_confidence = th.min_confidence

                    # 위기장(VIX>30) + Bear장에서는 매수 차단 가능
                    if th.regime_volatility == "crisis" and th.regime_market == "bear":
                        self.logger.warning(
                            f"[적응형] {symbol}: {th.detail} → 매수 차단 (위기+약세)"
                        )
                        continue

                    if ensemble_signal.confidence > min_confidence:
                        # [v2.2] 매도 쿨다운 체크
                        if self._is_in_cooldown(symbol):
                            self.logger.info(
                                f"[쿨다운] {symbol}: 최근 매도 후 재매수 대기 중 → 건너뜀"
                            )
                        else:
                            self._execute_buy(symbol, df_analyzed, tech_signal,
                                              ensemble_signal, module_scores,
                                              size_multiplier=th.position_size_multiplier)
                elif ensemble_signal.action == "SELL":
                    # ── ★ 시장 정지 체크: CB 발동 시 매도도 차단 ──
                    if (self._halt_check_result
                            and not self._halt_check_result.can_trade_exit):
                        self.logger.warning(
                            f"[시장 정지] {symbol}: 매도도 차단 "
                            f"({self._halt_check_result.detail})"
                        )
                        continue
                    if halt_block_this_symbol:
                        continue
                    self._execute_sell(symbol)

            except Exception as e:
                self.logger.error(f"분석 오류 ({symbol}): {e}")

        # ── 분석 결과 요약 로그 ──
        total = len(watchlist)
        analyzed = len(results)
        self.logger.info(
            f"[{market}] 분석 완료: {analyzed}/{total}개 성공, "
            f"{skipped}개 데이터 실패"
        )

        # 전체 실패 시 경고 (데이터 소스 문제일 가능성 높음)
        if analyzed == 0 and total > 0:
            self.logger.error(
                f"[{market}] ⚠️ 모든 종목 분석 실패! "
                f"데이터 소스(pykrx/yfinance) 상태를 확인하세요."
            )

        # 일일 리포트 전송
        if results:
            self.notifier.send_daily_report(results)

    def _ensure_kis_quote_client(self):
        """
        KIS 실시간 시세 전용 클라이언트 (lazy 생성).

        모의거래 모드에서는 executor가 PaperExecutor라 KIS 연결이 없으므로,
        실시간 시세 조회용 KIS 클라이언트를 별도로 1개 둡니다.
        시세 조회는 체결과 무관하므로 모의/실거래 자격증명 모두 동작하며,
        토큰은 캐시 파일을 공유하므로 추가 발급이 일어나지 않습니다.

        실패(자격증명 없음/연결 실패) 시 None을 반환하고, 한 번 실패하면
        다시 시도하지 않아 매 사이클이 느려지지 않습니다.
        """
        if getattr(self, "_kis_quote_client", None) is not None:
            return self._kis_quote_client
        if getattr(self, "_kis_quote_client_failed", False):
            return None
        try:
            import os
            app_key = os.environ.get("KIS_APP_KEY", "")
            app_secret = os.environ.get("KIS_APP_SECRET", "")
            if not app_key or not app_secret:
                self.logger.info("[실시간시세] KIS 자격증명 없음 → 일봉 종가로 대체")
                self._kis_quote_client_failed = True
                return None
            from executor.kis_executor import KISExecutor
            paper = os.environ.get("KIS_PAPER", "true").lower() in ("true", "1", "yes")
            client = KISExecutor(paper=paper)
            if client.connect():
                self._kis_quote_client = client
                self.logger.info(
                    f"[실시간시세] KIS 시세 클라이언트 준비 완료 "
                    f"({'모의' if paper else '실거래'} 도메인)"
                )
                return client
            self.logger.warning(
                "[실시간시세] KIS 시세 클라이언트 연결 실패 → 일봉 종가로 대체"
            )
            self._kis_quote_client_failed = True
            return None
        except Exception as e:
            self.logger.warning(
                f"[실시간시세] KIS 시세 클라이언트 생성 실패: {e} → 일봉 종가로 대체"
            )
            self._kis_quote_client_failed = True
            return None

    def _get_realtime_price(self, symbol: str):
        """
        한국 주식의 KIS 실시간 시세(~1초 지연)를 반환합니다.

        - 실거래(KIS/dual): executor 자체가 시세를 제공 → 그대로 사용
        - 모의거래: 시세 전용 KIS 클라이언트(_ensure_kis_quote_client) 사용
        - 미국 주식 / 조회 실패: None 반환 → 호출자가 일봉 종가로 fallback

        실거래·모의거래 모두 동일하게 실시간 시세를 받게 하는 단일 진입점입니다.
        """
        # 미국 종목은 KIS 시세 대상이 아님 → fallback
        try:
            from utils.market import is_us_stock
            if is_us_stock(symbol):
                return None
        except Exception:
            pass

        # 실거래 KIS/dual executor는 자체적으로 get_current_price 보유.
        # PaperExecutor는 없으므로 시세 전용 KIS 클라이언트를 사용.
        if hasattr(self.executor, "get_current_price"):
            client = self.executor
        else:
            client = self._ensure_kis_quote_client()

        if client is None:
            return None
        try:
            price = client.get_current_price(symbol)
            if price and float(price) > 0:
                return float(price)
        except Exception as e:
            self.logger.debug(f"[실시간시세] {symbol} 조회 실패: {e}")
        return None

    def _execute_buy(self, symbol: str, df, signal,
                     ensemble_signal=None, module_scores=None,
                     size_multiplier: float = 1.0):
        """
        매수 실행 (안전장치 체크 + 포지션 유형 분류 포함)

        Parameters:
            size_multiplier: 포지션 크기 배수 (적응형 임계값에서 0.5~1.0 전달)
                             변동성이 높으면 1.0 미만으로 포지션 축소
        """
        # ── 최대 낙폭(MDD) 한도 초과 시 신규 매수 자동 중단 ──
        # _check_risk가 MDD > max_drawdown 감지 시 이 래치를 켠다.
        # 손절·매도는 막지 않으므로 보유 포지션은 정상 청산 가능.
        if getattr(self, "_risk_halt_new_buys", False):
            self.logger.warning(
                f"[낙폭 차단] {symbol}: 최대 낙폭 한도 초과 — 신규 매수 중단 상태"
            )
            return

        # ── 추가 매수(피라미딩) 허용 — 종목당 한도 안에서 ──
        # 이미 보유 중이어도 매수 진행. SafetyGuard가 '기존 보유분 + 신규 주문'이
        # 종목당 최대 비중(max_position_weight)을 넘지 않도록 수량을 자동 조정/거부.
        existing_positions = self.executor.get_positions()

        # ★ CRITICAL: 포지션 조회 자체가 실패했으면 보유 현황을 알 수 없으므로 매수 보류
        # 이전 버그: API 실패 시 []를 반환 → "보유 없음"으로 오인 → 한도 초과 매수 위험
        if not self.executor.positions_query_succeeded():
            self.logger.error(
                f"[매수 보류] {symbol}: 브로커 포지션 조회 실패 — "
                f"보유 현황 불확실하여 매수 보류 (한도 초과 방지)"
            )
            return

        already_held = any(
            getattr(p, "symbol", None) == symbol and getattr(p, "quantity", 0) > 0
            for p in existing_positions
        )
        if already_held:
            self.logger.info(
                f"[추가 매수] {symbol}: 이미 보유 중 — 종목 한도 내에서 추가 매수 시도"
            )

        # ── ★ 엄격 화이트리스트 모드: 사용자 지정 종목만 매수 허용 ──
        # 자동 발굴 종목이 분석되어 BUY 신호가 와도 매수 차단
        # (안전망: _build_merged_watchlist에서 이미 발굴 종목 제외했지만 이중 확인)
        if getattr(self.settings.watchlist, "strict_mode", False):
            from utils.market import is_us_stock
            market = "us" if is_us_stock(symbol) else "kr"
            allowed = self._load_user_watchlist(market)
            if symbol not in allowed:
                self.logger.warning(
                    f"[엄격 화이트리스트] {symbol}: 사용자 워치리스트에 없는 종목 → 매수 차단"
                )
                return

        # ── 포지션 유형 사전 분류 + 활성 여부 체크 ──
        # 매수 실행 전에 단타/스윙/장기 분류 → 비활성 유형이면 매수 차단
        # (이전 코드는 체결 후 분류했지만, 사용자 토글을 존중하려면 사전 차단 필요)
        from strategy.ensemble import classify_position_type
        enabled_types = {
            "short": self.settings.position_types.short_enabled,
            "swing": self.settings.position_types.swing_enabled,
            "long": self.settings.position_types.long_enabled,
        }
        pos_info_pre = None
        if ensemble_signal and module_scores:
            pos_info_pre = classify_position_type(
                module_scores, ensemble_signal, enabled_types=enabled_types
            )
            if pos_info_pre is None:
                self.logger.warning(
                    f"[매수 차단] {symbol}: 모든 포지션 유형이 비활성화됨 "
                    f"(단타={enabled_types['short']}, "
                    f"스윙={enabled_types['swing']}, "
                    f"장기={enabled_types['long']})"
                )
                return

        # ★ float() 변환: pandas/numpy int64/float64 → Python native
        # JSON 직렬화 실패 + 타입 에러 방지 (WebSocket 브로드캐스트용)
        cached_price = float(df["Close"].iloc[-1])
        atr = float(df["ATR"].iloc[-1]) if "ATR" in df.columns else cached_price * 0.02

        # ★ Phase 12: 가격 유효성 가드 — 0/NaN/음수면 매수 불가
        # 데이터 소스 오류 시 0 division / NaN 전파 방지
        import math
        if not (cached_price > 0) or math.isnan(cached_price) or math.isinf(cached_price):
            self.logger.warning(
                f"[매수 취소] {symbol}: 유효하지 않은 가격 {cached_price} "
                f"(데이터 소스 오류 가능) → 매수 보류"
            )
            return

        # ★ CRITICAL: 분석 시점 가격이 아닌 실시간 가격으로 사이징
        # 이전 버그: 분석(09:05) → 매수 실행(09:10) 사이 갭으로 포지션 크기 오류
        # 5% 이상 차이나면 신호 신선도가 의심되므로 매수 보류
        price = cached_price
        try:
            # ★ 실시간 시세 우선 (모의·실거래 공통) — _get_realtime_price가
            #   실거래는 executor, 모의거래는 KIS 시세 클라이언트로 라우팅.
            live_price = self._get_realtime_price(symbol)
            if live_price and live_price > 0:
                deviation = abs(live_price - cached_price) / cached_price
                if deviation > 0.05:
                    self.logger.warning(
                        f"[매수 보류] {symbol}: 분석가 {cached_price:,.0f} vs "
                        f"실시간 {live_price:,.0f} (차이 {deviation*100:.1f}%, 한도 5%) "
                        f"— 빠른 가격 변동으로 신호 부정확 → 다음 사이클에서 재평가"
                    )
                    return
                price = live_price  # 실시간 가격으로 사이징/주문
        except Exception as e:
            self.logger.debug(f"[매수] 실시간 가격 조회 실패 (캐시 가격 사용): {e}")

        self.logger.info(
            f"[매수 시도] {symbol}: 현재가={price:,.0f}, ATR={atr:,.0f}, "
            f"신호강도={signal.strength:.2f}"
        )

        # ── Kelly 사이징 통계 (sizing_method이 'kelly'일 때만) ──
        # 닫힌 거래가 kelly_min_trades 이상 쌓였을 때만 Kelly 통계를 넘긴다.
        # 부족하면 통계 0 → calculate()가 fixed로 폴백하되, 이전과 달리
        # '조용히'가 아니라 로그를 남긴다.
        kelly_win_rate = kelly_avg_win = kelly_avg_loss = 0.0
        if self.settings.risk.sizing_method == "kelly" and self.db is not None:
            try:
                _kmode = getattr(self.executor, "mode", "paper")
                _kstats = self.db.get_kelly_stats(mode=_kmode)
                _kn = _kstats.get("sample_size", 0)
                _kmin = getattr(self.settings.risk, "kelly_min_trades", 20)
                if _kn >= _kmin:
                    kelly_win_rate = _kstats.get("win_rate", 0.0)
                    kelly_avg_win = _kstats.get("avg_win", 0.0)
                    kelly_avg_loss = _kstats.get("avg_loss", 0.0)
                    self.logger.info(
                        f"[Kelly] 거래이력 {_kn}건 (≥{_kmin}) → Kelly 적용 "
                        f"(승률 {kelly_win_rate*100:.0f}%)"
                    )
                else:
                    self.logger.info(
                        f"[Kelly] 거래이력 부족 ({_kn}/{_kmin}건) → fixed 사이징 사용"
                    )
            except Exception as e:
                self.logger.debug(f"[Kelly] 통계 조회 실패 → fixed 사용: {e}")

        # 포지션 사이징
        pos_size = self.position_sizer.calculate(
            price=price,
            atr=atr,
            method=self.settings.risk.sizing_method,
            confidence=signal.strength,
            win_rate=kelly_win_rate,
            avg_win=kelly_avg_win,
            avg_loss=kelly_avg_loss,
            symbol=symbol,  # ★ 환율 변환: USD 종목이면 KRW 환산 후 사이징
        )

        # ── 적응형 사이즈 배수 적용 ──
        # 변동성 高 / Bear 체제 시 0.5~0.7로 축소
        if size_multiplier < 1.0 and pos_size.shares > 0:
            original_shares = pos_size.shares
            pos_size.shares = max(1, int(pos_size.shares * size_multiplier))
            pos_size.value = pos_size.shares * price
            self.logger.info(
                f"[적응형] {symbol}: 포지션 축소 "
                f"{original_shares}주 → {pos_size.shares}주 "
                f"(배수 {size_multiplier:.2f})"
            )

        if pos_size.shares <= 0:
            self.logger.warning(
                f"[매수 취소] {symbol}: 포지션 사이징 결과 0주 "
                f"(가격={price:,.0f}, ATR={atr:,.0f})"
            )
            return

        self.logger.info(
            f"[포지션 사이징] {symbol}: {pos_size.shares}주 × {price:,.0f} = "
            f"{pos_size.shares * price:,.0f}"
        )

        # ── 안전장치 체크 (SafetyGuard) ──
        # ★ 핵심: check_order()는 이제 (bool, str, int) 3개 값을 반환
        # - 한도 초과 시 수량을 자동으로 줄여서라도 매수 진행
        # - 수량 조정으로도 해결 불가능한 경우에만 거부 (킬 스위치, 일일 손실 등)
        account = self.executor.get_account()
        positions = self.executor.get_positions()
        safe, reason, adjusted_shares = self.safety.check_order(
            symbol=symbol,
            side="BUY",
            quantity=pos_size.shares,
            price=price,
            account_equity=account.total_equity,
            positions=positions,
        )
        if not safe:
            self.logger.warning(f"[매수 거부] {symbol}: {reason}")
            self.notifier.send_risk_alert(f"⚠️ 매수 거부: {symbol}\n{reason}")
            return

        # ★ SafetyGuard가 수량을 조정했으면 반영 (CRITICAL FIX: 통화 환산)
        # 이전 버그: pos_size.value = shares × price (native)
        #   USD 종목이면 native=USD, account.total_equity=KRW → pct_of_capital이 1370배 작아짐
        #   포지션 한도 검사 무력화 (예: AAPL 100주 397% 노출인데 0.3%로 표시)
        if adjusted_shares != pos_size.shares:
            self.logger.info(
                f"[수량 조정] {symbol}: {pos_size.shares}주 → {adjusted_shares}주 "
                f"(SafetyGuard 한도 적용)"
            )
            pos_size.shares = adjusted_shares
            # native 단위 가치
            pos_size.value = adjusted_shares * price
            # KRW 환산 후 capital 대비 비중 계산 (total_equity = KRW)
            try:
                from utils.market import to_krw
                value_krw = to_krw(symbol, pos_size.value)
            except Exception:
                value_krw = pos_size.value  # fallback
            denom = float(account.total_equity) if account.total_equity > 0 else 1.0
            pos_size.pct_of_capital = value_krw / denom

        # ── 실거래 시 대기 (취소 기회) ──
        self.safety.wait_before_order()

        # 모의매매기에 현재가 설정
        if hasattr(self.executor, 'set_current_price'):
            self.executor.set_current_price(symbol, price)

        # ── 매매 결정 상세 (decision_json) 생성 ──
        # 거래 이력 모달에서 "왜 매수했는지" 클릭하면 보이는 내용
        import json as _dj
        decision = {
            "type": "BUY",
            "trigger": "ensemble_signal",
            "ensemble": {
                "action": ensemble_signal.action if ensemble_signal else "BUY",
                "score": round(float(ensemble_signal.score), 4) if ensemble_signal else 0,
                "confidence": round(float(ensemble_signal.confidence), 4) if ensemble_signal else 0,
                "components": {
                    k: round(float(v), 4)
                    for k, v in (ensemble_signal.components.items() if ensemble_signal else {})
                },
                "reasons": list(ensemble_signal.reasons[:5]) if ensemble_signal else [],
            },
            "thresholds": (
                {
                    "buy_threshold": round(float(self._current_thresholds.buy_threshold), 4),
                    "sell_threshold": round(float(self._current_thresholds.sell_threshold), 4),
                    "min_confidence": round(float(self._current_thresholds.min_confidence), 4),
                    "vix": round(float(self._current_thresholds.vix_value), 2),
                    "regime_volatility": self._current_thresholds.regime_volatility,
                    "regime_market": self._current_thresholds.regime_market,
                    "position_size_multiplier": round(float(self._current_thresholds.position_size_multiplier), 2),
                }
                if self._current_thresholds else {}
            ),
            "indicators": {
                "price": round(price, 4),
                "atr": round(atr, 4),
                "rsi": (
                    round(float(df["RSI"].iloc[-1]), 2)
                    if "RSI" in df.columns else None
                ),
                "macd": (
                    round(float(df["MACD"].iloc[-1]), 4)
                    if "MACD" in df.columns else None
                ),
                "sma_20": (
                    round(float(df["SMA_20"].iloc[-1]), 4)
                    if "SMA_20" in df.columns else None
                ),
                "sma_50": (
                    round(float(df["SMA_50"].iloc[-1]), 4)
                    if "SMA_50" in df.columns else None
                ),
            },
            "sizing": {
                "method": str(self.settings.risk.sizing_method),
                "shares": int(pos_size.shares),
                "value_native": round(pos_size.shares * price, 2),
                "size_multiplier": round(float(size_multiplier), 2),
            },
            "timestamp": datetime.now().isoformat(),
        }
        try:
            decision_json_str = _dj.dumps(decision, ensure_ascii=False)
        except (TypeError, ValueError):
            decision_json_str = "{}"

        # 주문 실행 (decision 정보 첨부)
        order = self.executor.buy_market(
            symbol, pos_size.shares,
            strategy="ensemble",
            decision_json=decision_json_str,
        )

        if order.status.value == "filled":
            # ★ Phase 12 FIX: 실제 체결 수량 사용 (KIS 부분 체결 대응)
            # 이전 버그: pos_size.shares(주문량)로 DB/알림/SafetyGuard 기록 →
            # KIS가 5/10주만 체결하면 DB엔 10주, 실제 5주 → 불일치
            # order.filled_quantity는 KISExecutor._poll_fill_and_record가 실제 체결분으로 설정.
            # paper executor는 항상 전량 체결이므로 filled_quantity=0일 수 있음 → fallback
            actual_qty = order.filled_quantity if order.filled_quantity > 0 else pos_size.shares
            actual_price = order.filled_price if order.filled_price and order.filled_price > 0 else price

            if actual_qty != pos_size.shares:
                self.logger.warning(
                    f"[부분 체결] {symbol}: 주문 {pos_size.shares}주 → 실제 체결 {actual_qty}주"
                )

            self.logger.info(
                f"[매수 체결] {symbol} {actual_qty}주 @ {actual_price:.2f}"
            )
            # 알림 금액은 KRW 기준 (USD 종목이면 환산) — 실제 체결분 기준
            total_krw = to_krw(symbol, actual_qty * actual_price)
            self.notifier.send_trade_executed(symbol, "BUY", actual_qty, actual_price, total_krw)
            self.discord.send_trade_executed(symbol, "BUY", actual_qty, actual_price, total_krw)
            # DB 저장은 paper_executor._execute_order() 내부에서 이미 처리됨
            # 안전장치에 거래 기록 (실제 체결분 기준)
            self.safety.record_trade(symbol, "BUY", value=total_krw)

            # ── 포지션 메타데이터 준비 (예외 안 나는 단순 dict 접근) ──
            # ★ 사전 분류 결과 재사용 (사용자 토글 반영됨)
            pos_info = pos_info_pre or {
                "position_type": "스윙", "position_type_en": "swing",
                "holding_period": "1~4주", "atr_stop_multiplier": 2.0,
                "rr_ratio": 2.0, "classification_reason": "기본값"
            }
            # .get()으로 안전 접근 — 키 누락 시에도 예외 없이 기본값
            type_atr_mult = pos_info.get("atr_stop_multiplier", 2.0)
            type_rr = pos_info.get("rr_ratio", 2.0)
            holding_days_max = {"단타": 5, "스윙": 30, "장기": 180}.get(
                pos_info.get("position_type", "스윙"), 30
            )

            # ── ★ 1단계: ExitManager 등록 (최우선 — 자동 손절 즉시 보장) ──
            # 이전 버그: 분류/DB 작업과 한 try 블록 → DB 오류 시 register_entry에
            # 도달 못 함 → 브로커엔 포지션 있는데 ExitManager엔 없음 → 손절 안 됨.
            # 수정: register_entry를 별도 try로 분리하고 DB 작업보다 먼저 실행.
            try:
                if symbol in self.exit_manager.states:
                    # ★ 추가 매수(피라미딩) — 기존 손절/트레일링 상태를 그대로 유지.
                    #   register_entry를 다시 부르면 손절선·트레일링·부분익절
                    #   진행이 리셋되므로, 이미 등록된 종목은 건너뛴다 (A안).
                    self.logger.info(
                        f"[추가 매수] {symbol}: ExitManager 기존 손절 상태 유지 "
                        f"(첫 진입 기준 손절선 보존)"
                    )
                else:
                    self.exit_manager.register_entry(
                        symbol=symbol,
                        entry_price=actual_price,  # ★ 실제 체결가 기준 손절/익절
                        atr=atr,
                        atr_stop_mult=type_atr_mult,
                        rr_ratio=type_rr,
                        holding_days_max=holding_days_max,
                    )
            except Exception as e:
                # ExitManager 등록 실패는 CRITICAL — 자동 손절이 안 되므로 경고 강조
                self.logger.error(
                    f"[🚨 CRITICAL] {symbol} ExitManager 등록 실패 — "
                    f"이 포지션은 자동 손절이 안 됩니다! 수동 감시 필요: {e}"
                )
                try:
                    self.notifier.send_risk_alert(
                        f"⚠️ {symbol} 매수됨 but 자동손절 등록 실패 — 수동 확인 필요"
                    )
                except Exception:
                    pass

            # ── 2단계: DB 메타데이터 저장 (실패해도 손절은 이미 보호됨) ──
            try:
                import json as _json
                reasons_list = []
                if ensemble_signal:
                    reasons_list = ensemble_signal.reasons[:5]

                stop_price = actual_price - (atr * type_atr_mult)
                target_price = actual_price + (atr * type_atr_mult * type_rr)

                self.logger.info(
                    f"[포지션 유형] {symbol}: {pos_info.get('position_type', '스윙')} "
                    f"({pos_info.get('holding_period', '')}) | "
                    f"목표 {target_price:,.0f} / 손절 {stop_price:,.0f} | "
                    f"분류근거: {pos_info.get('classification_reason', '')}"
                )

                if self.db:
                    self.db.update_position(
                        symbol=symbol,
                        quantity=actual_qty,
                        avg_price=actual_price,
                        current_price=actual_price,
                        position_type=pos_info.get("position_type", "스윙"),
                        position_type_en=pos_info.get("position_type_en", "swing"),
                        target_price=round(target_price, 2),
                        stop_price=round(stop_price, 2),
                        reasons_json=_json.dumps(reasons_list, ensure_ascii=False),
                        holding_period=pos_info.get("holding_period", ""),
                        bought_at=__import__("datetime").datetime.now().isoformat(),
                        mode=getattr(self.executor, "mode", "paper"),  # ★ Phase 5
                    )
                self._save_exit_state(symbol)  # DB 동기화
            except Exception as e:
                self.logger.warning(
                    f"[포지션 메타 저장] {symbol}: DB 저장 실패 "
                    f"(매수+손절등록은 성공, 다음 사이클에서 재동기화): {e}"
                )

    def _execute_sell(self, symbol: str, exit_reason: str = "signal_sell"):
        """
        매도 실행 (보유 시, 안전장치 체크 포함)

        Parameters:
            symbol: 종목 코드
            exit_reason: 청산 사유 ("signal_sell", "stop_loss", "take_profit_2",
                         "trailing_stop", "time_stop")
        """
        # 보유하지 않은 종목이면 스킵
        existing_positions = self.executor.get_positions()
        held = None
        for pos in existing_positions:
            if pos.symbol == symbol and pos.quantity > 0:
                held = pos
                break
        if not held:
            self.logger.debug(f"[매도 스킵] {symbol}: 미보유")
            return

        # 매도도 안전장치 기본 체크 (킬스위치, 일일 횟수 등)
        account = self.executor.get_account()
        safe, reason, _ = self.safety.check_order(
            symbol=symbol,
            side="SELL",
            quantity=held.quantity,
            price=held.current_price,
            account_equity=account.total_equity,
            positions=existing_positions,
        )
        if not safe:
            self.logger.warning(f"[매도 거부] {symbol}: {reason}")
            return

        # 모의매매기에 현재가 설정 — 실시간 시세 우선 (모의 체결도 현실적으로)
        if hasattr(self.executor, 'set_current_price'):
            _rt = self._get_realtime_price(symbol)
            self.executor.set_current_price(
                symbol, _rt if _rt else held.current_price
            )

        # ── 청산 결정 상세 생성 ──
        # 거래 이력 모달에서 "왜 매도했는지" 클릭 시 보이는 내용
        import json as _dj
        exit_state = self.exit_manager.get_state_dict(symbol)
        entry_price = (exit_state["entry_price"]
                       if exit_state else float(held.avg_price))
        current_price = float(held.current_price)
        pnl_pct = ((current_price / entry_price) - 1) * 100 if entry_price > 0 else 0
        decision = {
            "type": "SELL",
            "trigger": exit_reason,
            "exit_summary": _exit_reason_kr(exit_reason),
            "prices": {
                "entry_price": round(entry_price, 4),
                "current_price": round(current_price, 4),
                "pnl_pct": round(pnl_pct, 2),
                "quantity": int(held.quantity),
            },
            "exit_state": exit_state or {},
        }
        # 진입 이후 경과 시간
        if exit_state and exit_state.get("entry_time"):
            try:
                entry_dt = datetime.fromisoformat(exit_state["entry_time"])
                decision["holding_days"] = (datetime.now() - entry_dt).days
            except (ValueError, TypeError):
                pass
        decision["timestamp"] = datetime.now().isoformat()
        try:
            decision_json_str = _dj.dumps(decision, ensure_ascii=False)
        except (TypeError, ValueError):
            decision_json_str = "{}"

        order = self.executor.close_position(
            symbol, strategy=exit_reason, decision_json=decision_json_str,
        )
        if order and order.status.value == "filled":
            # ★ 체결가 안전 처리 — KIS phantom 방어
            # KIS가 드물게 filled_price=0/None을 반환하면:
            #  - None → quantity*None = TypeError → 청산 후처리 전부 누락
            #  - 0    → raw_pnl=(0-avg)*qty = 가짜 큰 손실 → kill_switch 오발동
            # 체결가 미수신 시 분석 시점 현재가 → 평균매수가 순으로 근사치 사용.
            price = order.filled_price
            if not price or price <= 0:
                fallback = float(held.current_price) if held.current_price and held.current_price > 0 \
                           else float(held.avg_price)
                self.logger.warning(
                    f"[매도] {symbol} 체결가 미수신(={order.filled_price}) → "
                    f"근사가 ₩{fallback:,.0f} 사용. 실현 PnL은 근사치 — "
                    f"정확한 금액은 KIS 거래내역에서 확인하세요."
                )
                price = fallback
            # ★ Phase 12 FIX: 실제 체결 수량 사용 (KIS 부분 체결 대응)
            # _execute_buy와 동일 — order.filled_quantity가 0이면(paper 등) 보유수량 fallback.
            # 부분 체결 시 held.quantity(주문량)로 기록하면 실현 PnL·알림·ExitManager가 어긋남.
            actual_qty = (order.filled_quantity
                          if order.filled_quantity and order.filled_quantity > 0
                          else held.quantity)
            fully_closed = actual_qty >= held.quantity
            if not fully_closed:
                self.logger.warning(
                    f"[부분 체결] {symbol}: 전량청산 주문 {held.quantity}주 중 "
                    f"{actual_qty}주만 체결 — 잔여 {held.quantity - actual_qty}주는 "
                    f"ExitManager 유지(손절 보호 지속)"
                )
            # 알림 금액은 KRW 기준 (USD 종목이면 환산)
            total_krw = to_krw(symbol, actual_qty * price)
            # ★ CRITICAL: 실현 PnL 계산 → SafetyGuard에 전달 (없으면 daily_pnl이 영원히 0이 됨)
            # PaperExecutor는 order에 realized_pnl을 attach하지 않지만 trade_history 최근 항목에 있음
            realized_pnl = 0.0
            try:
                if hasattr(self.executor, "trade_history") and self.executor.trade_history:
                    last_trade = self.executor.trade_history[-1]
                    if last_trade.get("symbol") == symbol and last_trade.get("side", "").lower() == "sell":
                        realized_pnl = float(last_trade.get("realized_pnl", 0) or 0)
                # KIS executor는 trade_history가 없으므로 PnL을 직접 계산
                # price는 위에서 유효성 보장됨 → 가짜 손실 발생 안 함
                if realized_pnl == 0.0 and held.avg_price > 0:
                    # 평균 매수가 vs 매도가 차이 × 실제 체결수량 → KRW 환산
                    raw_pnl = (price - held.avg_price) * actual_qty
                    realized_pnl = to_krw(symbol, raw_pnl)
            except Exception as pnl_err:
                self.logger.debug(f"[매도] PnL 계산 실패 ({pnl_err}) → 0 사용")

            self.logger.info(
                f"[매도 체결] {symbol} {actual_qty}주 @ {price:,.2f} "
                f"{'전량 청산' if fully_closed else '부분 체결'} "
                f"({exit_reason}) | 실현PnL ₩{realized_pnl:,.0f}"
            )
            self.notifier.send_trade_executed(symbol, "SELL", actual_qty, price, total_krw)
            self.discord.send_trade_executed(symbol, "SELL", actual_qty, price, total_krw)
            # DB 저장은 paper_executor._execute_order() 내부에서 이미 처리됨
            # ★ CRITICAL FIX: pnl도 전달해야 daily_pnl이 누적되고 kill_switch가 발동됨
            self.safety.record_trade(symbol, "SELL", pnl=realized_pnl, value=total_krw)

            # ── ExitManager 상태 정리 ──
            if fully_closed:
                # 전량 청산 → ExitManager 등록 해제
                self.exit_manager.unregister(symbol)

                # ── [v2.2] 매도 쿨다운 등록 (반복매매 방지) ──
                # 매도 직후 같은 종목을 재매수하면 수수료만 먹는 무의미한 거래
                # 일정 시간(기본 1시간) 동안 해당 종목 재매수를 차단
                self._sell_cooldowns[symbol] = time.time()
                self.logger.info(
                    f"[쿨다운] {symbol}: 매도 후 {self._cooldown_seconds // 60}분 "
                    f"재매수 쿨다운 시작"
                )
            else:
                # 부분 체결 → 잔여 포지션은 ExitManager 유지(손절 보호 지속).
                # 등록 해제·쿨다운 안 함 → 다음 사이클에서 잔여분 청산 재시도.
                self._save_exit_state(symbol)

    def _execute_partial_sell(
        self, symbol: str, ratio: float, exit_reason: str = "take_profit_1",
        decision=None,
    ):
        """
        분할 매도 실행 (1차 익절 등)

        전량 매도(_execute_sell)와 달리:
        - 일부만 매도 → ExitManager 상태 유지 (등록 해제 안 함)
        - 쿨다운 등록 안 함 (잔여 포지션 추가 매수 가능)

        ★ Phase 6C 수정: ExitDecision을 받아 매도 성공 후 commit_exit() 호출
        매도 실패 시 partial_sold_pct가 변경되지 않으므로 다음 사이클에서
        다시 시도 가능 (이전엔 미리 변경되어 보호 무력화되는 버그가 있었음)

        Parameters:
            symbol: 종목 코드
            ratio: 매도 비중 (0.5 = 50%)
            exit_reason: 청산 사유
            decision: ExitManager의 ExitDecision (commit_exit 호출용)
        """
        existing_positions = self.executor.get_positions()
        held = None
        for pos in existing_positions:
            if pos.symbol == symbol and pos.quantity > 0:
                held = pos
                break
        if not held:
            return

        # 안전장치 체크
        account = self.executor.get_account()
        safe, reason, _ = self.safety.check_order(
            symbol=symbol,
            side="SELL",
            quantity=int(held.quantity * ratio),
            price=held.current_price,
            account_equity=account.total_equity,
            positions=existing_positions,
        )
        if not safe:
            self.logger.warning(f"[부분 매도 거부] {symbol}: {reason}")
            return

        if hasattr(self.executor, 'set_current_price'):
            _rt = self._get_realtime_price(symbol)
            self.executor.set_current_price(
                symbol, _rt if _rt else held.current_price
            )

        # ── 분할 매도 결정 상세 ──
        import json as _dj
        exit_state = self.exit_manager.get_state_dict(symbol)
        entry_price = (exit_state["entry_price"]
                       if exit_state else float(held.avg_price))
        current_price = float(held.current_price)
        pnl_pct = ((current_price / entry_price) - 1) * 100 if entry_price > 0 else 0
        partial_decision = {
            "type": "SELL",
            "trigger": exit_reason,
            "exit_summary": _exit_reason_kr(exit_reason),
            "partial_ratio": round(float(ratio), 2),
            "prices": {
                "entry_price": round(entry_price, 4),
                "current_price": round(current_price, 4),
                "pnl_pct": round(pnl_pct, 2),
                "quantity": int(held.quantity * ratio),
            },
            "exit_state": exit_state or {},
            "timestamp": datetime.now().isoformat(),
        }
        try:
            partial_decision_str = _dj.dumps(partial_decision, ensure_ascii=False)
        except (TypeError, ValueError):
            partial_decision_str = "{}"

        order = self.executor.close_partial(
            symbol, ratio=ratio, strategy=exit_reason,
            decision_json=partial_decision_str,
        )
        if order and order.status.value == "filled":
            # ★ Phase 12 FIX: 실제 체결 수량 사용 (KIS 부분 체결 대응)
            # order.filled_quantity가 0이면(paper 등) 주문 수량 fallback.
            sold_qty = (order.filled_quantity
                        if order.filled_quantity and order.filled_quantity > 0
                        else order.quantity)
            # ★ 체결가 안전 처리 — KIS phantom(filled_price=0/None) 방어
            price = order.filled_price
            if not price or price <= 0:
                fallback = float(held.current_price) if held.current_price and held.current_price > 0 \
                           else float(held.avg_price)
                self.logger.warning(
                    f"[부분 매도] {symbol} 체결가 미수신(={order.filled_price}) → "
                    f"근사가 ₩{fallback:,.0f} 사용 (실현 PnL 근사치)"
                )
                price = fallback
            total_krw = to_krw(symbol, sold_qty * price)
            # ★ CRITICAL: 실현 PnL 계산 (분할 매도분)
            realized_pnl = 0.0
            try:
                if hasattr(self.executor, "trade_history") and self.executor.trade_history:
                    last_trade = self.executor.trade_history[-1]
                    if last_trade.get("symbol") == symbol and last_trade.get("side", "").lower() == "sell":
                        realized_pnl = float(last_trade.get("realized_pnl", 0) or 0)
                if realized_pnl == 0.0 and held.avg_price > 0:
                    raw_pnl = (price - held.avg_price) * sold_qty
                    realized_pnl = to_krw(symbol, raw_pnl)
            except Exception:
                pass

            self.logger.info(
                f"[부분 매도] {symbol} {sold_qty}주 @ {price:,.2f} "
                f"(전체의 {ratio*100:.0f}%, {exit_reason}) | PnL ₩{realized_pnl:,.0f}"
            )
            self.notifier.send_trade_executed(symbol, "SELL", sold_qty, price, total_krw)
            self.discord.send_trade_executed(symbol, "SELL", sold_qty, price, total_krw)
            # ★ CRITICAL FIX: pnl 전달 — 안 그러면 SafetyGuard daily_pnl 데드코드
            self.safety.record_trade(symbol, "SELL", pnl=realized_pnl, value=total_krw)

            # ★ Phase 6C: 매도 성공 확정 후에만 ExitManager 상태 갱신
            # (이전엔 evaluate()에서 미리 변경 → 매도 실패 시 보호 무력화)
            if decision is not None:
                self.exit_manager.commit_exit(symbol, decision)

            # ExitManager 상태는 그대로 유지 (잔여 포지션 트레일링 계속)
            self._save_exit_state(symbol)
        else:
            # 매도 실패 → state는 변경되지 않음 (다음 사이클에서 재시도 가능)
            status_value = order.status.value if order else "no_order"
            self.logger.warning(
                f"[부분 매도 실패] {symbol} ratio={ratio} 상태={status_value} "
                f"— ExitManager state 미변경 (재시도 가능)"
            )

    def _check_risk(self):
        """리스크 모니터링 (30분마다)"""
        try:
            account = self.executor.get_account()
            positions = self.executor.get_positions()

            # MDD 체크 (★ Phase 10: 현재 모드만)
            if self.db:
                mdd = self.db.calculate_max_drawdown(
                    days=30, mode=getattr(self.executor, "mode", "paper")
                )
                # ★ 최대 낙폭 한도 — 설정값(settings.risk.max_drawdown) 사용.
                #   초과 시 신규 매수를 자동 중단(보유·손절·매도는 유지).
                #   이전 버그: 하드코딩 0.15 + 경고만 (자동 중단 없음).
                mdd_limit = getattr(self.settings.risk, "max_drawdown", 0.15)
                if mdd > mdd_limit:
                    if not getattr(self, "_risk_halt_new_buys", False):
                        self._risk_halt_new_buys = True
                        msg = (
                            f"🚨 최대 낙폭 {mdd*100:.1f}% > 한도 {mdd_limit*100:.0f}% "
                            f"→ 신규 매수 자동 중단 (보유·손절·매도는 유지)"
                        )
                        self.logger.critical(msg)
                        self.notifier.send_risk_alert(msg)
                        self.discord.send_risk_alert(msg)

            # 포지션별 손절 체크
            for pos in positions:
                if pos.unrealized_pnl_pct < -0.05:  # -5% 이하
                    msg = (f"⚠️ 손절 경고: {pos.symbol} "
                           f"{pos.unrealized_pnl_pct*100:.1f}%")
                    self.logger.warning(msg)

            # equity 스냅샷 저장 (5분마다)
            if self.db:
                pnl_pct = (account.total_equity / self.settings.capital.total_capital - 1)
                self.db.save_equity_snapshot(
                    total_equity=account.total_equity,
                    cash=account.cash,
                    positions_value=account.positions_value,
                    daily_pnl=account.daily_pnl,
                    cumulative_return=pnl_pct,
                    mode=getattr(self.executor, "mode", "paper"),  # ★ Phase 5
                )

        except Exception as e:
            self.logger.error(f"리스크 체크 오류: {e}")


def main():
    """CLI 진입점"""
    parser = argparse.ArgumentParser(description="퀀트봇 자동매매")
    parser.add_argument("--capital", type=float, default=10_000_000,
                        help="투자 자본금 (기본: 10,000,000)")
    parser.add_argument("--currency", default="KRW", choices=["KRW", "USD"],
                        help="통화 단위")
    parser.add_argument("--broker", default="paper",
                        choices=["paper", "alpaca", "kis", "dual"],
                        help="브로커 선택 (dual=KIS+Alpaca 동시 운용)")
    parser.add_argument("--live", action="store_true",
                        help="실거래 모드 (주의!)")
    parser.add_argument("--risk-per-trade", type=float, default=0.02,
                        help="1회 거래당 최대 리스크 비율")
    args = parser.parse_args()

    settings = Settings(
        capital=CapitalConfig(
            total_capital=args.capital,
            currency=args.currency
        ),
        risk=RiskConfig(
            risk_per_trade=args.risk_per_trade
        )
    )

    bot = QuantBot(settings=settings, broker=args.broker, live=args.live)

    # Ctrl+C 시그널 핸들러
    def signal_handler(sig, frame):
        print("\n종료 신호 수신...")
        bot.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)

    bot.start()


if __name__ == "__main__":
    main()
