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
# Cython Limit Order Book Class Scaffold
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

    cdef inline uint32_t _hash_order_id(self, uint64_t order_id) noexcept nogil:
        """Fast multiplicative 64-bit integer hash for uniform bucket distribution."""
        return <uint32_t>((order_id * 11400714819323198485ULL) & self._hash_mask)

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
