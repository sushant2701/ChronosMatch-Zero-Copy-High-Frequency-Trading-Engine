"""
Binary codec for ChronosMatch order records.

Implements zero-copy, fixed-width 29-byte binary serialization using Python's
native struct module. Replaces JSON and pickle serialization with deterministic
C-struct memory layouts.

Record Layout (29 bytes, Little-Endian '<QBqIQ'):
    - order_id:     uint64 (8 bytes, offset 0)  - Unique monotonic order identifier
    - side:         uint8  (1 byte,  offset 8)  - 1 = BUY, 2 = SELL
    - price:        int64  (8 bytes, offset 9)  - Price in ticks (fixed-point integer)
    - quantity:     uint32 (4 bytes, offset 17) - Order quantity (lots)
    - timestamp_ns: uint64 (8 bytes, offset 21) - Nanosecond ingress timestamp
"""

import enum
import struct
from typing import Any, Union

from chronosmatch.exceptions import InvalidOrderError

# Format string: Little-Endian, uint64, uint8, int64, uint32, uint64
ORDER_STRUCT_FMT = "<QBqIQ"
ORDER_RECORD_SIZE = struct.calcsize(ORDER_STRUCT_FMT)  # Exactly 29 bytes

# Min/max boundaries for fixed-width integer fields
UINT64_MAX = 0xFFFFFFFFFFFFFFFF
UINT32_MAX = 0xFFFFFFFF
INT64_MIN = -0x8000000000000000
INT64_MAX = 0x7FFFFFFFFFFFFFFF


class OrderSide(enum.IntEnum):
    """Trading order side enumeration."""

    BUY = 1
    SELL = 2

    @classmethod
    def from_value(cls, value: int) -> "OrderSide":
        """Validate and return OrderSide enum member."""
        try:
            return cls(value)
        except ValueError:
            raise InvalidOrderError(
                f"Invalid order side value {value!r}. Must be 1 (BUY) or 2 (SELL)."
            )


class OrderRecord:
    """
    Fixed-width order record optimized for high-frequency low-latency execution.

    Uses __slots__ to eliminate per-instance dictionary overhead and accelerate
    field attribute lookups in hot paths.
    """

    __slots__ = ("order_id", "side", "price", "quantity", "timestamp_ns")

    def __init__(
        self,
        order_id: int,
        side: Union[OrderSide, int],
        price: int,
        quantity: int,
        timestamp_ns: int,
    ) -> None:
        """Initialize an OrderRecord with validation."""
        self._validate(order_id, side, price, quantity, timestamp_ns)
        self.order_id: int = order_id
        self.side: int = int(side)
        self.price: int = price
        self.quantity: int = quantity
        self.timestamp_ns: int = timestamp_ns

    @staticmethod
    def _validate(order_id: int, side: Any, price: int, quantity: int, timestamp_ns: int) -> None:
        """Validate range boundaries and domain constraints."""
        if not (0 <= order_id <= UINT64_MAX):
            raise InvalidOrderError(f"order_id {order_id} out of uint64 bounds [0, {UINT64_MAX}]")

        int_side = int(side)
        if int_side not in (OrderSide.BUY.value, OrderSide.SELL.value):
            raise InvalidOrderError(f"side {side!r} must be OrderSide.BUY (1) or OrderSide.SELL (2)")

        if not (INT64_MIN <= price <= INT64_MAX):
            raise InvalidOrderError(f"price {price} out of int64 bounds [{INT64_MIN}, {INT64_MAX}]")

        if not (0 <= quantity <= UINT32_MAX):
            raise InvalidOrderError(f"quantity {quantity} out of uint32 bounds [0, {UINT32_MAX}]")

        if not (0 <= timestamp_ns <= UINT64_MAX):
            raise InvalidOrderError(
                f"timestamp_ns {timestamp_ns} out of uint64 bounds [0, {UINT64_MAX}]"
            )

    @property
    def side_enum(self) -> OrderSide:
        """Return the side as an OrderSide enum."""
        return OrderSide(self.side)

    @property
    def is_buy(self) -> bool:
        """Check if order is on the BUY side."""
        return self.side == OrderSide.BUY.value

    @property
    def is_sell(self) -> bool:
        """Check if order is on the SELL side."""
        return self.side == OrderSide.SELL.value

    def to_bytes(self) -> bytes:
        """Serialize order into 29 packed binary bytes."""
        return encode_order(self)

    @classmethod
    def from_bytes(cls, data: bytes) -> "OrderRecord":
        """Deserialize order from 29 packed binary bytes."""
        return decode_order(data)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, OrderRecord):
            return NotImplemented
        return (
            self.order_id == other.order_id
            and self.side == other.side
            and self.price == other.price
            and self.quantity == other.quantity
            and self.timestamp_ns == other.timestamp_ns
        )

    def __repr__(self) -> str:
        side_str = "BUY" if self.side == OrderSide.BUY.value else "SELL"
        return (
            f"OrderRecord(id={self.order_id}, side={side_str}, price={self.price}, "
            f"qty={self.quantity}, ts_ns={self.timestamp_ns})"
        )


def encode_order(order: OrderRecord) -> bytes:
    """
    Pack an OrderRecord into a fixed 29-byte binary record.

    Args:
        order: The OrderRecord instance to encode.

    Returns:
        bytes: 29-byte binary packed representation.
    """
    return struct.pack(
        ORDER_STRUCT_FMT,
        order.order_id,
        order.side,
        order.price,
        order.quantity,
        order.timestamp_ns,
    )


def decode_order(data: Union[bytes, bytearray, memoryview]) -> OrderRecord:
    """
    Unpack 29 bytes into an OrderRecord instance.

    Args:
        data: Buffer containing exactly 29 bytes.

    Returns:
        OrderRecord: Deserialized order instance.

    Raises:
        InvalidOrderError: If buffer length does not equal 29 bytes.
    """
    if len(data) != ORDER_RECORD_SIZE:
        raise InvalidOrderError(
            f"Buffer length mismatch: expected {ORDER_RECORD_SIZE} bytes, got {len(data)}"
        )
    order_id, side, price, quantity, timestamp_ns = struct.unpack(ORDER_STRUCT_FMT, data)
    return OrderRecord(
        order_id=order_id,
        side=side,
        price=price,
        quantity=quantity,
        timestamp_ns=timestamp_ns,
    )


def encode_order_into(buffer: Any, offset: int, order: OrderRecord) -> None:
    """
    Zero-copy write of an OrderRecord into an existing buffer (e.g. mmap or bytearray).

    Args:
        buffer: Target mutable buffer supporting buffer protocol (mmap, bytearray).
        offset: Byte offset where record should be written.
        order: OrderRecord instance to pack.
    """
    struct.pack_into(
        ORDER_STRUCT_FMT,
        buffer,
        offset,
        order.order_id,
        order.side,
        order.price,
        order.quantity,
        order.timestamp_ns,
    )


def decode_order_from(buffer: Any, offset: int) -> OrderRecord:
    """
    Zero-copy read of an OrderRecord directly from an existing buffer at an offset.

    Args:
        buffer: Source buffer supporting buffer protocol (mmap, bytes, bytearray).
        offset: Byte offset where the 29-byte record begins.

    Returns:
        OrderRecord: Deserialized order instance.
    """
    order_id, side, price, quantity, timestamp_ns = struct.unpack_from(
        ORDER_STRUCT_FMT, buffer, offset
    )
    return OrderRecord(
        order_id=order_id,
        side=side,
        price=price,
        quantity=quantity,
        timestamp_ns=timestamp_ns,
    )
