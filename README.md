# ChronosMatch - Zero-Copy High-Frequency Trading Engine

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Code Style: Black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)

A high-performance, deterministic order-matching engine and zero-copy inter-process communication (IPC) bus designed for high-frequency trading (HFT) simulations.

Developed by **Sushant Shrimal** as part of the **Infotact Solutions Python Development Internship** (Batch-22, Ref: ITS/PY/1090).

---

## Key Highlights

- **Zero-Copy Shared Memory IPC:** High-throughput Single-Producer / Single-Consumer (SPSC) file-backed memory-mapped ring buffer with cache-line-aligned binary headers and bitmask index calculations.
- **Fixed-Width Binary Encoding:** 29-byte packed C-struct order records (`order_id`, `side`, `price`, `quantity`, `timestamp_ns`) without Python pickling or JSON serialization overhead.
- **Price-Time Priority Limit Order Book (LOB):** High-speed FIFO matching at discrete price ticks, supporting market orders, limit orders, partial fills, full fills, and cancellations.
- **Cython Accelerated + Pure-Python Fallback:** Pure C execution path in Cython with graceful automatic fallback to an identical pure-Python engine when no C compiler is installed.
- **Nanosecond Latency Telemetry:** Direct measurement of entry-to-match tick-to-trade latency using hardware-level `time.perf_counter_ns()`, reporting p50, p95, p99, and max microsecond percentiles.
- **Live Terminal Dashboard:** Curses-based real-time order book visualizer highlighting top-of-book market depth, spread, trade tape, and multi-level "Whale" orders.
- **Asynchronous Audit Ledger:** Background SQLite WAL-mode persistence for zero-overhead audit trails.

---

## System Architecture

```text
+-------------------------+                     +---------------------------+
|    Market Simulator     |                     |      Matching Engine      |
|    (Async Order Gen)    |                     | (Price-Time Priority LOB) |
+------------+------------+                     +-------------+-------------+
             |                                                ^
             | binary struct (29 bytes)                       | zero-copy read
             v                                                |
   +----------------------------------------------------------------+
   |             Memory-Mapped SPSC Ring Buffer (.ipc)              |
   |  [ Header: Magic | Version | Capacity | SlotSize | Head | Tail ]|
   |  [ Slots: Record 0 | Record 1 | Record 2 | ... | Record N-1 ]   |
   +----------------------------------------------------------------+
                                                              |
                                                              v
+-------------------------+                     +---------------------------+
|     Live Dashboard      | <--- IPC Stats ---- |   SQLite Audit Ledger     |
|   (Curses / Terminal)   |      & Latency      |  (Background WAL Worker)  |
+-------------------------+                     +---------------------------+
```

---

## Directory Structure

```text
ChronosMatch-Zero-Copy-High-Frequency-Trading-Engine/
├── chronosmatch/           # Core library package
│   ├── __init__.py         # Package exports and version
│   ├── codec.py            # 29-byte binary struct serialization
│   ├── ring_buffer.py      # Memory-mapped SPSC ring buffer
│   └── exceptions.py       # Domain-specific custom exceptions
├── docs/                   # Documentation & compliance records
│   ├── REFERENCES.md       # Attribution, citations, and licenses
│   ├── AI_USAGE.md         # Transparent record of AI assistance
│   ├── WEEKLY_LOG.md       # Daily and weekly progress updates
│   └── MENTOR_UPDATE.md    # Mentor status summary
├── tests/                  # Test suite
│   ├── test_codec.py       # Binary struct serialization tests
│   └── test_ring_buffer.py # SPSC buffer and multi-process tests
├── pyproject.toml          # Build configuration & tool settings
├── requirements.txt        # Pinned runtime and dev dependencies
├── PROJECT_SPEC.md         # Complete project specifications
└── AGENT_RULES.md          # Working rules & development standards
```

---

## Installation & Setup

1. **Clone the repository:**
   ```bash
   git clone https://github.com/sushant2701/ChronosMatch-Zero-Copy-High-Frequency-Trading-Engine.git
   cd ChronosMatch-Zero-Copy-High-Frequency-Trading-Engine
   ```

2. **Set up virtual environment (Python 3.11+):**
   ```bash
   python -m venv .venv
   # Windows:
   .venv\Scripts\activate
   # Linux/macOS:
   source .venv/bin/activate
   ```

3. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

4. **Run the test suite:**
   ```bash
   pytest tests -v
   ```

---

## License

This project is licensed under the MIT License. See [LICENSE.txt](LICENSE.txt) for details.