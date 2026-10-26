"""
Unit tests for the ChronosMatch pure-Python Limit Order Book.

Verifies:
1. Initial empty book state and top-of-book queries.
2. Domain input validation and custom exception enforcement.
3. Strict Buy-matches-Sell single trade execution and full fills.
4. Partial fill execution leaving remainder resting on the book.
5. Price priority: best prices match before inferior prices.
6. Time priority (FIFO): orders at the same price tick match in arrival order.
7. Cancel removes a resting order, unknown cancel raises OrderNotFoundError.
8. Aggressive order crossing multiple resting price levels in a single sweep.
"""

import pytest

from chronosmatch.codec import OrderRecord, OrderSide
from chronosmatch.exceptions import InvalidOrderError, OrderNotFoundError
from chronosmatch.order_book import LimitOrderBook, Trade


def test_empty_book_state() -> None:
    """Verify empty order book properties and dual method/property top-of-book access."""
    book = LimitOrderBook()

    assert book.order_count == 0
    assert book.bid_level_count == 0
    assert book.ask_level_count == 0
    assert book.total_bid_volume() == 0
    assert book.total_ask_volume() == 0

    # Test top-of-book query as both method and property
    assert book.best_bid() is None
    assert book.best_ask() is None
    assert book.best_bid == None  # noqa: E711
    assert book.best_ask == None  # noqa: E711
    assert book.get_best_bid() is None
    assert book.get_best_ask() is None


def test_invalid_order_rejection() -> None:
    """Verify order book rejects invalid orders with custom InvalidOrderError."""
    book = LimitOrderBook()

    # Zero or negative quantity
    with pytest.raises(InvalidOrderError, match="quantity"):
        book.add_order(OrderRecord(order_id=1, side=OrderSide.BUY, price=100_00, quantity=0, timestamp_ns=100))

    # Zero or negative price
    with pytest.raises(InvalidOrderError, match="price"):
        book.add_order(OrderRecord(order_id=2, side=OrderSide.BUY, price=0, quantity=10, timestamp_ns=100))

    # Invalid type
    with pytest.raises(InvalidOrderError, match="Expected OrderRecord"):
        book.add_order("invalid_string_order")  # type: ignore


def test_simple_buy_matches_sell_full_fill() -> None:
    """Verify an aggressive buy completely fills against a resting sell at passive price."""
    book = LimitOrderBook()

    # Passive Sell order resting on the book
    sell_order = OrderRecord(
        order_id=101,
        side=OrderSide.SELL,
        price=105_00,
        quantity=25,
        timestamp_ns=1_000,
    )
    trades_sell = book.add_order(sell_order)
    assert len(trades_sell) == 0  # No matches yet
    assert book.best_ask == 105_00
    assert book.best_ask() == 105_00
    assert book.best_bid() is None
    assert book.order_count == 1
    assert book.ask_volume_at(105_00) == 25

    # Aggressive Buy order crossing the resting ask
    buy_order = OrderRecord(
        order_id=102,
        side=OrderSide.BUY,
        price=105_00,
        quantity=25,
        timestamp_ns=2_000,
    )
    trades_buy = book.add_order(buy_order)

    # Must produce exactly one full fill Trade
    assert len(trades_buy) == 1
    trade = trades_buy[0]
    assert isinstance(trade, Trade)
    assert trade.buy_order_id == 102
    assert trade.sell_order_id == 101
    assert trade.price == 105_00  # Executed at passive ask price
    assert trade.quantity == 25

    # Tuple unpacking check: (buy_order_id, sell_order_id, price, quantity)
    b_id, s_id, p, q = trade
    assert (b_id, s_id, p, q) == (102, 101, 105_00, 25)

    # Book must now be completely empty
    assert book.order_count == 0
    assert book.best_bid() is None
    assert book.best_ask() is None


def test_partial_fill_leaves_remainder_resting() -> None:
    """Verify partial fill: aggressor partially fills and remainder rests on opposite book."""
    book = LimitOrderBook()

    # Resting Sell: 10 lots @ 100_00
    sell_order = OrderRecord(
        order_id=1,
        side=OrderSide.SELL,
        price=100_00,
        quantity=10,
        timestamp_ns=100,
    )
    book.add_order(sell_order)

    # Incoming Buy: 25 lots @ 100_00 (sweeps the 10 lots, 15 lots remainder)
    buy_order = OrderRecord(
        order_id=2,
        side=OrderSide.BUY,
        price=100_00,
        quantity=25,
        timestamp_ns=200,
    )
    trades = book.add_order(buy_order)

    assert len(trades) == 1
    assert trades[0] == Trade(buy_order_id=2, sell_order_id=1, price=100_00, quantity=10)

    # Remainder of Buy (15 lots) must now rest on the bid book at 100_00
    assert book.order_count == 1
    assert book.best_bid == 100_00
    assert book.best_bid() == 100_00
    assert book.best_ask() is None
    assert book.total_bid_volume() == 15
    assert book.bid_volume_at(100_00) == 15


def test_price_priority() -> None:
    """Verify price priority: best prices match first regardless of arrival time."""
    book = LimitOrderBook()

    # Place two resting asks: Ask1 at higher price (inferior), Ask2 at lower price (superior)
    inferior_ask = OrderRecord(order_id=1, side=OrderSide.SELL, price=102_00, quantity=10, timestamp_ns=100)
    superior_ask = OrderRecord(order_id=2, side=OrderSide.SELL, price=101_00, quantity=10, timestamp_ns=200)

    book.add_order(inferior_ask)
    book.add_order(superior_ask)

    # Top-of-book must reflect the best (lowest) ask
    assert book.best_ask == 101_00

    # Aggressive Buy willing to pay up to 105_00 for 10 lots
    buy_order = OrderRecord(order_id=3, side=OrderSide.BUY, price=105_00, quantity=10, timestamp_ns=300)
    trades = book.add_order(buy_order)

    assert len(trades) == 1
    # Must match the superior ask (101_00) first, NOT the earlier inferior ask
    assert trades[0].sell_order_id == 2
    assert trades[0].price == 101_00
    assert trades[0].quantity == 10

    # Inferior ask at 102_00 must remain resting
    assert book.order_count == 1
    assert book.best_ask == 102_00
    assert book.ask_volume_at(102_00) == 10


def test_time_priority_at_same_price() -> None:
    """Verify time priority: multiple orders at the same price level match FIFO."""
    book = LimitOrderBook()

    # Three resting bids at identical price 100_00 arriving in sequence
    bid1 = OrderRecord(order_id=10, side=OrderSide.BUY, price=100_00, quantity=5, timestamp_ns=100)
    bid2 = OrderRecord(order_id=20, side=OrderSide.BUY, price=100_00, quantity=10, timestamp_ns=200)
    bid3 = OrderRecord(order_id=30, side=OrderSide.BUY, price=100_00, quantity=15, timestamp_ns=300)

    book.add_order(bid1)
    book.add_order(bid2)
    book.add_order(bid3)

    assert book.total_bid_volume() == 30
    assert book.best_bid == 100_00

    # Incoming Sell for 12 lots: should fill bid1 (5 lots) fully, then bid2 (7 lots) partially
    sell_order = OrderRecord(order_id=99, side=OrderSide.SELL, price=100_00, quantity=12, timestamp_ns=400)
    trades = book.add_order(sell_order)

    assert len(trades) == 2
    # Trade 1: fills bid1 completely (5 lots)
    assert trades[0] == Trade(buy_order_id=10, sell_order_id=99, price=100_00, quantity=5)
    # Trade 2: partially fills bid2 (7 lots)
    assert trades[1] == Trade(buy_order_id=20, sell_order_id=99, price=100_00, quantity=7)

    # Remaining volume: bid2 has 3 lots left, bid3 has 15 lots left
    assert book.total_bid_volume() == 18
    assert book.order_count == 2


def test_cancel_order_removes_resting_order() -> None:
    """Verify cancel_order removes a resting order and updates book state."""
    book = LimitOrderBook()

    # Place two resting bids at different prices
    bid_low = OrderRecord(
        order_id=1, side=OrderSide.BUY, price=99_00, quantity=10, timestamp_ns=100
    )
    bid_high = OrderRecord(
        order_id=2, side=OrderSide.BUY, price=101_00, quantity=20, timestamp_ns=200
    )
    book.add_order(bid_low)
    book.add_order(bid_high)

    assert book.order_count == 2
    assert book.best_bid == 101_00
    assert book.total_bid_volume() == 30

    # Cancel the higher bid — best_bid should drop to the lower price
    book.cancel_order(2)
    assert book.order_count == 1
    assert book.best_bid == 99_00
    assert book.total_bid_volume() == 10
    assert book.bid_volume_at(101_00) == 0

    # Place and cancel a resting ask to verify ask-side cancellation
    ask = OrderRecord(
        order_id=3, side=OrderSide.SELL, price=105_00, quantity=15, timestamp_ns=300
    )
    book.add_order(ask)
    assert book.best_ask == 105_00
    assert book.order_count == 2

    book.cancel_order(3)
    assert book.order_count == 1
    assert book.best_ask() is None
    assert book.ask_volume_at(105_00) == 0


def test_cancel_unknown_order_raises_not_found() -> None:
    """Verify cancelling an order ID not in the book raises OrderNotFoundError."""
    book = LimitOrderBook()

    # No orders exist — any cancel should raise
    with pytest.raises(OrderNotFoundError, match="not found"):
        book.cancel_order(9999)

    # Add an order, cancel it, then try to cancel the same ID again
    order = OrderRecord(
        order_id=7, side=OrderSide.BUY, price=100_00, quantity=5, timestamp_ns=100
    )
    book.add_order(order)
    book.cancel_order(7)

    with pytest.raises(OrderNotFoundError, match="not found"):
        book.cancel_order(7)


def test_multi_level_crossing_sweep() -> None:
    """
    Verify that an aggressive order crossing multiple resting price levels
    produces the correct sequence of trades, each at the passive order's price.

    Scenario: Three resting asks at prices 100, 101, 102. An aggressive buy
    at price 103 for 25 lots should sweep through all three levels sequentially.
    """
    book = LimitOrderBook()

    # Three resting asks at ascending prices
    ask_100 = OrderRecord(
        order_id=1, side=OrderSide.SELL, price=100_00, quantity=8, timestamp_ns=100
    )
    ask_101 = OrderRecord(
        order_id=2, side=OrderSide.SELL, price=101_00, quantity=10, timestamp_ns=200
    )
    ask_102 = OrderRecord(
        order_id=3, side=OrderSide.SELL, price=102_00, quantity=12, timestamp_ns=300
    )
    book.add_order(ask_100)
    book.add_order(ask_101)
    book.add_order(ask_102)

    assert book.ask_level_count == 3
    assert book.best_ask == 100_00

    # Aggressive buy sweeping all three levels (8 + 10 + 7 remainder from 102)
    aggressive_buy = OrderRecord(
        order_id=4, side=OrderSide.BUY, price=103_00, quantity=25, timestamp_ns=400
    )
    trades = book.add_order(aggressive_buy)

    # Must produce exactly three trades, one per crossed price level
    assert len(trades) == 3

    # Trade 1: sweeps all 8 lots at ask price 100_00
    assert trades[0] == Trade(buy_order_id=4, sell_order_id=1, price=100_00, quantity=8)
    # Trade 2: sweeps all 10 lots at ask price 101_00
    assert trades[1] == Trade(buy_order_id=4, sell_order_id=2, price=101_00, quantity=10)
    # Trade 3: partially fills 7 lots at ask price 102_00 (only 7 of 12 needed)
    assert trades[2] == Trade(buy_order_id=4, sell_order_id=3, price=102_00, quantity=7)

    # After sweep: ask at 102_00 should have 5 lots remaining (12 - 7)
    assert book.order_count == 1
    assert book.best_ask == 102_00
    assert book.ask_volume_at(102_00) == 5

    # No unfilled buy quantity should rest on the bid side
    assert book.best_bid() is None
    assert book.total_bid_volume() == 0
