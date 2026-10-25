# Architectural References, Citations, and Third-Party Dependencies

This document maintains a transparent, comprehensive record of all architectural inspirations, academic/systems literature, design patterns, and external third-party software libraries used in **ChronosMatch**.

In adherence to the Infotact Solutions original work standards, all source code in this repository was authored completely from scratch without copying or reproducing external codebases.

---

## 1. Architectural Concepts & Theoretical Foundations

1. **LMAX Disruptor Pattern & Single-Producer/Single-Consumer (SPSC) Ring Buffers**
   - *Concept:* Ring buffer using atomic monotonic counters (head and tail) with power-of-two capacity for lockless $O(1)$ modulo bitmasking (`slot = counter & (capacity - 1)`).
   - *Design Choice:* Eliminates OS mutexes and condition variables on the critical path between the market simulator and the matching engine, ensuring predictable microsecond-level latency.
   - *Reference:* Thompson, M., et al. (2011). *LMAX Disruptor: High performance alternative to bounded queues for exchanging data between threads*. LMAX Exchange Whitepaper.

2. **Zero-Copy Memory-Mapped IPC (`mmap`)**
   - *Concept:* File-backed shared memory region mapped directly into virtual address spaces of distinct operating system processes.
   - *Design Choice:* Avoids kernel context switches and byte-copying across Unix sockets, named pipes, or TCP loops. The matching engine reads binary records directly from shared memory.
   - *Reference:* Stevens, W. R. (1998). *UNIX Network Programming, Volume 2: Interprocess Communications*. Prentice Hall.

3. **Fixed-Width Binary Record Serialization (`struct`)**
   - *Concept:* Packed C-level structures with deterministic byte layouts (`<QBqIQ` format string, 29 bytes).
   - *Design Choice:* Completely replaces dynamic serializers like JSON, MessagePack, or Python `pickle`. Guarantees zero arbitrary code execution risk, zero dynamic allocation, and predictable byte offsets.

4. **Limit Order Book (LOB) Price-Time Priority (FIFO)**
   - *Concept:* Bids and Asks organized into sorted price levels, where orders at identical price ticks are filled in strict order of arrival timestamp.
   - *Design Choice:* Matches the standard matching rule utilized by major equity and derivatives exchanges (NASDAQ, CME, LSE).

---

## 2. Third-Party Libraries & Licensing

The following open-source dependencies are utilized for tooling, packaging, and terminal visualization:

| Library | Version | License | Purpose in Project |
|---|---|---|---|
| **pytest** | `>=8.0.0` | MIT License | Automated testing framework for unit and integration verification. |
| **pytest-cov** | `>=4.1.0` | MIT License | Coverage metrics reporting for test suites. |
| **black** | `>=24.0.0` | MIT License | Deterministic PEP8 code formatter for Python sources. |
| **flake8** | `>=7.0.0` | MIT License | Static code analysis and linting compliance. |
| **Cython** | `>=3.0.0` | Apache 2.0 | C-extension compiler for high-performance order book matching loops. |
| **windows-curses** | `>=2.3.3` | PDCurses / MIT | Windows compatibility port of the POSIX standard `curses` library for terminal rendering. |

---

## 3. Design Choice Rationale Summary

- **Why `mmap` instead of `multiprocessing.Queue`?**  
  `multiprocessing.Queue` in standard Python uses an underlying pipe and serializes objects using `pickle`. Pickling introduces massive latency jitter (often 50–500 $\mu s$ per object) and heap allocation overhead. `mmap` with fixed binary records operates in sub-microsecond time.
- **Why power-of-two buffer sizes?**  
  The mathematical modulo operation (`counter % capacity`) requires an integer division instruction, which consumes 10–30 CPU cycles. When `capacity` is a power of two, `counter & (capacity - 1)` compiles to a single-cycle bitwise AND operation.
- **Why a fixed 29-byte struct?**  
  Fixed size guarantees that each slot's absolute memory offset is deterministically computed as `HEADER_SIZE + (slot_idx * SLOT_SIZE)`, eliminating any dynamic lookup or index tables.
