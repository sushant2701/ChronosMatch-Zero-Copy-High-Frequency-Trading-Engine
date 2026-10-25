"""
ChronosMatch - Zero-Copy High-Frequency Trading Engine.

High-throughput, low-latency inter-process communication bus and deterministic
order-matching engine for financial simulations.
"""

__version__ = "0.1.0"
__author__ = "Sushant Shrimal"
__all__ = [
    "OrderRecord",
    "OrderSide",
    "RingBuffer",
    "ChronosMatchError",
    "BufferFullError",
    "BufferEmptyError",
    "CorruptBufferHeaderError",
]

from chronosmatch.codec import OrderRecord, OrderSide
from chronosmatch.ring_buffer import RingBuffer
from chronosmatch.exceptions import (
    ChronosMatchError,
    BufferFullError,
    BufferEmptyError,
    CorruptBufferHeaderError,
)
