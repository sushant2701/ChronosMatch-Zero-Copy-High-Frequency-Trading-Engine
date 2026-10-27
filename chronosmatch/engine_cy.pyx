# cython: language_level=3
# cython: boundscheck=False
# cython: wraparound=False
# cython: cdivision=True
"""
ChronosMatch Cython Accelerated Limit Order Book (LOB).

Zero-Copy, C-level implementation of Price-Time Priority matching engine.

Design Rationale (Plain English):
1. Why C Structs Instead of Python Objects?
   In pure Python, every order is a PyObject containing a 16-byte header (ob_refcnt,
   ob_type) plus pointers and type metadata. Creating or modifying PyObjects incurs
   heap allocations via pymalloc, reference counting overhead, and garbage collection
   (GC) tracking. Cython C structs (CyOrder, CyPriceLevel) allocate raw continuous C
   bytes on the system heap with zero GC interaction.

2. Why Doubly-Linked Lists for Resting Orders at Each Price Level?
   A FIFO queue at a discrete price level requires:
   - O(1) push to back for incoming resting orders.
   - O(1) pop from front when matching fills oldest orders first.
   - O(1) arbitrary removal when an active resting order is canceled by ID.
   A doubly-linked list (CyOrder with next_order and prev_order pointers) achieves
   all three in constant time without memory shifting or reallocation.

3. Why Doubly-Linked Lists for Sorted Price Levels?
   In high-frequency electronic markets, price changes happen almost entirely at or
   adjacent to the inside market (top of book). Maintaining sorted price levels as a
   doubly-linked list allows O(1) best bid/ask inspection (always head_level) and
   rapid insertion/removal of adjacent tick levels without array reshuffling.

4. Why a C Hash Table for Order Cancellation Index?
   Pure Python dicts incur hashing and PyObject pointer chasing. A dedicated C-level
   chained hash table maps uint64 order_id -> CyOrder* in constant time without
   calling any Python C-API functions during cancel operations.

5. Why Reusable C Trade Buffer?
   Inside the matching loop, we write execution records directly to pre-allocated C
   memory (_trade_buffer). We only instantiate Python Trade NamedTuples once matching
   is complete at the return boundary, eliminating allocation overhead during crossing.

6. Why Explicit Manual Deallocation (__dealloc__ and clear)?
   Because all orders, levels, and hash buckets are allocated via malloc(), Python's
   Garbage Collector cannot see or free this C memory. __dealloc__ safely walks every
   ladder and bucket to free all memory when the CythonOrderBook is garbage collected,
   preventing memory leaks and eliminating any risk of double-freeing.
"""

from libc.stdint cimport uint8_t, uint32_t, uint64_t, int64_t
from libc.stdlib cimport malloc, free, realloc
from typing import Optional, List, NamedTuple

from chronosmatch.codec import OrderRecord, OrderSide
from chronosmatch.exceptions import InvalidOrderError, OrderNotFoundError
from chronosmatch.order_book import Trade, _PriceAccessor


# ---------------------------------------------------------------------------
# C Data Structures for Order Book State
# ---------------------------------------------------------------------------

cdef struct CyPriceLevel

cdef struct CyOrder:
    uint64_t order_id          # Unique 64-bit monotonic order identifier
    uint8_t side               # 1 = Buy, 2 = Sell (OrderSide enum value)
    int64_t price              # Limit price in integer ticks
    uint32_t quantity          # Remaining unfilled lot quantity
    uint64_t timestamp_ns      # Ingestion timestamp in nanoseconds
    CyOrder* next_order        # Next newer order in FIFO time queue
    CyOrder* prev_order        # Previous older order in FIFO time queue
    CyPriceLevel* parent_level # Direct pointer back to owning price level for fast cleanup

cdef struct CyPriceLevel:
    int64_t price              # Discrete limit price tick of this level
    uint64_t total_volume      # Aggregate unfilled lot volume at this price
    uint32_t order_count       # Number of resting orders at this price
    CyOrder* head_order        # Oldest resting order (next in line for FIFO execution)
    CyOrder* tail_order        # Newest resting order
    CyPriceLevel* next_level   # Next worse price level (lower bid or higher ask)
    CyPriceLevel* prev_level   # Next better price level (higher bid or lower ask)

cdef struct CyTrade:
    uint64_t buy_order_id      # Executed buyer order ID
    uint64_t sell_order_id     # Executed seller order ID
    int64_t price              # Match price in ticks (passive resting order price)
    uint32_t quantity          # Executed trade lot volume

# Dedicated C Hash Table Node for O(1) order lookup without Python dicts
cdef struct CyHashBucket:
    uint64_t order_id
    CyOrder* order
    CyHashBucket* next_bucket


# ---------------------------------------------------------------------------
# Cython Limit Order Book Class
# ---------------------------------------------------------------------------

cdef class CythonOrderBook:
    """
    High-Performance Limit Order Book implemented in Cython with raw C memory.

    Implements the identical public interface as chronosmatch.order_book.LimitOrderBook.
    """

    # Bid ladder: sorted descending (head is best/highest bid)
    cdef CyPriceLevel* _bids_head
    cdef CyPriceLevel* _bids_tail

    # Ask ladder: sorted ascending (head is best/lowest ask)
    cdef CyPriceLevel* _asks_head
    cdef CyPriceLevel* _asks_tail

    # Metrics
    cdef uint32_t _total_order_count
    cdef uint32_t _bid_level_count
    cdef uint32_t _ask_level_count

    # Order lookup hash table (power-of-two capacity for fast bitwise masking)
    cdef CyHashBucket** _hash_table
    cdef uint32_t _hash_mask
    cdef uint32_t _hash_capacity

    # Reusable scratch buffer for trade execution records inside matching loop
    cdef CyTrade* _trade_buffer
    cdef uint32_t _trade_capacity
    cdef uint32_t _trade_count

    def __cinit__(self, uint32_t initial_hash_capacity=65536, uint32_t initial_trade_capacity=256):
        """
        Allocate C-level hash table, trade scratch buffer, and initialize book pointers.

        Memory is managed manually via malloc/free to ensure zero GC interaction.
        """
        self._bids_head = NULL
        self._bids_tail = NULL
        self._asks_head = NULL
        self._asks_tail = NULL

        self._total_order_count = 0
        self._bid_level_count = 0
        self._ask_level_count = 0

        # Hash table initialization: default 65,536 buckets (power of two)
        self._hash_capacity = initial_hash_capacity
        self._hash_mask = initial_hash_capacity - 1
        self._hash_table = <CyHashBucket**>malloc(initial_hash_capacity * sizeof(CyHashBucket*))
        if self._hash_table == NULL:
            raise MemoryError("Failed to allocate order lookup hash table in CythonOrderBook.")
        
        cdef uint32_t i
        for i in range(initial_hash_capacity):
            self._hash_table[i] = NULL

        # Trade scratch buffer initialization
        self._trade_capacity = initial_trade_capacity
        self._trade_count = 0
        self._trade_buffer = <CyTrade*>malloc(initial_trade_capacity * sizeof(CyTrade))
        if self._trade_buffer == NULL:
            free(self._hash_table)
            self._hash_table = NULL
            raise MemoryError("Failed to allocate trade execution buffer in CythonOrderBook.")

    def __dealloc__(self):
        """
        Safely free all dynamically allocated C memory upon object destruction.

        Prevents native memory leaks and avoids double-frees by checking NULL guards.
        """
        self.clear()

        # Free C hash table bucket array
        if self._hash_table != NULL:
            free(self._hash_table)
            self._hash_table = NULL

        # Free trade scratch buffer
        if self._trade_buffer != NULL:
            free(self._trade_buffer)
            self._trade_buffer = NULL

    def clear(self) -> None:
        """
        Remove and deallocate all resting orders, price levels, and hash table entries.

        Safe to call multiple times (idempotent).
        """
        cdef CyPriceLevel* curr_level
        cdef CyPriceLevel* next_level
        cdef CyOrder* curr_order
        cdef CyOrder* next_order
        cdef CyHashBucket* curr_bucket
        cdef CyHashBucket* next_bucket
        cdef uint32_t i

        # 1. Clean up resting bids ladder
        curr_level = self._bids_head
        while curr_level != NULL:
            next_level = curr_level.next_level
            curr_order = curr_level.head_order
            while curr_order != NULL:
                next_order = curr_order.next_order
                free(curr_order)
                curr_order = next_order
            free(curr_level)
            curr_level = next_level

        self._bids_head = NULL
        self._bids_tail = NULL

        # 2. Clean up resting asks ladder
        curr_level = self._asks_head
        while curr_level != NULL:
            next_level = curr_level.next_level
            curr_order = curr_level.head_order
            while curr_order != NULL:
                next_order = curr_order.next_order
                free(curr_order)
                curr_order = next_order
            free(curr_level)
            curr_level = next_level

        self._asks_head = NULL
        self._asks_tail = NULL

        # 3. Clean up all chained hash table nodes
        if self._hash_table != NULL:
            for i in range(self._hash_capacity):
                curr_bucket = self._hash_table[i]
                while curr_bucket != NULL:
                    next_bucket = curr_bucket.next_bucket
                    free(curr_bucket)
                    curr_bucket = next_bucket
                self._hash_table[i] = NULL

        # Reset counts
        self._total_order_count = 0
        self._bid_level_count = 0
        self._ask_level_count = 0
        self._trade_count = 0

    cdef inline uint32_t _hash_order_id(self, uint64_t order_id) noexcept nogil:
        """Fast multiplicative 64-bit integer hash for uniform bucket distribution."""
        return <uint32_t>((order_id * 11400714819323198485ULL) & self._hash_mask)

    cdef inline void _record_trade(self, uint64_t buy_id, uint64_t sell_id, int64_t price, uint32_t qty) noexcept nogil:
        """Record trade match into contiguous C memory buffer without Python object creation."""
        cdef uint32_t new_cap
        cdef CyTrade* new_buf
        if self._trade_count >= self._trade_capacity:
            new_cap = self._trade_capacity * 2
            new_buf = <CyTrade*>realloc(self._trade_buffer, new_cap * sizeof(CyTrade))
            if new_buf != NULL:
                self._trade_buffer = new_buf
                self._trade_capacity = new_cap
            else:
                return

        self._trade_buffer[self._trade_count].buy_order_id = buy_id
        self._trade_buffer[self._trade_count].sell_order_id = sell_id
        self._trade_buffer[self._trade_count].price = price
        self._trade_buffer[self._trade_count].quantity = qty
        self._trade_count += 1

    cdef inline void _index_order(self, uint64_t order_id, CyOrder* order) noexcept nogil:
        """Insert order pointer into C hash table for O(1) cancel lookup."""
        cdef uint32_t bucket = self._hash_order_id(order_id)
        cdef CyHashBucket* node = <CyHashBucket*>malloc(sizeof(CyHashBucket))
        if node != NULL:
            node.order_id = order_id
            node.order = order
            node.next_bucket = self._hash_table[bucket]
            self._hash_table[bucket] = node

    cdef inline CyOrder* _find_order(self, uint64_t order_id) noexcept nogil:
        """Locate resting order pointer by ID in O(1) time without Python dicts."""
        cdef uint32_t bucket = self._hash_order_id(order_id)
        cdef CyHashBucket* curr = self._hash_table[bucket]
        while curr != NULL:
            if curr.order_id == order_id:
                return curr.order
            curr = curr.next_bucket
        return NULL

    cdef inline void _unindex_order(self, uint64_t order_id) noexcept nogil:
        """Remove order pointer from C hash table upon fill or cancellation."""
        cdef uint32_t bucket = self._hash_order_id(order_id)
        cdef CyHashBucket* curr = self._hash_table[bucket]
        cdef CyHashBucket* prev = NULL
        while curr != NULL:
            if curr.order_id == order_id:
                if prev == NULL:
                    self._hash_table[bucket] = curr.next_bucket
                else:
                    prev.next_bucket = curr.next_bucket
                free(curr)
                return
            prev = curr
            curr = curr.next_bucket

    cdef inline CyPriceLevel* _find_or_create_bid_level(self, int64_t price) noexcept nogil:
        """Find or insert bid price level into descending sorted doubly-linked list."""
        cdef CyPriceLevel* curr = self._bids_head
        cdef CyPriceLevel* new_level

        # Walk descending list: highest price at head
        while curr != NULL:
            if curr.price == price:
                return curr
            if curr.price < price:
                # Insert before curr (higher price goes closer to head)
                new_level = <CyPriceLevel*>malloc(sizeof(CyPriceLevel))
                new_level.price = price
                new_level.total_volume = 0
                new_level.order_count = 0
                new_level.head_order = NULL
                new_level.tail_order = NULL
                new_level.next_level = curr
                new_level.prev_level = curr.prev_level

                if curr.prev_level != NULL:
                    curr.prev_level.next_level = new_level
                else:
                    self._bids_head = new_level
                curr.prev_level = new_level

                self._bid_level_count += 1
                return new_level

            if curr.next_level == NULL:
                break
            curr = curr.next_level

        # Append at tail or initialize empty ladder
        new_level = <CyPriceLevel*>malloc(sizeof(CyPriceLevel))
        new_level.price = price
        new_level.total_volume = 0
        new_level.order_count = 0
        new_level.head_order = NULL
        new_level.tail_order = NULL
        new_level.next_level = NULL
        new_level.prev_level = curr

        if curr != NULL:
            curr.next_level = new_level
            self._bids_tail = new_level
        else:
            self._bids_head = new_level
            self._bids_tail = new_level

        self._bid_level_count += 1
        return new_level

    cdef inline CyPriceLevel* _find_or_create_ask_level(self, int64_t price) noexcept nogil:
        """Find or insert ask price level into ascending sorted doubly-linked list."""
        cdef CyPriceLevel* curr = self._asks_head
        cdef CyPriceLevel* new_level

        # Walk ascending list: lowest price at head
        while curr != NULL:
            if curr.price == price:
                return curr
            if curr.price > price:
                # Insert before curr (lower price goes closer to head)
                new_level = <CyPriceLevel*>malloc(sizeof(CyPriceLevel))
                new_level.price = price
                new_level.total_volume = 0
                new_level.order_count = 0
                new_level.head_order = NULL
                new_level.tail_order = NULL
                new_level.next_level = curr
                new_level.prev_level = curr.prev_level

                if curr.prev_level != NULL:
                    curr.prev_level.next_level = new_level
                else:
                    self._asks_head = new_level
                curr.prev_level = new_level

                self._ask_level_count += 1
                return new_level

            if curr.next_level == NULL:
                break
            curr = curr.next_level

        # Append at tail or initialize empty ladder
        new_level = <CyPriceLevel*>malloc(sizeof(CyPriceLevel))
        new_level.price = price
        new_level.total_volume = 0
        new_level.order_count = 0
        new_level.head_order = NULL
        new_level.tail_order = NULL
        new_level.next_level = NULL
        new_level.prev_level = curr

        if curr != NULL:
            curr.next_level = new_level
            self._asks_tail = new_level
        else:
            self._asks_head = new_level
            self._asks_tail = new_level

        self._ask_level_count += 1
        return new_level

    cdef inline void _remove_level(self, CyPriceLevel* level, bint is_bid) noexcept nogil:
        """Unlink and free an empty price level from the active ladder."""
        if level.prev_level != NULL:
            level.prev_level.next_level = level.next_level
        else:
            if is_bid:
                self._bids_head = level.next_level
            else:
                self._asks_head = level.next_level

        if level.next_level != NULL:
            level.next_level.prev_level = level.prev_level
        else:
            if is_bid:
                self._bids_tail = level.prev_level
            else:
                self._asks_tail = level.prev_level

        if is_bid:
            self._bid_level_count -= 1
        else:
            self._ask_level_count -= 1

        free(level)

    cdef inline void _insert_resting_order(self, CyPriceLevel* level, CyOrder* order) noexcept nogil:
        """Append resting order to tail of price level (FIFO time priority)."""
        order.parent_level = level
        order.next_order = NULL
        order.prev_order = level.tail_order

        if level.tail_order != NULL:
            level.tail_order.next_order = order
            level.tail_order = order
        else:
            level.head_order = order
            level.tail_order = order

        level.order_count += 1
        level.total_volume += order.quantity
        self._total_order_count += 1
        self._index_order(order.order_id, order)

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

    def add_order(self, order: OrderRecord) -> List[Trade]:
        """
        Process an incoming limit order with strict Price-Time Priority matching.

        Pure C execution inside matching loop:
        - Traverses C price level linked list.
        - Matches against FIFO resting orders at passive resting price.
        - Records trade records directly into preallocated C memory.
        - Rest any remaining quantity as raw C struct.
        - Converts trades to Python Trade objects only at final return.
        """
        self._validate_order(order)

        cdef uint64_t order_id = order.order_id
        cdef uint8_t side = order.side
        cdef int64_t price = order.price
        cdef uint32_t remaining_qty = order.quantity
        cdef uint64_t timestamp_ns = order.timestamp_ns

        cdef CyPriceLevel* curr_level
        cdef CyPriceLevel* next_level
        cdef CyOrder* resting
        cdef CyOrder* next_resting
        cdef uint32_t match_qty
        cdef int64_t trade_price
        cdef CyOrder* new_order
        cdef CyPriceLevel* resting_level

        # Reset trade scratch buffer
        self._trade_count = 0

        if side == 1:  # BUY
            # Cross against resting asks (ascending price priority)
            curr_level = self._asks_head
            while remaining_qty > 0 and curr_level != NULL:
                if curr_level.price > price:
                    # Best ask is higher than incoming buy limit price: match stop
                    break

                resting = curr_level.head_order
                while resting != NULL and remaining_qty > 0:
                    next_resting = resting.next_order
                    match_qty = remaining_qty if remaining_qty < resting.quantity else resting.quantity
                    trade_price = resting.price

                    self._record_trade(order_id, resting.order_id, trade_price, match_qty)

                    remaining_qty -= match_qty
                    resting.quantity -= match_qty
                    curr_level.total_volume -= match_qty

                    if resting.quantity == 0:
                        # Full fill: remove resting order
                        if resting.prev_order != NULL:
                            resting.prev_order.next_order = resting.next_order
                        else:
                            curr_level.head_order = resting.next_order

                        if resting.next_order != NULL:
                            resting.next_order.prev_order = resting.prev_order
                        else:
                            curr_level.tail_order = resting.prev_order

                        self._unindex_order(resting.order_id)
                        free(resting)
                        curr_level.order_count -= 1
                        self._total_order_count -= 1

                    resting = next_resting

                next_level = curr_level.next_level
                if curr_level.order_count == 0:
                    self._remove_level(curr_level, 0)
                curr_level = next_level

            # Rest any remaining unfilled quantity on the bid ladder
            if remaining_qty > 0:
                resting_level = self._find_or_create_bid_level(price)
                new_order = <CyOrder*>malloc(sizeof(CyOrder))
                new_order.order_id = order_id
                new_order.side = side
                new_order.price = price
                new_order.quantity = remaining_qty
                new_order.timestamp_ns = timestamp_ns
                self._insert_resting_order(resting_level, new_order)

        else:  # SELL
            # Cross against resting bids (descending price priority)
            curr_level = self._bids_head
            while remaining_qty > 0 and curr_level != NULL:
                if curr_level.price < price:
                    # Best bid is lower than incoming sell limit price: match stop
                    break

                resting = curr_level.head_order
                while resting != NULL and remaining_qty > 0:
                    next_resting = resting.next_order
                    match_qty = remaining_qty if remaining_qty < resting.quantity else resting.quantity
                    trade_price = resting.price

                    self._record_trade(resting.order_id, order_id, trade_price, match_qty)

                    remaining_qty -= match_qty
                    resting.quantity -= match_qty
                    curr_level.total_volume -= match_qty

                    if resting.quantity == 0:
                        # Full fill: remove resting order
                        if resting.prev_order != NULL:
                            resting.prev_order.next_order = resting.next_order
                        else:
                            curr_level.head_order = resting.next_order

                        if resting.next_order != NULL:
                            resting.next_order.prev_order = resting.prev_order
                        else:
                            curr_level.tail_order = resting.prev_order

                        self._unindex_order(resting.order_id)
                        free(resting)
                        curr_level.order_count -= 1
                        self._total_order_count -= 1

                    resting = next_resting

                next_level = curr_level.next_level
                if curr_level.order_count == 0:
                    self._remove_level(curr_level, 1)
                curr_level = next_level

            # Rest any remaining unfilled quantity on the ask ladder
            if remaining_qty > 0:
                resting_level = self._find_or_create_ask_level(price)
                new_order = <CyOrder*>malloc(sizeof(CyOrder))
                new_order.order_id = order_id
                new_order.side = side
                new_order.price = price
                new_order.quantity = remaining_qty
                new_order.timestamp_ns = timestamp_ns
                self._insert_resting_order(resting_level, new_order)

        # Convert C trade records to Python Trade namedtuples at return boundary
        cdef list trades = []
        cdef uint32_t i
        for i in range(self._trade_count):
            trades.append(
                Trade(
                    buy_order_id=self._trade_buffer[i].buy_order_id,
                    sell_order_id=self._trade_buffer[i].sell_order_id,
                    price=self._trade_buffer[i].price,
                    quantity=self._trade_buffer[i].quantity,
                )
            )

        return trades

    def cancel_order(self, uint64_t order_id) -> None:
        """
        Cancel a resting order in the book by order ID.

        Removes the order from its resting price ladder and index in O(1) time.

        Raises:
            OrderNotFoundError: If order_id is not actively resting in the book.
        """
        cdef CyOrder* order = self._find_order(order_id)
        if order == NULL:
            raise OrderNotFoundError(f"Order ID {order_id} not found in order book.")

        cdef CyPriceLevel* level = order.parent_level
        cdef bint is_bid = (order.side == 1)

        # Unlink order from level FIFO queue in O(1)
        if order.prev_order != NULL:
            order.prev_order.next_order = order.next_order
        else:
            level.head_order = order.next_order

        if order.next_order != NULL:
            order.next_order.prev_order = order.prev_order
        else:
            level.tail_order = order.prev_order

        level.order_count -= 1
        level.total_volume -= order.quantity
        self._total_order_count -= 1

        self._unindex_order(order_id)
        free(order)

        # Remove price level if empty
        if level.order_count == 0:
            self._remove_level(level, is_bid)

    def _get_best_bid_value(self) -> Optional[int]:
        """Internal helper returning integer best bid or None."""
        if self._bids_head != NULL:
            return self._bids_head.price
        return None

    def _get_best_ask_value(self) -> Optional[int]:
        """Internal helper returning integer best ask or None."""
        if self._asks_head != NULL:
            return self._asks_head.price
        return None

    @_PriceAccessor
    def best_bid(self) -> Optional[int]:
        """Return the highest resting bid price in ticks, or None if no resting bids."""
        return self._get_best_bid_value()

    @_PriceAccessor
    def best_ask(self) -> Optional[int]:
        """Return the lowest resting ask price in ticks, or None if no resting asks."""
        return self._get_best_ask_value()

    def get_best_bid(self) -> Optional[int]:
        """Direct getter returning raw Optional[int] for best bid price."""
        return self._get_best_bid_value()

    def get_best_ask(self) -> Optional[int]:
        """Direct getter returning raw Optional[int] for best ask price."""
        return self._get_best_ask_value()

    def bid_volume_at(self, int64_t price) -> int:
        """Calculate total resting volume at a specific bid price tick."""
        cdef CyPriceLevel* curr = self._bids_head
        while curr != NULL:
            if curr.price == price:
                return curr.total_volume
            if curr.price < price:
                break
            curr = curr.next_level
        return 0

    def ask_volume_at(self, int64_t price) -> int:
        """Calculate total resting volume at a specific ask price tick."""
        cdef CyPriceLevel* curr = self._asks_head
        while curr != NULL:
            if curr.price == price:
                return curr.total_volume
            if curr.price > price:
                break
            curr = curr.next_level
        return 0

    def total_bid_volume(self) -> int:
        """Calculate aggregate resting volume across all bid levels."""
        cdef uint64_t total = 0
        cdef CyPriceLevel* curr = self._bids_head
        while curr != NULL:
            total += curr.total_volume
            curr = curr.next_level
        return total

    def total_ask_volume(self) -> int:
        """Calculate aggregate resting volume across all ask levels."""
        cdef uint64_t total = 0
        cdef CyPriceLevel* curr = self._asks_head
        while curr != NULL:
            total += curr.total_volume
            curr = curr.next_level
        return total

    @property
    def order_count(self) -> int:
        """Total number of resting orders currently in the book."""
        return self._total_order_count

    @property
    def bid_level_count(self) -> int:
        """Number of active price levels on the bid side."""
        return self._bid_level_count

    @property
    def ask_level_count(self) -> int:
        """Number of active price levels on the ask side."""
        return self._ask_level_count
