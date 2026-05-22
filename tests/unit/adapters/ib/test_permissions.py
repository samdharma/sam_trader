"""Unit tests for IB permission checking."""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import MagicMock

import pytest
from nautilus_trader.trading.config import ImportableStrategyConfig

from sam_trader.adapters.ib.permissions import (
    DISABLED_BUNDLE_IDS,
    PERMISSION_REGISTRY,
    TradingPermission,
    disable_bundles_missing_permissions,
    get_bundle_permission_requirements,
    is_bundle_disabled,
    query_ib_permissions,
    required_permissions_for_bundles,
    set_bundle_permission_requirements,
)


def _make_bundle(
    strategy_path: str,
    bundle_id: str = "test-bundle",
    instrument_id: str = "AAPL.NASDAQ",
) -> ImportableStrategyConfig:
    """Helper to build an ImportableStrategyConfig for tests."""
    return ImportableStrategyConfig(
        strategy_path=strategy_path,
        config_path=f"{strategy_path}Config",
        config={
            "bundle_id": bundle_id,
            "instrument_id": instrument_id,
            "venue": "IB",
        },
    )


class TestRequiredPermissionsForBundles:
    def test_maps_known_strategy(self) -> None:
        """A bundle using a registered strategy path returns its permissions."""
        bundle = _make_bundle("sam_trader.strategies.momentum:MomentumStrategy")
        result = required_permissions_for_bundles([bundle])
        assert result == {"test-bundle": {TradingPermission.SHORT_SELLING}}

    def test_unknown_strategy_returns_empty(self) -> None:
        """An unregistered strategy path yields no permission requirements."""
        bundle = _make_bundle("sam_trader.strategies.unknown:UnknownStrategy")
        result = required_permissions_for_bundles([bundle])
        assert result == {}

    def test_mixed_strategies(self) -> None:
        """Only bundles with registered strategies appear in the result."""
        b1 = _make_bundle("sam_trader.strategies.momentum:MomentumStrategy", "m1")
        b2 = _make_bundle("sam_trader.strategies.orb:OrbStrategy", "o1")
        result = required_permissions_for_bundles([b1, b2])
        assert result == {"m1": {TradingPermission.SHORT_SELLING}}


class TestSetAndGetBundlePermissionRequirements:
    def test_store_and_retrieve(self) -> None:
        """``set_bundle_permission_requirements`` populates the internal store."""
        bundle = _make_bundle("sam_trader.strategies.momentum:MomentumStrategy")
        set_bundle_permission_requirements([bundle])
        assert get_bundle_permission_requirements() == {
            "test-bundle": {TradingPermission.SHORT_SELLING},
        }

    def test_clear_on_replacement(self) -> None:
        """Calling the setter twice replaces the previous contents."""
        set_bundle_permission_requirements(
            [_make_bundle("sam_trader.strategies.momentum:MomentumStrategy", "b1")]
        )
        set_bundle_permission_requirements([])
        assert get_bundle_permission_requirements() == {}


class TestQueryIBPermissions:
    def test_margin_via_sma(self) -> None:
        """SMA > 0 implies margin account -> short selling permitted."""
        summary = {"USD": {"SMA": 1000.0, "TotalCashValue": 5000.0}}
        assert query_ib_permissions(summary) == {TradingPermission.SHORT_SELLING}

    def test_margin_via_buying_power(self) -> None:
        """BuyingPower > TotalCashValue implies margin -> short selling permitted."""
        summary = {"USD": {"BuyingPower": 12000.0, "TotalCashValue": 5000.0}}
        assert query_ib_permissions(summary) == {TradingPermission.SHORT_SELLING}

    def test_margin_via_leverage(self) -> None:
        """Leverage > 1.0 implies margin -> short selling permitted."""
        summary = {"USD": {"Leverage": 2.0, "TotalCashValue": 5000.0}}
        assert query_ib_permissions(summary) == {TradingPermission.SHORT_SELLING}

    def test_cash_account_no_permissions(self) -> None:
        """No margin indicators -> no short selling permission."""
        summary = {"USD": {"TotalCashValue": 5000.0, "BuyingPower": 5000.0}}
        assert query_ib_permissions(summary) == set()

    def test_cash_account_low_buying_power(self) -> None:
        """BuyingPower equal to cash (within tolerance) -> no short selling."""
        summary = {"USD": {"TotalCashValue": 5000.0, "BuyingPower": 5050.0}}
        assert query_ib_permissions(summary) == set()

    def test_empty_summary(self) -> None:
        """Empty account summary yields no permissions."""
        assert query_ib_permissions({}) == set()

    def test_ignores_empty_currency(self) -> None:
        """Tags under empty currency string are skipped for the heuristic."""
        summary: dict[str, dict[str, Any]] = {
            "": {"AccountType": "Individual"},
            "USD": {"SMA": 100.0},
        }
        assert query_ib_permissions(summary) == {TradingPermission.SHORT_SELLING}

    def test_none_values_handled(self) -> None:
        """Missing or None tags do not crash the heuristic."""
        summary: dict[str, dict[str, Any]] = {"USD": {"TotalCashValue": None}}
        assert query_ib_permissions(summary) == set()

    def test_string_values_handled(self) -> None:
        """Non-numeric string tags are treated as None."""
        summary: dict[str, dict[str, Any]] = {
            "USD": {"SMA": "n/a", "TotalCashValue": "5000"},
        }
        assert query_ib_permissions(summary) == set()

    def test_multiple_currencies_short_circuit(self) -> None:
        """If any currency shows margin, permission is granted immediately."""
        summary = {
            "USD": {"TotalCashValue": 5000.0},
            "HKD": {"SMA": 100.0},
        }
        assert query_ib_permissions(summary) == {TradingPermission.SHORT_SELLING}


class TestDisableBundlesMissingPermissions:
    def test_disables_bundle_and_logs_critical(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Missing permissions trigger CRITICAL log and add to disabled set."""
        DISABLED_BUNDLE_IDS.clear()
        requirements = {"b1": {TradingPermission.SHORT_SELLING}}
        granted: set[TradingPermission] = set()

        with caplog.at_level(
            logging.CRITICAL, logger="sam_trader.adapters.ib.permissions"
        ):
            disabled = disable_bundles_missing_permissions(requirements, granted)

        assert disabled == ["b1"]
        assert "b1" in DISABLED_BUNDLE_IDS
        assert "CRITICAL" in caplog.text
        assert "SHORT_SELLING" in caplog.text

    def test_no_action_when_all_granted(self, caplog: pytest.LogCaptureFixture) -> None:
        """When all required permissions are granted, nothing is disabled."""
        DISABLED_BUNDLE_IDS.clear()
        requirements = {"b1": {TradingPermission.SHORT_SELLING}}
        granted = {TradingPermission.SHORT_SELLING}

        with caplog.at_level(
            logging.CRITICAL, logger="sam_trader.adapters.ib.permissions"
        ):
            disabled = disable_bundles_missing_permissions(requirements, granted)

        assert disabled == []
        assert "b1" not in DISABLED_BUNDLE_IDS
        assert caplog.text == ""

    def test_partial_permissions(self, caplog: pytest.LogCaptureFixture) -> None:
        """Only the missing subset is reported."""
        DISABLED_BUNDLE_IDS.clear()
        # If we had multiple permissions, only the missing ones would be logged
        requirements = {"b1": {TradingPermission.SHORT_SELLING}}
        granted: set[TradingPermission] = set()

        with caplog.at_level(
            logging.CRITICAL, logger="sam_trader.adapters.ib.permissions"
        ):
            disable_bundles_missing_permissions(requirements, granted)

        assert "b1" in DISABLED_BUNDLE_IDS


class TestPermissionRegistry:
    def test_momentum_strategy_requires_short(self) -> None:
        """The registry maps MomentumStrategy to SHORT_SELLING."""
        assert PERMISSION_REGISTRY.get(
            "sam_trader.strategies.momentum:MomentumStrategy"
        ) == {TradingPermission.SHORT_SELLING}


class TestIsBundleDisabled:
    def test_returns_true_when_disabled(self) -> None:
        DISABLED_BUNDLE_IDS.add("disabled-bundle")
        assert is_bundle_disabled("disabled-bundle") is True

    def test_returns_false_when_not_disabled(self) -> None:
        DISABLED_BUNDLE_IDS.discard("unknown-bundle")
        assert is_bundle_disabled("unknown-bundle") is False


class TestPermissionGuardMixin:
    def test_blocks_submit_order_when_disabled(self) -> None:
        from sam_trader.adapters.ib.permissions import PermissionGuardMixin

        DISABLED_BUNDLE_IDS.add("guard-test")

        class MockConfig:
            bundle_id = "guard-test"

        class BaseStrat:
            def submit_order(
                self, order, position_id=None, client_id=None, params=None
            ):
                return "submitted"

            def submit_order_list(
                self, order_list, position_id=None, client_id=None, params=None
            ):
                return "submitted-list"

        class MockStrat(PermissionGuardMixin, BaseStrat):
            def __init__(self):
                self.config = MockConfig()
                self.log = MagicMock()

        strat = MockStrat()
        result = strat.submit_order("fake-order")

        assert result is None
        strat.log.warning.assert_called_once()
        args, _ = strat.log.warning.call_args
        assert "guard-test" in args[1]

    def test_allows_submit_order_when_enabled(self) -> None:
        from sam_trader.adapters.ib.permissions import PermissionGuardMixin

        DISABLED_BUNDLE_IDS.discard("guard-test")

        class MockConfig:
            bundle_id = "guard-test"

        class BaseStrat:
            def submit_order(
                self, order, position_id=None, client_id=None, params=None
            ):
                return "submitted"

        class MockStrat(PermissionGuardMixin, BaseStrat):
            def __init__(self):
                self.config = MockConfig()
                self.log = MagicMock()

        strat = MockStrat()
        result = strat.submit_order("fake-order")

        assert result == "submitted"
        strat.log.warning.assert_not_called()

    def test_blocks_submit_order_list_when_disabled(self) -> None:
        from sam_trader.adapters.ib.permissions import PermissionGuardMixin

        DISABLED_BUNDLE_IDS.add("guard-list")

        class MockConfig:
            bundle_id = "guard-list"

        class BaseStrat:
            def submit_order(
                self, order, position_id=None, client_id=None, params=None
            ):
                return "submitted"

            def submit_order_list(
                self, order_list, position_id=None, client_id=None, params=None
            ):
                return "submitted-list"

        class MockStrat(PermissionGuardMixin, BaseStrat):
            def __init__(self):
                self.config = MockConfig()
                self.log = MagicMock()

        strat = MockStrat()
        result = strat.submit_order_list("fake-list")

        assert result is None
        strat.log.warning.assert_called_once()
