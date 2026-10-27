"""
Unit and integration tests for the ChronosMatch matching engine factory and shared backend.

Verifies:
1. Engine factory fallback behavior when Cython native extension is unavailable.
2. Logging of active engine backend during initialization.
3. Explicit backend selection ('python' and 'cython' validation and errors).
4. Shared backend compliance: whichever engine is returned by get_engine() must
   satisfy the complete LimitOrderBook interface specification:
   - Empty state initialization and top-of-book dual accessors
   - Input validation rejecting invalid orders
   - Buy matches Sell full fill at resting passive price
   - Partial fill with resting remainder
   - Strict Price Priority
   - Strict Time Priority (FIFO)
   - Order cancellation and unknown cancel error handling
"""

import logging
import pytest
from typing import Generator

from chronosmatch.codec import OrderRecord, OrderSide
from chronosmatch.engine import (
    EngineBackend,
    get_active_backend,
    get_engine,
    is_cython_available,
)
from chronosmatch.exceptions import InvalidOrderError, OrderNotFoundError
from chronosmatch.order_book import LimitOrderBook, Trade


def test_fallback_returns_python_order_book_when_cython_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify get_engine() gracefully returns LimitOrderBook when Cython is unavailable."""
    # Simulate missing Cython module
    monkeypatch.setattr("chronosmatch.engine._CYTHON_AVAILABLE", False)
    monkeypatch.setattr("chronosmatch.engine._CythonOrderBook", None)

    engine = get_engine()
    assert isinstance(engine, LimitOrderBook)
    assert get_active_backend() == EngineBackend.PYTHON
    assert not is_cython_available()


def test_explicit_python_backend_selection() -> None:
    """Verify get_engine(backend='python') explicitly returns pure-Python LimitOrderBook."""
    engine = get_engine(backend="python")
    assert isinstance(engine, LimitOrderBook)

    engine_enum = get_engine(backend=EngineBackend.PYTHON)
    assert isinstance(engine_enum, LimitOrderBook)


def test_invalid_backend_name_raises_value_error() -> None:
    """Verify get_engine() rejects unsupported backend names with ValueError."""
    with pytest.raises(ValueError, match="Unknown engine backend 'invalid_backend'"):
        get_engine(backend="invalid_backend")


def test_explicit_cython_when_unavailable_raises_runtime_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify requesting 'cython' raises RuntimeError if native extension is missing."""
    monkeypatch.setattr("chronosmatch.engine._CYTHON_AVAILABLE", False)
    monkeypatch.setattr("chronosmatch.engine._CythonOrderBook", None)

    with pytest.raises(RuntimeError, match="Cython engine backend explicitly requested, but native extension"):
        get_engine(backend="cython")


def test_engine_factory_logs_active_backend(caplog: pytest.LogCaptureFixture) -> None:
    """Verify get_engine() emits informational log message declaring active backend."""
    caplog.set_level(logging.INFO, logger="chronosmatch.engine")
    engine = get_engine()
    assert engine is not None
    assert any("Active matching engine backend" in record.message for record in caplog.records)


# ---------------------------------------------------------------------------
# Shared Backend Behavioral Tests (Runs against get_engine() active instance)
# ---------------------------------------------------------------------------

@pytest.fixture
def active_engine() -> Generator:
    """Fixture providing a freshly instantiated order engine from get_engine()."""
    engine = get_engine()
    yield engine
    if hasattr(engine, "clear"):
        engine.clear()


def test_shared_empty_book_state(active_engine) -> None:
    """Verify default empty book properties on active engine."""
    assert active_engine.order_count == 0
    assert active_engine.bid_level_count == 0
    assert active_engine.ask_level_count == 0
    assert active_engine.total_bid_volume() == 0
    assert active_engine.total_ask_volume() == 0

    assert active_engine.best_bid() is None
    assert active_engine.best_ask() is None
    assert active_engine.best_bid == None  # noqa: E711
    assert active_engine.best_ask == None  # noqa: E711
    assert active_engine.get_best_bid() is None
    assert active_engine.get_best_ask() is None


def test_shared_input_validation(active_engine) -> None:
    """Verify active engine enforces input validation."""
    with pytest.raises(InvalidOrderError, match="quantity"):
        active_engine.add_order(OrderRecord(order_id=1, side=OrderSide.BUY, price=100_00, quantity=0, timestamp_ns=100))

    with pytest.raises(InvalidOrderError, match="price"):
        active_engine.add_order(OrderRecord(order_id=2, side=OrderSide.BUY, price=0, quantity=10, timestamp_ns=100))

    with pytest.raises(InvalidOrderError, match="Expected OrderRecord"):
        active_engine.add_order("invalid_order_object")  # type: ignore


def test_shared_buy_matches_sell_full_fill(active_engine) -> None:
    """Verify full fill execution at resting passive order price."""
    # Place resting Sell order @ 101.00, qty 50
    sell_order = OrderRecord(order_id=101, side=OrderSide.SELL, price=101_00, quantity=50, timestamp_ns=1000)
    trades = active_engine.add_order(sell_order)
    assert trades == []
    assert active_engine.order_count == 1
    assert active_engine.best_ask == 101_00

    # Incoming crossing Buy order @ 101.50, qty 50 (crosses and matches @ 101.00)
    buy_order = OrderRecord(order_id=102, side=OrderSide.BUY, price=101_50, quantity=50, timestamp_ns=2000)
    trades = active_engine.add_order(buy_order)

    assert len(trades) == 1
    trade = trades[0]
    assert trade.buy_order_id == 102
    assert trade.sell_order_id == 101
    assert trade.price == 101_00  # Matched at passive resting ask price
    assert trade.quantity == 50

    # Book is now completely empty
    assert active_engine.order_count == 0
    assert active_engine.best_bid == None  # noqa: E711
    assert active_engine.best_ask == None  # noqa: E711
    assert active_engine.best_bid() is None
    assert active_engine.best_ask() is None


def test_shared_partial_fill_with_resting_remainder(active_engine) -> None:
    """Verify aggressive order partially fills and rests remainder on book."""
    # Place resting Ask @ 200.00, qty 30
    active_engine.add_order(OrderRecord(order_id=1, side=OrderSide.SELL, price=200_00, quantity=30, timestamp_ns=100))

    # Incoming Buy @ 200.00, qty 100 -> matches 30, rests 70 as Bid @ 200.00
    trades = active_engine.add_order(OrderRecord(order_id=2, side=OrderSide.BUY, price=200_00, quantity=100, timestamp_ns=200))

    assert len(trades) == 1
    assert trades[0].quantity == 30
    assert trades[0].price == 200_00

    assert active_engine.order_count == 1
    assert active_engine.best_ask == None  # noqa: E711
    assert active_engine.best_ask() is None
    assert active_engine.best_bid == 200_00
    assert active_engine.best_bid() == 200_00
    assert active_engine.bid_volume_at(200_00) == 70


def test_shared_price_priority(active_engine) -> None:
    """Verify superior prices match before inferior prices."""
    # Two resting Asks: order 1 @ 102.00, order 2 @ 101.00
    active_engine.add_order(OrderRecord(order_id=1, side=OrderSide.SELL, price=102_00, quantity=10, timestamp_ns=100))
    active_engine.add_order(OrderRecord(order_id=2, side=OrderSide.SELL, price=101_00, quantity=10, timestamp_ns=200))

    assert active_engine.best_ask == 101_00

    # Aggressive Buy @ 105.00 for 15 lots -> fills order 2 first (10 lots @ 101.00), then order 1 (5 lots @ 102.00)
    trades = active_engine.add_order(OrderRecord(order_id=3, side=OrderSide.BUY, price=105_00, quantity=15, timestamp_ns=300))

    assert len(trades) == 2
    assert trades[0].sell_order_id == 2
    assert trades[0].price == 101_00
    assert trades[0].quantity == 10

    assert trades[1].sell_order_id == 1
    assert trades[1].price == 102_00
    assert trades[1].quantity == 5

    assert active_engine.order_count == 1
    assert active_engine.best_ask == 102_00
    assert active_engine.ask_volume_at(102_00) == 5


def test_shared_time_priority_fifo(active_engine) -> None:
    """Verify orders at the same price match in strict FIFO arrival order."""
    # Two resting Asks at identical price 50.00
    active_engine.add_order(OrderRecord(order_id=10, side=OrderSide.SELL, price=50_00, quantity=10, timestamp_ns=1000))
    active_engine.add_order(OrderRecord(order_id=20, side=OrderSide.SELL, price=50_00, quantity=10, timestamp_ns=2000))

    # Buy matches 10 lots -> must match order 10 (earlier timestamp)
    trades = active_engine.add_order(OrderRecord(order_id=30, side=OrderSide.BUY, price=50_00, quantity=10, timestamp_ns=3000))

    assert len(trades) == 1
    assert trades[0].sell_order_id == 10
    assert trades[0].quantity == 10

    assert active_engine.order_count == 1
    assert active_engine.ask_volume_at(50_00) == 10


def test_shared_cancel_order(active_engine) -> None:
    """Verify order cancellation and rejection of unknown order IDs."""
    active_engine.add_order(OrderRecord(order_id=50, side=OrderSide.BUY, price=100_00, quantity=25, timestamp_ns=100))
    assert active_engine.order_count == 1
    assert active_engine.best_bid == 100_00

    # Cancel resting order
    active_engine.cancel_order(50)
    assert active_engine.order_count == 0
    assert active_engine.best_bid == None  # noqa: E711
    assert active_engine.best_bid() is None

    # Canceling non-existent order raises OrderNotFoundError
    with pytest.raises(OrderNotFoundError, match="Order ID 50 not found"):
        active_engine.cancel_order(50)
