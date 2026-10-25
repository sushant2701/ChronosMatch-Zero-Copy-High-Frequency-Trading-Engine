"""
Unit and integration tests for the memory-mapped SPSC RingBuffer.
"""

import multiprocessing as mp
from pathlib import Path

import pytest

from chronosmatch.codec import OrderRecord, OrderSide
from chronosmatch.exceptions import (
    BufferClosedError,
    BufferEmptyError,
    BufferFullError,
    CorruptBufferHeaderError,
)
from chronosmatch.ipc_worker import run_ipc_consumer, run_ipc_producer
from chronosmatch.ring_buffer import RingBuffer


@pytest.fixture
def temp_ipc_path(tmp_path: Path) -> Path:
    """Fixture providing a temporary IPC file path."""
    return tmp_path / "test_bus.ipc"


def test_capacity_must_be_power_of_two(temp_ipc_path: Path):
    """Verify non-power-of-two capacity values are rejected."""
    with pytest.raises(ValueError, match="must be a power of two"):
        RingBuffer(temp_ipc_path, capacity=1000, create=True)

    with pytest.raises(ValueError, match="must be a power of two"):
        RingBuffer(temp_ipc_path, capacity=0, create=True)


def test_create_and_attach(temp_ipc_path: Path):
    """Verify creating a buffer file and attaching to it from another instance."""
    with RingBuffer(temp_ipc_path, capacity=64, create=True) as writer:
        assert writer.capacity == 64
        assert writer.head == 0
        assert writer.tail == 0
        assert writer.is_empty() is True
        assert writer.is_full() is False
        assert writer.available_to_read() == 0
        assert writer.available_to_write() == 64

        # Attach reader
        with RingBuffer(temp_ipc_path, create=False) as reader:
            assert reader.capacity == 64
            assert reader.head == 0
            assert reader.tail == 0
            assert reader.is_empty() is True


def test_empty_buffer_read_behavior(temp_ipc_path: Path):
    """Verify reading from an empty buffer raises BufferEmptyError."""
    with RingBuffer(temp_ipc_path, capacity=16, create=True) as buf:
        with pytest.raises(BufferEmptyError):
            buf.read()

        assert buf.try_read() is None


def test_full_buffer_write_behavior(temp_ipc_path: Path):
    """Verify filling buffer to capacity raises BufferFullError on overflow."""
    capacity = 8
    with RingBuffer(temp_ipc_path, capacity=capacity, create=True) as buf:
        for i in range(capacity):
            order = OrderRecord(
                order_id=i,
                side=OrderSide.BUY,
                price=100 + i,
                quantity=10,
                timestamp_ns=1000 + i,
            )
            assert buf.try_write(order) is True

        assert buf.is_full() is True
        assert buf.available_to_read() == capacity
        assert buf.available_to_write() == 0

        # Next write must fail
        overflow_order = OrderRecord(
            order_id=999,
            side=OrderSide.SELL,
            price=200,
            quantity=1,
            timestamp_ns=2000,
        )
        assert buf.try_write(overflow_order) is False
        with pytest.raises(BufferFullError):
            buf.write(overflow_order)


def test_fifo_ordering_and_values(temp_ipc_path: Path):
    """Verify orders are dequeued in exact First-In-First-Out sequence."""
    with RingBuffer(temp_ipc_path, capacity=16, create=True) as buf:
        orders = [
            OrderRecord(order_id=1, side=OrderSide.BUY, price=1000, quantity=5, timestamp_ns=101),
            OrderRecord(order_id=2, side=OrderSide.SELL, price=1005, quantity=10, timestamp_ns=102),
            OrderRecord(order_id=3, side=OrderSide.BUY, price=995, quantity=2, timestamp_ns=103),
        ]

        for o in orders:
            buf.write(o)

        assert buf.available_to_read() == 3

        for expected in orders:
            received = buf.read()
            assert received == expected

        assert buf.is_empty() is True


def test_wraparound_continuous_streaming(temp_ipc_path: Path):
    """Verify ring buffer operates continuously across multiple cycles of capacity."""
    capacity = 16
    total_cycles = 10
    total_orders = capacity * total_cycles  # 160 orders through a 16-slot ring

    with RingBuffer(temp_ipc_path, capacity=capacity, create=True) as buf:
        for i in range(total_orders):
            order = OrderRecord(
                order_id=i,
                side=OrderSide.BUY if (i % 2 == 0) else OrderSide.SELL,
                price=50000 + (i % 100),
                quantity=10 + (i % 5),
                timestamp_ns=1_000_000 + i,
            )
            buf.write(order)
            read_back = buf.read()
            assert read_back == order

        assert buf.head == total_orders
        assert buf.tail == total_orders
        assert buf.is_empty() is True


def test_corrupt_header_detection(temp_ipc_path: Path):
    """Verify corrupted magic bytes or truncated file raises CorruptBufferHeaderError."""
    # Test non-existent file
    with pytest.raises(FileNotFoundError):
        RingBuffer(temp_ipc_path, create=False)

    # Test file smaller than header
    temp_ipc_path.write_bytes(b"SHORT")
    with pytest.raises(CorruptBufferHeaderError, match="smaller than minimum header"):
        RingBuffer(temp_ipc_path, create=False)

    # Test invalid magic bytes
    temp_ipc_path.write_bytes(b"BAD!" + b"\x00" * 124)
    with pytest.raises(CorruptBufferHeaderError, match="Invalid buffer header magic"):
        RingBuffer(temp_ipc_path, create=False)


def test_closed_buffer_access(temp_ipc_path: Path):
    """Verify accessing closed buffer raises BufferClosedError."""
    buf = RingBuffer(temp_ipc_path, capacity=16, create=True)
    buf.close()

    with pytest.raises(BufferClosedError):
        _ = buf.head

    with pytest.raises(BufferClosedError):
        buf.write(
            OrderRecord(order_id=1, side=OrderSide.BUY, price=100, quantity=1, timestamp_ns=1)
        )


def test_two_process_concurrent_ipc(temp_ipc_path: Path):
    """
    Stress-test IPC bus across two independent OS processes.

    Producer writes 5,000 orders while Consumer concurrently reads from the shared mmap file.
    Validates zero-copy IPC, head/tail synchronization, and zero message loss.
    """
    order_count = 5000
    capacity = 512  # Smaller than count to guarantee heavy wraparound

    # Create the buffer in the test process
    with RingBuffer(temp_ipc_path, capacity=capacity, create=True) as master:
        assert master.capacity == capacity

    ctx = mp.get_context("spawn")
    result_queue = ctx.Queue()

    # Launch consumer and producer as independent OS processes
    consumer_proc = ctx.Process(
        target=run_ipc_consumer,
        args=(str(temp_ipc_path), order_count, result_queue),
    )
    producer_proc = ctx.Process(
        target=run_ipc_producer,
        args=(str(temp_ipc_path), order_count),
    )

    consumer_proc.start()
    producer_proc.start()

    # Read from result_queue before join() to avoid feeder thread deadlock
    received_ids = result_queue.get(timeout=10.0)

    producer_proc.join(timeout=5.0)
    consumer_proc.join(timeout=5.0)

    assert not producer_proc.is_alive(), "Producer process timed out"
    assert not consumer_proc.is_alive(), "Consumer process timed out"

    assert len(received_ids) == order_count
    # Invariant: Exact FIFO sequence preserved
    assert received_ids == list(range(order_count))
