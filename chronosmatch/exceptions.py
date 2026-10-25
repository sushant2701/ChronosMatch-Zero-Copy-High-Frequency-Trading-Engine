"""
Custom domain exceptions for the ChronosMatch trading engine and IPC bus.

Hierarchy:
    ChronosMatchError (Base)
    ├── CodecError
    │   └── InvalidOrderError
    └── RingBufferError
        ├── BufferFullError
        ├── BufferEmptyError
        ├── CorruptBufferHeaderError
        └── BufferClosedError
"""


class ChronosMatchError(Exception):
    """Base exception for all ChronosMatch domain errors."""


class CodecError(ChronosMatchError):
    """Raised when binary serialization or deserialization fails."""


class InvalidOrderError(CodecError):
    """Raised when order parameters violate schema or bounds constraints."""


class RingBufferError(ChronosMatchError):
    """Base exception for ring buffer errors."""


class BufferFullError(RingBufferError):
    """Raised when attempting to write to a saturated SPSC ring buffer."""


class BufferEmptyError(RingBufferError):
    """Raised when attempting to read from an empty SPSC ring buffer."""


class CorruptBufferHeaderError(RingBufferError):
    """Raised when the ring buffer header has invalid magic, version, or geometry."""


class BufferClosedError(RingBufferError):
    """Raised when attempting an operation on a closed or unmapped buffer."""
