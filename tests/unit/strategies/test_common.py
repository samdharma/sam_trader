"""Unit tests for venue-aware order helpers in ``sam_trader.strategies.common``."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from nautilus_trader.model.identifiers import InstrumentId, Venue

from sam_trader.strategies.common import make_bracket, make_limit


@pytest.fixture
def mock_factory() -> MagicMock:
    """Return a mock order factory."""
    return MagicMock()


class TestMakeBracket:
    def test_sets_tp_post_only_false_for_ib(self, mock_factory: MagicMock) -> None:
        """IB venue injects tp_post_only=False when not explicitly provided."""
        inst = InstrumentId.from_str("AAPL.NASDAQ")
        # Force venue to IB
        inst = InstrumentId(
            symbol=inst.symbol,
            venue=Venue("IB"),
        )

        make_bracket(
            mock_factory,
            instrument_id=inst,
            order_side=MagicMock(),
            quantity=MagicMock(),
        )

        call_kwargs = mock_factory.bracket.call_args.kwargs
        assert call_kwargs["tp_post_only"] is False
        assert call_kwargs["instrument_id"] == inst

    def test_leaves_tp_post_only_untouched_for_futu(
        self, mock_factory: MagicMock
    ) -> None:
        """Futu venue does not touch tp_post_only."""
        inst = InstrumentId.from_str("00700.HK")

        make_bracket(
            mock_factory,
            instrument_id=inst,
            order_side=MagicMock(),
            quantity=MagicMock(),
        )

        call_kwargs = mock_factory.bracket.call_args.kwargs
        assert "tp_post_only" not in call_kwargs

    def test_respects_explicit_tp_post_only_for_ib(
        self, mock_factory: MagicMock
    ) -> None:
        """A strategy may still override the default if it really wants to."""
        inst = InstrumentId(
            symbol=InstrumentId.from_str("TSLA.NASDAQ").symbol,
            venue=Venue("IB"),
        )

        make_bracket(
            mock_factory,
            instrument_id=inst,
            order_side=MagicMock(),
            quantity=MagicMock(),
            tp_post_only=True,
        )

        call_kwargs = mock_factory.bracket.call_args.kwargs
        # setdefault should NOT overwrite the explicit True
        assert call_kwargs["tp_post_only"] is True

    def test_forwards_extra_kwargs(self, mock_factory: MagicMock) -> None:
        """All other kwargs are passed through unchanged."""
        inst = InstrumentId.from_str("00700.HK")
        extra = {"sl_trigger_price": MagicMock(), "tp_price": MagicMock()}

        make_bracket(mock_factory, instrument_id=inst, **extra)

        call_kwargs = mock_factory.bracket.call_args.kwargs
        assert call_kwargs["sl_trigger_price"] == extra["sl_trigger_price"]
        assert call_kwargs["tp_price"] == extra["tp_price"]


class TestMakeLimit:
    def test_sets_post_only_false_for_ib(self, mock_factory: MagicMock) -> None:
        """IB venue injects post_only=False when not explicitly provided."""
        inst = InstrumentId(
            symbol=InstrumentId.from_str("AAPL.NASDAQ").symbol,
            venue=Venue("IB"),
        )

        make_limit(
            mock_factory,
            instrument_id=inst,
            order_side=MagicMock(),
            quantity=MagicMock(),
            price=MagicMock(),
        )

        call_kwargs = mock_factory.limit.call_args.kwargs
        assert call_kwargs["post_only"] is False
        assert call_kwargs["instrument_id"] == inst

    def test_leaves_post_only_untouched_for_futu(self, mock_factory: MagicMock) -> None:
        """Futu venue does not touch post_only."""
        inst = InstrumentId.from_str("00700.HK")

        make_limit(
            mock_factory,
            instrument_id=inst,
            order_side=MagicMock(),
            quantity=MagicMock(),
            price=MagicMock(),
        )

        call_kwargs = mock_factory.limit.call_args.kwargs
        assert "post_only" not in call_kwargs

    def test_respects_explicit_post_only_for_ib(self, mock_factory: MagicMock) -> None:
        """A strategy may still override the default if it really wants to."""
        inst = InstrumentId(
            symbol=InstrumentId.from_str("TSLA.NASDAQ").symbol,
            venue=Venue("IB"),
        )

        make_limit(
            mock_factory,
            instrument_id=inst,
            order_side=MagicMock(),
            quantity=MagicMock(),
            price=MagicMock(),
            post_only=True,
        )

        call_kwargs = mock_factory.limit.call_args.kwargs
        assert call_kwargs["post_only"] is True

    def test_forwards_extra_kwargs(self, mock_factory: MagicMock) -> None:
        """All other kwargs are passed through unchanged."""
        inst = InstrumentId.from_str("00700.HK")
        extra = {"time_in_force": MagicMock()}

        make_limit(mock_factory, instrument_id=inst, **extra)

        call_kwargs = mock_factory.limit.call_args.kwargs
        assert call_kwargs["time_in_force"] == extra["time_in_force"]
