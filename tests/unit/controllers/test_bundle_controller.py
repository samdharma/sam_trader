"""Unit tests for BundleController."""

from __future__ import annotations

import asyncio
import json
import threading
from contextlib import contextmanager
from typing import Any, Generator
from unittest.mock import MagicMock, patch

import pytest

from sam_trader.controllers.bundle_controller import (
    CHANNEL_BUNDLE_LOAD,
    CHANNEL_BUNDLE_UNLOAD,
    BundleController,
    BundleControllerConfig,
)

# ── Helpers ────────────────────────────────────────────────────────


class _FakeStrategy:
    """Minimal fake strategy for trader.strategies() return."""

    def __init__(self, sid: str) -> None:
        class _FakeStrategyId:
            value = sid

        self.id = _FakeStrategyId()


def _make_config(**overrides: Any) -> BundleControllerConfig:
    defaults: dict[str, Any] = {
        "redis_host": "127.0.0.1",
        "redis_port": 6379,
        "redis_password": "",
        "bundles_path": "config/bundles.yaml",
        "market": "US",
    }
    defaults.update(overrides)
    return BundleControllerConfig(**defaults)


@contextmanager
def _patch_trader(controller: BundleController) -> Generator[MagicMock, None, None]:
    """Temporarily patch the controller's trader reference."""
    saved = controller._trader
    fake = MagicMock()
    fake.get_event_loop.return_value = None  # synchronous dispatch
    controller._trader = fake
    try:
        yield fake
    finally:
        controller._trader = saved


def _sample_bundle_dict(**overrides: Any) -> dict[str, Any]:
    bundle: dict[str, Any] = {
        "id": "tsla-orb-15m-us",
        "enabled": True,
        "venue": "FUTU",
        "market": "US",
        "strategy": {
            "path": "sam_trader.strategies.orb:OrbStrategy",
            "config": {
                "instrument_id": "TSLA.NASDAQ",
                "bar_type": "TSLA.NASDAQ-15-MINUTE-LAST-EXTERNAL",
                "trade_size": 5,
            },
        },
        "bracket": {
            "stop_loss_ticks": 10,
            "take_profit_ticks": 30,
        },
        "risk": {
            "max_position": 500,
            "max_daily_loss": 1000,
        },
    }
    bundle.update(overrides)
    return bundle


# ── Tests: Config ──────────────────────────────────────────────────


class TestBundleControllerConfig:
    """Tests for BundleControllerConfig."""

    def test_defaults(self) -> None:
        cfg = BundleControllerConfig()
        assert cfg.redis_host == ""
        assert cfg.redis_port == 6379
        assert cfg.redis_password == ""
        assert cfg.bundles_path == "config/bundles.yaml"
        assert cfg.market == ""

    def test_custom(self) -> None:
        cfg = BundleControllerConfig(
            redis_host="redis.local",
            redis_port=6380,
            redis_password="secret",
            bundles_path="/opt/bundles.yaml",
            market="HK",
        )
        assert cfg.redis_host == "redis.local"
        assert cfg.redis_port == 6380
        assert cfg.market == "HK"

    def test_frozen(self) -> None:
        cfg = BundleControllerConfig(market="US")
        with pytest.raises(Exception):
            cfg.market = "HK"  # type: ignore[misc]


# ── Tests: load_bundle ─────────────────────────────────────────────


class TestLoadBundle:
    """Tests for BundleController.load_bundle()."""

    def test_load_creates_strategy_via_config(self) -> None:
        ctrl = BundleController(_make_config())
        with _patch_trader(ctrl):
            ctrl.load_bundle(_sample_bundle_dict())

    @patch.object(BundleController, "create_strategy_from_config")
    def test_load_converts_dict_to_config(self, mock_create: MagicMock) -> None:
        ctrl = BundleController(_make_config())
        with _patch_trader(ctrl):
            ctrl.load_bundle(_sample_bundle_dict())
        mock_create.assert_called_once()
        cfg = mock_create.call_args[0][0]  # positional arg: strategy_config
        assert cfg.config["instrument_id"] == "TSLA.NASDAQ"
        assert cfg.config["bundle_id"] == "tsla-orb-15m-us"
        assert mock_create.call_args.kwargs["start"] is True

    @patch.object(BundleController, "create_strategy_from_config")
    def test_load_starts_strategy(self, mock_create: MagicMock) -> None:
        ctrl = BundleController(_make_config())
        with _patch_trader(ctrl):
            ctrl.load_bundle(_sample_bundle_dict())
        assert mock_create.call_args.kwargs.get("start") is True

    @patch.object(BundleController, "create_strategy_from_config")
    def test_load_merges_bracket_risk(self, mock_create: MagicMock) -> None:
        ctrl = BundleController(_make_config())
        with _patch_trader(ctrl):
            ctrl.load_bundle(_sample_bundle_dict())
        cfg = mock_create.call_args[0][0]  # positional: strategy_config
        assert cfg.config["stop_loss_ticks"] == 10
        assert cfg.config["take_profit_ticks"] == 30
        assert cfg.config["max_position"] == 500

    @patch.object(BundleController, "create_strategy_from_config")
    def test_load_preserves_venue_field(self, mock_create: MagicMock) -> None:
        ctrl = BundleController(_make_config())
        with _patch_trader(ctrl):
            ctrl.load_bundle(_sample_bundle_dict(venue="IB"))
        cfg = mock_create.call_args[0][0]  # positional: strategy_config
        assert cfg.config["venue"] == "IB"

    @patch.object(BundleController, "create_strategy_from_config")
    def test_load_preserves_market_field(self, mock_create: MagicMock) -> None:
        ctrl = BundleController(_make_config())
        with _patch_trader(ctrl):
            ctrl.load_bundle(_sample_bundle_dict(market="HK"))
        cfg = mock_create.call_args[0][0]  # positional: strategy_config
        assert cfg.config["market"] == "HK"


# ── Tests: unload_bundle ───────────────────────────────────────────


class TestUnloadBundle:
    """Tests for BundleController.unload_bundle()."""

    @patch.object(BundleController, "remove_strategy_from_id")
    def test_unload_calls_remove(self, mock_remove: MagicMock) -> None:
        ctrl = BundleController(_make_config())
        with _patch_trader(ctrl):
            ctrl.unload_bundle("OrbStrategy-0")
        mock_remove.assert_called_once_with("OrbStrategy-0")

    @patch.object(BundleController, "remove_strategy_from_id")
    def test_unload_with_different_id(self, mock_remove: MagicMock) -> None:
        ctrl = BundleController(_make_config())
        with _patch_trader(ctrl):
            ctrl.unload_bundle("MomentumStrategy-3")
        mock_remove.assert_called_once_with("MomentumStrategy-3")

    @patch.object(BundleController, "remove_strategy_from_id")
    def test_unload_empty_string(self, mock_remove: MagicMock) -> None:
        ctrl = BundleController(_make_config())
        with _patch_trader(ctrl):
            ctrl.unload_bundle("")
        mock_remove.assert_called_once_with("")


# ── Tests: reload_market ───────────────────────────────────────────


class TestReloadMarket:
    """Tests for BundleController.reload_market()."""

    @patch.object(BundleController, "remove_strategy_from_id")
    @patch.object(BundleController, "create_strategy_from_config")
    @patch("sam_trader.bundle_loader.load_bundles")
    def test_reload_market_unloads_all_then_loads_target(
        self,
        mock_load_bundles: MagicMock,
        mock_create: MagicMock,
        mock_remove: MagicMock,
    ) -> None:
        from sam_trader.bundle_loader import _load_bundle

        existing_hk = _sample_bundle_dict(
            id="tencent-orb-hk",
            market="HK",
            strategy={
                "path": "sam_trader.strategies.orb:OrbStrategy",
                "config": {"instrument_id": "00700.HKEX"},
            },
        )
        hk_cfg = _load_bundle(existing_hk)
        mock_load_bundles.return_value = [hk_cfg]

        ctrl = BundleController(_make_config(market="US"))
        with _patch_trader(ctrl) as fake:
            fake.strategies.return_value = [
                _FakeStrategy("OrbStrategy-0"),
                _FakeStrategy("OrbStrategy-1"),
            ]
            ctrl.reload_market("HK")

        assert mock_remove.call_count == 2
        mock_create.assert_called_once()
        assert ctrl._active_market == "HK"

    @patch.object(BundleController, "remove_strategy_from_id")
    @patch.object(BundleController, "create_strategy_from_config")
    @patch("sam_trader.bundle_loader.load_bundles")
    def test_reload_market_no_bundles_for_target(
        self,
        mock_load_bundles: MagicMock,
        mock_create: MagicMock,
        mock_remove: MagicMock,
    ) -> None:
        from sam_trader.bundle_loader import _load_bundle

        us_bundle = _load_bundle(_sample_bundle_dict(market="US"))
        mock_load_bundles.return_value = [us_bundle]

        ctrl = BundleController(_make_config(market="US"))
        with _patch_trader(ctrl) as fake:
            fake.strategies.return_value = [_FakeStrategy("OrbStrategy-0")]
            ctrl.reload_market("HK")

        mock_remove.assert_called_once()
        mock_create.assert_not_called()

    @patch.object(BundleController, "create_strategy_from_config")
    @patch("sam_trader.bundle_loader.load_bundles")
    def test_reload_bundles_load_error_graceful(
        self,
        mock_load_bundles: MagicMock,
        mock_create: MagicMock,
    ) -> None:
        mock_load_bundles.side_effect = RuntimeError("file not found")

        ctrl = BundleController(_make_config())
        with _patch_trader(ctrl) as fake:
            fake.strategies.return_value = []
            ctrl.reload_market("HK")

        mock_create.assert_not_called()

    @patch.object(BundleController, "remove_strategy_from_id")
    @patch.object(BundleController, "create_strategy_from_config")
    @patch("sam_trader.bundle_loader.load_bundles")
    def test_reload_market_no_strategies_running(
        self,
        mock_load_bundles: MagicMock,
        mock_create: MagicMock,
        mock_remove: MagicMock,
    ) -> None:
        from sam_trader.bundle_loader import _load_bundle

        hk = _load_bundle(_sample_bundle_dict(id="hk-bundle", market="HK"))
        mock_load_bundles.return_value = [hk]

        ctrl = BundleController(_make_config(market="HK"))
        with _patch_trader(ctrl) as fake:
            fake.strategies.return_value = []
            ctrl.reload_market("HK")

        mock_remove.assert_not_called()
        mock_create.assert_called_once()
        assert ctrl._active_market == "HK"


# ── Tests: Redis Dispatch ──────────────────────────────────────────


class TestRedisDispatch:
    """Tests for Redis message handling."""

    @patch.object(BundleController, "load_bundle")
    def test_handle_load_via_dispatch(self, mock_load: MagicMock) -> None:
        ctrl = BundleController(_make_config())
        with _patch_trader(ctrl):
            payload = json.dumps(_sample_bundle_dict())
            ctrl._handle_redis_message(CHANNEL_BUNDLE_LOAD, payload)
        mock_load.assert_called_once()
        loaded = mock_load.call_args[0][0]
        assert loaded["id"] == "tsla-orb-15m-us"

    @patch.object(BundleController, "unload_bundle")
    def test_handle_unload_via_dispatch(self, mock_unload: MagicMock) -> None:
        ctrl = BundleController(_make_config())
        with _patch_trader(ctrl):
            payload = json.dumps({"strategy_id": "OrbStrategy-0"})
            ctrl._handle_redis_message(CHANNEL_BUNDLE_UNLOAD, payload)
        mock_unload.assert_called_once_with("OrbStrategy-0")

    @patch.object(BundleController, "unload_bundle")
    def test_handle_unload_missing_strategy_id(self, mock_unload: MagicMock) -> None:
        ctrl = BundleController(_make_config())
        with _patch_trader(ctrl):
            ctrl._handle_redis_message(CHANNEL_BUNDLE_UNLOAD, json.dumps({}))
        mock_unload.assert_not_called()

    def test_handle_invalid_json(self) -> None:
        ctrl = BundleController(_make_config())
        with _patch_trader(ctrl):
            ctrl._handle_redis_message(CHANNEL_BUNDLE_LOAD, "not valid json")

    def test_handle_unknown_channel(self) -> None:
        ctrl = BundleController(_make_config())
        with _patch_trader(ctrl):
            ctrl._handle_redis_message("sam:unknown", json.dumps({}))

    @patch.object(BundleController, "load_bundle")
    def test_handle_load_with_error(self, mock_load: MagicMock) -> None:
        mock_load.side_effect = ValueError("bad config")
        ctrl = BundleController(_make_config())
        with _patch_trader(ctrl):
            ctrl._handle_load(_sample_bundle_dict())

    @patch.object(BundleController, "unload_bundle")
    def test_handle_unload_with_error(self, mock_unload: MagicMock) -> None:
        mock_unload.side_effect = RuntimeError("strategy not found")
        ctrl = BundleController(_make_config())
        with _patch_trader(ctrl):
            ctrl._handle_unload({"strategy_id": "bad-id"})

    def test_dispatch_redis_with_event_loop(self) -> None:
        ctrl = BundleController(_make_config())
        with _patch_trader(ctrl) as fake_trader:
            fake_loop = MagicMock()
            fake_loop.is_running.return_value = True
            fake_trader.get_event_loop.return_value = fake_loop

            ctrl._dispatch_redis(
                CHANNEL_BUNDLE_UNLOAD, json.dumps({"strategy_id": "test-0"})
            )
            fake_loop.call_soon_threadsafe.assert_called_once()

    def test_dispatch_redis_no_event_loop(self) -> None:
        ctrl = BundleController(_make_config())
        with _patch_trader(ctrl) as fake_trader:
            fake_trader.get_event_loop.return_value = None
            ctrl._dispatch_redis(
                CHANNEL_BUNDLE_UNLOAD, json.dumps({"strategy_id": "test-0"})
            )


# ── Tests: dict_to_config ──────────────────────────────────────────


class TestDictToConfig:
    """Tests for _dict_to_config helper."""

    def test_converts_valid_bundle(self) -> None:
        ctrl = BundleController(_make_config())
        cfg = ctrl._dict_to_config(_sample_bundle_dict())
        assert cfg.strategy_path == "sam_trader.strategies.orb:OrbStrategy"
        assert cfg.config["instrument_id"] == "TSLA.NASDAQ"

    def test_converts_with_metadata(self) -> None:
        ctrl = BundleController(_make_config())
        bundle = _sample_bundle_dict(
            family="ORB_aggressive", version="1.0.0", variant="beta"
        )
        cfg = ctrl._dict_to_config(bundle)
        assert cfg.config["family"] == "ORB_aggressive"
        assert cfg.config["version"] == "1.0.0"
        assert cfg.config["variant"] == "beta"


# ── Tests: Lifecycle ───────────────────────────────────────────────


class TestLifecycle:
    """Tests for on_start and on_stop."""

    def test_on_start_creates_thread(self) -> None:
        ctrl = BundleController(_make_config())
        assert ctrl._redis_thread is None
        with patch.object(ctrl, "_run", return_value=None):
            ctrl.on_start()
        assert ctrl._redis_thread is not None

    def test_on_stop_signals_and_joins(self) -> None:
        ctrl = BundleController(_make_config())
        fake_thread = threading.Thread(target=lambda: None)
        ctrl._redis_thread = fake_thread
        fake_thread.start()
        fake_thread.join()
        ctrl._stop_event.set()
        ctrl.on_stop()
        assert ctrl._stop_event.is_set()


# ── Tests: Redis listener thread ───────────────────────────────────


class TestRedisListenerThread:
    """Tests for the _run / _listen flow."""

    def test_run_calls_listen(self) -> None:
        ctrl = BundleController(_make_config())
        loop = asyncio.new_event_loop()
        fut: asyncio.Future[None] = loop.create_future()
        fut.set_result(None)
        with patch.object(ctrl, "_listen", return_value=fut) as mock_listen:
            ctrl._run()
            mock_listen.assert_called_once()
        loop.close()

    def test_run_handles_exception(self) -> None:
        ctrl = BundleController(_make_config())
        with patch.object(ctrl, "_listen", side_effect=RuntimeError("boom")):
            ctrl._run()

    @patch("sam_trader.controllers.bundle_controller.aioredis.Redis")
    def test_listen_connection_failure(self, mock_redis: MagicMock) -> None:
        mock_redis.side_effect = ConnectionError("no redis")
        ctrl = BundleController(_make_config())
        with patch("asyncio.run", side_effect=lambda coro: None):
            ctrl._run()
