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
Provides an explicit, fine-grained exception hierarchy rooted at `ChronosMatchError`. Allows client code to distinguish between serialization errors (`InvalidOrderError`), buffer capacity issues (`BufferFullError`), and structural data corruption (`CorruptBufferHeaderError`).
