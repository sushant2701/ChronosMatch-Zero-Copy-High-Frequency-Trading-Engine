# PROJECT SPECIFICATION

**Project Title:** ChronosMatch - Zero-Copy High-Frequency Trading Engine  
**Domain:** FinTech and Low-Latency Systems  
**Organization:** Infotact Solutions  
**Program:** Python Development Internship (Batch-22, Ref: ITS/PY/1090)  
**Project Period:** 05/10/2026 to 05/11/2026  
**Developer / Intern:** Sushant Shrimal  

---

## 1. Executive Summary & Core Objective

The primary objective of **ChronosMatch** is to architect and implement an ultra-low-latency, zero-copy inter-process communication (IPC) and order-matching platform in Python and Cython. High-frequency trading (HFT) environments demand deterministic execution speeds where serialization overheads (such as JSON or Python `pickle`) are unacceptable. 

In ChronosMatch:
- An ingestion process receives or simulates high-velocity market orders and writes binary order records directly into a file-backed, memory-mapped (`mmap`) ring buffer.
- A separate matching-engine process reads the shared memory concurrently without intermediate copying or deserialization overhead, matching incoming orders according to strict Price-Time Priority (FIFO at price levels).
- Matched trades and performance metrics are monitored in real time via a terminal-based curses dashboard and asynchronously persisted into an SQLite ledger with Write-Ahead Logging (WAL) for compliance and post-trade auditing.

---

## 2. System Architecture & Modules

### Module 1: IPC Bus (`mmap` + binary `struct`)
- **Shared Memory:** File-backed ring buffer using POSIX/Windows `mmap`.
- **Zero-Copy Serialization:** Fixed-width 29-byte binary records encoded with Python's native `struct` module (standard C types, no pickling or dynamic envelopes):
  - `order_id`: uint64 (8 bytes, standard C unsigned long long)
  - `side`: uint8 (1 byte, 1 = Buy, 2 = Sell)
  - `price`: int64 (8 bytes, fixed-point price in ticks)
  - `quantity`: uint32 (4 bytes, unsigned integer lots)
  - `timestamp_ns`: uint64 (8 bytes, hardware monotonic nanoseconds)
- **Concurrency Model:** Single-Producer / Single-Consumer (SPSC) lock-free design using atomic monotonic head and tail counters in an 80-byte header block (including magic bytes, version, capacity, slot size, and cache-line padding).
- **Buffer Mechanics:** Power-of-two capacity for fast bitwise masking (`slot_idx = counter & (capacity - 1)`), wraparound handling, full/empty condition detection, and cache-aligned padding.

### Module 2: Market Simulator (`asyncio`)
- Mock order generator simulating realistic order flow with geometric Brownian motion or mean-reverting drift around a dynamic mid-price.
- Generates Buy and Sell limit/market orders with configurable drift, spread, rate (target: 100,000 orders/sec), random seed, and duration.
- Pushes binary order payloads directly to the ring buffer.

### Module 3: Matching Engine (Cython `.pyx` with Pure-Python Reference)
- Limit Order Book (LOB) implementing strict Price-Time Priority.
- Optimized with Cython C-level structs and typed memory views, eliminating Python heap allocation and PyObject reference counting in the hot matching loop.
- Core actions: Add Limit Order, Cancel Order, Partial Fill, Full Fill.
- Automatic fallback: If a C compiler is unavailable in the environment, the engine seamlessly switches to a pure-Python reference implementation with the identical public API and emits an explicit backend log.

### Module 4: Latency Monitor
- High-precision benchmarking via `time.perf_counter_ns()`.
- Captures timestamp upon entering the ring buffer ingestion path and upon trade execution match completion.
- Aggregates percentiles: p50, p95, p99, and max latencies reported in microseconds ($\mu s$).

### Module 5: Live Terminal Dashboard (`curses`)
- Real-time terminal interface displaying:
  - Top-of-book depth (Bids and Asks ladder with quantities).
  - Bid/Ask spread and current mid-price.
  - Recent executed trades tape.
  - Throughput (orders/second) and latency percentiles.
  - Distinct visual highlighting for "Whale" orders (orders that sweep multiple price levels).
- Supports cross-platform terminal rendering via `windows-curses` on Windows.

### Module 6: SQLite Audit Ledger
- Dedicated background worker asynchronously recording executed trades into an SQLite database.
- Uses Write-Ahead Logging (`PRAGMA journal_mode=WAL;`), synchronous normal, and batch inserts to prevent I/O blocking on the critical matching path.

### Module 7: Structured JSON Logging
- Enterprise-grade structured logging using Python's `logging` module.
- Outputs machine-parsable JSON formatted log events with severity, timestamp, component name, and contextual metadata.

### Module 8: Command Line Interface (CLI)
- Single entry point: `python -m chronosmatch <command>`
  - `run`: Launches full pipeline (simulator, ring buffer, engine, dashboard, ledger).
  - `simulate`: Runs standalone market simulator.
  - `engine`: Runs standalone matching engine connected to IPC bus.
  - `dashboard`: Launches live curses monitoring UI.
  - `bench`: Executes latency and throughput benchmark suite.
  - `audit`: Inspects SQLite trade ledger records.

---

## 3. Milestones & Implementation Roadmap

| Timeline | Focus Area | Deliverables |
|---|---|---|
| **Weeks 1-2** | Core Architecture & IPC | Memory-mapped ring buffer, binary codec, market simulator, matching engine prototype (pure-Python + Cython scaffold), curses dashboard basic layout. |
| **Mid-Review** | Verification & IPC Audit | IPC verification stress test passing 1,000,000 orders between two separate OS processes with zero pickling and zero data corruption. Engine correctness verification (deterministic Buy-matches-Sell matching). |
| **Week 3** | Latency & Cython Optimization | Strip all Python object allocations (`PyObject*`) from Cython inner loops. Ensure garbage collection (GC) never triggers during active matching. Nanosecond benchmark harness with microsecond reporting. |
| **Week 4** | Audit Ledger & Production Polish | Asynchronous SQLite WAL ledger, Whale trade detection & UI highlight, comprehensive test coverage (90%+), packaging, and complete documentation. |

---

## 4. Performance Targets

All targets are measured and reported honestly on the host machine without fabrication:
- **Order Matching Latency:** Under 50 microseconds ($\mu s$) per matched order.
- **IPC Ingestion Throughput:** Up to 100,000 orders/sec sustained throughput.
- **End-to-End Execution:** Sub-millisecond tick-to-trade response.

---

## 5. Required Deliverables & Evaluation Criteria

### Deliverables:
1. Complete, modular source code repository.
2. Technical project documentation and architecture guides.
3. Mid-term and final project reports.
4. Professional slide presentation (PPT).
5. Live demonstration recording and executable demo.
6. Comprehensive `README.md` with setup, verification, and benchmark steps.
7. Weekly progress logs for mentorship reviews.

### Evaluation Metrics:
- **Attendance & Work Ethic:** Regular progress logs and consistent commit history.
- **Technical Competency:** Mastery of systems-level Python, memory mapping, binary serialization, and low-latency principles.
- **Quality of Deliverables:** Robust code, PEP8 compliance, type hints, docstrings, high unit test coverage.
- **Innovation & Problem Solving:** Zero-copy shared memory architecture, C-level optimization.
- **Communication & Teamwork:** Transparent reporting of roadblocks, clear commit messages, structured mentor updates.
- **Adherence to Timelines:** On-schedule milestone completion.
- **Documentation Standards:** Thorough, reproducible, and verifiable documentation.
- **Final Review & Defense:** Confident presentation and live walkthrough of engine internals.
