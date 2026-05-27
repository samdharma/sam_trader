"""Minimal echo strategy for integration testing.

Captures quote ticks and bars so tests can verify data flow
through the TradingNode.
"""

from __future__ import annotations

from nautilus_trader.model.data import Bar, QuoteTick
from nautilus_trader.trading.strategy import Strategy, StrategyConfig


class EchoStrategyConfig(StrategyConfig, frozen=True):  # type: ignore[call-arg]
    """Configuration for ``EchoStrategy`` instances.

    Includes fields injected by the bundle loader so the strategy can be
    instantiated without msgspec validation errors.
    """

    instrument_id: str
    bar_type: str
    market: str = "US"
    futu_code: str = ""
    venue: str = ""
    bundle_id: str = "unknown"
    exchange: str = ""


class EchoStrategy(Strategy):
    """A test strategy that records incoming quote ticks and bars."""

    def __init__(self, config: EchoStrategyConfig) -> None:
        super().__init__(config)
        self.quote_ticks: list[QuoteTick] = []
        self.bars: list[Bar] = []

    def on_quote_tick(self, tick: QuoteTick) -> None:
        self.quote_ticks.append(tick)

    def on_bar(self, bar: Bar) -> None:
        self.bars.append(bar)
