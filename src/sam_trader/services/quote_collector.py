"""QuoteCollectionService — reusable Nautilus data client wrapper.

A lightweight bridge between Nautilus data clients and sam-services.
Connects to a broker (Futu or IB), subscribes quote ticks for a watchlist,
collects them for a fixed duration, and returns the latest tick per symbol.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from nautilus_trader.cache.cache import Cache
from nautilus_trader.common.component import LiveClock, MessageBus
from nautilus_trader.common.config import InstrumentProviderConfig
from nautilus_trader.common.providers import InstrumentProvider
from nautilus_trader.core.uuid import UUID4
from nautilus_trader.data.messages import SubscribeQuoteTicks
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.identifiers import ClientId, InstrumentId, TraderId

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
    partial_failures: list[str] = field(default_factory=list)
    elapsed_secs: float = 0.0


class QuoteCollectionService:
    """Reusable Nautilus data client wrapper for quote collection.

    Parameters
    ----------
    broker : str
        ``"FUTU"`` or ``"IB"``.
    host : str
        Broker gateway host.
    port : int
        Broker gateway port.
    watchlist : list[str]
        Nautilus instrument ID strings (e.g. ``["TSLA.NASDAQ", "AAPL.NASDAQ"]``).
    collection_period_secs : int, default 60
        How long to collect quotes after successful subscription.
    connection_timeout_secs : int, default 10
        Maximum seconds to wait for the broker connection.

    """

    def __init__(
        self,
        broker: str,
        host: str,
        port: int,
        watchlist: list[str],
        collection_period_secs: int = 60,
        connection_timeout_secs: int = 10,
    ) -> None:
        self._broker = broker.upper()
        self._host = host
        self._port = port
        self._watchlist = list(watchlist)
        self._collection_period_secs = collection_period_secs
        self._connection_timeout_secs = connection_timeout_secs

        # Lightweight in-process infrastructure
        self._msgbus: MessageBus | None = None
        self._cache: Cache | None = None
        self._clock: LiveClock | None = None
        self._instrument_provider: InstrumentProvider | None = None
        self._data_client: FutuLiveDataClient | None = None
        self._subscription_manager: FutuSubscriptionManager | None = None

        # Collection state
        self._quotes: dict[InstrumentId, QuoteTick] = {}
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
            If the broker is not supported.
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
            # IB support deferred — no IB data client wrapper exists yet in
            # sam-services.  The architecture is identical; only the client
            # factory changes.
            raise NotImplementedError(
                "IB broker not yet supported by QuoteCollectionService"
            )

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

        # Register our collector on the message bus so QuoteTicks
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
        """Subscribe QuoteTicks for every symbol in the watchlist."""
        if self._data_client is None:
            return

        for symbol in self._watchlist:
            try:
                instrument_id = InstrumentId.from_str(symbol)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Invalid instrument ID %r: %s", symbol, exc)
                self._partial_failures.append(symbol)
                continue

            # Check subscription quota first (Futu only)
            if self._subscription_manager is not None:
                ok = await self._subscription_manager.subscribe(
                    instrument_id, SubDataType.QUOTE
                )
                if not ok:
                    logger.warning("Quote subscription quota exceeded for %s", symbol)
                    self._partial_failures.append(symbol)
                    continue

            cmd = SubscribeQuoteTicks(
                instrument_id=instrument_id,
                client_id=ClientId("FUTU-1"),
                venue=None,
                command_id=UUID4(),
                ts_init=0,
            )
            try:
                await self._data_client._subscribe_quote_ticks(cmd)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Failed to subscribe quote ticks for %s: %s", symbol, exc
                )
                self._partial_failures.append(symbol)
                # Roll back quota entry if we tracked it
                if self._subscription_manager is not None:
                    await self._subscription_manager.unsubscribe(
                        instrument_id, SubDataType.QUOTE
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
        """MessageBus endpoint handler — captures QuoteTicks."""
        if isinstance(data, QuoteTick):
            self._quotes[data.instrument_id] = data
