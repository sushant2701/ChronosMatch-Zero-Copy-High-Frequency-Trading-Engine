# Code Walkthrough & Conceptual Guide

**Project:** ChronosMatch - Zero-Copy High-Frequency Trading Engine  
**Intern:** Sushant Shrimal  
**Company:** Infotact Solutions (Batch-22, Ref: ITS/PY/1090)  

This walkthrough explains the internal design and mechanics of every module created in plain, straightforward English, enabling the intern to clearly explain and defend each file during mentorship sessions and code reviews.

---

## 1. `chronosmatch/codec.py` (Binary Order Record Serialization)

### Why this module exists:
In high-frequency trading (HFT), processes cannot afford the CPU cycles required to parse JSON strings (`json.loads`) or deserialize Python objects (`pickle.loads`). Python `pickle` also carries arbitrary code execution risks and creates variable-length payloads with substantial memory allocation jitter.

### How it works:
- **Struct Format String (`<QBqIQ`):**
  - `<` ensures standard Little-Endian byte ordering across all CPU architectures with no compiler-dependent struct padding.
  - `Q` (uint64, 8 bytes): Unique monotonic order ID.
  - `B` (uint8, 1 byte): Order side (1 = BUY, 2 = SELL).
  - `q` (int64, 8 bytes): Order price represented as integer ticks (e.g., $185.25 stored as 18525 ticks).
  - `I` (uint32, 4 bytes): Quantity lots.
  - `Q` (uint64, 8 bytes): Ingress timestamp in nanoseconds from `time.perf_counter_ns()`.
  - **Total:** Exactly 29 bytes.
- **`OrderRecord` Class:**
  - Implements `__slots__ = ("order_id", "side", "price", "quantity", "timestamp_ns")` which bypasses the default `__dict__` attribute storage, reducing memory consumption by over 60% per object and speeding up attribute lookups.
- **Zero-Copy Packing (`encode_order_into` / `decode_order_from`):**
  - Uses `struct.pack_into()` and `struct.unpack_from()` to write and read directly from memory-mapped byte offsets without generating intermediate `bytes` objects on the Python heap.

---

## 2. `chronosmatch/ring_buffer.py` (Memory-Mapped SPSC Ring Buffer)

### Why this module exists:
To transmit market orders from an ingestion or simulation process to a matching engine process at microsecond latency, we use a shared memory file backed by `mmap`. This avoids kernel networking stacks, TCP loopbacks, or Unix pipes.

### How it works:
- **Dual Cache-Line Header (128 bytes):**
  - Modern CPU architectures operate on 64-byte cache lines. If two CPU cores write to variables on the same cache line, the CPU hardware continuously invalidates and reloads the line ("false sharing").
  - To prevent this, the `head` counter (written by the Producer) is placed in Cache Line 0 (offset 16), while the `tail` counter (written by the Consumer) is placed in Cache Line 1 (offset 64).
  - Monotonic 64-bit integer counters (`0, 1, 2, ...`) are incremented on each read/write and will not overflow for centuries.
- **Bitwise Modulo Slot Addressing:**
  - Standard ring buffers compute slot index via modulo division: `index = counter % capacity`. Division takes 10–30 clock cycles on modern x86/ARM CPUs.
  - By strictly enforcing that `capacity` is a power of two ($2^n$), the slot index is calculated via a single-cycle bitwise AND operation:
    $$\text{slot\_index} = \text{counter} \ \& \ (\text{capacity} - 1)$$
- **Full and Empty Invariants:**
  - *Empty:* When `head == tail` (all written orders have been read).
  - *Full:* When `(head - tail) >= capacity` (the buffer has no available slots; further writes would overwrite unread orders).
  - Writing when full raises `BufferFullError`. Reading when empty raises `BufferEmptyError`. Non-blocking `try_write()` and `try_read()` are provided for high-frequency polling loops.

---

## 3. `chronosmatch/ipc_worker.py` (Cross-Process Concurrency Helpers)

### Why this module exists:
Provides clean, reusable multiprocessing worker targets (`run_ipc_producer` and `run_ipc_consumer`) for integration testing across independent operating system processes.

### How it works:
- Uses `time.sleep(0)` (yielding the operating system timeslice) when a buffer is temporarily saturated or empty, avoiding busy-waiting lockups on single- or dual-core test environments without introducing the 15-millisecond timer quant delay of standard `sleep()`.

---

## 4. `chronosmatch/exceptions.py` (Domain Exceptions)

### Why this module exists:
Provides an explicit, fine-grained exception hierarchy rooted at `ChronosMatchError`. Allows client code to distinguish between serialization errors (`InvalidOrderError`), buffer capacity issues (`BufferFullError`), order book lookup failures (`OrderNotFoundError`), and structural data corruption (`CorruptBufferHeaderError`).

---

## 5. `chronosmatch/simulator.py` (Asyncio Market Simulator)

### Why this module exists:
Before building the real matching engine, we need a realistic, high-throughput source of synthetic order flow to stress-test the ring buffer and benchmark latency. The simulator acts as the "producer" half of the SPSC pipeline.

### How it works:
- **SimulatorConfig Dataclass:**
  - Centralises every tuneable parameter — initial mid-price, drift standard deviation, spread, quantity range, target order rate (100 000 orders/sec), simulation duration, random seed, batch size, and back-off retry limits — with full domain validation in `__post_init__`.
- **MarketOrderGenerator (Seeded Random Walk):**
  - Uses an *isolated* `random.Random` instance seeded with `config.seed`. Because the RNG is private, global random state from other modules cannot interfere, guaranteeing that a given seed always produces the identical order sequence.
  - Each call to `generate_order()` first evolves a floating-point mid-price by a discrete Gaussian step (`gauss(0, drift_std)`), clamped to `[min_price, max_price]`. A buy order is placed at or below mid, a sell at or above mid, with a random spread offset. Quantity is uniform-random within `[min_quantity, max_quantity]`.
  - The monotonic nanosecond timestamp is taken from an injectable clock callable (defaults to `time.perf_counter_ns`), enabling deterministic timestamp injection in tests.
- **MarketSimulator (Asyncio Loop with Back-Off):**
  - Generates orders in configurable batch sizes. After each batch, the loop calculates how far ahead of the pacing target it is (`expected_time − elapsed`) and yields with `asyncio.sleep(ahead)` if leading by more than 1 ms, or `asyncio.sleep(0)` for cooperative yielding.
  - If `try_write()` returns `False` (buffer full), the simulator retries up to `max_retries` with a brief `asyncio.sleep(backoff_delay)` between attempts. After exhausting retries, the order is counted as dropped — never crashes.
- **SimulatorStats:**
  - After the loop, `elapsed_time` is computed from `time.perf_counter()` wall-clock measurements. `achieved_rate` is strictly `orders_sent / elapsed_time` — never a target or fabricated number.

---

## 6. `chronosmatch/order_book.py` (Pure-Python Limit Order Book)

### Why this module exists:
The Limit Order Book (LOB) is the core matching logic of any exchange. This pure-Python reference implementation establishes the exact public API (`add_order`, `cancel_order`, `best_bid`, `best_ask`) that the Cython C-optimised engine will replicate on Day 3 — ensuring correctness can be verified before speed optimisation.

### How it works:
- **Price-Time Priority (FIFO) Algorithm:**
  - *Price Priority:* On the bid side, the highest price matches first (a buyer willing to pay more has priority). On the ask side, the lowest price matches first (a seller offering the cheapest has priority).
  - *Time Priority:* At each discrete price level, orders are queued in `collections.deque` in arrival order and consumed from the front (FIFO). The first order to arrive at a price is the first to be filled.
- **Sorted Price Ladders via `bisect`:**
  - `_bid_prices` is maintained in descending order (highest at index 0) so `_bid_prices[0]` is always the best bid. `_ask_prices` is ascending (lowest at index 0) so `_ask_prices[0]` is always the best ask.
  - Insertions and removals use `bisect.bisect_right` / `bisect.bisect_left` for O(log N) position finding within the price list.
- **`_BookOrder` Internal Container:**
  - Uses `__slots__` to eliminate the per-instance `__dict__`, cutting memory overhead. The `quantity` field is mutated in-place during partial fills, avoiding object re-allocation.
- **`add_order(order)` Matching Flow:**
  1. Validate the incoming `OrderRecord` (positive price, positive quantity, valid side).
  2. If BUY: walk the ask ladder starting from the best (lowest) ask. At each level, pop from the FIFO queue, create a `Trade` at the resting order's price, and deduct quantities. Stop when no more asks cross the buy limit price or buy quantity is exhausted.
  3. If any buy quantity remains unfilled, rest it on the bid ladder.
  4. SELL is the mirror: walk the bid ladder from the best (highest) bid downward.
- **`cancel_order(order_id)`:**
  - O(1) lookup in the `_orders` dict, then O(N_level) removal from the price-level deque. If the deque empties, the price level is pruned from the sorted list.
- **`best_bid` / `best_ask` Dual Access:**
  - A custom `_PriceAccessor` descriptor allows both `book.best_bid` (property-style) and `book.best_bid()` (method-style) to work, preventing calling convention mismatches between the Python reference and the future Cython implementation.

---

## 7. `chronosmatch/engine_cy.pyx` (Cython C-Accelerated Matching Engine)

### Why this module exists:
Python's interpreted execution, object allocation overhead, dynamic dispatch, and garbage collection (GC) introduce multi-microsecond latency spikes (jitter) that are prohibitive in algorithmic trading. `engine_cy.pyx` replaces Python objects inside the hot matching loop with pure C structs and direct pointers.

### Why C types are faster:
1. **Zero `PyObject` Overhead:** Every Python object requires a 16-byte header (`ob_refcnt`, `ob_type`), reference counting operations, and GC tracking. Cython C structs (`CyOrder`, `CyPriceLevel`, `CyTrade`) allocate pure, compact C memory on the heap with zero GC interaction.
2. **Contiguous and Linked Memory Layout:**
   - Resting orders within a price level form a doubly-linked FIFO queue (`CyOrder* head_order`, `CyOrder* tail_order`). Appending new orders, popping fully filled orders, and unlinking canceled orders are all strict O(1) pointer operations without array copying.
   - Price levels are linked in a sorted doubly-linked list (`_bids_head` descending, `_asks_head` ascending). Because market quotes cluster tightly around the spread, walking the ladder takes few or zero steps in practice.
3. **C Hash Table for O(1) Cancellation:**
   - Rather than calling Python `dict` methods, a lightweight open-chained C hash table with power-of-two bitmask indexing maps 64-bit `order_id` to `CyOrder*` in single-digit nanoseconds.
4. **Preallocated Trade Scratch Buffer:**
   - Inside the matching loop, execution records are written directly to a reusable contiguous array of `CyTrade` structs (`_trade_buffer`). Python `Trade` namedtuples are instantiated only at the boundary when returning from `add_order()`.
5. **Memory Safety and Deterministic Deallocation:**
   - `__dealloc__` and `clear()` systematically traverse all ladders and hash table buckets to `free()` every allocated node, guaranteeing zero memory leaks, no dangling pointers, and no double-free errors.

---

## 8. `chronosmatch/engine.py` (Factory & Dynamic Fallback Dispatcher)

### Why this module exists:
To achieve true cross-platform portability across development machines (e.g. environments without MSVC Build Tools or C compilers), the matching engine must transparently adapt without breaking client code or test suites.

### How it works:
- **`get_engine(backend=None)` Factory:**
  - Evaluates whether `chronosmatch.engine_cy` compiled successfully at import time.
  - If Cython is available, instantiates `CythonOrderBook` and logs an informational notice.
  - If Cython is missing or failed to compile (e.g. missing MSVC compiler on Windows), it catches the `ImportError` gracefully, logs an informational notice declaring that the pure-Python reference `LimitOrderBook` is active, and returns an instance of `LimitOrderBook`.
  - Supports explicit backend overrides (`backend="cython"` or `backend="python"`), allowing test harnesses and benchmarks to target a specific backend on demand.
- **Identical Public API:**
  - Both backends implement the exact same methods, properties, and exceptions (`add_order`, `cancel_order`, `best_bid`, `best_ask`, `order_count`, `InvalidOrderError`, `OrderNotFoundError`). Calling code never needs to know which backend is executing underneath.
