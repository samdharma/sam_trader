"""IB account trading permission checks.

On IB exec client connect we inspect account summary data to infer which
trading permissions are available.  If an active bundle requires a permission
that the account does not have, the bundle is logged as CRITICAL and its
``bundle_id`` is added to ``DISABLED_BUNDLE_IDS`` so that strategies can
refuse to trade.

> v2 Post-Mortem: 189 short orders were rejected over 9 hours because the
> paper account lacked short-selling permissions.  This module prevents that
> scenario.
"""

from __future__ import annotations

import logging
from enum import Enum, auto
from typing import Any

from nautilus_trader.trading.config import ImportableStrategyConfig

logger = logging.getLogger(__name__)


class TradingPermission(Enum):
    """Trading permissions that may be required by a strategy bundle."""

    SHORT_SELLING = auto()


# Registry mapping known strategy paths to the permissions they require.
# Populated now for Phase-7 strategies; entries are no-ops until those
# strategies exist.
PERMISSION_REGISTRY: dict[str, set[TradingPermission]] = {
    "sam_trader.strategies.momentum:MomentumStrategy": {
        TradingPermission.SHORT_SELLING
    },
}

# Module-level set of bundle IDs that have been disabled for the current
# session because the IB account lacks required permissions.
DISABLED_BUNDLE_IDS: set[str] = set()

# Internal mutable store populated by ``set_bundle_permission_requirements``.
_bundle_permission_requirements: dict[str, set[TradingPermission]] = {}


def required_permissions_for_bundles(
    bundles: list[ImportableStrategyConfig],
) -> dict[str, set[TradingPermission]]:
    """Map bundle IDs to the trading permissions they require.

    Parameters
    ----------
    bundles : list[ImportableStrategyConfig]
        Loaded strategy bundles.

    Returns
    -------
    dict[str, set[TradingPermission]]
        ``{bundle_id: {permission, ...}, ...}``

    """
    result: dict[str, set[TradingPermission]] = {}
    for bundle in bundles:
        bundle_id = bundle.config.get("bundle_id", "unknown")
        strategy_path = bundle.strategy_path
        required = PERMISSION_REGISTRY.get(strategy_path, set())
        if required:
            result[bundle_id] = required.copy()
    return result


def set_bundle_permission_requirements(
    bundles: list[ImportableStrategyConfig],
) -> None:
    """Compute and store permission requirements for the given bundles.

    This should be called once in ``main.py`` after bundles are loaded and
    before the TradingNode is built.

    Parameters
    ----------
    bundles : list[ImportableStrategyConfig]
        Active strategy bundles.

    """
    _bundle_permission_requirements.clear()
    _bundle_permission_requirements.update(required_permissions_for_bundles(bundles))
    if _bundle_permission_requirements:
        logger.debug(
            "IB permission requirements: %s",
            {
                k: [p.name for p in v]
                for k, v in _bundle_permission_requirements.items()
            },
        )


def get_bundle_permission_requirements() -> dict[str, set[TradingPermission]]:
    """Return a shallow copy of the current bundle permission requirements."""
    return _bundle_permission_requirements.copy()


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def query_ib_permissions(
    account_summary: dict[str, dict[str, Any]],
) -> set[TradingPermission]:
    """Infer IB trading permissions from loaded account-summary data.

    Heuristic
    ---------
    A margin account is required for short selling.  We detect margin
    capability through any of the following indicators in the IB account
    summary:

    * ``SMA`` > 0  (Special Memorandum Account – only tracked for margin)
    * ``BuyingPower`` > ``TotalCashValue`` × 1.01
    * ``Leverage`` > 1.0

    If none of the indicators are present we conservatively assume the
    account does **not** support short selling.

    Parameters
    ----------
    account_summary : dict[str, dict[str, Any]]
        Raw account summary dict as stored by
        ``InteractiveBrokersExecutionClient._account_summary``.

    Returns
    -------
    set[TradingPermission]
        Permissions inferred to be granted by the account.

    """
    permissions: set[TradingPermission] = set()

    for currency, tags in account_summary.items():
        if not currency:
            # Tags with empty currency (e.g. AccountType) are not
            # currency-specific and do not help with the margin heuristic.
            continue

        total_cash = _to_float(tags.get("TotalCashValue"))
        buying_power = _to_float(tags.get("BuyingPower"))
        sma = _to_float(tags.get("SMA"))
        leverage = _to_float(tags.get("Leverage"))

        is_margin = False
        if sma is not None and sma > 0:
            is_margin = True
        elif (
            buying_power is not None
            and total_cash is not None
            and buying_power > total_cash * 1.01
        ):
            is_margin = True
        elif leverage is not None and leverage > 1.0:
            is_margin = True

        if is_margin:
            permissions.add(TradingPermission.SHORT_SELLING)
            break

    return permissions


def disable_bundles_missing_permissions(
    bundle_requirements: dict[str, set[TradingPermission]],
    granted_permissions: set[TradingPermission],
) -> list[str]:
    """Disable bundles that require permissions the account does not have.

    Bundle IDs are added to ``DISABLED_BUNDLE_IDS`` and a CRITICAL log is
    emitted for each disabled bundle.

    Parameters
    ----------
    bundle_requirements : dict[str, set[TradingPermission]]
        Mapping from bundle ID to required permissions.
    granted_permissions : set[TradingPermission]
        Permissions the account is inferred to support.

    Returns
    -------
    list[str]
        IDs of bundles that were disabled.

    """
    disabled: list[str] = []
    for bundle_id, required in bundle_requirements.items():
        missing = required - granted_permissions
        if missing:
            logger.critical(
                "Bundle %s requires IB permissions %s but account only grants %s. "
                "Disabling for this session.",
                bundle_id,
                [p.name for p in sorted(missing, key=lambda x: x.name)],
                [p.name for p in sorted(granted_permissions, key=lambda x: x.name)],
            )
            DISABLED_BUNDLE_IDS.add(bundle_id)
            disabled.append(bundle_id)
    return disabled


def is_bundle_disabled(bundle_id: str) -> bool:
    """Return ``True`` if the bundle has been disabled for this session."""
    return bundle_id in DISABLED_BUNDLE_IDS


class PermissionGuardMixin:
    """Mixin for Nautilus strategies that refuse to trade when their bundle is disabled.

    The strategy remains instantiated for observability, but ``submit_order``
    and ``submit_order_list`` become no-ops while the bundle is in
    ``DISABLED_BUNDLE_IDS``.
    """

    def submit_order(
        self, order, position_id=None, client_id=None, params=None
    ):  # type: ignore[override]
        bundle_id = getattr(self.config, "bundle_id", None)
        if bundle_id and is_bundle_disabled(bundle_id):
            self.log.warning(
                "Bundle %s is disabled (missing IB permissions); order rejected.",
                bundle_id,
            )
            return
        return super().submit_order(order, position_id, client_id, params)

    def submit_order_list(
        self, order_list, position_id=None, client_id=None, params=None
    ):  # type: ignore[override]
        bundle_id = getattr(self.config, "bundle_id", None)
        if bundle_id and is_bundle_disabled(bundle_id):
            self.log.warning(
                "Bundle %s is disabled (missing IB permissions); order list rejected.",
                bundle_id,
            )
            return
        return super().submit_order_list(order_list, position_id, client_id, params)
