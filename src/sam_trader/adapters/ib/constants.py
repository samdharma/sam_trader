"""IBKR adapter constants for SAM Trader V3.

Venue and symbology constants consistent with the Futu adapter pattern.
"""

from __future__ import annotations

from nautilus_trader.model.identifiers import Venue

IB_VENUE = Venue("IB")
"""Primary venue identifier for Interactive Brokers."""

IB_SMART_EXCHANGE = "SMART"
"""Default IB exchange for equities to avoid direct-routing fees."""
