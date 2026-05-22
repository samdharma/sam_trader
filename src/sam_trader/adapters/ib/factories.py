"""SAM Trader factory for the permission-checking IB execution client."""

from __future__ import annotations

import asyncio
import os
from typing import Any

from nautilus_trader.adapters.interactive_brokers.client import InteractiveBrokersClient
from nautilus_trader.adapters.interactive_brokers.common import IB_VENUE
from nautilus_trader.adapters.interactive_brokers.config import (
    InteractiveBrokersExecClientConfig,
)
from nautilus_trader.adapters.interactive_brokers.factories import (
    get_cached_ib_client,
    get_cached_interactive_brokers_instrument_provider,
)
from nautilus_trader.adapters.interactive_brokers.providers import (
    InteractiveBrokersInstrumentProvider,
)
from nautilus_trader.cache.cache import Cache
from nautilus_trader.common.component import LiveClock, MessageBus
from nautilus_trader.live.factories import LiveExecClientFactory
from nautilus_trader.model.identifiers import AccountId

from sam_trader.adapters.ib.exec_client import PermissionCheckingIBExecutionClient


class SamInteractiveBrokersLiveExecClientFactory(LiveExecClientFactory):
    """Factory that creates ``PermissionCheckingIBExecutionClient``.

    The signature matches the standard Nautilus
    ``InteractiveBrokersLiveExecClientFactory`` so it can be registered
    directly with ``TradingNode.add_exec_client_factory``.

    """

    @staticmethod
    def create(  # type: ignore[override]
        loop: asyncio.AbstractEventLoop,
        name: str,
        config: InteractiveBrokersExecClientConfig,
        msgbus: MessageBus,
        cache: Cache,
        clock: LiveClock,
        **kwargs: Any,
    ) -> PermissionCheckingIBExecutionClient:
        """Create a new permission-checking IB execution client.

        Parameters
        ----------
        loop : asyncio.AbstractEventLoop
            The event loop for the client.
        name : str
            The custom client ID.
        config : InteractiveBrokersExecClientConfig
            The configuration for the client.
        msgbus : MessageBus
            The message bus for the client.
        cache : Cache
            The cache for the client.
        clock : LiveClock
            The clock for the client.
        **kwargs : Any
            Ignored – present for compatibility with the base factory
            interface.

        Returns
        -------
        PermissionCheckingIBExecutionClient

        """
        client: InteractiveBrokersClient = get_cached_ib_client(
            loop=loop,
            msgbus=msgbus,
            cache=cache,
            clock=clock,
            host=config.ibg_host,
            port=config.ibg_port,
            client_id=config.ibg_client_id,
            dockerized_gateway=config.dockerized_gateway,
            fetch_all_open_orders=config.fetch_all_open_orders,
            request_timeout_secs=config.request_timeout_secs,
        )

        provider: InteractiveBrokersInstrumentProvider = (
            get_cached_interactive_brokers_instrument_provider(
                client=client,
                clock=clock,
                config=config.instrument_provider,
            )
        )

        ib_account = config.account_id or os.environ.get("TWS_ACCOUNT")
        assert ib_account, (
            f"Must pass `{config.__class__.__name__}.account_id` "
            f"or set `TWS_ACCOUNT` env var."
        )

        account_issuer = name or IB_VENUE.value
        account_id = AccountId(f"{account_issuer}-{ib_account}")

        return PermissionCheckingIBExecutionClient(
            loop=loop,
            client=client,
            account_id=account_id,
            msgbus=msgbus,
            cache=cache,
            clock=clock,
            instrument_provider=provider,
            config=config,
            name=name,
            connection_timeout=config.connection_timeout,
            track_option_exercise_from_position_update=(
                config.track_option_exercise_from_position_update
            ),
        )
