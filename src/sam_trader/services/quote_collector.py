"""QuoteCollectionService — reusable Nautilus data client wrapper.

A lightweight bridge between Nautilus data clients and sam-services.
Connects to a broker (Futu or IB), subscribes quote ticks or bars for a
watchlist, collects them for a fixed duration, and returns the latest tick
or bar per symbol.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

from nautilus_trader.cache.cache import Cache
from nautilus_trader.common.component import LiveClock, MessageBus
from nautilus_trader.common.config import InstrumentProviderConfig
from nautilus_trader.common.providers import InstrumentProvider
from nautilus_trader.core.uuid import UUID4
from nautilus_trader.data.messages import SubscribeBars, SubscribeQuoteTicks
from nautilus_trader.model.data import Bar, BarType, QuoteTick
from nautilus_trader.model.identifiers import (
    ClientId,
    InstrumentId,
    Symbol,
    TraderId,
    Venue,
)

from sam_trader.adapters.futu.config import FutuDataClientConfig
from sam_trader.adapters.futu.connection import get_cached_futu_quote_context
from sam_trader.adapters.futu.data import FutuLiveDataClient
from sam_trader.adapters.futu.instrument_provider import FutuInstrumentProvider
from sam_trader.adapters.futu.subscription_manager import DataType as SubDataType
from sam_trader.adapters.futu.subscription_manager import (
    FutuSubscriptionManager,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class QuoteCollectionResult:
    """Result of a quote collection run."""

    quotes: dict[InstrumentId, QuoteTick] = field(default_factory=dict)
    bars: dict[InstrumentId, Bar] = field(default_factory=dict)
    partial_failures: list[str] = field(default_factory=list)
    elapsed_secs: float = 0.0


class QuoteCollectionService:
    """Reusable Nautilus data client wrapper for quote or bar collection.

    Parameters
    ----------
    broker : str
        ``"FUTU"`` or ``"IB"``.
    host : str, optional
        Broker gateway host. Defaults to ``FUTU_OPEND_HOST`` (Futu) or
        ``IB_GATEWAY_HOST`` (IB) env vars.
    port : int, optional
        Broker gateway port. Defaults to ``FUTU_OPEND_PORT`` (Futu) or
        ``IB_GATEWAY_PORT`` (IB) env vars.
    watchlist : list[str]
        Nautilus instrument ID strings (e.g. ``["TSLA.NASDAQ", "AAPL.NASDAQ"]``).
    data_type : str, default "quotes"
        ``"quotes"`` or ``"bars"``.
    bar_type_str : str, optional
        Nautilus bar type string (e.g. ``"TSLA.NASDAQ-1-MINUTE-LAST-EXTERNAL"``).
        Required when *data_type* is ``"bars"``.  If omitted, a default
        ``1-MINUTE-LAST-EXTERNAL`` spec is used.
    collection_period_secs : int, default 60
        How long to collect after successful subscription.
    connection_timeout_secs : int, default 10
        Maximum seconds to wait for the broker connection.
    client_id : int, default 1
        Broker client/session ID (used by IB).

    """

    def __init__(
        self,
        broker: str,
        watchlist: list[str],
        host: str | None = None,
        port: int | None = None,
        data_type: str = "quotes",
        bar_type_str: str | None = None,
        collection_period_secs: int = 60,
        connection_timeout_secs: int = 10,
        client_id: int = 1,
    ) -> None:
        self._broker = broker.upper()
        self._watchlist = list(watchlist)
        self._data_type = data_type.lower()
        self._bar_type_str = bar_type_str

        if self._data_type not in ("quotes", "bars"):
            raise ValueError(f"Unsupported data_type: {data_type}")

        # Env-var-driven defaults
        if host is None:
            host = os.environ.get(
                "IB_GATEWAY_HOST" if self._broker == "IB" else "FUTU_OPEND_HOST",
                "sam-ib-gateway" if self._broker == "IB" else "sam-futu-opend",
            )
        if port is None:
            port = int(
                os.environ.get(
                    "IB_GATEWAY_PORT" if self._broker == "IB" else "FUTU_OPEND_PORT",
                    "4004" if self._broker == "IB" else "11111",
                )
            )

        self._host = host
        self._port = port
        self._collection_period_secs = collection_period_secs
        self._connection_timeout_secs = connection_timeout_secs
        self._client_id = client_id

        # Lightweight in-process infrastructure
        self._msgbus: MessageBus | None = None
        self._cache: Cache | None = None
        self._clock: LiveClock | None = None
        self._instrument_provider: InstrumentProvider | None = None
        self._data_client: Any | None = None
        self._subscription_manager: FutuSubscriptionManager | None = None

        # Collection state
        self._quotes: dict[InstrumentId, QuoteTick] = {}
        self._bars: dict[InstrumentId, Bar] = {}
        self._partial_failures: list[str] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def collect(self) -> QuoteCollectionResult:
        """Connect, subscribe, collect, disconnect, return.

        Returns
        -------
        QuoteCollectionResult

        Raises
        ------
        RuntimeError
            If the broker is not supported or IB adapter is unavailable.
        ConnectionError
            If the client cannot connect within *connection_timeout_secs*.

        """
        if self._broker not in ("FUTU", "IB"):
            raise RuntimeError(f"Unsupported broker: {self._broker}")

        start_time = time.monotonic()
        try:
            await self._setup()
            await self._connect_with_timeout()
            await self._subscribe_all()
            await self._collect_loop()
        finally:
            await self._teardown()

        elapsed = time.monotonic() - start_time
        return QuoteCollectionResult(
            quotes=dict(self._quotes),
            bars=dict(self._bars),
            partial_failures=list(self._partial_failures),
            elapsed_secs=round(elapsed, 3),
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def _setup(self) -> None:
        """Create in-process Nautilus infrastructure."""
        self._clock = LiveClock()
        self._msgbus = MessageBus(
            trader_id=TraderId("SAM-001"),
            clock=self._clock,
        )
        self._cache = Cache()

        if self._broker == "FUTU":
            self._setup_futu()
        else:
            self._setup_ib()

    def _setup_futu(self) -> None:
        """Create Futu-specific client and provider."""
        config = FutuDataClientConfig(
            host=self._host,
            port=self._port,
            trd_env="SIMULATE",
            trd_market="US",
            client_id=1,
        )
        quote_ctx = get_cached_futu_quote_context(
            host=config.host,
            port=config.port,
            trade_env=config.trd_env,
        )
        self._instrument_provider = FutuInstrumentProvider(
            quote_context=quote_ctx,
            config=InstrumentProviderConfig(),
        )
        self._subscription_manager = FutuSubscriptionManager()

        loop = asyncio.get_running_loop()
        self._data_client = FutuLiveDataClient(
            loop=loop,
            client=quote_ctx,
            msgbus=self._msgbus,
            cache=self._cache,
            clock=self._clock,
            instrument_provider=self._instrument_provider,
            config=config,
            subscription_manager=self._subscription_manager,
        )

        # Register our collector on the message bus so QuoteTicks / Bars
        # dispatched by the client are captured.
        assert self._msgbus is not None
        self._msgbus.register(
            endpoint="DataEngine.process",
            handler=self._on_data,
        )

    def _setup_ib(self) -> None:
        """Create IB-specific client and provider."""
        try:
            from nautilus_trader.adapters.interactive_brokers.config import (
                InteractiveBrokersDataClientConfig,
                InteractiveBrokersInstrumentProviderConfig,
                SymbologyMethod,
            )
            from nautilus_trader.adapters.interactive_brokers.factories import (
                InteractiveBrokersLiveDataClientFactory,
            )
        except ImportError as exc:
            logger.warning("ibapi not installed; IB data client unavailable")
            raise RuntimeError(
                "IB data client requires nautilus-ibapi. "
                "Install with: pip install nautilus-ibapi"
            ) from exc

        # Convert watchlist symbols to InstrumentIds for eager loading
        ids: list[InstrumentId] = []
        for s in self._watchlist:
            if "." in s:
                try:
                    sym, venue = s.split(".", 1)
                    ids.append(InstrumentId(Symbol(sym), Venue(venue)))
                except Exception:  # noqa: BLE001
                    pass
        load_ids = frozenset(ids) if ids else None

        provider_config = InteractiveBrokersInstrumentProviderConfig(
            symbology_method=SymbologyMethod.IB_SIMPLIFIED,
            load_ids=load_ids,
        )

        config = InteractiveBrokersDataClientConfig(
            ibg_host=self._host,
            ibg_port=self._port,
            ibg_client_id=self._client_id,
            instrument_provider=provider_config,
        )

        loop = asyncio.get_running_loop()
        self._data_client = InteractiveBrokersLiveDataClientFactory.create(
            loop=loop,
            name="IB",
            config=config,
            msgbus=self._msgbus,
            cache=self._cache,
            clock=self._clock,
        )

        self._instrument_provider = self._data_client.instrument_provider

        # Register our collector on the message bus so QuoteTicks / Bars
        # dispatched by the client are captured.
        assert self._msgbus is not None
        self._msgbus.register(
            endpoint="DataEngine.process",
            handler=self._on_data,
        )

    async def _connect_with_timeout(self) -> None:
        """Connect the data client with a timeout."""
        if self._data_client is None:
            raise RuntimeError("Data client not initialized")

        try:
            await asyncio.wait_for(
                self._data_client._connect(),
                timeout=self._connection_timeout_secs,
            )
        except asyncio.TimeoutError as exc:
            raise ConnectionError(
                f"Timed out after {self._connection_timeout_secs}s "
                f"connecting to {self._broker} at {self._host}:{self._port}"
            ) from exc

    async def _subscribe_all(self) -> None:
        """Subscribe QuoteTicks or Bars for every symbol in the watchlist."""
        if self._data_client is None:
            return

        client_id_str = "FUTU-1" if self._broker == "FUTU" else "IB"

        for symbol in self._watchlist:
            try:
                instrument_id = InstrumentId.from_str(symbol)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Invalid instrument ID %r: %s", symbol, exc)
                self._partial_failures.append(symbol)
                continue

            if self._data_type == "bars":
                await self._subscribe_bars_for_instrument(
                    instrument_id, client_id_str, symbol
                )
            else:
                await self._subscribe_quotes_for_instrument(
                    instrument_id, client_id_str, symbol
                )

    async def _subscribe_quotes_for_instrument(
        self, instrument_id: InstrumentId, client_id_str: str, symbol: str
    ) -> None:
        """Subscribe QuoteTicks for a single instrument."""
        assert self._data_client is not None
        # Check subscription quota first (Futu only)
        if self._subscription_manager is not None:
            ok = await self._subscription_manager.subscribe(
                instrument_id, SubDataType.QUOTE
            )
            if not ok:
                logger.warning("Quote subscription quota exceeded for %s", symbol)
                self._partial_failures.append(symbol)
                return

        cmd = SubscribeQuoteTicks(
            instrument_id=instrument_id,
            client_id=ClientId(client_id_str),
            venue=None,
            command_id=UUID4(),
            ts_init=0,
        )
        try:
            await self._data_client._subscribe_quote_ticks(cmd)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to subscribe quote ticks for %s: %s", symbol, exc)
            self._partial_failures.append(symbol)
            # Roll back quota entry if we tracked it
            if self._subscription_manager is not None:
                await self._subscription_manager.unsubscribe(
                    instrument_id, SubDataType.QUOTE
                )

    async def _subscribe_bars_for_instrument(
        self, instrument_id: InstrumentId, client_id_str: str, symbol: str
    ) -> None:
        """Subscribe Bars for a single instrument."""
        assert self._data_client is not None
        bar_type_str = self._bar_type_str
        if bar_type_str is None:
            bar_type_str = f"{symbol}-1-MINUTE-LAST-EXTERNAL"

        try:
            bar_type = BarType.from_str(bar_type_str)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Invalid bar type %r for %s: %s", bar_type_str, symbol, exc)
            self._partial_failures.append(symbol)
            return

        # Check subscription quota first (Futu only)
        if self._subscription_manager is not None:
            ok = await self._subscription_manager.subscribe(
                instrument_id, SubDataType.KLINE
            )
            if not ok:
                logger.warning("Bar subscription quota exceeded for %s", symbol)
                self._partial_failures.append(symbol)
                return

        cmd = SubscribeBars(
            bar_type=bar_type,
            client_id=ClientId(client_id_str),
            venue=None,
            command_id=UUID4(),
            ts_init=0,
        )
        try:
            await self._data_client._subscribe_bars(cmd)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to subscribe bars for %s: %s", symbol, exc)
            self._partial_failures.append(symbol)
            # Roll back quota entry if we tracked it
            if self._subscription_manager is not None:
                await self._subscription_manager.unsubscribe(
                    instrument_id, SubDataType.KLINE
                )

    async def _collect_loop(self) -> None:
        """Wait for the collection period to elapse."""
        await asyncio.sleep(self._collection_period_secs)

    async def _teardown(self) -> None:
        """Disconnect client and release all resources."""
        if self._data_client is not None:
            try:
                await self._data_client._disconnect()
            except Exception:  # noqa: BLE001
                logger.exception("Error during data client disconnect")
            self._data_client = None

        if self._msgbus is not None:
            try:
                self._msgbus.deregister(
                    endpoint="DataEngine.process",
                    handler=self._on_data,
                )
            except Exception:  # noqa: BLE001
                pass
            self._msgbus = None

        self._cache = None
        self._clock = None
        self._instrument_provider = None
        self._subscription_manager = None

    # ------------------------------------------------------------------
    # Data handler
    # ------------------------------------------------------------------

    def _on_data(self, data: Any) -> None:
        """MessageBus endpoint handler — captures QuoteTicks or Bars."""
        if self._data_type == "bars" and isinstance(data, Bar):
            self._bars[data.bar_type.instrument_id] = data
        elif self._data_type == "quotes" and isinstance(data, QuoteTick):
            self._quotes[data.instrument_id] = data
