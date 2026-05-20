"""Common helpers for Futu adapter.

Instrument ID ↔ Futu security code conversions.
"""

from __future__ import annotations

from nautilus_trader.model.identifiers import InstrumentId, Venue

from sam_trader.adapters.futu.constants import FUTU_TO_NAUTILUS_VENUE

# Reverse mapping: Venue -> Futu market string
_NAUTILUS_VENUE_TO_FUTU_MARKET: dict[Venue, str] = {
    v: k for k, v in FUTU_TO_NAUTILUS_VENUE.items()
}
# NYSE also maps to US market code
_NAUTILUS_VENUE_TO_FUTU_MARKET[Venue("NYSE")] = "US"


def instrument_id_to_futu_security(instrument_id: InstrumentId) -> str:
    """Convert NautilusTrader InstrumentId to Futu security code.

    Nautilus format: ``SYMBOL.VENUE`` (e.g., ``AAPL.NASDAQ``).
    Futu format: ``MARKET.SYMBOL`` (e.g., ``US.AAPL``).

    Parameters
    ----------
    instrument_id : InstrumentId
        Nautilus instrument identifier.

    Returns
    -------
    str
        Futu security code string.

    Raises
    ------
    ValueError
        If the venue has no known Futu market mapping.

    """
    symbol = instrument_id.symbol.value
    venue = instrument_id.venue
    market = _NAUTILUS_VENUE_TO_FUTU_MARKET.get(venue)
    if market is None:
        raise ValueError(f"Unknown venue for Futu mapping: {venue}")
    return f"{market}.{symbol}"
