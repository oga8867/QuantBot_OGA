# -*- coding: utf-8 -*-
"""
test_paper_executor_db.py - PaperExecutor and DatabaseManager integration test

This test verifies:
1. Backward compatibility (works without DB)
2. DB integration (save/restore trades and positions)
3. Position synchronization
4. Equity snapshot recording
"""

import sys
import os
import tempfile
from datetime import datetime

# Add project path
sys.path.insert(0, str(os.path.dirname(__file__)))

from executor.paper_executor import PaperExecutor
from executor.base import Order, OrderSide, OrderType
from database.cache import DatabaseManager


def test_without_db():
    """Test 1: Works without DB (backward compatibility)"""
    print("\n" + "="*60)
    print("TEST 1: Backward compatibility (without DB)")
    print("="*60)

    executor = PaperExecutor(initial_capital=1_000_000, currency="USD")
    executor.connect()

    # Set current prices
    executor.set_current_price("AAPL", 150.0)
    executor.set_current_price("MSFT", 330.0)

    # Buy
    print("\n[BUY] AAPL 10 @ $150")
    order1 = executor.buy_market("AAPL", 10)
    print(f"  Filled: ${order1.filled_price}, Order ID: {order1.order_id}")

    print("\n[BUY] MSFT 5 @ $330")
    order2 = executor.buy_market("MSFT", 5)
    print(f"  Filled: ${order2.filled_price}, Order ID: {order2.order_id}")

    # Check account
    print("\n[Account Summary]")
    print(executor.get_summary())

    # Check trade history
    print(f"\n[Trade History] Total {len(executor.trade_history)} trades")
    for trade in executor.trade_history:
        print(f"  {trade['symbol']}: {trade['side']} {trade['quantity']} @ ${trade['price']}")

    assert len(executor.trade_history) == 2, "Should have 2 trades"
    print("\n✓ Test 1 passed")


def test_with_db():
    """Test 2: DB integration for trades and positions"""
    print("\n" + "="*60)
    print("TEST 2: DB integration (save trades and positions)")
    print("="*60)

    # Initialize DB in temp directory
    temp_dir = tempfile.gettempdir()
    db_path = os.path.join(temp_dir, "test_quantbot.db")
    if os.path.exists(db_path):
        os.remove(db_path)

    db = DatabaseManager(db_path)
    db.initialize()

    # Create executor with DB
    executor = PaperExecutor(initial_capital=1_000_000, currency="USD", db=db)
    executor.connect()

    # Set current prices
    executor.set_current_price("AAPL", 150.0)
    executor.set_current_price("MSFT", 330.0)

    # Buy
    print("\n[BUY] AAPL 10 @ $150")
    order1 = executor.buy_market("AAPL", 10)
    print(f"  Filled: ${order1.filled_price}")

    print("\n[BUY] MSFT 5 @ $330")
    order2 = executor.buy_market("MSFT", 5)
    print(f"  Filled: ${order2.filled_price}")

    # Check DB trades
    print("\n[DB Trades]")
    db_trades = db.get_trades()
    for trade in db_trades:
        print(f"  {trade['symbol']}: {trade['side']} {trade['quantity']} @ ${trade['price']}")
    assert len(db_trades) == 2, "Should have 2 trades in DB"

    # Check DB positions
    print("\n[DB Positions]")
    db_positions = db.load_positions()
    for pos in db_positions:
        print(f"  {pos['symbol']}: {pos['quantity']} @ ${pos['avg_price']}")
    assert len(db_positions) == 2, "Should have 2 positions in DB"

    # Save equity snapshot
    print("\n[Save Equity Snapshot]")
    executor.save_equity_snapshot()
    print(f"  Total Equity: ${executor.get_account().total_equity:,.2f}")

    db.close()

    print("\n✓ Test 2 passed")


def test_restore_from_db():
    """Test 3: Restore from DB on restart"""
    print("\n" + "="*60)
    print("TEST 3: Restore from DB on restart")
    print("="*60)

    temp_dir = tempfile.gettempdir()
    db_path = os.path.join(temp_dir, "test_quantbot.db")

    if not os.path.exists(db_path):
        print("  [SKIP] Run Test 2 first")
        return

    # Reopen DB
    db = DatabaseManager(db_path)
    db.initialize()

    # Create new executor (will restore from DB)
    print("\n[New Executor - Restore from DB]")
    executor2 = PaperExecutor(initial_capital=1_000_000, currency="USD", db=db)
    executor2.connect()  # Auto-restores from DB

    # Check restored positions
    print(f"\n[Restored Positions] {len(executor2.positions)} positions")
    for symbol, pos in executor2.positions.items():
        print(f"  {symbol}: {pos.quantity} @ ${pos.avg_price}")

    # Check restored trades
    print(f"\n[Restored Trades] {len(executor2.trade_history)} trades")
    for trade in executor2.trade_history:
        print(f"  {trade['symbol']}: {trade['side']} {trade['quantity']} @ ${trade['price']}")

    assert len(executor2.positions) == 2, "Should have 2 restored positions"
    assert len(executor2.trade_history) == 2, "Should have 2 restored trades"

    # Check equity history
    print("\n[Equity History]")
    equity_history = db.get_equity_history(days=1)
    for record in equity_history:
        print(f"  {record['timestamp']}: Total ${record['total_equity']:,.2f}")

    db.close()

    print("\n✓ Test 3 passed")


def test_position_sync():
    """Test 4: Position sync on buy/sell"""
    print("\n" + "="*60)
    print("TEST 4: Position sync on partial/full sell")
    print("="*60)

    temp_dir = tempfile.gettempdir()
    db_path = os.path.join(temp_dir, "test_quantbot_sync.db")
    if os.path.exists(db_path):
        os.remove(db_path)

    db = DatabaseManager(db_path)
    db.initialize()

    executor = PaperExecutor(initial_capital=1_000_000, currency="USD", db=db)
    executor.connect()

    executor.set_current_price("AAPL", 150.0)

    # Buy
    print("\n[BUY] AAPL 100 @ $150")
    executor.buy_market("AAPL", 100)

    db_pos = db.load_positions()
    print(f"  DB Position: {db_pos[0]['quantity']} shares")
    assert db_pos[0]['quantity'] == 100

    # Partial sell
    print("\n[SELL] AAPL 30 @ $150")
    executor.sell_market("AAPL", 30)

    db_pos = db.load_positions()
    print(f"  DB Position: {db_pos[0]['quantity']} shares")
    assert db_pos[0]['quantity'] == 70, "Should have 70 shares left"

    # Full liquidation
    print("\n[SELL] AAPL 70 @ $150")
    executor.sell_market("AAPL", 70)

    db_pos = db.load_positions()
    print(f"  Remaining DB Positions: {len(db_pos)} positions")
    assert len(db_pos) == 0, "Should be fully liquidated"

    db.close()

    print("\n✓ Test 4 passed")


if __name__ == "__main__":
    print("\n" + "="*60)
    print("PaperExecutor DB Integration Test")
    print("="*60)

    try:
        test_without_db()
        test_with_db()
        test_restore_from_db()
        test_position_sync()

        print("\n" + "="*60)
        print("All tests passed!")
        print("="*60)

    except AssertionError as e:
        print(f"\n✗ Test failed: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n✗ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
