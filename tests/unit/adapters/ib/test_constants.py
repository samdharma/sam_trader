"""Unit tests for IB adapter constants."""

from __future__ import annotations

from nautilus_trader.model.identifiers import Venue

from sam_trader.adapters.ib.constants import IB_SMART_EXCHANGE, IB_VENUE


class TestIBConstants:
    def test_ib_venue(self) -> None:
        """IB_VENUE matches the Nautilus IB adapter venue."""
        assert IB_VENUE == Venue("IB")

    def test_ib_smart_exchange(self) -> None:
        """IB_SMART_EXCHANGE is the SMART routing default."""
        assert IB_SMART_EXCHANGE == "SMART"
