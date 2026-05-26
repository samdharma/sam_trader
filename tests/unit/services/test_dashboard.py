"""Unit tests for the read-only dashboard server."""

from __future__ import annotations

import json
import socket
from http.client import HTTPConnection
from threading import Thread
from time import sleep
from typing import Any, Iterator
from unittest.mock import MagicMock, patch

import pytest

from sam_trader.services.dashboard import (
    DashboardConfig,
    check_all_services,
    query_fills,
    query_market_data_from_redis,
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


class TestMarketDataEndpoint:
    """Tests for market data retrieval from Redis."""

    def test_market_data_reads_bars_and_venues(self) -> None:
        """query_market_data_from_redis returns instruments, counts, venues."""
        mock_redis = MagicMock()
        mock_redis.scan_iter.side_effect = [
            ["sam:bars:last:TSLA.NASDAQ", "sam:bars:last:NVDA.NASDAQ"],
            ["sam:venue:conn:FUTU"],
        ]
        mock_redis.get.side_effect = [
            "2026-05-26T13:30:00+00:00",
            "2026-05-26T13:28:00+00:00",
            "UP:2026-05-26T13:00:00+00:00",
        ]
        mock_redis.hgetall.return_value = {
            "TSLA.NASDAQ": "42",
            "NVDA.NASDAQ": "17",
        }

        with patch(
            "sam_trader.services.dashboard._redis_client", return_value=mock_redis
        ):
            result = query_market_data_from_redis(DashboardConfig())

        assert len(result["instruments"]) == 2
        assert result["instruments"][0]["instrument_id"] == "NVDA.NASDAQ"
        assert result["instruments"][1]["instrument_id"] == "TSLA.NASDAQ"
        assert result["counts"]["TSLA.NASDAQ"] == 42
        assert result["counts"]["NVDA.NASDAQ"] == 17
        assert len(result["venues"]) == 1
        assert result["venues"][0]["venue"] == "FUTU"
        assert result["venues"][0]["status"] == "UP"

    def test_market_data_staleness_classes(self) -> None:
        """Staleness is fresh (<2min), stale (<5min), or old (>5min)."""
        from datetime import datetime, timedelta, timezone

        now = datetime(2026, 5, 26, 12, 0, 0, tzinfo=timezone.utc)
        fresh_ts = (now - timedelta(seconds=90)).isoformat()
        stale_ts = (now - timedelta(seconds=180)).isoformat()
        old_ts = (now - timedelta(seconds=400)).isoformat()

        mock_redis = MagicMock()
        mock_redis.scan_iter.side_effect = [
            [
                "sam:bars:last:FRESH.NASDAQ",
                "sam:bars:last:STALE.NASDAQ",
                "sam:bars:last:OLD.NASDAQ",
            ],
            [],
        ]
        mock_redis.get.side_effect = [fresh_ts, stale_ts, old_ts]
        mock_redis.hgetall.return_value = {}

        with patch(
            "sam_trader.services.dashboard._redis_client", return_value=mock_redis
        ):
            with patch("sam_trader.services.dashboard.datetime") as mock_dt:
                mock_dt.now.return_value = now
                mock_dt.fromisoformat = datetime.fromisoformat
                mock_dt.side_effect = lambda *a, **k: datetime(*a, **k)
                result = query_market_data_from_redis(DashboardConfig())

        assert len(result["instruments"]) == 3
        stale_map = {i["instrument_id"]: i["staleness"] for i in result["instruments"]}
        assert stale_map["FRESH.NASDAQ"] == "fresh"
        assert stale_map["STALE.NASDAQ"] == "stale"
        assert stale_map["OLD.NASDAQ"] == "old"

    def test_market_data_returns_empty_on_redis_error(self) -> None:
        """Redis errors yield empty market data without raising."""
        mock_redis = MagicMock()
        mock_redis.scan_iter.side_effect = Exception("connection refused")

        with patch(
            "sam_trader.services.dashboard._redis_client", return_value=mock_redis
        ):
            result = query_market_data_from_redis(DashboardConfig())

        assert result["instruments"] == []
        assert result["counts"] == {}
        assert result["venues"] == []


class TestBarsRecentEndpoint:
    """Tests for GET /api/bars/recent."""

    def test_bars_recent_returns_all_instruments(self) -> None:
        """_handle_bars_recent returns bars from all instruments when no filter."""
        from datetime import datetime, timezone

        now = datetime(2026, 5, 26, 12, 0, 0, tzinfo=timezone.utc)
        bar_json = (
            '{"ts":"' + now.isoformat() + '",'
            '"open":"150.00","high":"151.00",'
            '"low":"149.00","close":"150.50","volume":"1000"}'
        )

        mock_redis = MagicMock()
        mock_redis.scan_iter.return_value = ["sam:bars:recent:TSLA.NASDAQ"]
        mock_redis.lrange.return_value = [bar_json]

        with patch(
            "sam_trader.services.dashboard._redis_client", return_value=mock_redis
        ):
            with patch("sam_trader.services.dashboard.datetime") as mock_dt:
                mock_dt.now.return_value = now
                mock_dt.fromisoformat = datetime.fromisoformat
                mock_dt.side_effect = lambda *a, **k: datetime(*a, **k)
                from sam_trader.services.dashboard import _handle_bars_recent

                result = _handle_bars_recent(
                    "/api/bars/recent?seconds=300", DashboardConfig()
                )

        assert result["count"] == 1
        assert result["seconds"] == 300
        assert result["bars"][0]["instrument_id"] == "TSLA.NASDAQ"
        assert result["bars"][0]["open"] == "150.00"
        assert result["bars"][0]["volume"] == "1000"

    def test_bars_recent_filters_by_instrument(self) -> None:
        """_handle_bars_recent filters to a single instrument when provided."""
        from datetime import datetime, timezone

        now = datetime(2026, 5, 26, 12, 0, 0, tzinfo=timezone.utc)
        bar_json = (
            '{"ts":"' + now.isoformat() + '",'
            '"open":"150.00","high":"151.00",'
            '"low":"149.00","close":"150.50","volume":"1000"}'
        )

        mock_redis = MagicMock()
        mock_redis.scan_iter.return_value = []
        mock_redis.lrange.return_value = [bar_json]

        with patch(
            "sam_trader.services.dashboard._redis_client", return_value=mock_redis
        ):
            with patch("sam_trader.services.dashboard.datetime") as mock_dt:
                mock_dt.now.return_value = now
                mock_dt.fromisoformat = datetime.fromisoformat
                mock_dt.side_effect = lambda *a, **k: datetime(*a, **k)
                from sam_trader.services.dashboard import _handle_bars_recent

                result = _handle_bars_recent(
                    "/api/bars/recent?instrument=TSLA.NASDAQ&seconds=300",
                    DashboardConfig(),
                )

        assert result["count"] == 1
        mock_redis.lrange.assert_called_once_with("sam:bars:recent:TSLA.NASDAQ", 0, 99)

    def test_bars_recent_filters_by_seconds(self) -> None:
        """_handle_bars_recent excludes bars older than the cutoff."""
        from datetime import datetime, timedelta, timezone

        now = datetime(2026, 5, 26, 12, 0, 0, tzinfo=timezone.utc)
        old_ts = (now - timedelta(seconds=400)).isoformat()
        fresh_ts = (now - timedelta(seconds=60)).isoformat()

        mock_redis = MagicMock()
        mock_redis.scan_iter.return_value = ["sam:bars:recent:TSLA.NASDAQ"]
        mock_redis.lrange.return_value = [
            (
                '{"ts":"' + old_ts + '",'
                '"open":"1","high":"2","low":"0",'
                '"close":"1","volume":"1"}'
            ),
            (
                '{"ts":"' + fresh_ts + '",'
                '"open":"2","high":"3","low":"1",'
                '"close":"2","volume":"2"}'
            ),
        ]

        with patch(
            "sam_trader.services.dashboard._redis_client", return_value=mock_redis
        ):
            with patch("sam_trader.services.dashboard.datetime") as mock_dt:
                mock_dt.now.return_value = now
                mock_dt.fromisoformat = datetime.fromisoformat
                mock_dt.side_effect = lambda *a, **k: datetime(*a, **k)
                from sam_trader.services.dashboard import _handle_bars_recent

                result = _handle_bars_recent(
                    "/api/bars/recent?seconds=300", DashboardConfig()
                )

        assert result["count"] == 1
        assert result["bars"][0]["open"] == "2"

    def test_bars_recent_returns_empty_on_redis_error(self) -> None:
        """Redis errors yield empty bars list without raising."""
        mock_redis = MagicMock()
        mock_redis.scan_iter.side_effect = Exception("connection refused")

        with patch(
            "sam_trader.services.dashboard._redis_client", return_value=mock_redis
        ):
            from sam_trader.services.dashboard import _handle_bars_recent

            result = _handle_bars_recent("/api/bars/recent", DashboardConfig())

        assert result["bars"] == []
        assert result["count"] == 0


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
            "market_data": {
                "instruments": [
                    {
                        "instrument_id": "TSLA.NASDAQ",
                        "last_ts": "13:30:00",
                        "age_seconds": 90,
                        "staleness": "fresh",
                    }
                ],
                "counts": {"TSLA.NASDAQ": 42},
                "venues": [
                    {"venue": "FUTU", "status": "UP", "last_change": "13:00:00"}
                ],
                "timestamp": "2026-05-24T10:00:00+00:00",
            },
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
        assert "MARKET DATA" in html
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
                "market_data": {
                    "instruments": [],
                    "counts": {},
                    "venues": [],
                    "timestamp": "",
                },
                "pnl": {"strategies": {}, "total": 0.0, "date": ""},
                "timestamp": "",
            }
        )
        assert "No bar telemetry" in html
        assert "No fills today" in html
        assert "No open positions" in html
        assert "No P&L data" in html

    def test_html_renders_market_data_summary(self) -> None:
        """Collapsed Market Data panel shows compact summary text."""
        from sam_trader.services.dashboard import _render_html

        html = _render_html(
            {
                "health": {"status": "healthy", "services": {}},
                "fills": [],
                "positions": [],
                "market_data": {
                    "instruments": [
                        {
                            "instrument_id": "TSLA.NASDAQ",
                            "last_ts": "13:30:00",
                            "age_seconds": 45,
                            "staleness": "fresh",
                        },
                        {
                            "instrument_id": "NVDA.NASDAQ",
                            "last_ts": "13:28:00",
                            "age_seconds": 165,
                            "staleness": "stale",
                        },
                    ],
                    "counts": {},
                    "venues": [],
                    "timestamp": "",
                },
                "pnl": {"strategies": {}, "total": 0.0, "date": ""},
                "timestamp": "",
            }
        )
        assert "2 instruments | last bar 45s ago" in html
        assert 'id="market-data-summary"' in html
        assert 'id="market-data-detail"' in html
        assert "Recent Bars" in html
        assert "toggleMarketData" in html
        assert "loadRecentBars" in html
        assert "/api/bars/recent?seconds=300" in html

    def test_html_renders_no_instruments_summary(self) -> None:
        """When no instruments are present, summary shows 'No instruments'."""
        from sam_trader.services.dashboard import _render_html

        html = _render_html(
            {
                "health": {"status": "healthy", "services": {}},
                "fills": [],
                "positions": [],
                "market_data": {
                    "instruments": [],
                    "counts": {},
                    "venues": [],
                    "timestamp": "",
                },
                "pnl": {"strategies": {}, "total": 0.0, "date": ""},
                "timestamp": "",
            }
        )
        assert "No instruments" in html


class TestDashboardServer:
    """Integration-style tests against a real HTTP server on a random port."""

    @pytest.fixture(scope="class")
    def server_port(self) -> Iterator[int]:
        """Spin up the dashboard server on localhost and return its port."""
        # Find a free port
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        sock.close()

        config = DashboardConfig(host="127.0.0.1", port=port)

        # Patch external health checks so the daemon thread never blocks on
        # real network connections (prevents TimeoutError in downstream tests).
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
                    return_value={"status": "running", "health": "healthy"},
                ):
                    thread = Thread(target=run_server, args=(config,), daemon=True)
                    thread.start()
                    sleep(0.3)  # Let the server start
                    yield port

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
                    "sam_trader.services.dashboard.query_market_data_from_redis"
                ) as mock_md:
                    mock_md.return_value = {
                        "instruments": [],
                        "counts": {},
                        "venues": [],
                        "timestamp": "",
                    }
                    with patch(
                        "sam_trader.services.dashboard._redis_client"
                    ) as mock_redis_cls:
                        mock_client = MagicMock()
                        mock_client.scan_iter.return_value = [
                            "sam:pnl:aapl-orb:2026-05-24"
                        ]
                        mock_client.get.return_value = "-5.00"
                        mock_redis_cls.return_value = mock_client

                        conn = HTTPConnection("127.0.0.1", server_port, timeout=5)
                        conn.request("GET", "/api/dashboard")
                        resp = conn.getresponse()
                        body = json.loads(resp.read().decode())
                        conn.close()

        assert resp.status == 200
        assert "health" in body
        assert "market_data" in body
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
                    "sam_trader.services.dashboard.query_market_data_from_redis"
                ) as mock_md:
                    mock_md.return_value = {
                        "instruments": [],
                        "counts": {},
                        "venues": [],
                        "timestamp": "",
                    }
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
        assert "MARKET DATA" in html
        assert "TODAY'S FILLS" in html
        assert "CURRENT POSITIONS" in html
        assert "P&L SUMMARY" in html

    def test_get_api_bars_recent_via_http(self, server_port: int) -> None:
        """GET /api/bars/recent over HTTP returns JSON with filtered bars."""
        from datetime import datetime, timezone

        now = datetime(2026, 5, 26, 12, 0, 0, tzinfo=timezone.utc)
        bar_json = (
            '{"ts":"' + now.isoformat() + '",'
            '"open":"150.00","high":"151.00",'
            '"low":"149.00","close":"150.50","volume":"1000"}'
        )

        with patch("sam_trader.services.dashboard._redis_client") as mock_redis_cls:
            mock_client = MagicMock()
            mock_client.scan_iter.return_value = ["sam:bars:recent:TSLA.NASDAQ"]
            mock_client.lrange.return_value = [bar_json]
            mock_redis_cls.return_value = mock_client

            with patch("sam_trader.services.dashboard.datetime") as mock_dt:
                mock_dt.now.return_value = now
                mock_dt.fromisoformat = datetime.fromisoformat
                mock_dt.side_effect = lambda *a, **k: datetime(*a, **k)

                conn = HTTPConnection("127.0.0.1", server_port, timeout=5)
                conn.request(
                    "GET", "/api/bars/recent?instrument=TSLA.NASDAQ&seconds=300"
                )
                resp = conn.getresponse()
                body = json.loads(resp.read().decode())
                conn.close()

        assert resp.status == 200
        assert body["count"] == 1
        assert body["seconds"] == 300
        assert body["bars"][0]["instrument_id"] == "TSLA.NASDAQ"
        assert body["bars"][0]["open"] == "150.00"


class TestDashboardStartup:
    """Tests for dashboard main() startup behaviour including schema validation."""

    @patch("sam_trader.services.dashboard.validate_schema", return_value=True)
    @patch("sam_trader.services.dashboard.run_server")
    def test_main_starts_server_when_schema_valid(
        self, mock_run: Any, mock_validate: Any
    ) -> None:
        """main() starts the HTTP server when schema validation passes."""
        from sam_trader.services.dashboard import main

        result = main()
        assert result == 0
        mock_validate.assert_called_once()
        mock_run.assert_called_once()

    @patch("sam_trader.services.dashboard.validate_schema", return_value=False)
    @patch("sam_trader.services.dashboard.run_server")
    def test_main_exits_without_server_when_schema_invalid(
        self, mock_run: Any, mock_validate: Any
    ) -> None:
        """main() returns 1 and does NOT start the server when schema is missing."""
        from sam_trader.services.dashboard import main

        result = main()
        assert result == 1
        mock_validate.assert_called_once()
        mock_run.assert_not_called()
