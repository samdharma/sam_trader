"""Extended IB execution client with pre-flight permission checking."""

from __future__ import annotations

import asyncio
from typing import Any

from nautilus_trader.adapters.interactive_brokers.client import InteractiveBrokersClient
from nautilus_trader.adapters.interactive_brokers.execution import (
    InteractiveBrokersExecutionClient,
)
from nautilus_trader.adapters.interactive_brokers.providers import (
    InteractiveBrokersInstrumentProvider,
)
from nautilus_trader.cache.cache import Cache
from nautilus_trader.common.component import LiveClock, MessageBus
from nautilus_trader.model.identifiers import AccountId

from sam_trader.adapters.ib.permissions import (
    disable_bundles_missing_permissions,
    get_bundle_permission_requirements,
    query_ib_permissions,
)


class PermissionCheckingIBExecutionClient(InteractiveBrokersExecutionClient):
    """InteractiveBrokersExecutionClient that validates trading permissions on connect.

    After the parent class finishes its handshake (account summary loaded,
    positions reconciled, etc.) we inspect the account summary to infer
    which trading permissions are available.  If any active bundle requires
    a permission that is missing, a CRITICAL log is emitted and the bundle
    ID is added to the module-level ``DISABLED_BUNDLE_IDS`` registry.

    Parameters
    ----------
    All parameters from ``InteractiveBrokersExecutionClient`` are accepted.

    """

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        client: InteractiveBrokersClient,
        account_id: AccountId,
        msgbus: MessageBus,
        cache: Cache,
        clock: LiveClock,
        instrument_provider: InteractiveBrokersInstrumentProvider,
        config: Any,
        name: str | None = None,
        connection_timeout: int = 300,
        track_option_exercise_from_position_update: bool = False,
    ) -> None:
        # Pass everything through to the Nautilus parent unchanged.
        super().__init__(
            loop=loop,
            client=client,
            account_id=account_id,
            msgbus=msgbus,
            cache=cache,
            clock=clock,
            instrument_provider=instrument_provider,
            config=config,
            name=name,
            connection_timeout=connection_timeout,
            track_option_exercise_from_position_update=(
                track_option_exercise_from_position_update
            ),
        )

    async def _connect(self) -> None:
        """Connect, then run permission checks against loaded bundles.

        On reconnect we clear the previous disabled set so that bundles can be
        re-enabled if the account permissions have changed.
        """
        from sam_trader.adapters.ib.permissions import DISABLED_BUNDLE_IDS

        await super()._connect()
        DISABLED_BUNDLE_IDS.clear()
        self._check_bundle_permissions()

    def _check_bundle_permissions(self) -> None:
        """Inspect account summary and disable bundles that need missing permissions."""
        bundle_requirements = get_bundle_permission_requirements()
        if not bundle_requirements:
            return

        granted = query_ib_permissions(self._account_summary)
        disabled = disable_bundles_missing_permissions(
            bundle_requirements,
            granted,
        )

        if disabled:
            self._log.critical(
                "Disabled %d bundle(s) due to missing IB trading permissions: %s",
                len(disabled),
                disabled,
            )
