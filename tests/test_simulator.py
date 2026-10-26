"""
Unit tests for the ChronosMatch market simulator.

Verifies:
1. Deterministic order sequence reproducibility with fixed random seeds.
2. Generator reset mechanics and different-seed divergence.
3. Configuration validation invariants.
4. Asyncio simulation execution, rate-pacing, and empirical rate reporting.
"""

import asyncio
from pathlib import Path

import pytest

from chronosmatch.codec import OrderRecord
from chronosmatch.ring_buffer import RingBuffer
from chronosmatch.simulator import (
    MarketOrderGenerator,
    MarketSimulator,
    SimulatorConfig,
    SimulatorStats,
)


def test_order_generator_seed_determinism() -> None:
    """Verify that identical seeds produce bit-for-bit identical order sequences."""
    clock_val = 1_700_000_000_000_000
    mock_clock = lambda: clock_val  # noqa: E731

    config = SimulatorConfig(
        initial_price=150_00,
        drift_std=1.5,
        spread=3,
        min_price=10_00,
        max_price=500_00,
        seed=12345,
    )

    gen1 = MarketOrderGenerator(config=config, clock=mock_clock)
    gen2 = MarketOrderGenerator(config=config, clock=mock_clock)

    orders1 = [gen1.generate_order() for _ in range(150)]
    orders2 = [gen2.generate_order() for _ in range(150)]

    assert len(orders1) == len(orders2) == 150
    for idx, (o1, o2) in enumerate(zip(orders1, orders2)):
        assert o1.order_id == o2.order_id, f"Order ID mismatch at index {idx}"
        assert o1.side == o2.side, f"Side mismatch at index {idx}"
        assert o1.price == o2.price, f"Price mismatch at index {idx}"
        assert o1.quantity == o2.quantity, f"Quantity mismatch at index {idx}"
        assert o1.timestamp_ns == o2.timestamp_ns, f"Timestamp mismatch at index {idx}"
        assert o1 == o2


def test_order_generator_reset_and_divergence() -> None:
    """Verify generator reset restores sequence and different seeds diverge."""
    clock_val = 1_000_000
    mock_clock = lambda: clock_val  # noqa: E731

    config_a = SimulatorConfig(initial_price=100_00, seed=42)
    config_b = SimulatorConfig(initial_price=100_00, seed=999)

    gen_a = MarketOrderGenerator(config=config_a, clock=mock_clock)
    gen_b = MarketOrderGenerator(config=config_b, clock=mock_clock)

    batch_a1 = gen_a.generate_batch(20)
    batch_b = gen_b.generate_batch(20)

    # Different seeds should produce distinct price/side sequences
    assert any(a.price != b.price or a.side != b.side for a, b in zip(batch_a1, batch_b))

    # Resetting gen_a must reproduce batch_a1 exactly
    gen_a.reset()
    batch_a2 = gen_a.generate_batch(20)
    assert batch_a1 == batch_a2


def test_simulator_config_validation() -> None:
    """Verify that domain validation rejects invalid configuration parameters."""
    with pytest.raises(ValueError, match="initial_price"):
        SimulatorConfig(initial_price=0)

    with pytest.raises(ValueError, match="min_price"):
        SimulatorConfig(min_price=0)

    with pytest.raises(ValueError, match="max_price"):
        SimulatorConfig(min_price=100, max_price=50)

    with pytest.raises(ValueError, match="reside within"):
        SimulatorConfig(min_price=100, max_price=200, initial_price=300)

    with pytest.raises(ValueError, match="spread"):
        SimulatorConfig(spread=0)

    with pytest.raises(ValueError, match="quantity"):
        SimulatorConfig(min_quantity=10, max_quantity=5)

    with pytest.raises(ValueError, match="target_rate"):
        SimulatorConfig(target_rate=0)

    with pytest.raises(ValueError, match="duration_seconds"):
        SimulatorConfig(duration_seconds=0.0)

    with pytest.raises(ValueError, match="batch_size"):
        SimulatorConfig(batch_size=0)


def test_simulator_stats_rate_calculation() -> None:
    """Verify honest achieved rate computation without fabrication."""
    stats = SimulatorStats(orders_sent=50_000, elapsed_time=0.5)
    assert stats.compute_achieved_rate() == 100_000.0
    assert stats.achieved_rate == 100_000.0

    stats_zero = SimulatorStats(orders_sent=0, elapsed_time=0.0)
    assert stats_zero.compute_achieved_rate() == 0.0


def test_simulator_duration_and_execution(tmp_path: Path) -> None:
    """Verify simulator runs for the requested duration and respects order pacing."""
    buffer_file = tmp_path / "sim_test_bus.bin"
    capacity = 16384

    with RingBuffer(buffer_file, capacity=capacity, create=True) as rb:
        config = SimulatorConfig(
            initial_price=100_00,
            target_rate=50_000,
            duration_seconds=0.2,
            batch_size=50,
            seed=42,
        )
        sim = MarketSimulator(ring_buffer=rb, config=config)

        stats = asyncio.run(sim.run())

        # Empirical runtime verification
        assert stats.elapsed_time >= 0.18, f"Simulation ended too early: {stats.elapsed_time}s"
        assert stats.orders_sent > 0
        assert stats.achieved_rate > 0.0
        # The achieved rate must equal orders_sent / elapsed_time exactly
        expected_rate = stats.orders_sent / stats.elapsed_time
        assert abs(stats.achieved_rate - expected_rate) < 1e-6

        # Check that written orders in RingBuffer are valid and readable
        assert rb.available_to_read() == stats.orders_sent
        first_order = rb.read()
        assert isinstance(first_order, OrderRecord)
        assert first_order.order_id == 1
        assert first_order.price > 0
        assert first_order.quantity > 0
