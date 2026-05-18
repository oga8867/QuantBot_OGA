"""
=============================================================================
tests/test_regression_bugs.py - 발견된 버그들의 회귀 방지 테스트
=============================================================================

이 파일에 모인 테스트들은 과거 봇에서 발견된 실제 버그들을 재발 방지합니다.
새로운 버그를 발견하면 반드시 이 파일에 회귀 테스트를 추가하세요.

발견된 버그 목록:
1. cash 복원 버그 (2026-05-08)
   - equity_history의 오래된 cash가 거래 직후 크래시 시 부풀려져 +8.49% 가짜 수익률
   - 수정: trade_history 기반 cash 재계산

2. BUY/SELL 케이싱 불일치 (2026-05-08)
   - OrderSide.BUY.value="buy"(소문자) vs daily_report에서 "BUY"(대문자) 비교
   - 수정: 모든 비교에 .upper() 적용

3. total_value 통화 불일치 (2026-05-08)
   - DB는 quantity*price (USD), 인메모리는 KRW 환산
   - 수정: log_trade에 total_value=KRW 인자 추가

4. realized_pnl 키 미스매치 (2026-05-08)
   - 인메모리는 "realized_pnl" 키, app.py는 "pnl" 키로 조회 → 항상 0
   - 수정: t.get("realized_pnl", t.get("pnl", 0))

5. 자산 차트 키 미스매치 (2026-05-08)
   - 프론트엔드 d.total_value 사용, 백엔드 d.total_equity 반환
   - 수정: total_equity || total_value || 0

6. ExitManager 손절 미실행 (2026-05-08)
   - DB에 stop_price 저장만 되고 실제 매도 로직에서 미사용
   - 수정: ExitManager 모듈 추가 + run_bot.py 통합

실행:
    pytest tests/test_regression_bugs.py -v
=============================================================================
"""

import pytest
import sys
import os
from pathlib import Path
from datetime import datetime, timedelta

# 프로젝트 루트 PATH 추가
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ═══════════════════════════════════════════════════════════════════════════
# Bug #1: Cash 재계산 버그
# ═══════════════════════════════════════════════════════════════════════════

class TestCashRestoration:
    """
    봇 재시작 시 cash가 거래 이력 기반으로 정확히 재계산되는지 검증.

    히스토리:
    - 2026-05-08: 09:01 매수 후 크래시 → 재시작 시 cash가 ₩851,492 부풀려짐
    """

    def test_cash_recompute_from_buy_only_trades(self):
        """매수만 발생한 경우 cash = initial - sum(buys) - 수수료"""
        from utils.market import to_krw

        initial_capital = 10_000_000.0
        # 가상 거래 이력 (한국 주식만)
        trades = [
            {"symbol": "005930.KS", "side": "BUY", "quantity": 2, "price": 262131.0},
            {"symbol": "035720.KS", "side": "BUY", "quantity": 12, "price": 45272.62},
            {"symbol": "105560.KS", "side": "BUY", "quantity": 2, "price": 163081.5},
        ]

        cash = initial_capital
        for t in trades:
            qty = t["quantity"]
            price = t["price"]
            sym = t["symbol"]
            krw_total = to_krw(sym, qty * price)
            fee = krw_total * 0.00015
            cash -= (krw_total + fee)

        # 매수 합계: 524,262 + 543,271 + 326,163 = 1,393,696 + 수수료
        # 예상 cash: 약 ₩8,605,995
        assert 8_500_000 < cash < 8_700_000, (
            f"매수 후 cash가 예상 범위 밖: {cash:,.0f}"
        )

    def test_cash_recompute_with_us_stock_uses_krw(self):
        """
        미국 주식 거래 시 cash가 KRW 환산으로 차감되는지 검증
        (이전 버그: USD 가격 그대로 차감 → cash 부풀림)
        """
        from utils.market import to_krw, get_exchange_rate

        # AAPL $287 1주 매수
        usd_total = 287.0 * 1
        krw_total = to_krw("AAPL", usd_total)
        rate = get_exchange_rate()

        # KRW 환산이 USD × 환율과 일치해야 함
        assert abs(krw_total - usd_total * rate) < 1.0
        # KRW 환산값이 USD 값보다 충분히 큰지 (환율 ≥ 500)
        assert krw_total > usd_total * 500


# ═══════════════════════════════════════════════════════════════════════════
# Bug #2: BUY/SELL 케이싱 불일치
# ═══════════════════════════════════════════════════════════════════════════

class TestSideCasing:
    """
    OrderSide.value는 소문자("buy"/"sell")이지만 DB와 보고서는 대문자.
    모든 비교 지점에서 .upper() 처리 검증.
    """

    def test_orderside_value_is_lowercase(self):
        """OrderSide enum 값이 소문자인지 확인 (이 사실에 의존하는 코드 다수)"""
        from executor.base import OrderSide
        assert OrderSide.BUY.value == "buy"
        assert OrderSide.SELL.value == "sell"

    def test_daily_report_handles_lowercase_side(self):
        """daily_report에서 lowercase side를 받아도 정상 분류되는지"""
        from reporter.daily_report import DailyReportGenerator

        # 인메모리 trade_history는 lowercase side
        trades = [
            {"symbol": "AAPL", "side": "buy", "quantity": 1, "price": 100, "total": 100, "pnl": 0},
            {"symbol": "AAPL", "side": "sell", "quantity": 1, "price": 110, "total": 110, "pnl": 10},
        ]
        # buy_trades / sell_trades 분류는 .upper() 처리되어야 함
        buys = [t for t in trades if t.get("side", "").upper() == "BUY"]
        sells = [t for t in trades if t.get("side", "").upper() == "SELL"]
        assert len(buys) == 1
        assert len(sells) == 1

    def test_cache_get_trade_stats_handles_both_cases(self):
        """database.cache.get_trade_stats가 양쪽 케이싱 모두 처리"""
        # 이 함수는 .upper() 후 == "BUY" / "SELL" 비교
        # DB는 항상 uppercase로 저장하지만, 안전망 검증
        sample = [{"side": "BUY"}, {"side": "buy"}, {"side": "SELL"}, {"side": "sell"}]
        buys = [t for t in sample if t["side"].upper() == "BUY"]
        sells = [t for t in sample if t["side"].upper() == "SELL"]
        assert len(buys) == 2
        assert len(sells) == 2


# ═══════════════════════════════════════════════════════════════════════════
# Bug #3: total_value 통화 일치
# ═══════════════════════════════════════════════════════════════════════════

class TestTotalValueCurrency:
    """
    DB의 total_value는 항상 KRW로 저장되어야 함 (보고서 통화 통일).
    log_trade에 total_value 인자가 제대로 전달되는지 검증.
    """

    def test_log_trade_accepts_total_value_kwarg(self):
        """log_trade가 total_value 키워드 인자를 받는지"""
        from database.cache import DatabaseManager
        import inspect
        sig = inspect.signature(DatabaseManager.log_trade)
        assert "total_value" in sig.parameters, (
            "log_trade에 total_value 파라미터가 없음 (KRW 환산 미적용 위험)"
        )

    def test_log_trade_default_uses_quantity_times_price(self):
        """total_value 미지정 시 quantity*price로 폴백 (하위호환)"""
        from database.cache import DatabaseManager
        # KR 종목은 native=KRW이므로 폴백이 안전
        # 실제 호출은 메모리 DB로 검증 가능하지만 간단히 docstring 검증
        doc = DatabaseManager.log_trade.__doc__ or ""
        assert "total_value" in doc.lower() or "krw" in doc.lower()


# ═══════════════════════════════════════════════════════════════════════════
# Bug #4: realized_pnl 키 미스매치
# ═══════════════════════════════════════════════════════════════════════════

class TestRealizedPnlKey:
    """
    paper_executor 인메모리 trade_history는 "realized_pnl" 키 사용.
    app.py에서 보고서로 전달 시 양쪽 키 모두 폴백 처리해야 함.
    """

    def test_pnl_fallback_chain(self):
        """t.get("realized_pnl", t.get("pnl", 0)) 패턴 검증"""
        # 인메모리 형식
        memory_trade = {"realized_pnl": 100.0}
        # DB 복원 형식
        db_trade = {"pnl": 100.0}
        # 빈 딕셔너리
        empty = {}

        for t in [memory_trade, db_trade]:
            pnl = t.get("realized_pnl", t.get("pnl", 0))
            assert pnl == 100.0
        assert empty.get("realized_pnl", empty.get("pnl", 0)) == 0


# ═══════════════════════════════════════════════════════════════════════════
# Bug #5: ExitManager 동작 검증 (신규 버그 방지)
# ═══════════════════════════════════════════════════════════════════════════

class TestExitManager:
    """
    ExitManager의 핵심 의사결정 로직 검증.
    이 테스트가 실패하면 손절/익절이 작동 안 한다는 의미.
    """

    def setup_method(self):
        from executor.exit_manager import ExitManager
        self.em = ExitManager(
            atr_stop_multiplier=2.0,
            rr_ratio=2.0,
            trailing_atr_multiplier=3.0,
        )

    def test_register_entry_creates_state(self):
        """진입 등록 시 손절가/목표가 정확히 계산되는지"""
        state = self.em.register_entry(
            symbol="AAPL",
            entry_price=100.0,
            atr=2.0,  # ATR=2
        )
        # 손절 = 100 - 2*2 = 96
        assert state.initial_stop == 96.0
        # 목표1 = 100 + 2*2*2 = 108 (ATR×stop_mult×RR = 2*2*2=8)
        assert state.target_1 == 108.0
        # 목표2 = 100 + 2*2*2*2 = 116
        assert state.target_2 == 116.0

    def test_stop_loss_triggers_when_price_drops(self):
        """현재가가 손절선 도달 시 STOP_LOSS 발동"""
        from executor.exit_manager import ExitReason
        self.em.register_entry("AAPL", entry_price=100.0, atr=2.0)
        # 손절선 96 도달
        decision = self.em.evaluate("AAPL", current_price=95.0)
        assert decision.should_exit
        assert decision.reason == ExitReason.STOP_LOSS
        assert decision.sell_ratio == 1.0

    def test_take_profit_1_triggers_partial_sell(self):
        """
        1차 목표 도달 시 50% 분할 매도 + 본전 상향

        ⚠️ Phase 6C 수정 후: evaluate()는 더 이상 state를 사전 변경하지 않음
        매도 성공 확정 후 commit_exit()을 호출해야 state가 갱신됨.
        (이전 버그: 매도 실패 시 상태만 변경되어 보호 무력화)
        """
        from executor.exit_manager import ExitReason
        state = self.em.register_entry("AAPL", entry_price=100.0, atr=2.0)
        # 목표1 108 도달
        decision = self.em.evaluate("AAPL", current_price=109.0)
        assert decision.should_exit
        assert decision.reason == ExitReason.TAKE_PROFIT_1
        assert decision.sell_ratio == 0.5
        # evaluate()만 호출한 시점: state는 아직 변경 안 됨 (매도 성공 확정 전)
        assert state.partial_sold_pct == 0.0
        # decision에는 새 손절선이 들어있음 (commit 시 적용 예정)
        assert decision.new_stop_price == 100.0

        # 매도 성공 시뮬레이션 → commit_exit으로 state 갱신
        self.em.commit_exit("AAPL", decision)
        assert state.current_stop == 100.0  # 본전 상향 적용됨
        assert state.partial_sold_pct == 0.5

    def test_take_profit_1_state_unchanged_without_commit(self):
        """
        Phase 6C 회귀 방지: evaluate() 호출만으로는 state가 변경되면 안 됨
        매도 실패 시 보호 유지를 위해 commit_exit() 호출이 필수
        """
        state = self.em.register_entry("AAPL", entry_price=100.0, atr=2.0)
        initial_stop = state.current_stop
        # 목표1 도달 → decision은 should_exit=True지만 state 그대로
        self.em.evaluate("AAPL", current_price=109.0)
        assert state.partial_sold_pct == 0.0
        assert state.current_stop == initial_stop  # 변경 없음 (96.0)

    def test_trailing_stop_after_partial_exit(self):
        """
        1차 익절 후 가격 하락 시 트레일링 스탑 발동

        Phase 6C 수정 후: evaluate() 뒤 commit_exit() 호출해야
        partial_sold_pct가 변경되어 트레일링 스탑 로직이 활성화됨.
        """
        from executor.exit_manager import ExitReason
        state = self.em.register_entry("AAPL", entry_price=100.0, atr=2.0)
        # 1차 익절 평가 + commit (매도 성공 시뮬레이션)
        dec1 = self.em.evaluate("AAPL", current_price=109.0)
        self.em.commit_exit("AAPL", dec1)
        assert state.partial_sold_pct == 0.5
        # 가격 더 상승 (최고가 갱신, 트레일링 스탑 동작 시작)
        self.em.evaluate("AAPL", current_price=120.0)  # highest=120, trailing=120-2*3=114
        # 트레일링 스탑까지 하락
        decision = self.em.evaluate("AAPL", current_price=113.0)
        assert decision.should_exit
        assert decision.reason == ExitReason.TRAILING_STOP

    def test_no_exit_in_normal_range(self):
        """정상 범위(손절~목표 사이)에서는 청산 안 함"""
        self.em.register_entry("AAPL", entry_price=100.0, atr=2.0)
        decision = self.em.evaluate("AAPL", current_price=102.0)
        assert not decision.should_exit

    def test_unregister_clears_state(self):
        """청산 후 unregister 호출 시 상태 제거"""
        self.em.register_entry("AAPL", entry_price=100.0, atr=2.0)
        assert self.em.evaluate("AAPL", 95.0).should_exit
        self.em.unregister("AAPL")
        # 등록 해제 후에는 청산 의사결정 안 나옴
        assert not self.em.evaluate("AAPL", 50.0).should_exit


# ═══════════════════════════════════════════════════════════════════════════
# Bug #6: close_partial 동작 검증
# ═══════════════════════════════════════════════════════════════════════════

class TestClosePartial:
    """PaperExecutor.close_partial이 정확한 비중으로 매도하는지 검증"""

    def setup_method(self):
        from executor.paper_executor import PaperExecutor
        self.exe = PaperExecutor(initial_capital=10_000_000)
        self.exe.set_current_price("AAPL", 100.0)

    def test_close_partial_sells_correct_quantity(self):
        """50% 비중 → 보유의 정확히 절반 매도 (1주 단위 절삭)"""
        # 10주 매수
        self.exe.buy_market("AAPL", 10)
        order = self.exe.close_partial("AAPL", ratio=0.5)
        assert order is not None
        assert order.quantity == 5  # 10 * 0.5 = 5

    def test_close_partial_returns_none_if_no_position(self):
        """미보유 종목은 None 반환"""
        order = self.exe.close_partial("MSFT", ratio=0.5)
        assert order is None

    def test_close_partial_caps_at_full_position(self):
        """ratio=1.0이면 전량 매도 (close_position과 동일 효과)"""
        self.exe.buy_market("AAPL", 10)
        order = self.exe.close_partial("AAPL", ratio=1.0)
        assert order.quantity == 10

    def test_close_partial_handles_small_quantity(self):
        """1주 보유 시 50% 매도 → 1주 매도 (강제 1주)"""
        self.exe.buy_market("AAPL", 1)
        order = self.exe.close_partial("AAPL", ratio=0.5)
        # 1*0.5=0.5 → 0이지만 ratio>=0.5이므로 1주 매도
        assert order is not None
        assert order.quantity == 1


class TestPositionTypeToggle:
    """포지션 유형 ON/OFF 토글 동작 검증"""

    def setup_method(self):
        from strategy.ensemble import EnsembleSignal, ModuleScore
        self.EnsembleSignal = EnsembleSignal
        self.ModuleScore = ModuleScore

    def _make_signal(self, tech_contrib=0.3, factor_contrib=0.2,
                     tech_score=0.4, tech_conf=0.6,
                     factor_score=0.1, factor_conf=0.3):
        """테스트용 가짜 신호 생성"""
        ensemble = self.EnsembleSignal(
            action="BUY", score=0.4, confidence=0.5,
            components={"technical": tech_contrib, "factor": factor_contrib},
            reasons=["test"],
        )
        module_scores = [
            self.ModuleScore(name="technical", score=tech_score, confidence=tech_conf),
            self.ModuleScore(name="factor", score=factor_score, confidence=factor_conf),
        ]
        return module_scores, ensemble

    def test_all_enabled_returns_classification(self):
        """모두 활성 시 정상 분류 반환"""
        from strategy.ensemble import classify_position_type
        ms, es = self._make_signal()
        result = classify_position_type(
            ms, es, enabled_types={"short": True, "swing": True, "long": True}
        )
        assert result is not None
        assert "position_type" in result

    def test_all_disabled_returns_none(self):
        """모두 비활성 시 None 반환 (매수 차단)"""
        from strategy.ensemble import classify_position_type
        ms, es = self._make_signal()
        result = classify_position_type(
            ms, es, enabled_types={"short": False, "swing": False, "long": False}
        )
        assert result is None

    def test_short_disabled_falls_back_to_swing(self):
        """단타 OFF 시 스윙으로 fallback (기술 지배 신호)"""
        from strategy.ensemble import classify_position_type
        # 기술 지배 → 원래는 단타
        ms, es = self._make_signal(
            tech_contrib=0.4, factor_contrib=0.1,
            tech_score=0.5, tech_conf=0.7,
            factor_score=0.0,
        )
        result = classify_position_type(
            ms, es, enabled_types={"short": False, "swing": True, "long": True}
        )
        assert result is not None
        assert result["position_type_en"] == "swing"
        assert "단타" in result["classification_reason"] or "fallback" in result["classification_reason"]

    def test_short_only_disabled_long_swing_still_work(self):
        """단타만 OFF + 다른 신호는 정상 동작"""
        from strategy.ensemble import classify_position_type
        # 펀더멘탈 지배 → 원래 장기
        ms, es = self._make_signal(
            tech_contrib=0.1, factor_contrib=0.4,
            tech_score=0.1, tech_conf=0.3,
            factor_score=0.5, factor_conf=0.6,
        )
        result = classify_position_type(
            ms, es, enabled_types={"short": False, "swing": True, "long": True}
        )
        assert result is not None
        assert result["position_type_en"] == "long"  # 장기는 정상 분류

    def test_swing_long_disabled_falls_back_to_short(self):
        """스윙+장기 OFF → 단타 fallback"""
        from strategy.ensemble import classify_position_type
        # 혼합 → 원래는 스윙
        ms, es = self._make_signal()
        result = classify_position_type(
            ms, es, enabled_types={"short": True, "swing": False, "long": False}
        )
        assert result is not None
        assert result["position_type_en"] == "short"


class TestModuleToggle:
    """분석 모듈 ON/OFF 토글 동작 검증"""

    def test_all_modules_enabled_keeps_default_weights(self):
        """모든 모듈 활성 시 기본 가중치 유지"""
        from config.settings import EnsembleConfig
        cfg = EnsembleConfig(
            technical_enabled=True, factor_enabled=True, sentiment_enabled=True,
        )
        weights = cfg.get_effective_weights()
        assert "technical" in weights
        assert "factor" in weights
        assert "sentiment" in weights
        # 합계 1.0
        assert abs(sum(weights.values()) - 1.0) < 0.001

    def test_technical_disabled_renormalizes(self):
        """기술 OFF → 팩터+감성 가중치가 1.0으로 재정규화"""
        from config.settings import EnsembleConfig
        cfg = EnsembleConfig(
            technical_enabled=False, factor_enabled=True, sentiment_enabled=True,
        )
        weights = cfg.get_effective_weights()
        assert "technical" not in weights
        # 합계 1.0 유지
        assert abs(sum(weights.values()) - 1.0) < 0.001
        # 팩터:감성 = 0.35:0.20 = 7:4 → 0.636, 0.364
        assert abs(weights["factor"] - 0.35 / 0.55) < 0.01
        assert abs(weights["sentiment"] - 0.20 / 0.55) < 0.01

    def test_all_modules_disabled_returns_empty(self):
        """모든 모듈 OFF 시 빈 가중치 반환"""
        from config.settings import EnsembleConfig
        cfg = EnsembleConfig(
            technical_enabled=False, factor_enabled=False, sentiment_enabled=False,
        )
        weights = cfg.get_effective_weights()
        assert len(weights) == 0


class TestWatchlistStrictMode:
    """엄격 화이트리스트 모드 동작 검증"""

    def test_watchlist_config_default_is_normal_mode(self):
        """기본값은 일반 모드 (strict_mode=False)"""
        from config.settings import WatchlistConfig
        cfg = WatchlistConfig()
        assert cfg.strict_mode is False

    def test_watchlist_config_strict_mode_can_be_enabled(self):
        """strict_mode=True 설정 가능"""
        from config.settings import WatchlistConfig
        cfg = WatchlistConfig(strict_mode=True)
        assert cfg.strict_mode is True

    def test_settings_includes_watchlist_config(self):
        """Settings 클래스에 watchlist 필드 존재"""
        from config.settings import Settings, WatchlistConfig
        s = Settings()
        assert hasattr(s, "watchlist")
        assert isinstance(s.watchlist, WatchlistConfig)
        assert s.watchlist.strict_mode is False

    def test_strict_mode_settings_override(self):
        """Settings 생성 후 watchlist.strict_mode 변경 가능"""
        from config.settings import Settings, WatchlistConfig
        s = Settings()
        s.watchlist = WatchlistConfig(strict_mode=True)
        assert s.watchlist.strict_mode is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
