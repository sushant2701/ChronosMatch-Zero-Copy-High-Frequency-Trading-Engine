"""
Pure-Python Reference Limit Order Book (LOB).

Implements strict Price-Time Priority (FIFO matching per discrete price level):
1. Best Price Priority: Highest buy price (Bid) and lowest sell price (Ask) match first.
2. Time Priority: Orders resting at the identical price tick are matched in strict
   arrival order (first-in, first-out).

Serves as the deterministic reference implementation for Day 2, providing the exact
public API that the Cython matching engine will copy on Day 3.
"""

import bisect
import collections
from typing import Deque, Dict, List, NamedTuple, Optional

from chronosmatch.codec import OrderRecord, OrderSide
from chronosmatch.exceptions import InvalidOrderError, OrderNotFoundError


class Trade(NamedTuple):
    """
    Execution trade record generated when two crossing orders match.

    Attributes:
        buy_order_id: Monotonic identifier of the buyer order.
        sell_order_id: Monotonic identifier of the seller order.
        price: Match execution price in integer ticks.
        quantity: Matched lot quantity.
    """

    buy_order_id: int
    sell_order_id: int
    price: int
    quantity: int


class _BookOrder:
    """
    Lightweight resting order container inside the limit order book.

    Uses __slots__ to eliminate dictionary overhead and allow in-place quantity deduction
    upon partial fills without re-allocating new records.
    """

    __slots__ = ("order_id", "side", "price", "quantity", "timestamp_ns")

    def __init__(
        self,
        order_id: int,
        side: int,
        price: int,
        quantity: int,
        timestamp_ns: int,
    ) -> None:
        self.order_id: int = order_id
        self.side: int = side
        self.price: int = price
        self.quantity: int = quantity
        self.timestamp_ns: int = timestamp_ns

    def __repr__(self) -> str:
        side_str = "BUY" if self.side == OrderSide.BUY.value else "SELL"
        return f"_BookOrder(id={self.order_id}, side={side_str}, price={self.price}, qty={self.quantity})"


class LimitOrderBook:
    """
    In-memory Price-Time Priority Limit Order Book.

    Maintains sorted price ladders for bids (descending) and asks (ascending),
    backed by FIFO queues (collections.deque) at each active price level.

    Design Rationale:
        - Sorted Price Lists via bisect: Maintaining sorted integer price lists enables
          O(1) access to top-of-book (best bid / best ask) and fast logarithmic insertion.
        - collections.deque for Price Levels: Deques provide O(1) appends and O(1) pops
          from both ends, ensuring strict FIFO time priority without queue shifting overhead.
        - Direct ID Index (_orders dict): Maintains O(1) lookup mapping order_id to
          _BookOrder, enabling rapid cancellation without full book scanning.
    """

    def __init__(self) -> None:
        """Initialize empty limit order book ladders and index."""
        # Active price ladders: price -> deque of resting _BookOrder instances
        self._bids: Dict[int, Deque[_BookOrder]] = collections.defaultdict(collections.deque)
        self._asks: Dict[int, Deque[_BookOrder]] = collections.defaultdict(collections.deque)

        # Sorted prices: _bid_prices is descending (highest price first at index 0)
        #                _ask_prices is ascending (lowest price first at index 0)
        self._bid_prices: List[int] = []
        self._ask_prices: List[int] = []

        # Order index: order_id -> _BookOrder for O(1) cancellation lookup
        self._orders: Dict[int, _BookOrder] = {}

    @property
    def order_count(self) -> int:
        """Total number of resting orders currently in the book."""
        return len(self._orders)

    @property
    def bid_level_count(self) -> int:
        """Number of distinct price levels on the bid side."""
        return len(self._bid_prices)

    @property
    def ask_level_count(self) -> int:
        """Number of distinct price levels on the ask side."""
        return len(self._ask_prices)

    def _validate_order(self, order: OrderRecord) -> None:
        """Validate order attributes before processing."""
        if not isinstance(order, OrderRecord):
            raise InvalidOrderError(f"Expected OrderRecord instance, got {type(order).__name__}.")
        if order.quantity <= 0:
            raise InvalidOrderError(f"Order quantity must be strictly positive, got {order.quantity}.")
        if order.price <= 0:
            raise InvalidOrderError(f"Order price must be strictly positive, got {order.price}.")
        if order.side not in (OrderSide.BUY.value, OrderSide.SELL.value):
            raise InvalidOrderError(f"Invalid order side {order.side}.")

    def _add_resting_bid(self, order: _BookOrder) -> None:
        """Add an unfilled order to the resting bid ladder (price descending)."""
        price = order.price
        if price not in self._bids or len(self._bids[price]) == 0:
            # Maintain descending order: higher price at smaller index
            # bisect_right on negated price gives position
            idx = bisect.bisect_right([-p for p in self._bid_prices], -price)
            self._bid_prices.insert(idx, price)

        self._bids[price].append(order)
        self._orders[order.order_id] = order

    def _add_resting_ask(self, order: _BookOrder) -> None:
        """Add an unfilled order to the resting ask ladder (price ascending)."""
        price = order.price
        if price not in self._asks or len(self._asks[price]) == 0:
            # Maintain ascending order: lower price at smaller index
            idx = bisect.bisect_right(self._ask_prices, price)
            self._ask_prices.insert(idx, price)

        self._asks[price].append(order)
        self._orders[order.order_id] = order

    def _remove_empty_bid_level(self, price: int) -> None:
        """Clean up price level if no resting bid orders remain."""
        if price in self._bids and len(self._bids[price]) == 0:
            del self._bids[price]
            # Remove from _bid_prices list
            idx = bisect.bisect_left([-p for p in self._bid_prices], -price)
            if idx < len(self._bid_prices) and self._bid_prices[idx] == price:
                self._bid_prices.pop(idx)

    def _remove_empty_ask_level(self, price: int) -> None:
        """Clean up price level if no resting ask orders remain."""
        if price in self._asks and len(self._asks[price]) == 0:
            del self._asks[price]
            # Remove from _ask_prices list
            idx = bisect.bisect_left(self._ask_prices, price)
            if idx < len(self._ask_prices) and self._ask_prices[idx] == price:
                self._ask_prices.pop(idx)
