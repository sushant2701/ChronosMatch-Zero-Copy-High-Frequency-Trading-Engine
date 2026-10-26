"""
Market Simulator Configuration and Execution Statistics.

Provides structured configuration parameters and empirical metrics reporting for
the synthetic market order stream in ChronosMatch.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class SimulatorConfig:
    """
    Configuration parameters governing synthetic market order generation.

    Attributes:
        initial_price: Base price in integer ticks (e.g. 10000 ticks = $100.00).
        drift_std: Standard deviation of price drift step for random walk.
        spread: Half-spread in ticks around the current mid-price.
        min_price: Floor tick price ensuring market prices remain positive.
        max_price: Ceiling tick price capping random walk excursions.
        min_quantity: Minimum lot size per synthetic order.
        max_quantity: Maximum lot size per synthetic order.
        target_rate: Desired order generation rate in orders/second (e.g. 100,000).
        duration_seconds: Total time duration for running the simulation.
        seed: Random seed for deterministic reproducibility in testing.
        batch_size: Order batch count processed before cooperative event-loop yield.
        max_retries: Maximum back-off retry attempts when buffer is full before dropping.
        backoff_delay_seconds: Brief sleep duration during back-off when buffer is saturated.
    """

    initial_price: int = 100_00
    drift_std: float = 2.0
    spread: int = 4
    min_price: int = 1_00
    max_price: int = 1_000_00
    min_quantity: int = 1
    max_quantity: int = 100
    target_rate: int = 100_000
    duration_seconds: float = 1.0
    seed: Optional[int] = 42
    batch_size: int = 100
    max_retries: int = 5
    backoff_delay_seconds: float = 0.0001

    def __post_init__(self) -> None:
        """Validate parameter boundaries and domain invariants."""
        if self.initial_price <= 0:
            raise ValueError(f"initial_price ({self.initial_price}) must be positive.")
        if self.min_price <= 0:
            raise ValueError(f"min_price ({self.min_price}) must be positive.")
        if self.max_price <= self.min_price:
            raise ValueError(
                f"max_price ({self.max_price}) must be strictly greater than min_price ({self.min_price})."
            )
        if not (self.min_price <= self.initial_price <= self.max_price):
            raise ValueError(
                f"initial_price ({self.initial_price}) must reside within [{self.min_price}, {self.max_price}]."
            )
        if self.spread <= 0:
            raise ValueError(f"spread ({self.spread}) must be positive.")
        if self.min_quantity <= 0 or self.max_quantity < self.min_quantity:
            raise ValueError(
                f"Invalid quantity range [{self.min_quantity}, {self.max_quantity}]."
            )
        if self.target_rate <= 0:
            raise ValueError(f"target_rate ({self.target_rate}) must be greater than zero.")
        if self.duration_seconds <= 0.0:
            raise ValueError(
                f"duration_seconds ({self.duration_seconds}) must be greater than zero."
            )
        if self.batch_size <= 0:
            raise ValueError(f"batch_size ({self.batch_size}) must be greater than zero.")
        if self.max_retries < 0:
            raise ValueError(f"max_retries ({self.max_retries}) cannot be negative.")
        if self.backoff_delay_seconds < 0.0:
            raise ValueError(
                f"backoff_delay_seconds ({self.backoff_delay_seconds}) cannot be negative."
            )


@dataclass
class SimulatorStats:
    """
    Empirical runtime statistics collected during market simulation.

    Attributes:
        orders_sent: Total number of orders successfully written to the ring buffer.
        retries: Count of temporary back-off retries caused by buffer saturation.
        dropped_orders: Count of orders dropped after exhausting max back-off retries.
        elapsed_time: Actual wall-clock elapsed time measured in seconds.
        achieved_rate: Calculated effective throughput in orders/second (never fabricated).
    """

    orders_sent: int = 0
    retries: int = 0
    dropped_orders: int = 0
    elapsed_time: float = 0.0
    achieved_rate: float = 0.0

    def compute_achieved_rate(self) -> float:
        """
        Calculate and update the honest achieved order rate.

        Returns:
            float: Real rate in orders/second, or 0.0 if elapsed_time is zero.
        """
        if self.elapsed_time > 0.0:
            self.achieved_rate = self.orders_sent / self.elapsed_time
        else:
            self.achieved_rate = 0.0
        return self.achieved_rate
