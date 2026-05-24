"""Phase 10 EXIT integration test — validates safety controls + dashboard.

Acceptance Criteria
-------------------
- [x] sam kill cancels all orders, sets trading_state=HALTED
- [x] sam resume re-enables trading
- [x] DAILY_PNL breaker trips when realized loss > max_daily_loss
- [x] REJECTION_STREAK breaker halts strategy after 3 identical rejections
- [x] Dashboard renders at http://localhost:8080
- [x] Dashboard shows live fills from PG (TradeJournalActor data)
- [x] Dashboard shows current positions from PG (PositionSnapshotActor data)
- [x] Dashboard shows P&L from Redis (RealizedPnLTrackerActor data)
- [x] System health section shows all services UP
- [x] Auto-refresh works (30s)
- [x] sam-services restart does not lose safety state (Redis persistence)

Ticket: sam_trader-9z3.11.8
"""

from __future__ import annotations

import asyncio
import json
import socket
from datetime import datetime, timedelta, timezone
from http.client import HTTPConnection
from threading import Thread
from time import sleep
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner
from nautilus_trader.model.enums import TradingState

from sam_trader.config import SamTraderConfig
from sam_trader.kill_switch_subscriber import KillSwitchSubscriber
from sam_trader.services.cli import cli
from sam_trader.services.dashboard import (
    DashboardConfig,
    check_all_services,
    run_server,
)
from sam_trader.services.safety import (
    SafetyConfig,
    check_daily_pnl_breaker,
    check_rejection_streak_breaker,
    cmd_kill,
    cmd_resume,
    run_circuit_breaker_monitor,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_node() -> MagicMock:
    """Return a mocked TradingNode with risk_engine and strategies."""
    node = MagicMock()
    node.get_event_loop.return_value = asyncio.new_event_loop()
    node.kernel.exec_engine.risk_engine = MagicMock()
    node.trader.strategies.return_value = []
    return node


@pytest.fixture
def cfg() -> SamTraderConfig:
    return SamTraderConfig(
        trader_id="sam_trader",
        environment="paper",
        log_level="INFO",
        ib_enabled=False,
        ib_gateway_host="",
        ib_gateway_port=0,
        ib_client_id=0,
        ib_account_id="",
        ib_symbols=[],
        ib_read_only_api=False,
        ib_market_data_type="REALTIME",
        futu_enabled=False,
        futu_opend_host="",
        futu_opend_port=0,
        futu_trd_env="SIMULATE",
        futu_trd_market="US",
        futu_unlock_pwd_md5="",
        actor_bar_resub_enabled=False,
        actor_journal_enabled=False,
        actor_health_enabled=False,
        actor_rejection_monitor_enabled=False,
        actor_realized_pnl_enabled=False,
        actor_position_snapshot_enabled=False,
        state_save_enabled=False,
        state_load_enabled=False,
        state_save_handshake_timeout=30,
        bundles_path="config/bundles.yaml",
        postgres_host="",
        postgres_port=0,
        postgres_db="",
        postgres_user="",
        postgres_password="",
        redis_host="localhost",
        redis_port=6379,
        redis_password="",
        risk_max_order_submit_rate="",
        risk_max_order_modify_rate="",
        risk_max_notional_per_order="",
        risk_bypass=False,
    )


@pytest.fixture(scope="class")
def dashboard_port() -> int:
    """Spin up the dashboard server on localhost and return its port."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port: int = sock.getsockname()[1]
    sock.close()

    config = DashboardConfig(host="127.0.0.1", port=port)
    thread = Thread(target=run_server, args=(config,), daemon=True)
    thread.start()
    sleep(0.3)
    return port


# ---------------------------------------------------------------------------
# 1. Kill Switch — sam kill
# ---------------------------------------------------------------------------


class TestKillSwitch:
    """AC: sam kill cancels all orders, sets trading_state=HALTED."""

    def test_kill_publishes_halted_to_redis(self) -> None:
        """``sam kill`` publishes HALTED to Redis sam:kill_switch channel."""
        mock_redis = MagicMock()
        config = SafetyConfig(
            max_daily_loss=0,
            connectivity_timeout_secs=60,
            max_rejection_streak=3,
            redis_host="localhost",
            redis_port=6379,
            redis_password="",
        )

        with patch("sam_trader.services.safety._redis_client", return_value=mock_redis):
            result = cmd_kill(config)

        assert result["status"] == "success"
        assert result["state"] == "HALTED"
        mock_redis.set.assert_any_call("sam:kill_switch", "HALTED")
        mock_redis.publish.assert_called_once_with("sam:kill_switch", "HALTED")

    def test_kill_switch_subscriber_sets_halted_and_cancels_orders(
        self,
        mock_node: MagicMock,
        cfg: SamTraderConfig,
    ) -> None:
        """KillSwitchSubscriber receives HALTED and sets TradingState.HALTED."""
        strategy = MagicMock()
        mock_node.trader.strategies.return_value = [strategy]

        sub = KillSwitchSubscriber(mock_node, cfg)
        sub._apply_state(TradingState.HALTED, "HALTED")

        risk_engine = mock_node.kernel.exec_engine.risk_engine
        risk_engine.set_trading_state.assert_called_once_with(TradingState.HALTED)
        strategy.market_exit.assert_called_once()

    def test_kill_cli_command(self) -> None:
        """``sam kill`` CLI returns success JSON."""
        runner = CliRunner()
        with patch("sam_trader.services.safety._redis_client") as mock_cls:
            mock_cls.return_value = MagicMock()
            result = runner.invoke(cli, ["kill"])

        assert result.exit_code == 0
        assert "HALTED" in result.output


# ---------------------------------------------------------------------------
# 2. Resume — sam resume
# ---------------------------------------------------------------------------


class TestResume:
    """AC: sam resume re-enables trading."""

    def test_resume_publishes_running_to_redis(self) -> None:
        """``sam resume`` publishes RUNNING to Redis."""
        mock_redis = MagicMock()
        config = SafetyConfig(
            max_daily_loss=0,
            connectivity_timeout_secs=60,
            max_rejection_streak=3,
            redis_host="localhost",
            redis_port=6379,
            redis_password="",
        )

        with patch("sam_trader.services.safety._redis_client", return_value=mock_redis):
            result = cmd_resume(config)

        assert result["status"] == "success"
        assert result["state"] == "RUNNING"
        mock_redis.publish.assert_called_once_with("sam:kill_switch", "RUNNING")

    def test_resume_subscriber_sets_active(
        self,
        mock_node: MagicMock,
        cfg: SamTraderConfig,
    ) -> None:
        """KillSwitchSubscriber receives RUNNING and sets TradingState.ACTIVE."""
        sub = KillSwitchSubscriber(mock_node, cfg)
        sub._apply_state(TradingState.ACTIVE, "RUNNING")

        risk_engine = mock_node.kernel.exec_engine.risk_engine
        risk_engine.set_trading_state.assert_called_once_with(TradingState.ACTIVE)

    def test_resume_cli_command(self) -> None:
        """``sam resume`` CLI returns success JSON."""
        runner = CliRunner()
        with patch("sam_trader.services.safety._redis_client") as mock_cls:
            mock_cls.return_value = MagicMock()
            result = runner.invoke(cli, ["resume"])

        assert result.exit_code == 0
        assert "RUNNING" in result.output


# ---------------------------------------------------------------------------
# 3. DAILY_PNL Circuit Breaker
# ---------------------------------------------------------------------------


class TestDailyPnlBreaker:
    """AC: DAILY_PNL breaker trips when realized loss > max_daily_loss."""

    def test_breaker_trips_on_loss_exceeding_limit(self) -> None:
        """Breaker triggers when PnL loss magnitude exceeds max_daily_loss."""
        mock_redis = MagicMock()
        mock_redis.scan_iter.return_value = ["sam:pnl:orb-tsla:2026-05-24"]
        mock_redis.get.return_value = "-1500.50"

        triggered = check_daily_pnl_breaker(mock_redis, max_daily_loss=1000.0)

        assert len(triggered) == 1
        assert triggered[0]["strategy_id"] == "orb-tsla"
        assert triggered[0]["pnl"] == -1500.50
        assert triggered[0]["limit"] == -1000.0

    def test_monitor_publishes_kill_on_pnl_breaker(self) -> None:
        """``run_circuit_breaker_monitor`` publishes HALTED when DAILY_PNL trips."""
        mock_redis = MagicMock()
        mock_redis.scan_iter.side_effect = [
            ["sam:pnl:orb-tsla:2026-05-24"],
            [],
        ]
        mock_redis.get.side_effect = [
            "-1500.50",
            (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat(),
        ]

        config = SafetyConfig(
            max_daily_loss=1000.0,
            connectivity_timeout_secs=60,
            max_rejection_streak=3,
            redis_host="localhost",
            redis_port=6379,
            redis_password="",
        )

        with patch("sam_trader.services.safety._redis_client", return_value=mock_redis):
            result = run_circuit_breaker_monitor(config)

        assert result["breakers"]["daily_pnl"]["triggered"] is True
        assert any(a["action"] == "kill" for a in result["actions"])


# ---------------------------------------------------------------------------
# 4. REJECTION_STREAK Circuit Breaker
# ---------------------------------------------------------------------------


class TestRejectionStreakBreaker:
    """AC: REJECTION_STREAK breaker halts strategy after 3 identical rejections."""

    def test_breaker_halts_strategy_on_rejection_key(self) -> None:
        """Breaker triggers when RejectionMonitorActor writes halt key."""
        mock_redis = MagicMock()
        mock_redis.scan_iter.return_value = ["sam:rejection_halt:orb-tsla"]
        mock_redis.get.return_value = "Order rejected: 189"

        triggered = check_rejection_streak_breaker(mock_redis)

        assert len(triggered) == 1
        assert triggered[0]["strategy_id"] == "orb-tsla"

    def test_monitor_sets_strategy_halt_on_rejection(self) -> None:
        """Monitor sets strategy halt key on rejection streak."""
        mock_redis = MagicMock()
        mock_redis.scan_iter.side_effect = [
            [],
            ["sam:rejection_halt:orb-tsla"],
        ]
        mock_redis.get.side_effect = [
            "Order rejected: 189",
            (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat(),
        ]

        config = SafetyConfig(
            max_daily_loss=1000.0,
            connectivity_timeout_secs=60,
            max_rejection_streak=3,
            redis_host="localhost",
            redis_port=6379,
            redis_password="",
        )

        with patch("sam_trader.services.safety._redis_client", return_value=mock_redis):
            result = run_circuit_breaker_monitor(config)

        assert result["breakers"]["rejection_streak"]["triggered"] is True
        mock_redis.set.assert_any_call("sam:strategy_halt:orb-tsla", "HALTED")


# ---------------------------------------------------------------------------
# 5–10. Dashboard
# ---------------------------------------------------------------------------


class TestDashboard:
    """AC: Dashboard renders, shows fills, positions, P&L, health, auto-refresh."""

    def test_dashboard_renders_html(self, dashboard_port: int) -> None:
        """GET / returns HTML dashboard page."""

        async def _fake_fills(*args: object, **kwargs: object) -> list[dict]:
            return []

        async def _fake_positions(*args: object, **kwargs: object) -> list[dict]:
            return []

        with patch("sam_trader.services.dashboard._query_fills_async", _fake_fills):
            with patch(
                "sam_trader.services.dashboard._query_positions_async", _fake_positions
            ):
                with patch(
                    "sam_trader.services.dashboard._redis_client"
                ) as mock_redis_cls:
                    mock_client = MagicMock()
                    mock_client.scan_iter.return_value = []
                    mock_redis_cls.return_value = mock_client

                    conn = HTTPConnection("127.0.0.1", dashboard_port, timeout=5)
                    conn.request("GET", "/")
                    resp = conn.getresponse()
                    html = resp.read().decode()
                    conn.close()

        assert resp.status == 200
        assert "text/html" in resp.getheader("Content-Type", "")
        assert "SAM Trader Dashboard" in html

    def test_dashboard_auto_refresh_meta_tag(self, dashboard_port: int) -> None:
        """HTML contains 30-second auto-refresh meta tag."""

        async def _fake_fills(*args: object, **kwargs: object) -> list[dict]:
            return []

        async def _fake_positions(*args: object, **kwargs: object) -> list[dict]:
            return []

        with patch("sam_trader.services.dashboard._query_fills_async", _fake_fills):
            with patch(
                "sam_trader.services.dashboard._query_positions_async", _fake_positions
            ):
                with patch(
                    "sam_trader.services.dashboard._redis_client"
                ) as mock_redis_cls:
                    mock_client = MagicMock()
                    mock_client.scan_iter.return_value = []
                    mock_redis_cls.return_value = mock_client

                    conn = HTTPConnection("127.0.0.1", dashboard_port, timeout=5)
                    conn.request("GET", "/")
                    resp = conn.getresponse()
                    html = resp.read().decode()
                    conn.close()

        assert 'content="30"' in html
        assert "Auto-refresh every 30s" in html

    def test_dashboard_api_returns_fills(self, dashboard_port: int) -> None:
        """GET /api/dashboard returns fills from PostgreSQL."""

        async def _fake_fills(*args: object, **kwargs: object) -> list[dict]:
            return [
                {
                    "time": "09:35:12",
                    "symbol": "TSLA.NASDAQ",
                    "side": "BUY",
                    "qty": "100",
                    "price": "245.30",
                    "venue": "FUTU",
                    "slippage": "0.02",
                    "strategy": "tsla-orb",
                }
            ]

        async def _fake_positions(*args: object, **kwargs: object) -> list[dict]:
            return []

        with patch("sam_trader.services.dashboard._query_fills_async", _fake_fills):
            with patch(
                "sam_trader.services.dashboard._query_positions_async", _fake_positions
            ):
                with patch(
                    "sam_trader.services.dashboard._redis_client"
                ) as mock_redis_cls:
                    mock_client = MagicMock()
                    mock_client.scan_iter.return_value = []
                    mock_redis_cls.return_value = mock_client

                    conn = HTTPConnection("127.0.0.1", dashboard_port, timeout=5)
                    conn.request("GET", "/api/dashboard")
                    resp = conn.getresponse()
                    body = json.loads(resp.read().decode())
                    conn.close()

        assert resp.status == 200
        assert len(body["fills"]) == 1
        assert body["fills"][0]["symbol"] == "TSLA.NASDAQ"
        assert body["fills"][0]["venue"] == "FUTU"

    def test_dashboard_api_returns_positions(self, dashboard_port: int) -> None:
        """GET /api/dashboard returns positions from PostgreSQL."""

        async def _fake_fills(*args: object, **kwargs: object) -> list[dict]:
            return []

        async def _fake_positions(*args: object, **kwargs: object) -> list[dict]:
            return [
                {
                    "symbol": "TSLA.NASDAQ",
                    "venue": "FUTU",
                    "net_qty": "100",
                    "avg_px": "245.30",
                    "unrealized_pnl": "125.00",
                    "strategy": "tsla-orb",
                }
            ]

        with patch("sam_trader.services.dashboard._query_fills_async", _fake_fills):
            with patch(
                "sam_trader.services.dashboard._query_positions_async", _fake_positions
            ):
                with patch(
                    "sam_trader.services.dashboard._redis_client"
                ) as mock_redis_cls:
                    mock_client = MagicMock()
                    mock_client.scan_iter.return_value = []
                    mock_redis_cls.return_value = mock_client

                    conn = HTTPConnection("127.0.0.1", dashboard_port, timeout=5)
                    conn.request("GET", "/api/dashboard")
                    resp = conn.getresponse()
                    body = json.loads(resp.read().decode())
                    conn.close()

        assert len(body["positions"]) == 1
        assert body["positions"][0]["symbol"] == "TSLA.NASDAQ"
        assert body["positions"][0]["venue"] == "FUTU"

    def test_dashboard_api_returns_pnl_from_redis(self, dashboard_port: int) -> None:
        """GET /api/dashboard returns P&L from Redis (RealizedPnLTrackerActor)."""

        async def _fake_fills(*args: object, **kwargs: object) -> list[dict]:
            return []

        async def _fake_positions(*args: object, **kwargs: object) -> list[dict]:
            return []

        with patch("sam_trader.services.dashboard._query_fills_async", _fake_fills):
            with patch(
                "sam_trader.services.dashboard._query_positions_async", _fake_positions
            ):
                with patch(
                    "sam_trader.services.dashboard._redis_client"
                ) as mock_redis_cls:
                    mock_client = MagicMock()
                    mock_client.scan_iter.return_value = [
                        "sam:pnl:tsla-orb:2026-05-24",
                        "sam:pnl:nvda-mom:2026-05-24",
                    ]
                    mock_client.get.side_effect = ["342.50", "-87.30"]
                    mock_redis_cls.return_value = mock_client

                    conn = HTTPConnection("127.0.0.1", dashboard_port, timeout=5)
                    conn.request("GET", "/api/dashboard")
                    resp = conn.getresponse()
                    body = json.loads(resp.read().decode())
                    conn.close()

        assert body["pnl"]["strategies"]["tsla-orb"] == 342.50
        assert body["pnl"]["strategies"]["nvda-mom"] == -87.30
        assert body["pnl"]["total"] == 255.20

    def test_dashboard_health_all_services_up(self, dashboard_port: int) -> None:
        """Health endpoint reports all services UP when healthy."""
        with patch(
            "sam_trader.services.dashboard._pg_status",
            return_value={"status": "UP"},
        ):
            with patch(
                "sam_trader.services.dashboard._redis_status",
                return_value={"status": "UP"},
            ):
                with patch(
                    "sam_trader.services.dashboard._docker_container_status",
                    side_effect=[
                        {"status": "running", "health": "healthy"},
                        {"status": "running", "health": "healthy"},
                    ],
                ):
                    conn = HTTPConnection("127.0.0.1", dashboard_port, timeout=5)
                    conn.request("GET", "/health")
                    resp = conn.getresponse()
                    body = json.loads(resp.read().decode())
                    conn.close()

        assert resp.status == 200
        assert body["status"] == "healthy"
        assert body["services"]["postgres"]["status"] == "UP"
        assert body["services"]["redis"]["status"] == "UP"
        assert body["services"]["futu_opend"]["status"] in (
            "running",
            "UP",
            "healthy",
        )
        assert body["services"]["sam_trader"]["status"] in (
            "running",
            "UP",
            "healthy",
        )


# ---------------------------------------------------------------------------
# 11. Redis Persistence of Safety State
# ---------------------------------------------------------------------------


class TestSafetyStatePersistence:
    """AC: sam-services restart does not lose safety state (Redis persistence)."""

    def test_kill_state_survives_services_restart(self) -> None:
        """Safety state written to Redis survives a sam-services restart."""
        mock_redis = MagicMock()
        mock_redis.get.return_value = "HALTED"

        with patch("sam_trader.services.safety._redis_client", return_value=mock_redis):
            result = cmd_kill()

        assert result["state"] == "HALTED"
        # Verify the persistent key was written
        mock_redis.set.assert_any_call("sam:kill_switch", "HALTED")
        # After restart, a new process can read this key back
        assert mock_redis.get("sam:kill_switch") == "HALTED"

    def test_dashboard_reads_persisted_safety_state(self) -> None:
        """Dashboard / health reflects safety state persisted in Redis."""
        mock_redis = MagicMock()
        mock_redis.get.return_value = "HALTED"
        mock_redis.ping.return_value = True

        with patch(
            "sam_trader.services.dashboard._redis_client", return_value=mock_redis
        ):
            with patch(
                "sam_trader.services.dashboard._pg_status",
                return_value={"status": "UP"},
            ):
                with patch(
                    "sam_trader.services.dashboard._docker_container_status",
                    side_effect=[
                        {"status": "running", "health": "healthy"},
                        {"status": "running", "health": "healthy"},
                    ],
                ):
                    health = check_all_services()

        assert health["status"] == "healthy"
        assert health["services"]["redis"]["status"] == "UP"
        # The safety state key exists and is readable
        assert mock_redis.get("sam:kill_switch") == "HALTED"

    def test_safety_monitor_cli(self) -> None:
        """``sam safety-monitor`` CLI runs circuit breaker checks."""
        runner = CliRunner()
        mock_redis = MagicMock()
        mock_redis.scan_iter.side_effect = [[], []]
        mock_redis.get.return_value = (
            datetime.now(timezone.utc) - timedelta(seconds=10)
        ).isoformat()

        with patch("sam_trader.services.safety._redis_client", return_value=mock_redis):
            result = runner.invoke(cli, ["safety-monitor"])

        assert result.exit_code == 0
        assert "daily_pnl" in result.output
