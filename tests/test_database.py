"""
=============================================================================
tests/test_database.py - DatabaseManager 통합 테스트
=============================================================================

SQLite 기반 DatabaseManager의 CRUD 동작을 검증합니다.
temp_db_path와 db_manager fixture를 사용하여 격리된 환경에서 테스트합니다.

실행:
    pytest tests/test_database.py -v
=============================================================================
"""

import pytest
import json
from datetime import datetime, timedelta


# =============================================================================
# 1. 초기화 + Context Manager
# =============================================================================

class TestInitialization:

    def test_db_creates_tables(self, db_manager):
        """initialize() 후 6개 테이블이 존재해야 함"""
        cursor = db_manager.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cursor.fetchall()}
        expected = {"cache", "trades", "portfolio_snapshots",
                    "signals", "positions", "equity_history"}
        assert expected.issubset(tables)

    def test_context_manager(self, temp_db_path):
        """with문으로 사용 시 자동 초기화 + 종료"""
        from database.cache import DatabaseManager
        with DatabaseManager(db_path=temp_db_path) as db:
            assert db.conn is not None
            db.set_cache("test_key", {"hello": "world"})
        # with 블록 종료 후 conn은 닫혀야 함

    def test_double_initialize_safe(self, db_manager):
        """initialize() 두 번 호출해도 에러 없이 동작"""
        db_manager.initialize()  # 이미 초기화된 상태에서 재호출
        # 테이블이 여전히 존재
        row = db_manager.conn.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()
        assert row[0] >= 6


# =============================================================================
# 2. 캐시 테스트
# =============================================================================

class TestCache:

    def test_set_and_get_cache(self, db_manager):
        """캐시 저장 후 조회"""
        db_manager.set_cache("price_AAPL", {"close": 150.0}, ttl=3600)
        result = db_manager.get_cache("price_AAPL")
        assert result == {"close": 150.0}

    def test_cache_miss_returns_none(self, db_manager):
        """존재하지 않는 키 → None"""
        result = db_manager.get_cache("nonexistent_key")
        assert result is None

    def test_cache_expired_returns_none(self, db_manager):
        """만료된 캐시 → None 반환 + 자동 삭제"""
        db_manager.set_cache("expired_key", "data", ttl=-1)  # 이미 만료
        result = db_manager.get_cache("expired_key")
        assert result is None

    def test_cache_overwrite(self, db_manager):
        """같은 키에 재저장 시 덮어쓰기"""
        db_manager.set_cache("key1", "old_value")
        db_manager.set_cache("key1", "new_value")
        assert db_manager.get_cache("key1") == "new_value"

    def test_clear_expired_cache(self, db_manager):
        """만료 캐시 일괄 정리"""
        db_manager.set_cache("valid", "ok", ttl=3600)
        db_manager.set_cache("expired1", "old", ttl=-1)
        db_manager.set_cache("expired2", "old", ttl=-1)
        db_manager.clear_expired_cache()
        assert db_manager.get_cache("valid") == "ok"
        # expired는 이미 get_cache에서 삭제되거나 clear에서 삭제됨


# =============================================================================
# 3. 거래 기록 테스트
# =============================================================================

class TestTrades:

    def test_log_trade(self, db_manager):
        """거래 기록 저장"""
        db_manager.log_trade(
            symbol="AAPL", side="BUY", quantity=10,
            price=150.0, strategy="ensemble", market="US",
            signal_score=0.75
        )
        trades = db_manager.get_trades()
        assert len(trades) == 1
        assert trades[0]["symbol"] == "AAPL"
        assert trades[0]["side"] == "BUY"
        assert trades[0]["quantity"] == 10
        assert trades[0]["total_value"] == 1500.0

    def test_multiple_trades(self, db_manager):
        """여러 거래 기록 저장 + 최신순 조회"""
        db_manager.log_trade("AAPL", "BUY", 10, 150.0)
        db_manager.log_trade("MSFT", "BUY", 5, 400.0)
        db_manager.log_trade("AAPL", "SELL", 10, 160.0, pnl=100.0)
        trades = db_manager.get_trades()
        assert len(trades) == 3
        # 최신순 (SELL이 마지막 기록 → 첫 번째로 조회)
        assert trades[0]["side"] == "SELL"

    def test_get_trades_by_symbol(self, db_manager):
        """종목별 필터 조회"""
        db_manager.log_trade("AAPL", "BUY", 10, 150.0)
        db_manager.log_trade("MSFT", "BUY", 5, 400.0)
        db_manager.log_trade("AAPL", "SELL", 10, 160.0)
        trades = db_manager.get_trades(symbol="AAPL")
        assert len(trades) == 2
        assert all(t["symbol"] == "AAPL" for t in trades)

    def test_trade_pnl_stored(self, db_manager):
        """pnl 컬럼이 정상 저장되는지 확인"""
        db_manager.log_trade("AAPL", "SELL", 10, 160.0, pnl=100.0)
        trades = db_manager.get_trades()
        assert trades[0]["pnl"] == 100.0

    def test_get_trade_stats(self, db_manager):
        """거래 통계 계산"""
        db_manager.log_trade("AAPL", "BUY", 10, 150.0)
        db_manager.log_trade("MSFT", "BUY", 5, 400.0)
        db_manager.log_trade("AAPL", "SELL", 10, 160.0)
        stats = db_manager.get_trade_stats()
        assert stats["total_trades"] == 3
        assert stats["buy_count"] == 2
        assert stats["sell_count"] == 1
        assert stats["unique_symbols"] == 2

    def test_empty_trade_stats(self, db_manager):
        """거래 없을 때 통계"""
        stats = db_manager.get_trade_stats()
        assert stats["total_trades"] == 0


# =============================================================================
# 4. 포지션 관리 테스트
# =============================================================================

class TestPositions:

    def test_save_and_load_positions(self, db_manager):
        """포지션 저장 + 로드"""
        positions = [
            {"symbol": "AAPL", "quantity": 10, "avg_price": 150.0, "current_price": 155.0},
            {"symbol": "MSFT", "quantity": 5, "avg_price": 400.0, "current_price": 410.0},
        ]
        db_manager.save_positions(positions)
        loaded = db_manager.load_positions()
        assert len(loaded) == 2
        symbols = {p["symbol"] for p in loaded}
        assert "AAPL" in symbols
        assert "MSFT" in symbols

    def test_save_positions_overwrites(self, db_manager):
        """포지션 재저장 시 이전 데이터 교체"""
        db_manager.save_positions([
            {"symbol": "AAPL", "quantity": 10, "avg_price": 150.0, "current_price": 155.0}
        ])
        db_manager.save_positions([
            {"symbol": "MSFT", "quantity": 5, "avg_price": 400.0, "current_price": 410.0}
        ])
        loaded = db_manager.load_positions()
        assert len(loaded) == 1
        assert loaded[0]["symbol"] == "MSFT"

    def test_empty_positions(self, db_manager):
        """포지션 없을 때 빈 리스트"""
        loaded = db_manager.load_positions()
        assert loaded == []


# =============================================================================
# 5. 포트폴리오 스냅샷 테스트
# =============================================================================

class TestPortfolioSnapshots:

    def test_save_snapshot(self, db_manager):
        """포트폴리오 스냅샷 저장"""
        db_manager.save_snapshot(
            total_value=105000.0, cash=50000.0,
            positions={"AAPL": 10},
            daily_return=0.02
        )
        cursor = db_manager.conn.execute(
            "SELECT * FROM portfolio_snapshots ORDER BY id DESC LIMIT 1")
        row = dict(cursor.fetchone())
        assert row["total_value"] == 105000.0
        assert row["cash"] == 50000.0
        assert row["daily_return"] == 0.02

    def test_get_latest_snapshot(self, db_manager):
        """최신 스냅샷 조회"""
        db_manager.save_snapshot(100000.0, 50000.0, {})
        latest = db_manager.get_latest_snapshot()
        assert latest is not None
        assert latest["total_value"] == 100000.0

    def test_no_snapshot_returns_none(self, db_manager):
        """스냅샷 없을 때 None"""
        assert db_manager.get_latest_snapshot() is None


# =============================================================================
# 6. Equity History 테스트
# =============================================================================

class TestEquityHistory:

    def test_save_equity(self, db_manager):
        """equity 스냅샷 저장"""
        db_manager.save_equity_snapshot(
            total_equity=105000.0, cash=50000.0,
            positions_value=55000.0, daily_pnl=500.0
        )
        cursor = db_manager.conn.execute(
            "SELECT * FROM equity_history ORDER BY id DESC LIMIT 1")
        row = dict(cursor.fetchone())
        assert row["total_equity"] == 105000.0
        assert row["positions_value"] == 55000.0
