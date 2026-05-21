"""Futu instrument provider.

Maps Futu security codes to NautilusTrader instruments via
``get_stock_basicinfo``.  Supports bulk loading (all markets) and
per-ID loading for efficient instrument resolution.
"""

from __future__ import annotations

import asyncio
from typing import Any

from futu import RET_OK, Market, SecurityType
from nautilus_trader.common.config import InstrumentProviderConfig
from nautilus_trader.common.providers import InstrumentProvider
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.instruments import Instrument

from sam_trader.adapters.futu.common import instrument_id_to_futu_security
from sam_trader.adapters.futu.parsing.instruments import parse_futu_instrument
from sam_trader.adapters.futu.parsing.market_data import security_to_instrument_id

# Markets to query for ``load_all_async``
_SUPPORTED_MARKETS: list[str] = [
    Market.US,
    Market.HK,
    Market.SH,
    Market.SZ,
]


class FutuInstrumentProvider(InstrumentProvider):
    """Instrument provider backed by Futu OpenD ``get_stock_basicinfo``.

    Parameters
    ----------
    quote_context : OpenQuoteContext
        Connected Futu quote context used for instrument queries.
    config : InstrumentProviderConfig, optional
        Standard Nautilus instrument provider configuration.

    """

    def __init__(
        self,
        quote_context: Any,
        config: InstrumentProviderConfig | None = None,
    ) -> None:
        super().__init__(config)
        self._quote_ctx = quote_context

    # ------------------------------------------------------------------
    # Async loaders
    # ------------------------------------------------------------------

    async def load_all_async(self, filters: dict | None = None) -> None:
        """Load instruments for all supported markets.

        Queries ``get_stock_basicinfo`` for each of US, HK, SH, SZ and
        parses the resulting DataFrame rows into Nautilus instruments.

        Parameters
        ----------
        filters : dict, optional
            Ignored — present for API compatibility.

        """
        for market in _SUPPORTED_MARKETS:
            await self._fetch_and_parse(
                market=market,
                stock_type=SecurityType.STOCK,
            )

    async def load_ids_async(
        self,
        instrument_ids: list[InstrumentId],
        filters: dict | None = None,
    ) -> None:
        """Load instruments for the given IDs.

        Converts each :class:`InstrumentId` to a Futu code, then queries
        ``get_stock_basicinfo(code_list=...)`` and parses the results.

        Parameters
        ----------
        instrument_ids : list[InstrumentId]
            The Nautilus instrument IDs to load.
        filters : dict, optional
            Ignored — present for API compatibility.

        """
        if not instrument_ids:
            return

        codes: list[str] = []
        for iid in instrument_ids:
            try:
                codes.append(instrument_id_to_futu_security(iid))
            except ValueError:
                self._log.warning(f"Cannot map {iid} to Futu code")
                continue

        if not codes:
            return

        await self._fetch_and_parse(
            market=Market.US,
            stock_type=SecurityType.STOCK,
            code_list=codes,
        )

    async def load_async(
        self,
        instrument_id: InstrumentId,
        filters: dict | None = None,
    ) -> None:
        """Load a single instrument.

        Delegates to ``load_ids_async``.

        Parameters
        ----------
        instrument_id : InstrumentId
            The instrument ID to load.
        filters : dict, optional
            Ignored — present for API compatibility.

        """
        if self.find(instrument_id) is not None:
            return
        await self.load_ids_async([instrument_id], filters)

    # ------------------------------------------------------------------
    # Position auto-loading
    # ------------------------------------------------------------------

    def load_from_position_data(self, code: str) -> Instrument | None:
        """Auto-load an instrument from Futu position data.

        If the instrument is already cached it is returned immediately.
        Otherwise a synchronous ``get_stock_basicinfo`` query is issued
        for the specific code.

        Parameters
        ----------
        code : str
            Futu security code (e.g. ``US.AAPL``, ``HK.00700``).

        Returns
        -------
        Instrument or ``None``
            The parsed instrument, or ``None`` if the query failed.

        """
        try:
            instrument_id = security_to_instrument_id(code)
        except ValueError:
            self._log.warning(f"Cannot convert position code {code} to instrument_id")
            return None

        instrument = self.find(instrument_id)
        if instrument is not None:
            return instrument

        ret, data = self._quote_ctx.get_stock_basicinfo(
            Market.US,
            SecurityType.STOCK,
            code_list=[code],
        )
        if ret != RET_OK or data is None or data.empty:
            self._log.warning(f"Failed to load instrument for position code {code}")
            return None

        instrument = parse_futu_instrument(data.iloc[0].to_dict())
        if instrument is not None:
            self.add(instrument)
            return instrument
        return None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _fetch_and_parse(
        self,
        market: str,
        stock_type: str,
        code_list: list[str] | None = None,
    ) -> None:
        """Query Futu and parse the resulting DataFrame into instruments.

        Runs the blocking Futu SDK call in the default thread pool so
        the event loop is not blocked.

        """
        loop = asyncio.get_running_loop()
        ret, data = await loop.run_in_executor(
            None,
            self._quote_ctx.get_stock_basicinfo,
            market,
            stock_type,
            code_list,
        )

        if ret != RET_OK:
            self._log.warning(f"get_stock_basicinfo failed for market={market}: {data}")
            return

        if data is None or getattr(data, "empty", True):
            return

        for _, row in data.iterrows():
            try:
                instrument = parse_futu_instrument(row.to_dict())
                if instrument is not None:
                    self.add(instrument)
            except Exception as e:  # noqa: BLE001
                code = row.get("code", "unknown")
                self._log.warning(f"Failed to parse instrument {code}: {e}")
