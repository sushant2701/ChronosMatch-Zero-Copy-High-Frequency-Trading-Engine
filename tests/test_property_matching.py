"""
Property-style differential matching tests comparing Cython and pure-Python engines.

Feeds an identical pseudo-random stream of incoming limit orders and cancellations
to both the reference pure-Python LimitOrderBook and the compiled CythonOrderBook.
Asserts bit-for-bit identical trade executions, top-of-book prices, order counts,
and ladder volumes after every single operation.

If the Cython extension is not compiled (e.g. MSVC compiler unavailable), the test
cleanly skips using `pytest.skip` with an explicit reason.
"""

import random
import pytest

from chronosmatch.codec import OrderRecord, OrderSide
from chronosmatch.engine import is_cython_available
from chronosmatch.order_book import LimitOrderBook


def test_differential_seeded_random_stream() -> None:
    """
    Feed an identical seeded stream of orders to Python and Cython order books.

    Asserts:
    1. Identical trade execution lists (buy_order_id, sell_order_id, price, quantity).
    2. Identical top-of-book best bid and best ask prices.
    3. Identical resting order count and aggregate volume across books.
    """
    if not is_cython_available():
        pytest.skip("Cython native engine (chronosmatch.engine_cy) is not compiled; skipping differential test.")

    # Dynamically import compiled Cython engine
    from chronosmatch.engine_cy import CythonOrderBook  # type: ignore

    py_book = LimitOrderBook()
    cy_book = CythonOrderBook()

    rng = random.Random(1337)
    order_id_counter = 1
    active_order_ids: list[int] = []

    # Run 500 interleaved actions: 80% adds, 20% cancels
    num_steps = 500
    base_price = 100_00  # $100.00 base

    for step in range(num_steps):
        # Decide between cancel or add
        should_cancel = len(active_order_ids) > 10 and rng.random() < 0.20

        if should_cancel:
            # Pick a resting order to cancel
            cancel_idx = rng.randrange(len(active_order_ids))
            target_id = active_order_ids.pop(cancel_idx)

            try:
                py_book.cancel_order(target_id)
                py_cancelled = True
            except Exception:
                py_cancelled = False

            try:
                cy_book.cancel_order(target_id)
                cy_cancelled = True
            except Exception:
                cy_cancelled = False

            assert py_cancelled == cy_cancelled, f"Step {step}: Cancel disparity on ID {target_id}"

        else:
            # Generate random order
            order_id = order_id_counter
            order_id_counter += 1
            side = OrderSide.BUY if rng.random() < 0.50 else OrderSide.SELL
            # Clustered price tick spread around base price
            price_offset = rng.randint(-15, 15)
            price = max(1, base_price + price_offset)
            quantity = rng.randint(1, 50)
            timestamp_ns = 1_000_000_000 + step * 1000

            order = OrderRecord(
                order_id=order_id,
                side=side,
                price=price,
                quantity=quantity,
                timestamp_ns=timestamp_ns,
            )

            trades_py = py_book.add_order(order)
            trades_cy = cy_book.add_order(order)

            # Assert identical trade execution stream
            assert len(trades_py) == len(trades_cy), (
                f"Step {step}: Trade count mismatch. Python={len(trades_py)}, Cython={len(trades_cy)}"
            )

            for t_py, t_cy in zip(trades_py, trades_cy):
                assert t_py.buy_order_id == t_cy.buy_order_id, f"Step {step}: Trade buy ID mismatch"
                assert t_py.sell_order_id == t_cy.sell_order_id, f"Step {step}: Trade sell ID mismatch"
                assert t_py.price == t_cy.price, f"Step {step}: Trade price mismatch"
                assert t_py.quantity == t_cy.quantity, f"Step {step}: Trade quantity mismatch"

            active_order_ids.append(order_id)

        # Assert identical state after every step
        assert py_book.order_count == cy_book.order_count, f"Step {step}: Order count mismatch"
        assert py_book.bid_level_count == cy_book.bid_level_count, f"Step {step}: Bid level count mismatch"
        assert py_book.ask_level_count == cy_book.ask_level_count, f"Step {step}: Ask level count mismatch"

        assert py_book.best_bid() == cy_book.best_bid(), f"Step {step}: Best bid mismatch"
        assert py_book.best_ask() == cy_book.best_ask(), f"Step {step}: Best ask mismatch"

        assert py_book.total_bid_volume() == cy_book.total_bid_volume(), f"Step {step}: Bid volume mismatch"
        assert py_book.total_ask_volume() == cy_book.total_ask_volume(), f"Step {step}: Ask volume mismatch"
