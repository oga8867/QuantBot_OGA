# -*- coding: utf-8 -*-
"""
PaperExecutor DB integration test - Simple version
Tests backward compatibility and method signatures
"""

import sys
import os
sys.path.insert(0, str(os.path.dirname(__file__)))

from executor.paper_executor import PaperExecutor
from executor.base import OrderSide, OrderType
from database.cache import DatabaseManager
import tempfile


def test_1_backward_compat():
    """Test: Works without DB"""
    print("\n[Test 1] Backward Compatibility (no DB)")
    print("-" * 50)

    executor = PaperExecutor(initial_capital=1_000_000, currency="USD")
    executor.connect()

    executor.set_current_price("AAPL", 150.0)
    order = executor.buy_market("AAPL", 10)

    assert order.filled_price == 150.0
    assert len(executor.trade_history) == 1
    assert len(executor.positions) == 1
    print("✓ Works without DB")


def test_2_db_parameter():
    """Test: DB parameter works"""
    print("\n[Test 2] DB parameter initialization")
    print("-" * 50)

    temp_dir = tempfile.gettempdir()
    db_path = os.path.join(temp_dir, "test_db.db")
    if os.path.exists(db_path):
        os.remove(db_path)

    db = DatabaseManager(db_path)
    db.initialize()

    executor = PaperExecutor(initial_capital=1_000_000, currency="USD", db=db)
    assert executor.db is not None
    assert executor.db == db
    print("✓ DB parameter accepted")

    executor.connect()
    print("✓ Connected with DB")

    executor.set_current_price("AAPL", 150.0)
    order = executor.buy_market("AAPL", 10)

    assert order.filled_price == 150.0
    print("✓ Buy order executed")

    # Check DB
    db_trades = db.get_trades()
    print(f"  Trades in DB: {len(db_trades)}")
    assert len(db_trades) == 1
    print("✓ Trade saved to DB")

    db_positions = db.load_positions()
    print(f"  Positions in DB: {len(db_positions)}")
    assert len(db_positions) == 1
    print("✓ Position saved to DB")

    db.close()


def test_3_equity_snapshot():
    """Test: Equity snapshot method"""
    print("\n[Test 3] Equity snapshot")
    print("-" * 50)

    temp_dir = tempfile.gettempdir()
    db_path = os.path.join(temp_dir, "test_equity.db")
    if os.path.exists(db_path):
        os.remove(db_path)

    db = DatabaseManager(db_path)
    db.initialize()

    executor = PaperExecutor(initial_capital=1_000_000, currency="USD", db=db)
    executor.connect()

    executor.set_current_price("AAPL", 150.0)
    executor.buy_market("AAPL", 10)

    # Save equity snapshot
    executor.save_equity_snapshot()
    print("✓ Equity snapshot saved")

    # Check equity history
    history = db.get_equity_history(days=1)
    print(f"  Equity records: {len(history)}")
    assert len(history) > 0
    print("✓ Equity history retrieved")

    db.close()


def test_4_restore_from_db():
    """Test: Restore positions from DB"""
    print("\n[Test 4] Restore from DB")
    print("-" * 50)

    temp_dir = tempfile.gettempdir()
    db_path = os.path.join(temp_dir, "test_restore.db")
    if os.path.exists(db_path):
        os.remove(db_path)

    # Step 1: Create and trade
    db = DatabaseManager(db_path)
    db.initialize()

    executor1 = PaperExecutor(initial_capital=1_000_000, currency="USD", db=db)
    executor1.connect()

    executor1.set_current_price("AAPL", 150.0)
    executor1.set_current_price("MSFT", 330.0)

    executor1.buy_market("AAPL", 10)
    executor1.buy_market("MSFT", 5)

    print(f"  Executor1 positions: {len(executor1.positions)}")
    print(f"  Executor1 trades: {len(executor1.trade_history)}")

    db.close()

    # Step 2: Reopen and restore
    db = DatabaseManager(db_path)
    db.initialize()

    executor2 = PaperExecutor(initial_capital=1_000_000, currency="USD", db=db)
    executor2.connect()

    print(f"  Executor2 positions (restored): {len(executor2.positions)}")
    print(f"  Executor2 trades (restored): {len(executor2.trade_history)}")

    assert len(executor2.positions) == 2, "Should restore 2 positions"
    assert len(executor2.trade_history) == 2, "Should restore 2 trades"
    print("✓ Positions restored from DB")

    db.close()


if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("PaperExecutor DB Integration Tests")
    print("=" * 60)

    try:
        test_1_backward_compat()
        test_2_db_parameter()
        test_3_equity_snapshot()
        test_4_restore_from_db()

        print("\n" + "=" * 60)
        print("All tests passed!")
        print("=" * 60)
    except Exception as e:
        print(f"\n✗ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
