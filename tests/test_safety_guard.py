"""
=============================================================================
tests/test_safety_guard.py - SafetyGuard 단위 테스트
=============================================================================

SafetyGuard는 모든 주문이 반드시 통과해야 하는 안전장치입니다.
실거래에서 자본을 보호하는 핵심 모듈이므로, 가장 많은 테스트 케이스를 가집니다.

실행:
    pytest tests/test_safety_guard.py -v
=============================================================================
"""

import pytest
from datetime import date, datetime
from unittest.mock import patch, MagicMock


# =============================================================================
# 1. 기본 주문 승인 테스트
# =============================================================================

class TestOrderApproval:
    """정상 주문이 올바르게 승인되는지 확인"""

    def test_normal_buy_approved(self, safety_guard):
        ok, reason, qty = safety_guard.check_order(
            symbol="AAPL", side="BUY", quantity=10,
            price=150.0, account_equity=100000, positions=[]
        )
        assert ok is True
        assert qty == 10
        assert "승인" in reason

    def test_sell_always_approved(self, safety_guard):
        ok, reason, qty = safety_guard.check_order(
            symbol="AAPL", side="SELL", quantity=100,
            price=150.0, account_equity=100000, positions=[]
        )
        assert ok is True
        assert qty == 100
        assert "매도" in reason

    def test_sell_case_insensitive(self, safety_guard):
        ok, _, qty = safety_guard.check_order(
            symbol="AAPL", side="sell", quantity=50,
            price=200.0, account_equity=100000, positions=[]
        )
        assert ok is True
        assert qty == 50


# =============================================================================
# 2. 주문 거부 테스트
# =============================================================================

class TestOrderRejection:
    """위험 조건에서 주문이 거부되는지 확인"""

    def test_min_order_value_reject(self, safety_guard):
        ok, reason, qty = safety_guard.check_order(
            symbol="PENNY", side="BUY", quantity=1,
            price=5.0, account_equity=100000, positions=[]
        )
        assert ok is False
        assert qty == 0
        assert "최소 주문 금액" in reason

    def test_max_positions_reject(self, safety_guard, mock_positions):
        from tests.conftest import MockPosition
        full_positions = mock_positions + [
            MockPosition(symbol="TSLA", quantity=5, avg_price=250.0,
                         current_price=260.0, market_value=1300.0),
            MockPosition(symbol="GOOGL", quantity=3, avg_price=170.0,
                         current_price=175.0, market_value=525.0),
        ]
        ok, reason, qty = safety_guard.check_order(
            symbol="AMZN", side="BUY", quantity=5, price=180.0,
            account_equity=100000, positions=full_positions
        )
        assert ok is False
        assert qty == 0
        assert "포지션 수" in reason

    def test_existing_symbol_bypasses_position_limit(self, safety_guard, mock_positions):
        from tests.conftest import MockPosition
        full_positions = mock_positions + [
            MockPosition(symbol="TSLA", quantity=5, avg_price=250.0,
                         current_price=260.0, market_value=1300.0),
            MockPosition(symbol="GOOGL", quantity=3, avg_price=170.0,
                         current_price=175.0, market_value=525.0),
        ]
        ok, reason, qty = safety_guard.check_order(
            symbol="AAPL", side="BUY", quantity=1,
            price=155.0, account_equity=100000, positions=full_positions
        )
        if not ok:
            assert "포지션 수" not in reason

    def test_daily_trade_limit_reject(self, safety_guard):
        for i in range(10):
            safety_guard.record_trade(f"SYM{i}", "BUY", value=100.0)
        ok, reason, qty = safety_guard.check_order(
            symbol="AAPL", side="BUY", quantity=1,
            price=150.0, account_equity=100000, positions=[]
        )
        assert ok is False
        assert "거래 횟수" in reason

    def test_consecutive_loss_reject(self, safety_guard):
        for i in range(3):
            safety_guard.record_trade(f"LOSS{i}", "SELL", pnl=-100.0)
        assert safety_guard.consecutive_losses == 3
        ok, reason, qty = safety_guard.check_order(
            symbol="AAPL", side="BUY", quantity=1,
            price=150.0, account_equity=100000, positions=[]
        )
        assert ok is False
        assert "연속 손실" in reason

    def test_profit_resets_consecutive_losses(self, safety_guard):
        safety_guard.record_trade("L1", "SELL", pnl=-50.0)
        safety_guard.record_trade("L2", "SELL", pnl=-30.0)
        assert safety_guard.consecutive_losses == 2
        safety_guard.record_trade("W1", "SELL", pnl=100.0)
        assert safety_guard.consecutive_losses == 0


# =============================================================================
# 3. 수량 자동 조정 테스트
# =============================================================================

class TestQuantityAdjustment:

    def test_max_order_value_adjustment(self, safety_guard):
        ok, reason, qty = safety_guard.check_order(
            symbol="AAPL", side="BUY", quantity=1000,
            price=100.0, account_equity=1000000, positions=[]
        )
        assert ok is True
        assert qty <= 500
        assert qty < 1000

    def test_max_order_pct_adjustment(self, safety_guard):
        ok, reason, qty = safety_guard.check_order(
            symbol="AAPL", side="BUY", quantity=100,
            price=150.0, account_equity=100000, positions=[]
        )
        assert ok is True
        assert qty <= 66
        assert qty < 100

    def test_position_weight_adjustment(self, safety_guard):
        from tests.conftest import MockPosition
        existing = [
            MockPosition(symbol="AAPL", quantity=100, avg_price=150.0,
                         current_price=150.0, market_value=15000.0)
        ]
        ok, reason, qty = safety_guard.check_order(
            symbol="AAPL", side="BUY", quantity=100,
            price=100.0, account_equity=100000, positions=existing
        )
        assert ok is True
        assert qty <= 50

    def test_high_price_stock_one_share_allowed(self, safety_guard):
        ok, reason, qty = safety_guard.check_order(
            symbol="BRK.A", side="BUY", quantity=1,
            price=15000.0, account_equity=100000, positions=[]
        )
        assert ok is True
        assert qty == 1

    def test_high_price_stock_reject_if_weight_exceeded(self, safety_guard):
        ok, reason, qty = safety_guard.check_order(
            symbol="EXPENSIVE", side="BUY", quantity=1,
            price=25000.0, account_equity=100000, positions=[]
        )
        assert ok is False
        assert qty == 0

    def test_position_weight_already_at_limit(self, safety_guard):
        from tests.conftest import MockPosition
        existing = [
            MockPosition(symbol="AAPL", quantity=100, avg_price=200.0,
                         current_price=200.0, market_value=20000.0)
        ]
        ok, reason, qty = safety_guard.check_order(
            symbol="AAPL", side="BUY", quantity=1,
            price=200.0, account_equity=100000, positions=existing
        )
        assert ok is False
        assert "비중" in reason


# =============================================================================
# 4. 킬 스위치 테스트
# =============================================================================

class TestKillSwitch:

    def test_kill_switch_blocks_all_orders(self, safety_guard):
        safety_guard.activate_kill_switch("테스트 킬 스위치")
        ok, reason, qty = safety_guard.check_order(
            symbol="AAPL", side="BUY", quantity=1,
            price=150.0, account_equity=100000, positions=[]
        )
        assert ok is False
        assert "킬 스위치" in reason
        ok2, reason2, qty2 = safety_guard.check_order(
            symbol="AAPL", side="SELL", quantity=1,
            price=150.0, account_equity=100000, positions=[]
        )
        assert ok2 is False
        assert "킬 스위치" in reason2

    def test_kill_switch_deactivation(self, safety_guard):
        safety_guard.activate_kill_switch("테스트")
        safety_guard.deactivate_kill_switch()
        ok, _, _ = safety_guard.check_order(
            symbol="AAPL", side="BUY", quantity=1,
            price=150.0, account_equity=100000, positions=[]
        )
        assert ok is True
        assert safety_guard.kill_switch is False

    def test_daily_loss_triggers_kill_switch(self, safety_guard):
        """일일 최대 손실 초과 -> 킬 스위치 자동 활성화"""
        # record_trade로 손실을 누적 (reset 타이밍 문제 회피)
        safety_guard.record_trade("LOSS1", "SELL", pnl=-1600.0)
        safety_guard.record_trade("LOSS2", "SELL", pnl=-1600.0)
        # 누적 PnL: -3200 > 한도 -3000
        assert safety_guard.daily_pnl == -3200.0
        ok, reason, qty = safety_guard.check_order(
            symbol="AAPL", side="BUY", quantity=1,
            price=150.0, account_equity=100000, positions=[]
        )
        assert ok is False
        assert safety_guard.kill_switch is True


# =============================================================================
# 5. 일일 리셋 테스트
# =============================================================================

class TestDailyReset:

    def test_manual_reset(self, safety_guard):
        safety_guard.daily_trades_count = 5
        safety_guard.daily_pnl = -1000.0
        safety_guard.blocked_reasons = ["reason1", "reason2"]
        safety_guard.trade_log = [{"test": True}]
        safety_guard.reset_daily()
        assert safety_guard.daily_trades_count == 0
        assert safety_guard.daily_pnl == 0.0
        assert safety_guard.trade_log == []
        assert safety_guard.blocked_reasons == []

    def test_auto_reset_on_date_change(self, safety_guard):
        safety_guard.daily_trades_count = 5
        safety_guard.daily_pnl = -500.0
        safety_guard.last_trade_date = date(2020, 1, 1)
        ok, _, _ = safety_guard.check_order(
            symbol="AAPL", side="BUY", quantity=1,
            price=150.0, account_equity=100000, positions=[]
        )
        assert ok is True
        assert safety_guard.daily_trades_count == 0
        assert safety_guard.daily_pnl == 0.0


# =============================================================================
# 6. 거래 기록 테스트
# =============================================================================

class TestRecordTrade:

    def test_trade_count_increments(self, safety_guard):
        assert safety_guard.daily_trades_count == 0
        safety_guard.record_trade("AAPL", "BUY", value=1500.0)
        assert safety_guard.daily_trades_count == 1
        safety_guard.record_trade("AAPL", "SELL", pnl=50.0)
        assert safety_guard.daily_trades_count == 2

    def test_sell_pnl_accumulates(self, safety_guard):
        safety_guard.record_trade("AAPL", "SELL", pnl=100.0)
        safety_guard.record_trade("MSFT", "SELL", pnl=-50.0)
        assert safety_guard.daily_pnl == 50.0

    def test_buy_does_not_affect_pnl(self, safety_guard):
        safety_guard.record_trade("AAPL", "BUY", pnl=0.0, value=1500.0)
        assert safety_guard.daily_pnl == 0.0

    def test_trade_log_stored(self, safety_guard):
        safety_guard.record_trade("AAPL", "BUY", value=1500.0)
        assert len(safety_guard.trade_log) == 1
        assert safety_guard.trade_log[0]["symbol"] == "AAPL"


# =============================================================================
# 7. 상태 조회 테스트
# =============================================================================

class TestGetStatus:

    def test_initial_status(self, safety_guard):
        status = safety_guard.get_status()
        assert status["kill_switch"] is False
        assert status["daily_trades"] == 0
        assert status["daily_pnl"] == 0.0
        assert status["consecutive_losses"] == 0
        assert status["paper_mode"] is True
        assert status["blocked_count"] == 0
        assert status["last_blocked"] is None

    def test_status_after_blocks(self, safety_guard):
        safety_guard.activate_kill_switch("테스트")
        safety_guard.check_order(
            symbol="AAPL", side="BUY", quantity=1,
            price=150.0, account_equity=100000, positions=[]
        )
        status = safety_guard.get_status()
        assert status["blocked_count"] >= 1
        assert status["last_blocked"] is not None

    def test_status_max_daily_loss_calculated(self, safety_guard):
        status = safety_guard.get_status()
        expected = round(100000 * 0.03, 2)
        assert status["max_daily_loss"] == expected


# =============================================================================
# 8. 타임존 처리 테스트
# =============================================================================

class TestTimezone:

    def test_default_timezone_uses_local(self, safety_guard):
        today = safety_guard._get_today()
        assert today == date.today()

    def test_timezone_configured(self):
        from executor.safety_guard import SafetyGuard, SafetyConfig
        try:
            from zoneinfo import ZoneInfo
        except ImportError:
            try:
                from backports.zoneinfo import ZoneInfo
            except ImportError:
                pytest.skip("ZoneInfo not available")
        config = SafetyConfig(timezone="America/New_York")
        guard = SafetyGuard(capital=100000, paper=True, config=config)
        today_ny = guard._get_today()
        expected = datetime.now(ZoneInfo("America/New_York")).date()
        assert today_ny == expected


# =============================================================================
# 9. dict 포지션 호환성 테스트
# =============================================================================

class TestDictPositionCompat:

    def test_dict_positions_in_check_order(self, safety_guard):
        from tests.conftest import make_dict_position
        positions = [
            make_dict_position(symbol=f"SYM{i}", quantity=10)
            for i in range(5)
        ]
        ok, reason, qty = safety_guard.check_order(
            symbol="NEWSYM", side="BUY", quantity=1,
            price=100.0, account_equity=100000, positions=positions
        )
        assert ok is False
        assert "포지션 수" in reason

    def test_dict_position_weight_check(self, safety_guard):
        from tests.conftest import make_dict_position
        positions = [
            make_dict_position(
                symbol="AAPL", quantity=100,
                avg_price=180.0, current_price=180.0,
                market_value=18000.0
            )
        ]
        ok, reason, qty = safety_guard.check_order(
            symbol="AAPL", side="BUY", quantity=30,
            price=100.0, account_equity=100000, positions=positions
        )
        if ok:
            assert qty < 30
        else:
            assert "비중" in reason


# =============================================================================
# 10. 엣지 케이스
# =============================================================================

class TestEdgeCases:

    def test_zero_quantity_order(self, safety_guard):
        ok, reason, qty = safety_guard.check_order(
            symbol="AAPL", side="BUY", quantity=0,
            price=150.0, account_equity=100000, positions=[]
        )
        assert ok is False

    def test_zero_equity(self, safety_guard):
        ok, _, _ = safety_guard.check_order(
            symbol="AAPL", side="SELL", quantity=1,
            price=150.0, account_equity=0, positions=[]
        )
        assert ok is True

    def test_paper_mode_flag(self, safety_guard):
        assert safety_guard.paper is True

    def test_wait_before_order_paper_mode(self, safety_guard):
        import time
        start = time.time()
        safety_guard.wait_before_order()
        elapsed = time.time() - start
        assert elapsed < 0.1

    def test_empty_positions_list(self, safety_guard):
        ok, _, qty = safety_guard.check_order(
            symbol="AAPL", side="BUY", quantity=5,
            price=100.0, account_equity=100000, positions=[]
        )
        assert ok is True
        assert qty == 5
