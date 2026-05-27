"""Futu live client factories.

Provides shared client caching — one ``OpenQuoteContext`` and one
``OpenSecTradeContext`` per ``(host, port, trd_env)`` tuple.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from nautilus_trader.cache.cache import Cache
from nautilus_trader.common.component import LiveClock, MessageBus
from nautilus_trader.common.config import InstrumentProviderConfig
from nautilus_trader.live.factories import LiveDataClientFactory, LiveExecClientFactory
from nautilus_trader.model.identifiers import AccountId, Venue

from sam_trader.adapters.futu.config import FutuDataClientConfig, FutuExecClientConfig
from sam_trader.adapters.futu.connection import (
    get_cached_futu_quote_context,
    get_cached_futu_trade_context,
)
from sam_trader.adapters.futu.constants import FUTU_VENUE
from sam_trader.adapters.futu.data import FutuLiveDataClient
from sam_trader.adapters.futu.execution import FutuLiveExecutionClient
from sam_trader.adapters.futu.instrument_provider import FutuInstrumentProvider

# ---------------------------------------------------------------------------
# Shared context helpers
# ---------------------------------------------------------------------------


def _get_shared_quote_context(
    config: FutuDataClientConfig | FutuExecClientConfig,
) -> Any:
    """Return a cached ``OpenQuoteContext`` for *config*.

    One context is maintained per ``(host, port, trd_env)`` tuple so
    multiple clients (data + exec) can share the same connection.

    Parameters
    ----------
    config : FutuDataClientConfig | FutuExecClientConfig
        The adapter configuration containing host, port and trd_env.

    Returns
    -------
    OpenQuoteContext
        The shared (cached) quote context.

    """
    return get_cached_futu_quote_context(
        host=config.host,
        port=config.port,
        trade_env=config.trd_env,
    )


def _get_shared_trade_context(config: FutuExecClientConfig) -> Any:
    """Return a cached ``OpenSecTradeContext`` for *config*.

    One context is maintained per ``(host, port, trd_env)`` tuple so
    multiple execution clients can share the same connection.

    Parameters
    ----------
    config : FutuExecClientConfig
        The adapter configuration containing host, port, trd_env and
        trd_market.

    Returns
    -------
    OpenSecTradeContext
        The shared (cached) trade context.

    """
    return get_cached_futu_trade_context(
        host=config.host,
        port=config.port,
        trade_env=config.trd_env,
        trd_market=config.trd_market,
    )


# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------


class FutuLiveDataClientFactory(LiveDataClientFactory):
    """Factory for creating :class:`FutuLiveDataClient` instances."""

    @staticmethod
    def create(  # type: ignore[override]
        loop: asyncio.AbstractEventLoop,
        name: str,
        config: FutuDataClientConfig,
        msgbus: MessageBus,
        cache: Cache,
        clock: LiveClock,
    ) -> FutuLiveDataClient:
        """Create a new Futu live data client.

        Parameters
        ----------
        loop : asyncio.AbstractEventLoop
            The event loop for the client.
        name : str
            The custom client ID (unused — Futu uses config.client_id).
        config : FutuDataClientConfig
            The configuration for the client.
        msgbus : MessageBus
            The message bus for the client.
        cache : Cache
            The cache for the client.
        clock : LiveClock
            The clock for the client.

        Returns
        -------
        FutuLiveDataClient

        """
        quote_ctx = _get_shared_quote_context(config)
        load_ids = getattr(config, "load_ids", None)
        instrument_provider = FutuInstrumentProvider(
            quote_context=quote_ctx,
            config=InstrumentProviderConfig(load_ids=load_ids),
        )
        return FutuLiveDataClient(
            loop=loop,
            client=quote_ctx,
            msgbus=msgbus,
            cache=cache,
            clock=clock,
            instrument_provider=instrument_provider,
            config=config,
        )


class FutuLiveExecClientFactory(LiveExecClientFactory):
    """Factory for creating :class:`FutuLiveExecutionClient` instances."""

    @staticmethod
    def create(  # type: ignore[override]
        loop: asyncio.AbstractEventLoop,
        name: str,
        config: FutuExecClientConfig,
        msgbus: MessageBus,
        cache: Cache,
        clock: LiveClock,
    ) -> FutuLiveExecutionClient:
        """Create a new Futu live execution client.

        Parameters
        ----------
        loop : asyncio.AbstractEventLoop
            The event loop for the client.
        name : str
            The custom client ID (unused — Futu uses config.client_id).
        config : FutuExecClientConfig
            The configuration for the client.
        msgbus : MessageBus
            The message bus for the client.
        cache : Cache
            The cache for the client.
        clock : LiveClock
            The clock for the client.

        Returns
        -------
        FutuLiveExecutionClient

        """
        quote_ctx = _get_shared_quote_context(config)
        trade_ctx = _get_shared_trade_context(config)
        load_ids = getattr(config, "load_ids", None)
        instrument_provider = FutuInstrumentProvider(
            quote_context=quote_ctx,
            config=InstrumentProviderConfig(load_ids=load_ids),
        )
        _futu_account = os.environ.get("FUTU_ACCOUNT_ID", "").strip()
        if _futu_account:
            account_id = AccountId(f"FUTU-{_futu_account}")
        else:
            account_id = AccountId(f"FUTU-{config.client_id}")
        # Use a synthetic venue per market so Nautilus can register multiple
        # Futu exec clients simultaneously without venue collision.
        venue = (
            FUTU_VENUE
            if config.trd_market == "US"
            else Venue(f"FUTU_{config.trd_market}")
        )
        return FutuLiveExecutionClient(
            loop=loop,
            client=trade_ctx,
            msgbus=msgbus,
            cache=cache,
            clock=clock,
            instrument_provider=instrument_provider,
            config=config,
            account_id=account_id,
            venue=venue,
        )
