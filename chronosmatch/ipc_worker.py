"""
Worker routines for multi-process ring buffer testing and verification.

Designed to be safely spawned across distinct operating system processes
on both POSIX and Windows (spawn context).
"""

import time
from typing import Any

from chronosmatch.codec import OrderRecord, OrderSide
from chronosmatch.ring_buffer import RingBuffer


def run_ipc_producer(ipc_path: str, order_count: int) -> None:
    """
    Produce a deterministic stream of order records into an SPSC ring buffer.

    Args:
        ipc_path: Path to the backing shared memory file.
        order_count: Total number of orders to sequentially emit.
    """
    buf = RingBuffer(ipc_path, create=False)
    try:
        for i in range(order_count):
            order = OrderRecord(
                order_id=i,
                side=OrderSide.BUY if (i % 2 == 0) else OrderSide.SELL,
                price=20000 + (i % 100),
                quantity=1 + (i % 20),
                timestamp_ns=1_000_000_000 + i,
            )
            # High-performance spin with yield to prevent CPU starvation
            spin_count = 0
            while not buf.try_write(order):
                spin_count += 1
                if spin_count % 100 == 0:
                    time.sleep(0)  # Yield slice to consumer
    finally:
        buf.close()


def run_ipc_consumer(ipc_path: str, order_count: int, result_queue: Any, timeout_seconds: float = 10.0) -> None:
    """
    Consume a deterministic stream of order records from an SPSC ring buffer.

    Args:
        ipc_path: Path to the backing shared memory file.
        order_count: Expected number of orders to receive.
        result_queue: Multiprocessing Queue to return received order IDs.
        timeout_seconds: Maximum wall-clock time to wait before aborting.
    """
    buf = RingBuffer(ipc_path, create=False)
    received_ids = []
    start_time = time.time()
    spin_count = 0

    try:
        while len(received_ids) < order_count:
            order = buf.try_read()
            if order is not None:
                received_ids.append(order.order_id)
                spin_count = 0
            else:
                spin_count += 1
                if spin_count % 100 == 0:
                    if (time.time() - start_time) > timeout_seconds:
                        break
                    time.sleep(0)  # Yield slice to producer
    finally:
        buf.close()

    result_queue.put(received_ids)
