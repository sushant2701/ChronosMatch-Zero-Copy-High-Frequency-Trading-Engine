"""
Order Matching Engine Factory and Backend Dispatcher.

Provides a unified factory function `get_engine()` that dynamically selects
between the high-performance compiled Cython matching engine (`engine_cy.pyx`)
and the deterministic pure-Python reference Limit Order Book (`order_book.py`).

Design Principles:
1. Seamless Fallback: Automatically falls back to pure-Python reference book
   if Cython extension is not compiled, without throwing unhandled ImportErrors.
2. Explicit Observability: Emits structured log events via Python's `logging`
   module indicating which backend is activated and the root cause of fallbacks.
3. Identical API Guarantee: Both backends implement the identical public interface:
   - `add_order(order: OrderRecord) -> List[Trade]`
   - `cancel_order(order_id: int) -> None`
   - `best_bid` / `best_ask` (dual property & callable)
   - `get_best_bid()` / `get_best_ask()`
   - `order_count`, `bid_level_count`, `ask_level_count`
   - Volume inquiry methods (`bid_volume_at`, `total_bid_volume`, etc.)
"""

import enum
import logging
from typing import Any, Optional, Type, Union

from chronosmatch.order_book import LimitOrderBook

logger = logging.getLogger("chronosmatch.engine")


class EngineBackend(str, enum.Enum):
    """Enumeration of supported order matching engine execution backends."""

    CYTHON = "cython"
    PYTHON = "python"


# Module-level cached backend class and active state
_CYTHON_AVAILABLE: bool = False
_CythonOrderBook: Optional[Type[Any]] = None

try:
    from chronosmatch.engine_cy import CythonOrderBook as _CompiledCythonOrderBook

    _CythonOrderBook = _CompiledCythonOrderBook
    _CYTHON_AVAILABLE = True
except ImportError as _import_err:
    _CYTHON_AVAILABLE = False
    _CythonOrderBook = None
    logger.debug("Cython native engine extension not available: %s", _import_err)


def is_cython_available() -> bool:
    """
    Check if the native compiled Cython matching engine is available.

    Returns:
        bool: True if CythonOrderBook was successfully compiled and imported.
    """
    return _CYTHON_AVAILABLE


def get_active_backend() -> EngineBackend:
    """
    Determine the default active matching engine backend.

    Returns:
        EngineBackend: CYTHON if compiled extension is present, otherwise PYTHON.
    """
    if _CYTHON_AVAILABLE:
        return EngineBackend.CYTHON
    return EngineBackend.PYTHON


def get_engine(
    backend: Optional[Union[EngineBackend, str]] = None,
    **kwargs: Any,
) -> Union[Any, LimitOrderBook]:
    """
    Factory function creating an order matching engine instance.

    Attempts to instantiate the high-performance Cython matching engine. If
    unavailable, or if explicitly requested, automatically falls back to the
    pure-Python reference implementation with full logging.

    Args:
        backend: Optional explicit backend selection ('cython' or 'python').
                 If None, automatically prefers Cython and falls back to Python.
        **kwargs: Additional configuration parameters forwarded to engine constructor.

    Returns:
        Union[CythonOrderBook, LimitOrderBook]: Matching engine instance.

    Raises:
        RuntimeError: If 'cython' was explicitly requested but the native extension
                      is not compiled or available in the environment.
        ValueError: If an unknown backend string is specified.
    """
    if backend is not None:
        if isinstance(backend, str):
            try:
                backend = EngineBackend(backend.lower())
            except ValueError:
                raise ValueError(
                    f"Unknown engine backend '{backend}'. Supported: "
                    f"{[b.value for b in EngineBackend]}"
                )

        if backend == EngineBackend.CYTHON:
            if not _CYTHON_AVAILABLE or _CythonOrderBook is None:
                msg = (
                    "Cython engine backend explicitly requested, but native extension "
                    "'chronosmatch.engine_cy' is not compiled or available."
                )
                logger.error(msg)
                raise RuntimeError(msg)

            logger.info("Initializing explicitly requested Cython matching engine.")
            return _CythonOrderBook(**kwargs)

        if backend == EngineBackend.PYTHON:
            logger.info("Initializing explicitly requested pure-Python LimitOrderBook.")
            return LimitOrderBook(**kwargs)

    # Automatic detection and graceful fallback
    if _CYTHON_AVAILABLE and _CythonOrderBook is not None:
        logger.info("Active matching engine backend: Cython native extension (engine_cy).")
        return _CythonOrderBook(**kwargs)

    logger.info(
        "Active matching engine backend: pure-Python reference (order_book.LimitOrderBook) "
        "[Cython extension not compiled; running fallback]."
    )
    return LimitOrderBook(**kwargs)
