"""
Unit tests for the binary OrderRecord struct codec.
"""

import pytest

from chronosmatch.codec import (
    ORDER_RECORD_SIZE,
    OrderRecord,
    OrderSide,
    decode_order,
    decode_order_from,
    encode_order,
    encode_order_into,
)
from chronosmatch.exceptions import InvalidOrderError


def test_order_record_size():
    """Verify order struct matches the exact specification of 29 bytes."""
    assert ORDER_RECORD_SIZE == 29


def test_roundtrip_encode_decode():
    """Verify encode followed by decode returns an identical OrderRecord."""
    original = OrderRecord(
        order_id=100452,
        side=OrderSide.BUY,
        price=1850050,  # 18500.50 in ticks
        quantity=500,
        timestamp_ns=1700000000123456789,
    )
    raw_bytes = encode_order(original)
    assert len(raw_bytes) == 29

    decoded = decode_order(raw_bytes)
    assert decoded == original
    assert decoded.order_id == 100452
    assert decoded.side == OrderSide.BUY.value
    assert decoded.price == 1850050
    assert decoded.quantity == 500
    assert decoded.timestamp_ns == 1700000000123456789
    assert decoded.is_buy is True
    assert decoded.is_sell is False


def test_convenience_methods():
    """Verify to_bytes() and from_bytes() class methods."""
    order = OrderRecord(
        order_id=999,
        side=OrderSide.SELL,
        price=250000,
        quantity=10,
        timestamp_ns=123456789,
    )
    data = order.to_bytes()
    assert len(data) == 29

    reconstructed = OrderRecord.from_bytes(data)
    assert reconstructed == order
    assert reconstructed.is_sell is True
    assert reconstructed.side_enum == OrderSide.SELL
    assert "SELL" in repr(reconstructed)


def test_encode_into_decode_from_buffer():
    """Verify zero-copy buffer pack_into and unpack_from operations."""
    buffer = bytearray(128)  # preallocated memory region
    offset = 32

    order = OrderRecord(
        order_id=42,
        side=OrderSide.BUY,
        price=99900,
        quantity=100,
        timestamp_ns=555555555,
    )

    encode_order_into(buffer, offset, order)

    # Validate that byte content at offset is non-zero
    assert buffer[offset : offset + ORDER_RECORD_SIZE] == order.to_bytes()

    # Unpack from buffer at offset
    read_back = decode_order_from(buffer, offset)
    assert read_back == order


def test_boundary_values():
    """Verify extreme integer ranges supported by the 29-byte C-struct layout."""
    max_u64 = 0xFFFFFFFFFFFFFFFF
    max_u32 = 0xFFFFFFFF
    max_i64 = 0x7FFFFFFFFFFFFFFF
    min_i64 = -0x8000000000000000

    # Max limits
    order_max = OrderRecord(
        order_id=max_u64,
        side=OrderSide.SELL,
        price=max_i64,
        quantity=max_u32,
        timestamp_ns=max_u64,
    )
    raw_max = order_max.to_bytes()
    assert OrderRecord.from_bytes(raw_max) == order_max

    # Min limits
    order_min = OrderRecord(
        order_id=0,
        side=OrderSide.BUY,
        price=min_i64,
        quantity=0,
        timestamp_ns=0,
    )
    raw_min = order_min.to_bytes()
    assert OrderRecord.from_bytes(raw_min) == order_min


def test_invalid_order_side_raises_error():
    """Verify invalid side integers raise InvalidOrderError."""
    with pytest.raises(InvalidOrderError, match="must be OrderSide.BUY"):
        OrderRecord(order_id=1, side=0, price=100, quantity=10, timestamp_ns=1000)

    with pytest.raises(InvalidOrderError, match="must be OrderSide.BUY"):
        OrderRecord(order_id=1, side=3, price=100, quantity=10, timestamp_ns=1000)


def test_invalid_bounds_raise_error():
    """Verify numbers outside C type limits raise InvalidOrderError."""
    with pytest.raises(InvalidOrderError, match="order_id"):
        OrderRecord(order_id=-1, side=1, price=100, quantity=10, timestamp_ns=1000)

    with pytest.raises(InvalidOrderError, match="quantity"):
        OrderRecord(order_id=1, side=1, price=100, quantity=-5, timestamp_ns=1000)

    with pytest.raises(InvalidOrderError, match="quantity"):
        OrderRecord(order_id=1, side=1, price=100, quantity=0xFFFFFFFF + 1, timestamp_ns=1000)

    with pytest.raises(InvalidOrderError, match="timestamp_ns"):
        OrderRecord(order_id=1, side=1, price=100, quantity=10, timestamp_ns=-1)


def test_decode_invalid_length_raises_error():
    """Verify decode fails if byte payload is truncated or oversized."""
    with pytest.raises(InvalidOrderError, match="Buffer length mismatch"):
        decode_order(b"\x00" * 28)

    with pytest.raises(InvalidOrderError, match="Buffer length mismatch"):
        decode_order(b"\x00" * 30)
