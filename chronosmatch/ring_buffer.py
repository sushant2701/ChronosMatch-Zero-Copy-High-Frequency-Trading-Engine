"""
Zero-Copy Memory-Mapped Single-Producer / Single-Consumer (SPSC) Ring Buffer.

Provides high-throughput inter-process communication between market data ingestion
and the order-matching engine via shared file-backed memory mapping (mmap).

Header Layout (128 bytes total, aligned across two 64-byte CPU cache lines):
    Cache Line 0 (0..63 bytes - Metadata and Producer-written Head Counter):
        - 0..3:   Magic bytes (ASCII 'CMRB' -> 0x434D5242)
        - 4..5:   Version uint16 (1)
        - 6..7:   Header size uint16 (128)
        - 8..11:  Slot size uint32 (29 bytes for OrderRecord)
        - 12..15: Capacity uint32 (Power of 2)
        - 16..23: Head uint64 (Monotonic write counter, 8-byte aligned)
        - 24..63: Cache-line padding (40 bytes reserved)

    Cache Line 1 (64..127 bytes - Consumer-written Tail Counter):
        - 64..71:  Tail uint64 (Monotonic read counter, 8-byte aligned)
        - 72..127: Cache-line padding (56 bytes reserved)

Separating head and tail across distinct 64-byte cache lines prevents false
sharing in multi-core processors. Monotonic 64-bit counters prevent wraparound
aliasing and enable lock-free single-cycle bitmask slot addressing:
    slot_index = counter & (capacity - 1)
"""

import mmap
import os
import struct
from pathlib import Path
from typing import Optional, Union

from chronosmatch.codec import (
    ORDER_RECORD_SIZE,
    OrderRecord,
    decode_order_from,
    encode_order_into,
)
from chronosmatch.exceptions import (
    BufferClosedError,
    BufferEmptyError,
    BufferFullError,
    CorruptBufferHeaderError,
)

# Header Constants
MAGIC_BYTES = b"CMRB"
PROTOCOL_VERSION = 1
HEADER_SIZE = 128

# Struct format for header fields up to head counter:
# Magic (4s), Version (H), HeaderSize (H), SlotSize (I), Capacity (I), Head (Q)
HEADER_INIT_FMT = "<4sHHIIQ"
HEADER_INIT_SIZE = struct.calcsize(HEADER_INIT_FMT)  # 24 bytes

# Direct counter offsets (both are 8-byte aligned)
HEAD_OFFSET = 16
TAIL_OFFSET = 64
U64_FMT = "<Q"


def _is_power_of_two(value: int) -> bool:
    """Return True if value is an integer power of two greater than zero."""
    return value > 0 and (value & (value - 1)) == 0


class RingBuffer:
    """
    File-backed, memory-mapped SPSC ring buffer for high-frequency trading data.

    Operates across independent OS processes without locking or object serialization.
    """

    def __init__(
        self,
        filepath: Union[str, Path],
        capacity: int = 1024,
        slot_size: int = ORDER_RECORD_SIZE,
        create: bool = False,
    ) -> None:
        """
        Initialize or attach to a shared memory ring buffer.

        Args:
            filepath: Path to the backing shared memory file.
            capacity: Number of slots in the ring buffer (must be a power of two).
            slot_size: Size in bytes of each record slot (default: 29 bytes).
            create: If True, initializes a new ring buffer file; if False, attaches to existing.
        """
        self._filepath = Path(filepath)
        self._closed = False
        self._file_obj = None
        self._mmap: Optional[mmap.mmap] = None

        if create:
            if not _is_power_of_two(capacity):
                raise ValueError(
                    f"Buffer capacity ({capacity}) must be a power of two (e.g. 1024, 4096, 65536)."
                )
            if slot_size <= 0:
                raise ValueError(f"Slot size ({slot_size}) must be greater than zero.")
            self._capacity = capacity
            self._slot_size = slot_size
            self._mask = capacity - 1
            self._total_size = HEADER_SIZE + (self._capacity * self._slot_size)
            self._create_buffer_file()
        else:
            self._attach_buffer_file()

    def _create_buffer_file(self) -> None:
        """Create and initialize a new buffer file with zeroed memory and header."""
        self._filepath.parent.mkdir(parents=True, exist_ok=True)

        # Open in read-write-binary mode, create if absent
        self._file_obj = open(self._filepath, "w+b")
        self._file_obj.truncate(self._total_size)
        self._file_obj.flush()

        # Memory map the full buffer size
        self._mmap = mmap.mmap(self._file_obj.fileno(), self._total_size, access=mmap.ACCESS_WRITE)

        # Write pristine 128-byte header block
        # Zero out the entire 128 bytes first
        self._mmap[0:HEADER_SIZE] = b"\x00" * HEADER_SIZE

        # Pack primary header fields (magic, version, header_size, slot_size, capacity, head=0)
        struct.pack_into(
            HEADER_INIT_FMT,
            self._mmap,
            0,
            MAGIC_BYTES,
            PROTOCOL_VERSION,
            HEADER_SIZE,
            self._slot_size,
            self._capacity,
            0,  # initial head = 0
        )
        # Tail counter is already 0 at TAIL_OFFSET (64)

    def _attach_buffer_file(self) -> None:
        """Attach to an existing buffer file and validate header integrity."""
        if not self._filepath.exists():
            raise FileNotFoundError(f"Ring buffer file not found: {self._filepath}")

        file_size = self._filepath.stat().st_size
        if file_size < HEADER_SIZE:
            raise CorruptBufferHeaderError(
                f"Buffer file size ({file_size} bytes) is smaller than minimum header ({HEADER_SIZE} bytes)."
            )

        self._file_obj = open(self._filepath, "r+b")
        self._mmap = mmap.mmap(self._file_obj.fileno(), 0, access=mmap.ACCESS_WRITE)

        # Validate header magic bytes
        magic = self._mmap[0:4]
        if magic != MAGIC_BYTES:
            raise CorruptBufferHeaderError(
                f"Invalid buffer header magic {magic!r}; expected {MAGIC_BYTES!r}."
            )

        # Read and validate version, header size, slot size, and capacity
        version, header_size, slot_size, capacity = struct.unpack_from(
            "<HHII", self._mmap, 4
        )
        if version != PROTOCOL_VERSION:
            raise CorruptBufferHeaderError(
                f"Unsupported protocol version {version}; expected {PROTOCOL_VERSION}."
            )
        if header_size != HEADER_SIZE:
            raise CorruptBufferHeaderError(
                f"Unexpected header size {header_size}; expected {HEADER_SIZE}."
            )
        if not _is_power_of_two(capacity):
            raise CorruptBufferHeaderError(f"Buffer capacity {capacity} is not a power of two.")

        self._capacity = capacity
        self._slot_size = slot_size
        self._mask = capacity - 1
        self._total_size = HEADER_SIZE + (self._capacity * self._slot_size)

        if len(self._mmap) < self._total_size:
            raise CorruptBufferHeaderError(
                f"Buffer mmap length ({len(self._mmap)}) is less than expected ({self._total_size})."
            )

    @property
    def capacity(self) -> int:
        """Total slot capacity of the ring buffer."""
        return self._capacity

    @property
    def slot_size(self) -> int:
        """Size in bytes of an individual slot."""
        return self._slot_size

    @property
    def head(self) -> int:
        """Monotonic write sequence counter (read from shared memory)."""
        self._ensure_open()
        return struct.unpack_from(U64_FMT, self._mmap, HEAD_OFFSET)[0]

    @head.setter
    def head(self, value: int) -> None:
        """Atomic write sequence counter update."""
        self._ensure_open()
        struct.pack_into(U64_FMT, self._mmap, HEAD_OFFSET, value)

    @property
    def tail(self) -> int:
        """Monotonic read sequence counter (read from shared memory)."""
        self._ensure_open()
        return struct.unpack_from(U64_FMT, self._mmap, TAIL_OFFSET)[0]

    @tail.setter
    def tail(self, value: int) -> None:
        """Atomic read sequence counter update."""
        self._ensure_open()
        struct.pack_into(U64_FMT, self._mmap, TAIL_OFFSET, value)

    def available_to_read(self) -> int:
        """Number of unread orders currently residing in the buffer."""
        return self.head - self.tail

    def available_to_write(self) -> int:
        """Number of free slots available for writing before buffer saturation."""
        return self._capacity - (self.head - self.tail)

    def is_empty(self) -> bool:
        """Check if buffer has no unread orders."""
        return self.head == self.tail

    def is_full(self) -> bool:
        """Check if buffer has reached capacity."""
        return (self.head - self.tail) >= self._capacity

    def write(self, order: OrderRecord) -> None:
        """
        Write an OrderRecord into the next available buffer slot.

        Args:
            order: OrderRecord to write.

        Raises:
            BufferFullError: If no free slots are available.
            BufferClosedError: If ring buffer is closed.
        """
        self._ensure_open()
        current_head = self.head
        current_tail = self.tail

        if (current_head - current_tail) >= self._capacity:
            raise BufferFullError(
                f"Ring buffer saturated (head={current_head}, tail={current_tail}, cap={self._capacity})."
            )

        slot_idx = current_head & self._mask
        offset = HEADER_SIZE + (slot_idx * self._slot_size)
        encode_order_into(self._mmap, offset, order)

        # Monotonically increment head after payload is fully written
        self.head = current_head + 1

    def try_write(self, order: OrderRecord) -> bool:
        """
        Attempt non-blocking write.

        Returns:
            bool: True if written successfully, False if buffer was full.
        """
        try:
            self.write(order)
            return True
        except BufferFullError:
            return False

    def read(self) -> OrderRecord:
        """
        Read the next OrderRecord from the buffer.

        Returns:
            OrderRecord: The next pending order in sequence.

        Raises:
            BufferEmptyError: If no unread orders are available.
            BufferClosedError: If ring buffer is closed.
        """
        self._ensure_open()
        current_tail = self.tail
        current_head = self.head

        if current_tail >= current_head:
            raise BufferEmptyError(
                f"Ring buffer empty (tail={current_tail}, head={current_head})."
            )

        slot_idx = current_tail & self._mask
        offset = HEADER_SIZE + (slot_idx * self._slot_size)
        order = decode_order_from(self._mmap, offset)

        # Monotonically increment tail after record is successfully read
        self.tail = current_tail + 1
        return order

    def try_read(self) -> Optional[OrderRecord]:
        """
        Attempt non-blocking read.

        Returns:
            Optional[OrderRecord]: OrderRecord if available, None if empty.
        """
        try:
            return self.read()
        except BufferEmptyError:
            return None

    def _ensure_open(self) -> None:
        """Validate buffer has not been closed."""
        if self._closed or self._mmap is None:
            raise BufferClosedError("Ring buffer is closed.")

    def close(self) -> None:
        """Flush and release the memory mapping and file descriptors."""
        if not self._closed:
            self._closed = True
            if self._mmap is not None:
                try:
                    self._mmap.flush()
                except Exception:
                    pass
                self._mmap.close()
                self._mmap = None
            if self._file_obj is not None:
                self._file_obj.close()
                self._file_obj = None

    def __enter__(self) -> "RingBuffer":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def __del__(self) -> None:
        self.close()
