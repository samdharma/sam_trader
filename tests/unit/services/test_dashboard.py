"""Unit tests for the read-only dashboard server."""

from __future__ import annotations

import json
import socket
from http.client import HTTPConnection
from threading import Thread
from time import sleep
from unittest.mock import MagicMock, patch

import pytest

from sam_trader.services.dashboard import (
    DashboardConfig,
    check_all_services,
    query_fills,
    query_pnl_from_redis,
    query_positions,
    run_server,
)


class TestHealthEndpoint:
    """Tests for GET /health."""

    def test_health_endpoint(self) -> None:
        """GET /health returns JSON with status and services keys."""
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
                    result = check_all_services()

        assert result["status"] == "healthy"
        assert "services" in result
        assert result["services"]["postgres"]["status"] == "UP"
        assert result["services"]["redis"]["status"] == "UP"
        assert result["services"]["futu_opend"]["status"] == "running"
        assert result["services"]["sam_trader"]["status"] == "running"

    def test_health_degraded_when_pg_down(self) -> None:
        """Health status is degraded when PostgreSQL is down."""
        with patch(
            "sam_trader.services.dashboard._pg_status",
            return_value={"status": "DOWN"},
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
                    result = check_all_services()

        assert result["status"] == "degraded"


class TestFillsEndpoint:
    """Tests for fills data retrieval."""

    def test_fills_endpoint_returns_data(self) -> None:
        """query_fills returns a list of dict rows."""
        mock_rows = [
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

        async def _fake_query(*args: object, **kwargs: object) -> list[dict]:
            return mock_rows

        with patch("sam_trader.services.dashboard._query_fills_async", _fake_query):
            fills = query_fills(DashboardConfig())

        assert len(fills) == 1
        assert fills[0]["symbol"] == "TSLA.NASDAQ"
        assert fills[0]["side"] == "BUY"


class TestPositionsEndpoint:
    """Tests for positions data retrieval."""

    def test_positions_endpoint(self) -> None:
        """query_positions returns a list of dict rows."""
        mock_rows = [
            {
                "symbol": "TSLA.NASDAQ",
                "venue": "FUTU",
                "net_qty": "100",
                "avg_px": "245.30",
                "unrealized_pnl": "125.00",
                "strategy": "tsla-orb",
            }
        ]

        async def _fake_query(*args: object, **kwargs: object) -> list[dict]:
            return mock_rows

        with patch("sam_trader.services.dashboard._query_positions_async", _fake_query):
            positions = query_positions(DashboardConfig())

        assert len(positions) == 1
        assert positions[0]["symbol"] == "TSLA.NASDAQ"
        assert positions[0]["venue"] == "FUTU"


class TestPnlEndpoint:
    """Tests for P&L data from Redis."""

    def test_pnl_endpoint_from_redis(self) -> None:
        """query_pnl_from_redis reads sam:pnl:* keys and returns totals."""
        mock_redis = MagicMock()
        mock_redis.scan_iter.return_value = [
            "sam:pnl:tsla-orb:2026-05-24",
            "sam:pnl:nvda-mom:2026-05-24",
        ]
        mock_redis.get.side_effect = ["342.50", "-87.30"]

        with patch(
            "sam_trader.services.dashboard._redis_client", return_value=mock_redis
        ):
            result = query_pnl_from_redis(DashboardConfig())

        assert result["strategies"]["tsla-orb"] == 342.50
        assert result["strategies"]["nvda-mom"] == -87.30
        assert result["total"] == 255.20


class TestHtmlRendering:
    """Tests for the dashboard HTML page."""

    def test_html_renders_all_sections(self) -> None:
        """GET / returns HTML containing all expected section headers."""
        data = {
            "health": {
                "status": "healthy",
                "services": {
                    "postgres": {"status": "UP"},
                    "redis": {"status": "UP"},
                    "futu_opend": {"status": "running"},
                    "sam_trader": {"status": "running"},
                },
            },
            "fills": [
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
            ],
            "positions": [
                {
                    "symbol": "TSLA.NASDAQ",
                    "venue": "FUTU",
                    "net_qty": "100",
                    "avg_px": "245.30",
                    "unrealized_pnl": "125.00",
                    "strategy": "tsla-orb",
                }
            ],
            "pnl": {
                "strategies": {"tsla-orb": 342.50, "nvda-mom": -87.30},
                "total": 255.20,
                "date": "2026-05-24",
            },
            "timestamp": "2026-05-24T10:00:00+00:00",
        }

        from sam_trader.services.dashboard import _render_html

        html = _render_html(data)

        assert "SYSTEM HEALTH" in html
        assert "TODAY'S FILLS" in html
        assert "CURRENT POSITIONS" in html
        assert "P&L SUMMARY" in html
        assert "TSLA.NASDAQ" in html
        assert "BUY" in html
        assert "tsla-orb" in html
        assert "+$255.20" in html
        assert "Auto-refresh every 30s" in html  # auto-refresh meta

    def test_html_shows_no_data_messages(self) -> None:
        """Empty data sets render informative no-data rows."""
        from sam_trader.services.dashboard import _render_html

        html = _render_html(
            {
                "health": {"status": "healthy", "services": {}},
                "fills": [],
                "positions": [],
                "pnl": {"strategies": {}, "total": 0.0, "date": ""},
                "timestamp": "",
            }
        )
        assert "No fills today" in html
        assert "No open positions" in html
        assert "No P&L data" in html


class TestDashboardServer:
    """Integration-style tests against a real HTTP server on a random port."""

    @pytest.fixture(scope="class")
    def server_port(self) -> int:
        """Spin up the dashboard server on localhost and return its port."""
        # Find a free port
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        sock.close()

        config = DashboardConfig(host="127.0.0.1", port=port)
        thread = Thread(target=run_server, args=(config,), daemon=True)
        thread.start()
        sleep(0.3)  # Let the server start
        return port

    def test_get_health_via_http(self, server_port: int) -> None:
        """GET /health over HTTP returns valid JSON."""
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
                    conn = HTTPConnection("127.0.0.1", server_port, timeout=5)
                    conn.request("GET", "/health")
                    resp = conn.getresponse()
                    body = json.loads(resp.read().decode())
                    conn.close()

        assert resp.status == 200
        assert body["status"] == "healthy"
        assert "services" in body

    def test_get_api_dashboard_via_http(self, server_port: int) -> None:
        """GET /api/dashboard over HTTP returns JSON with all data sections."""

        async def _fake_fills(*args: object, **kwargs: object) -> list[dict]:
            return [
                {
                    "symbol": "AAPL.NASDAQ",
                    "side": "SELL",
                    "qty": "10",
                    "price": "150.00",
                    "venue": "IB",
                    "slippage": None,
                    "strategy": "aapl-orb",
                    "time": "10:00:00",
                }
            ]

        async def _fake_positions(*args: object, **kwargs: object) -> list[dict]:
            return [
                {
                    "symbol": "AAPL.NASDAQ",
                    "venue": "IB",
                    "net_qty": "10",
                    "avg_px": "150.00",
                    "unrealized_pnl": "-5.00",
                    "strategy": "aapl-orb",
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
                    mock_client.scan_iter.return_value = ["sam:pnl:aapl-orb:2026-05-24"]
                    mock_client.get.return_value = "-5.00"
                    mock_redis_cls.return_value = mock_client

                    conn = HTTPConnection("127.0.0.1", server_port, timeout=5)
                    conn.request("GET", "/api/dashboard")
                    resp = conn.getresponse()
                    body = json.loads(resp.read().decode())
                    conn.close()

        assert resp.status == 200
        assert "health" in body
        assert "fills" in body
        assert "positions" in body
        assert "pnl" in body
        assert body["fills"][0]["symbol"] == "AAPL.NASDAQ"
        assert body["positions"][0]["venue"] == "IB"
        assert body["pnl"]["strategies"]["aapl-orb"] == -5.0

    def test_get_root_returns_html(self, server_port: int) -> None:
        """GET / returns HTML with the dashboard page."""

        async def _fake_fills(*args: object, **kwargs: object) -> list[dict]:
            return []

        async def _fake_positions(*args: object, **kwargs: object) -> list[dict]:
            return []

        with patch("sam_trader.services.dashboard._query_fills_async", _fake_fills):
            with patch(
                "sam_trader.services.dashboard._query_positions_async",
                _fake_positions,
            ):
                with patch(
                    "sam_trader.services.dashboard._redis_client"
                ) as mock_redis_cls:
                    mock_client = MagicMock()
                    mock_client.scan_iter.return_value = []
                    mock_redis_cls.return_value = mock_client

                    conn = HTTPConnection("127.0.0.1", server_port, timeout=5)
                    conn.request("GET", "/")
                    resp = conn.getresponse()
                    html = resp.read().decode()
                    conn.close()

        assert resp.status == 200
        assert "text/html" in resp.getheader("Content-Type", "")
        assert "SYSTEM HEALTH" in html
        assert "TODAY'S FILLS" in html
        assert "CURRENT POSITIONS" in html
        assert "P&L SUMMARY" in html
