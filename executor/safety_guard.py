"""
=============================================================================
executor/safety_guard.py - 실거래 안전장치 모듈
=============================================================================

실거래에서 발생할 수 있는 위험을 사전에 차단하는 안전장치입니다.
모의매매에서는 비활성화 가능하지만, 실거래 시 반드시 활성화해야 합니다.

사용법:
    guard = SafetyGuard(capital=100000, paper=False)
    ok, reason, adjusted_qty = guard.check_order(order, account, positions)
    if not ok:
        logger.error(f"주문 거부: {reason}")
    else:
        order.quantity = adjusted_qty
        executor.submit_order(order)
        guard.record_trade(order)
=============================================================================
"""

import time
import logging
from typing import Tuple, Optional, List, Dict, Any
from datetime import datetime, date
from dataclasses import dataclass, field

# ── 타임존 처리 ──
# Python 3.9+ 에는 zoneinfo가 내장, 이전 버전은 pytz 사용
try:
    from zoneinfo import ZoneInfo
except ImportError:
    try:
        from backports.zoneinfo import ZoneInfo  # type: ignore
    except ImportError:
        ZoneInfo = None  # type: ignore  # 타임존 없으면 로컬 시간 사용

logger = logging.getLogger("SafetyGuard")


@dataclass
class SafetyConfig:
    """안전장치 설정값"""
    max_daily_loss_pct: float = 0.03
    max_order_pct: float = 0.10
    max_positions: int = 10
    max_position_weight: float = 0.20
    max_daily_trades: int = 50
    consecutive_loss_limit: int = 5
    order_delay_sec: int = 3
    min_order_value: float = 10.0
    max_order_value: float = 50000.0
    # 일일 리셋 기준 타임존
    # "Asia/Seoul" → KST 자정에 리셋 (한국장 기준)
    # "America/New_York" → EST/EDT 자정에 리셋 (미국장 기준)
    # None → 서버 로컬 시간 사용
    timezone: Optional[str] = None
    # 종목별 한도 오버라이드 — {symbol: fraction}
    # 등록된 종목은 max_position_weight 대신 이 값을 적용한다.
    # 예: {"005930.KS": 0.30} → 삼성전자는 30%까지 허용
    position_limit_overrides: Dict[str, float] = field(default_factory=dict)


class SafetyGuard:
    """
    실거래 안전장치 - 모든 주문은 이 클래스를 통과해야 실행됨

    ⚠️ CRITICAL: 상태 영속화 (Phase 4)
       kill_switch / daily_pnl / consecutive_losses는 디스크에 저장되어
       봇 재시작 시 복원됩니다. 이렇게 안 하면 "손실 한도 도달 → 봇 크래시 →
       재시작 → 한도 초기화 → 거래 재개"라는 우회 경로가 생깁니다.

       파일 위치: data/safety_guard_{mode}.json (paper/live 분리)
       원자적 쓰기로 손상 방지.
    """

    # 영속화 파일 경로 (paper/live 분리하여 모드 전환 시 혼재 방지)
    STATE_DIR = "data"

    def __init__(self, capital: float = 100000,
                 paper: bool = True,
                 config: Optional[SafetyConfig] = None):
        self.capital = capital
        self.paper = paper
        self.config = config or SafetyConfig()
        self.kill_switch = False
        self.kill_switch_reason = ""
        self.kill_switch_at: Optional[str] = None  # 활성화 시각 (ISO)
        self.daily_trades_count = 0
        self.daily_pnl = 0.0
        self.consecutive_losses = 0
        self.last_trade_date = None
        self.trade_log: List[Dict] = []
        self.blocked_reasons: List[str] = []

        # ── 영속화된 상태 복원 ──
        # 재시작 시 이전 상태(kill_switch, daily_pnl, consecutive_losses 등)를 복원합니다.
        # 같은 날짜이면 daily_pnl도 보존 (날짜가 바뀌었으면 자동 리셋).
        self._restore_state()

        logger.info(
            f"[SafetyGuard] 초기화 완료 "
            f"(자본: {capital:,.0f}, "
            f"{'모의매매' if paper else '실거래'}, "
            f"일일 손실 제한: {self.config.max_daily_loss_pct*100:.1f}%, "
            f"킬스위치: {'ON ('+self.kill_switch_reason+')' if self.kill_switch else 'OFF'})"
        )

    def _state_file_path(self) -> str:
        """모드별 상태 파일 경로 (paper/live 분리)"""
        import os
        mode = "paper" if self.paper else "live"
        return os.path.join(self.STATE_DIR, f"safety_guard_{mode}.json")

    def _restore_state(self):
        """디스크에서 안전장치 상태 복원 (재시작 시 손실 한도 유지)"""
        import os
        path = self._state_file_path()
        if not os.path.exists(path):
            return
        try:
            from utils.atomic_io import safe_load_json
            saved = safe_load_json(path, default=None, backup_on_corruption=True)
        except ImportError:
            import json as _json
            try:
                with open(path, "r", encoding="utf-8") as f:
                    saved = _json.load(f)
            except (OSError, ValueError):
                saved = None

        if saved is None:
            return

        try:
            # 날짜 비교: 어제 데이터면 daily만 리셋, kill_switch는 유지
            saved_date_str = saved.get("last_trade_date")
            today_str = self._get_today().isoformat() if hasattr(self._get_today(), "isoformat") else None

            self.kill_switch = bool(saved.get("kill_switch", False))
            self.kill_switch_reason = saved.get("kill_switch_reason", "")
            self.kill_switch_at = saved.get("kill_switch_at")
            self.consecutive_losses = int(saved.get("consecutive_losses", 0))

            if saved_date_str and saved_date_str == today_str:
                # 같은 날짜 → daily 카운터 복원
                self.daily_trades_count = int(saved.get("daily_trades_count", 0))
                self.daily_pnl = float(saved.get("daily_pnl", 0.0))
                self.blocked_reasons = saved.get("blocked_reasons", [])[-20:]
                logger.info(
                    f"[SafetyGuard] 같은 날짜 상태 복원 — "
                    f"오늘 거래 {self.daily_trades_count}회, "
                    f"일일PnL {self.daily_pnl:,.0f}"
                )
            else:
                # 다른 날짜 → daily만 리셋, kill_switch는 유지
                self.daily_trades_count = 0
                self.daily_pnl = 0.0
                self.blocked_reasons = []
                logger.info(
                    f"[SafetyGuard] 날짜 변경 — daily만 리셋, "
                    f"kill_switch={self.kill_switch} 유지"
                )

            if saved_date_str:
                try:
                    from datetime import date as _date
                    self.last_trade_date = _date.fromisoformat(saved_date_str)
                except ValueError:
                    self.last_trade_date = None
        except Exception as e:
            logger.warning(f"[SafetyGuard] 상태 복원 실패 (무시): {e}")

    def _persist_state(self):
        """현재 상태를 디스크에 원자적으로 저장"""
        import os
        os.makedirs(self.STATE_DIR, exist_ok=True)
        path = self._state_file_path()
        try:
            state = {
                "kill_switch": self.kill_switch,
                "kill_switch_reason": self.kill_switch_reason,
                "kill_switch_at": self.kill_switch_at,
                "daily_trades_count": self.daily_trades_count,
                "daily_pnl": self.daily_pnl,
                "consecutive_losses": self.consecutive_losses,
                "last_trade_date": (
                    self.last_trade_date.isoformat() if self.last_trade_date else None
                ),
                "blocked_reasons": self.blocked_reasons[-20:],
                "paper": self.paper,
                "saved_at": datetime.now().isoformat(),
            }
            from utils.atomic_io import atomic_write_json
            atomic_write_json(path, state)
        except Exception as e:
            logger.warning(f"[SafetyGuard] 상태 저장 실패 (무시): {e}")

    def check_order(self, symbol: str, side: str, quantity: int,
                    price: float, account_equity: float,
                    positions: list) -> Tuple[bool, str, int]:
        """
        주문 안전 검사 - 수량 자동 조정 기능 포함

        Parameters:
            price: 종목의 원래 통화 가격 (USD 또는 KRW)
                   내부에서 KRW 환산 후 금액 비교합니다.

        Returns:
            (bool, str, int): (통과 여부, 사유 메시지, 조정된 수량)
            - 승인 시: (True, 사유, 조정된 수량) - 수량이 줄어들 수 있음
            - 거부 시: (False, 사유, 0) - 수량 조정으로도 해결 불가능한 경우
        """
        # ★ 환율 변환: USD 종목이면 KRW 환산 가격으로 금액 비교
        try:
            from utils.market import to_krw
            price_krw = to_krw(symbol, price)
        except ImportError:
            price_krw = price

        adjusted_qty = quantity
        order_value = adjusted_qty * price_krw

        # 1. 킬 스위치
        if self.kill_switch:
            reason = f"킬 스위치 활성화: {self.kill_switch_reason}"
            self._log_block(reason)
            return False, reason, 0

        # 2. 날짜 리셋
        self._reset_daily_if_needed()

        # 3. 일일 최대 손실
        max_loss = self.capital * self.config.max_daily_loss_pct
        if self.daily_pnl < -max_loss:
            reason = f"일일 최대 손실 초과: {self.daily_pnl:,.0f} / 한도 -{max_loss:,.0f}"
            self._log_block(reason)
            self.activate_kill_switch(f"일일 손실 {self.daily_pnl:,.0f} 초과")
            return False, reason, 0

        # 4. 일일 거래 횟수
        if self.daily_trades_count >= self.config.max_daily_trades:
            reason = f"일일 최대 거래 횟수 초과: {self.daily_trades_count}/{self.config.max_daily_trades}"
            self._log_block(reason)
            return False, reason, 0

        # 5. 연속 손실
        if self.consecutive_losses >= self.config.consecutive_loss_limit:
            reason = f"연속 손실 {self.consecutive_losses}회 -> 쿨다운 필요 (한도: {self.config.consecutive_loss_limit}회)"
            self._log_block(reason)
            return False, reason, 0

        # 매도는 기본 체크만 통과하면 허용
        if side.upper() == "SELL":
            return True, "매도 주문 승인", quantity

        # === 이하 매수 전용 체크 (수량 조정 가능) ===

        # 6. 최소 주문 금액
        if order_value < self.config.min_order_value:
            reason = f"최소 주문 금액 미달: {order_value:,.0f} < {self.config.min_order_value:,.0f}"
            self._log_block(reason)
            return False, reason, 0

        # 7. 최대 주문 금액 -> 수량 조정
        if order_value > self.config.max_order_value:
            new_qty = int(self.config.max_order_value / price_krw)
            if new_qty < 1:
                reason = f"최대 주문 금액 초과: 1주({price_krw:,.0f}원) > 한도({self.config.max_order_value:,.0f})"
                self._log_block(reason)
                return False, reason, 0
            logger.info(f"[SafetyGuard] 수량 조정 (최대 주문 금액): {adjusted_qty}주 -> {new_qty}주")
            adjusted_qty = new_qty
            order_value = adjusted_qty * price_krw

        # 8. 단일 주문 최대 비율 -> 수량 조정
        # 예외: 1주 가격이 한도를 넘더라도, 종목 비중 한도(20%) 이내면 1주 허용
        max_order = account_equity * self.config.max_order_pct
        if order_value > max_order:
            new_qty = int(max_order / price_krw)
            if new_qty < 1:
                one_share_weight = price_krw / account_equity
                if one_share_weight <= self.config.max_position_weight:
                    logger.info(
                        f"[SafetyGuard] 고가주 1주 허용: {price_krw:,.0f}원 > "
                        f"단일주문한도({max_order:,.0f}) BUT "
                        f"비중 {one_share_weight*100:.1f}% <= {self.config.max_position_weight*100:.0f}%"
                    )
                    new_qty = 1
                else:
                    reason = (
                        f"단일 주문 비율 초과: 1주({price_krw:,.0f}원) > "
                        f"한도({max_order:,.0f}, {self.config.max_order_pct*100:.0f}%), "
                        f"비중도 {one_share_weight*100:.1f}% > {self.config.max_position_weight*100:.0f}%"
                    )
                    self._log_block(reason)
                    return False, reason, 0
            logger.info(f"[SafetyGuard] 수량 조정 (단일 주문 비율): {adjusted_qty}주 -> {new_qty}주")
            adjusted_qty = new_qty
            order_value = adjusted_qty * price_krw

        # 9. 최대 포지션 수
        from utils.market import get_position_attr as _gpa
        current_symbols = set()
        for p in positions:
            sym = _gpa(p, 'symbol', '')
            qty = _gpa(p, 'quantity', 0)
            if qty > 0:
                current_symbols.add(sym)
        if symbol not in current_symbols:
            if len(current_symbols) >= self.config.max_positions:
                reason = f"최대 포지션 수 초과: {len(current_symbols)}/{self.config.max_positions}"
                self._log_block(reason)
                return False, reason, 0

        # 10. 종목당 최대 비중 -> 수량 조정
        # ★ 종목별 오버라이드가 등록돼 있으면 그 한도, 없으면 전역 max_position_weight 적용
        sym_limit = self.config.position_limit_overrides.get(
            symbol, self.config.max_position_weight
        )
        current_value = 0
        for p in positions:
            sym = _gpa(p, 'symbol', '')
            if sym == symbol:
                current_value = _gpa(p, 'market_value', 0)
                break

        total_weight = (current_value + order_value) / account_equity
        if total_weight > sym_limit:
            max_allowed_value = (account_equity * sym_limit) - current_value
            if max_allowed_value <= 0:
                reason = (
                    f"종목 비중 이미 한도 도달: {symbol} "
                    f"현재 {current_value/account_equity*100:.1f}% "
                    f"(한도 {sym_limit*100:.0f}%)"
                )
                self._log_block(reason)
                return False, reason, 0
            new_qty = int(max_allowed_value / price_krw)
            if new_qty < 1:
                reason = (
                    f"종목 비중 한도 근접: {symbol} "
                    f"1주 추가 시 {(current_value + price_krw)/account_equity*100:.1f}% "
                    f"> {sym_limit*100:.0f}%"
                )
                self._log_block(reason)
                return False, reason, 0
            logger.info(
                f"[SafetyGuard] 수량 조정 (종목 비중): "
                f"{adjusted_qty}주 -> {new_qty}주 "
                f"(기존 {current_value:,.0f} + 신규 {new_qty * price_krw:,.0f} = "
                f"{(current_value + new_qty * price_krw)/account_equity*100:.1f}%, "
                f"한도 {sym_limit*100:.0f}%)"
            )
            adjusted_qty = new_qty
            order_value = adjusted_qty * price_krw

        # 최종 결과
        if adjusted_qty < quantity:
            reason = f"수량 조정 승인: {quantity}주 -> {adjusted_qty}주 (금액: {adjusted_qty * price_krw:,.0f}원)"
            logger.info(f"[SafetyGuard] {symbol}: {reason}")
            return True, reason, adjusted_qty
        else:
            return True, "주문 승인", adjusted_qty

    def record_trade(self, symbol: str, side: str,
                     pnl: float = 0.0, value: float = 0.0):
        """거래 결과 기록 (디스크 영속화 포함)"""
        self._reset_daily_if_needed()
        self.daily_trades_count += 1
        self.trade_log.append({
            "time": datetime.now().isoformat(),
            "symbol": symbol,
            "side": side,
            "pnl": pnl,
            "value": value,
        })
        if side.upper() == "SELL":
            self.daily_pnl += pnl
            if pnl < 0:
                self.consecutive_losses += 1
                logger.warning(
                    f"[SafetyGuard] 손실 거래: {symbol} {pnl:,.0f} "
                    f"(연속 {self.consecutive_losses}회)"
                )
            else:
                self.consecutive_losses = 0
        logger.info(
            f"[SafetyGuard] 거래 기록: {side} {symbol} "
            f"(오늘 {self.daily_trades_count}회, 일일PnL: {self.daily_pnl:,.0f})"
        )
        # ★ 매 거래 후 상태 저장 — 크래시 시 손실 한도 유지
        self._persist_state()

    def activate_kill_switch(self, reason: str = "수동 활성화"):
        """
        킬 스위치 활성화 - 모든 신규 주문 즉시 차단

        ⚠️ 활성화 즉시 디스크에 저장하여 봇 재시작 후에도 유지됩니다.
        한도 도달 후 크래시→재시작으로 우회하는 것을 막습니다.
        """
        self.kill_switch = True
        self.kill_switch_reason = reason
        self.kill_switch_at = datetime.now().isoformat()
        logger.critical(f"[SafetyGuard] 킬 스위치 활성화: {reason}")
        self._persist_state()

    def deactivate_kill_switch(self):
        """
        킬 스위치 해제 (사용자 명시적 호출만)

        ⚠️ 봇 재시작으로는 자동 해제되지 않습니다.
        사용자가 대시보드에서 명시적으로 해제 버튼을 눌러야 합니다.
        """
        self.kill_switch = False
        self.kill_switch_reason = ""
        self.kill_switch_at = None
        self.consecutive_losses = 0
        logger.info("[SafetyGuard] 킬 스위치 해제")
        self._persist_state()

    def wait_before_order(self):
        """주문 전 대기 (실거래 전용)"""
        if not self.paper and self.config.order_delay_sec > 0:
            delay = self.config.order_delay_sec
            logger.info(f"[SafetyGuard] 주문 전 {delay}초 대기...")
            time.sleep(delay)

    def get_status(self) -> Dict[str, Any]:
        """현재 안전장치 상태 반환 (대시보드용)"""
        return {
            "kill_switch": self.kill_switch,
            "kill_switch_reason": self.kill_switch_reason,
            "daily_trades": self.daily_trades_count,
            "max_daily_trades": self.config.max_daily_trades,
            "daily_pnl": round(self.daily_pnl, 2),
            "max_daily_loss": round(self.capital * self.config.max_daily_loss_pct, 2),
            "consecutive_losses": self.consecutive_losses,
            "consecutive_loss_limit": self.config.consecutive_loss_limit,
            "blocked_count": len(self.blocked_reasons),
            "last_blocked": self.blocked_reasons[-1] if self.blocked_reasons else None,
            "paper_mode": self.paper,
        }

    def reset_daily(self):
        """일일 카운터 수동 리셋 (디스크에도 반영)"""
        self.daily_trades_count = 0
        self.daily_pnl = 0.0
        self.trade_log = []
        self.blocked_reasons = []
        self.last_trade_date = self._get_today()
        logger.info("[SafetyGuard] 일일 카운터 리셋")
        self._persist_state()

    def _get_today(self) -> date:
        """
        설정된 타임존 기준 '오늘' 날짜 반환

        왜 중요한가:
        - 한국 서버(KST)에서 미국장(EST) 거래 시,
          KST 자정에 리셋되면 미국장 중간에 카운터가 초기화됨
        - timezone="America/New_York"으로 설정하면
          뉴욕 자정(= KST 14시) 기준으로 리셋됨
        """
        tz_name = self.config.timezone
        if tz_name and ZoneInfo is not None:
            return datetime.now(ZoneInfo(tz_name)).date()
        return date.today()

    def _reset_daily_if_needed(self):
        """날짜가 바뀌면 자동으로 일일 카운터 리셋 (타임존 인식, kill_switch는 유지)"""
        today = self._get_today()
        if self.last_trade_date != today:
            self.daily_trades_count = 0
            self.daily_pnl = 0.0
            self.trade_log = []
            self.blocked_reasons = []
            self.last_trade_date = today
            # 날짜 변경 시에도 영속화 (kill_switch는 유지됨)
            self._persist_state()

    def _log_block(self, reason: str):
        """차단 사유 기록"""
        self.blocked_reasons.append(reason)
        logger.warning(f"[SafetyGuard] 주문 차단: {reason}")
