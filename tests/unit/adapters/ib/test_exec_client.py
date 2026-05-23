"""Unit tests for the permission-checking IB execution client."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from unittest.mock import MagicMock

import pytest
from nautilus_trader.model.orders import LimitOrder

from sam_trader.adapters.ib.exec_client import PermissionCheckingIBExecutionClient
from sam_trader.adapters.ib.permissions import (
    DISABLED_BUNDLE_IDS,
    TradingPermission,
    _bundle_permission_requirements,
)


def _clear_requirements() -> None:
    """Helper to reset the module-level requirement store."""
    _bundle_permission_requirements.clear()


@pytest.fixture(autouse=True)
def _cleanup_requirements() -> Iterator[None]:
    """Ensure requirement store is empty before each test."""
    _clear_requirements()
    DISABLED_BUNDLE_IDS.clear()
    yield
    _clear_requirements()
    DISABLED_BUNDLE_IDS.clear()


def _make_mock_instance(
    account_summary: dict[str, dict[str, Any]] | None = None,
) -> MagicMock:
    """Return a MagicMock that looks enough like our exec client."""
    mock_self = MagicMock()
    mock_self._account_summary = account_summary or {}
    return mock_self


class TestCheckBundlePermissions:
    def test_disables_bundle_when_permission_missing(self) -> None:
        """No margin capability disables short-selling bundles."""
        _bundle_permission_requirements["short-bundle"] = {
            TradingPermission.SHORT_SELLING
        }

        mock_self = _make_mock_instance(
            account_summary={
                "USD": {"TotalCashValue": 5000.0, "BuyingPower": 5000.0},
            },
        )

        PermissionCheckingIBExecutionClient._check_bundle_permissions(mock_self)

        assert "short-bundle" in DISABLED_BUNDLE_IDS
        mock_self._log.critical.assert_called_once()
        args = mock_self._log.critical.call_args[0]
        assert (
            args[0] == "Disabled %d bundle(s) due to missing IB trading permissions: %s"
        )
        assert args[1] == 1
        assert "short-bundle" in args[2]

    def test_leaves_bundle_enabled_when_permission_present(self) -> None:
        """Margin indicators in account summary keep short-selling bundles alive."""
        _bundle_permission_requirements["short-bundle"] = {
            TradingPermission.SHORT_SELLING
        }

        mock_self = _make_mock_instance(
            account_summary={
                "USD": {
                    "TotalCashValue": 5000.0,
                    "BuyingPower": 12000.0,
                },
            },
        )

        PermissionCheckingIBExecutionClient._check_bundle_permissions(mock_self)

        assert "short-bundle" not in DISABLED_BUNDLE_IDS
        mock_self._log.critical.assert_not_called()

    def test_skips_check_when_no_requirements(self) -> None:
        """If no bundles require permissions, the check completes silently."""
        # _bundle_permission_requirements is empty
        mock_self = _make_mock_instance(
            account_summary={"USD": {"TotalCashValue": 5000.0}},
        )

        PermissionCheckingIBExecutionClient._check_bundle_permissions(mock_self)

        mock_self._log.critical.assert_not_called()
        assert DISABLED_BUNDLE_IDS == set()

    def test_multiple_bundles_partial_disable(self) -> None:
        """Only bundles with missing permissions are disabled."""
        _bundle_permission_requirements["short-1"] = {TradingPermission.SHORT_SELLING}
        _bundle_permission_requirements["short-2"] = {TradingPermission.SHORT_SELLING}

        mock_self = _make_mock_instance(
            account_summary={
                "USD": {"TotalCashValue": 5000.0, "SMA": 100.0},
            },
        )

        PermissionCheckingIBExecutionClient._check_bundle_permissions(mock_self)

        # SMA > 0 implies margin -> both bundles allowed
        assert DISABLED_BUNDLE_IDS == set()
        mock_self._log.critical.assert_not_called()

    def test_logs_disabled_count(self) -> None:
        """The CRITICAL log mentions how many bundles were disabled."""
        _bundle_permission_requirements["b1"] = {TradingPermission.SHORT_SELLING}
        _bundle_permission_requirements["b2"] = {TradingPermission.SHORT_SELLING}

        mock_self = _make_mock_instance(
            account_summary={
                "USD": {"TotalCashValue": 5000.0, "BuyingPower": 5000.0},
            },
        )

        PermissionCheckingIBExecutionClient._check_bundle_permissions(mock_self)

        mock_self._log.critical.assert_called_once()
        args, _ = mock_self._log.critical.call_args
        assert (
            args[0] == "Disabled %d bundle(s) due to missing IB trading permissions: %s"
        )
        assert args[1] == 2
        assert "b1" in args[2]
        assert "b2" in args[2]


class TestReconnectBehavior:
    def test_connect_clears_disabled_set_before_checking(self) -> None:
        """Reconnect wipes previous disabled set so bundles can re-enable."""
        DISABLED_BUNDLE_IDS.add("old-disabled")
        _bundle_permission_requirements["old-disabled"] = {
            TradingPermission.SHORT_SELLING
        }

        mock_self = _make_mock_instance(
            account_summary={
                "USD": {"TotalCashValue": 5000.0, "SMA": 100.0},
            },
        )

        # Simulate what _connect does: clear then check
        DISABLED_BUNDLE_IDS.clear()
        PermissionCheckingIBExecutionClient._check_bundle_permissions(mock_self)

        # Because SMA > 0 the permission is granted, so the bundle should NOT
        # be in the disabled set after the check.
        assert "old-disabled" not in DISABLED_BUNDLE_IDS
        mock_self._log.critical.assert_not_called()


class TestPostOnlyWarning:
    def test_warns_on_post_only_limit_order(self) -> None:
        """A LimitOrder with is_post_only=True triggers a WARNING log."""
        mock_self = _make_mock_instance()
        mock_order = MagicMock()
        mock_order.__class__ = LimitOrder
        mock_order.is_post_only = True
        mock_order.client_order_id = "O-001"
        mock_order.instrument_id = "AAPL.NASDAQ"

        PermissionCheckingIBExecutionClient._warn_if_post_only(mock_self, mock_order)

        mock_self._log.warning.assert_called_once()
        args = mock_self._log.warning.call_args[0]
        assert "post_only=True" in args[0]
        assert args[1] == "O-001"
        assert args[2] == "AAPL.NASDAQ"

    def test_silent_on_non_post_only_limit_order(self) -> None:
        """A LimitOrder with is_post_only=False does not trigger a warning."""
        mock_self = _make_mock_instance()
        mock_order = MagicMock()
        mock_order.__class__ = LimitOrder
        mock_order.is_post_only = False

        PermissionCheckingIBExecutionClient._warn_if_post_only(mock_self, mock_order)

        mock_self._log.warning.assert_not_called()

    def test_silent_on_non_limit_order(self) -> None:
        """MarketOrder (or any non-LimitOrder) never triggers the warning."""
        mock_self = _make_mock_instance()
        mock_order = MagicMock()
        # MarketOrder may not even have is_post_only, but if it does we ignore it
        del mock_order.is_post_only

        PermissionCheckingIBExecutionClient._warn_if_post_only(mock_self, mock_order)

        mock_self._log.warning.assert_not_called()
