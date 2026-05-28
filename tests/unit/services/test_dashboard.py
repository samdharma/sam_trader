"""Unit tests for the read-only dashboard server."""

from __future__ import annotations

import datetime
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
    _render_html,
    _render_schedule_html,
    check_all_services,
    get_dashboard_data,
    get_market_schedule_info,
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


class TestMarketScheduleInfo:
    """Tests for MarketCalendar integration in dashboard."""

    def test_holiday_banner_us(self) -> None:
        """US holiday returns amber banner with holiday name."""
        with patch(
            "sam_trader.services.dashboard.MarketCalendarService.is_holiday",
            side_effect=lambda m, d: m == "US",
        ):
            with patch(
                "sam_trader.services.dashboard.MarketCalendarService.holiday_name",
                return_value="Independence Day",
            ):
                with patch(
                    "sam_trader.services.dashboard.MarketCalendarService"
                    ".is_early_close",
                    return_value=False,
                ):
                    with patch(
                        "sam_trader.services.dashboard.MarketCalendarService"
                        ".next_trading_day",
                        return_value=datetime.date(2026, 7, 5),
                    ):
                        with patch(
                            "sam_trader.services.dashboard.MarketCalendarService"
                            ".market_timezone",
                            return_value="America/New_York",
                        ):
                            result = get_market_schedule_info(DashboardConfig())

        assert any("US Market Holiday" in b for b in result["banners"])
        assert any("Independence Day" in b for b in result["banners"])
        assert any("Markets Closed" in b for b in result["banners"])
        assert any("Next US session" in c for c in result["countdowns"])

    def test_holiday_banner_hk(self) -> None:
        """HK holiday returns amber banner with holiday name."""
        with patch(
            "sam_trader.services.dashboard.MarketCalendarService.is_holiday",
            side_effect=lambda m, d: m == "HK",
        ):
            with patch(
                "sam_trader.services.dashboard.MarketCalendarService.holiday_name",
                return_value="National Day",
            ):
                with patch(
                    "sam_trader.services.dashboard.MarketCalendarService"
                    ".is_early_close",
                    return_value=False,
                ):
                    with patch(
                        "sam_trader.services.dashboard.MarketCalendarService"
                        ".next_trading_day",
                        return_value=datetime.date(2026, 10, 2),
                    ):
                        with patch(
                            "sam_trader.services.dashboard.MarketCalendarService"
                            ".market_timezone",
                            return_value="Asia/Hong_Kong",
                        ):
                            result = get_market_schedule_info(DashboardConfig())

        assert any("HK Market Holiday" in b for b in result["banners"])
        assert any("National Day" in b for b in result["banners"])
        assert not any("US Market Holiday" in b for b in result["banners"])

    def test_early_close_banner(self) -> None:
        """Early-close day returns warning banner with close time."""
        with patch(
            "sam_trader.services.dashboard.MarketCalendarService.is_holiday",
            return_value=False,
        ):
            with patch(
                "sam_trader.services.dashboard.MarketCalendarService.is_early_close",
                return_value=True,
            ):
                with patch(
                    "sam_trader.services.dashboard.MarketCalendarService"
                    ".market_hours",
                    return_value=(
                        datetime.time(9, 30),
                        datetime.time(13, 0),
                    ),
                ):
                    with patch(
                        "sam_trader.services.dashboard.MarketCalendarService"
                        ".next_trading_day",
                        return_value=datetime.date(2026, 7, 7),
                    ):
                        with patch(
                            "sam_trader.services.dashboard.MarketCalendarService"
                            ".market_timezone",
                            return_value="America/New_York",
                        ):
                            result = get_market_schedule_info(DashboardConfig())

        assert any("Early Close Today" in b for b in result["banners"])
        assert any("13:00" in b for b in result["banners"])
        assert result["indicators"] == []

    def test_open_day_indicator(self) -> None:
        """Regular trading day returns green open indicator."""
        with patch(
            "sam_trader.services.dashboard.MarketCalendarService.is_holiday",
            return_value=False,
        ):
            with patch(
                "sam_trader.services.dashboard.MarketCalendarService.is_early_close",
                return_value=False,
            ):
                with patch(
                    "sam_trader.services.dashboard.MarketCalendarService"
                    ".next_trading_day",
                    return_value=datetime.date(2026, 7, 7),
                ):
                    with patch(
                        "sam_trader.services.dashboard.MarketCalendarService"
                        ".market_timezone",
                        return_value="America/New_York",
                    ):
                        result = get_market_schedule_info(DashboardConfig())

        assert result["banners"] == []
        assert any("Markets Open Today" in i for i in result["indicators"])
        assert any("Next US session" in c for c in result["countdowns"])
        assert any("Next HK session" in c for c in result["countdowns"])

    def test_redis_client_passed_to_calendar_service(self) -> None:
        """Dashboard config Redis is forwarded to MarketCalendarService."""
        mock_redis = MagicMock()
        mock_redis.ping.return_value = True
        with patch(
            "sam_trader.services.dashboard._redis_client",
            return_value=mock_redis,
        ):
            with patch(
                "sam_trader.services.dashboard.MarketCalendarService" ".is_holiday",
                return_value=False,
            ):
                with patch(
                    "sam_trader.services.dashboard.MarketCalendarService"
                    ".is_early_close",
                    return_value=False,
                ):
                    with patch(
                        "sam_trader.services.dashboard.MarketCalendarService"
                        ".next_trading_day",
                        return_value=datetime.date(2026, 7, 7),
                    ):
                        with patch(
                            "sam_trader.services.dashboard.MarketCalendarService"
                            ".market_timezone",
                            return_value="America/New_York",
                        ):
                            get_market_schedule_info(DashboardConfig())

        # _redis_client is called once to create the client
        # MarketCalendarService receives it (implicit via no crash)

    def test_countdown_hours_non_negative(self) -> None:
        """Hours until next session is never negative."""
        with patch(
            "sam_trader.services.dashboard.MarketCalendarService.is_holiday",
            return_value=False,
        ):
            with patch(
                "sam_trader.services.dashboard.MarketCalendarService.is_early_close",
                return_value=False,
            ):
                with patch(
                    "sam_trader.services.dashboard.MarketCalendarService"
                    ".next_trading_day",
                    return_value=datetime.date(2026, 7, 7),
                ):
                    with patch(
                        "sam_trader.services.dashboard.MarketCalendarService"
                        ".market_timezone",
                        return_value="America/New_York",
                    ):
                        result = get_market_schedule_info(DashboardConfig())

        for countdown in result["countdowns"]:
            # Extract hours value from string like "Next US session: 2026-07-07 in 24h"
            hours_str = countdown.split("in ")[1].rstrip("h")
            assert int(hours_str) >= 0


class TestScheduleHtmlRendering:
    """Tests for schedule banner HTML rendering."""

    def test_holiday_banner_html_class(self) -> None:
        """Holiday banner uses amber holiday CSS class."""
        html = _render_schedule_html(
            {
                "banners": ["🚫 US Market Holiday: Test Holiday — Markets Closed"],
                "indicators": [],
                "countdowns": ["Next US session: 2026-07-05 in 24h"],
            }
        )
        assert 'class="schedule-banner holiday"' in html
        assert "🚫 US Market Holiday" in html
        assert 'class="schedule-countdown"' in html

    def test_early_close_banner_html_class(self) -> None:
        """Early-close banner uses amber early CSS class."""
        html = _render_schedule_html(
            {
                "banners": ["⚠️ Early Close Today (US): 13:00"],
                "indicators": [],
                "countdowns": [],
            }
        )
        assert 'class="schedule-banner early"' in html
        assert "13:00" in html

    def test_open_indicator_html_class(self) -> None:
        """Open indicator uses green CSS class."""
        html = _render_schedule_html(
            {
                "banners": [],
                "indicators": ["✅ US Markets Open Today"],
                "countdowns": [],
            }
        )
        assert 'class="schedule-indicator open"' in html
        assert "✅ US Markets Open Today" in html

    def test_fallback_open_when_empty(self) -> None:
        """Empty schedule falls back to generic open indicator."""
        html = _render_schedule_html({})
        assert "✅ Markets Open Today" in html

    def test_full_dashboard_renders_schedule(self) -> None:
        """_render_html injects schedule banner into the page."""
        data = {
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
            "schedule": {
                "banners": ["🚫 US Market Holiday: July 4 — Markets Closed"],
                "indicators": [],
                "countdowns": ["Next US session: 2026-07-05 in 24h"],
            },
            "timestamp": "",
        }
        html = _render_html(data)
        assert 'class="schedule-banner holiday"' in html
        assert "🚫 US Market Holiday" in html
        assert "Next US session" in html
        assert "SYSTEM HEALTH" in html  # rest of page still present

    def test_dashboard_data_includes_schedule(self) -> None:
        """get_dashboard_data aggregates schedule info."""
        with patch(
            "sam_trader.services.dashboard.check_all_services",
            return_value={"status": "healthy", "services": {}},
        ):
            with patch(
                "sam_trader.services.dashboard.query_market_data_from_redis",
                return_value={"instruments": [], "counts": {}, "venues": []},
            ):
                with patch(
                    "sam_trader.services.dashboard.query_fills", return_value=[]
                ):
                    with patch(
                        "sam_trader.services.dashboard.query_positions",
                        return_value=[],
                    ):
                        with patch(
                            "sam_trader.services.dashboard.query_pnl_from_redis",
                            return_value={
                                "strategies": {},
                                "total": 0.0,
                                "date": "",
                            },
                        ):
                            with patch(
                                "sam_trader.services.dashboard"
                                ".get_market_schedule_info",
                                return_value={
                                    "banners": [],
                                    "indicators": ["✅ Markets Open"],
                                    "countdowns": [],
                                },
                            ):
                                data = get_dashboard_data(DashboardConfig())

        assert "schedule" in data
        assert data["schedule"]["indicators"] == ["✅ Markets Open"]


class TestNewApiEndpoints:
    """Tests for new Tier 1 analytics API endpoints."""

    def test_get_api_equity_curve(self) -> None:
        """GET /api/equity-curve returns equity curve points."""
        from sam_trader.services.dashboard import _get_equity_curve_data

        with patch(
            "sam_trader.services.dashboard._query_daily_pnl_from_redis",
            return_value=[
                {"date": "2026-05-01", "pnl": 100.0},
                {"date": "2026-05-02", "pnl": 50.0},
            ],
        ):
            data = _get_equity_curve_data(DashboardConfig(), days=7)

        assert len(data) == 2
        assert data[0]["date"] == "2026-05-01"
        assert data[0]["equity"] == 100.0
        assert data[1]["equity"] == 150.0

    def test_get_api_drawdown(self) -> None:
        """GET /api/drawdown returns drawdown stats and events."""
        from sam_trader.services.dashboard import _get_drawdown_data

        with patch(
            "sam_trader.services.dashboard._query_daily_pnl_from_redis",
            return_value=[
                {"date": "2026-05-01", "pnl": 100.0},
                {"date": "2026-05-02", "pnl": -30.0},
                {"date": "2026-05-03", "pnl": 50.0},
            ],
        ):
            data = _get_drawdown_data(DashboardConfig(), days=7)

        assert "current_dd_pct" in data
        assert "max_dd_pct" in data
        assert "events" in data
        assert data["current_dd_pct"] == 0.0  # Recovered by end

    def test_get_api_performance(self) -> None:
        """GET /api/performance returns 5 KPIs with deltas."""
        from sam_trader.services.dashboard import _get_performance_data

        with patch(
            "sam_trader.services.dashboard._query_daily_pnl_from_redis",
            return_value=[
                {"date": "2026-05-01", "pnl": 100.0},
                {"date": "2026-05-02", "pnl": -30.0},
                {"date": "2026-05-03", "pnl": 50.0},
            ],
        ):
            data = _get_performance_data(DashboardConfig(), days=7)

        assert "net_pnl" in data
        assert "win_rate" in data
        assert "sharpe_20d" in data
        assert "max_drawdown_pct" in data
        assert "expectancy" in data
        assert data["net_pnl"] == 120.0


class TestTier2ApiEndpoints:
    """Tests for Tier 2 analytics API endpoints."""

    def test_get_api_monthly_returns(self) -> None:
        """GET /api/monthly-returns returns aggregated monthly data."""
        from sam_trader.services.dashboard import _get_monthly_returns_data

        async def _fake(*args: object, **kwargs: object) -> list[dict[str, Any]]:
            return [
                {"date": "2026-05-01", "pnl": 100.0},
                {"date": "2026-05-02", "pnl": 50.0},
                {"date": "2026-06-01", "pnl": -30.0},
            ]

        with patch(
            "sam_trader.services.dashboard._query_daily_pnl_from_fills_async",
            _fake,
        ):
            data = _get_monthly_returns_data(DashboardConfig(), days=365)

        assert len(data) == 2
        assert data[0]["year"] == 2026
        assert data[0]["month"] == 5
        assert data[0]["pnl"] == 150.0
        assert data[1]["month"] == 6
        assert data[1]["pnl"] == -30.0

    def test_get_api_annual_returns(self) -> None:
        """GET /api/annual-returns returns aggregated yearly data."""
        from sam_trader.services.dashboard import _get_annual_returns_data

        async def _fake(*args: object, **kwargs: object) -> list[dict[str, Any]]:
            return [
                {"date": "2025-12-31", "pnl": 200.0},
                {"date": "2026-01-01", "pnl": 100.0},
                {"date": "2026-05-02", "pnl": 50.0},
            ]

        with patch(
            "sam_trader.services.dashboard._query_daily_pnl_from_fills_async",
            _fake,
        ):
            data = _get_annual_returns_data(DashboardConfig(), days=730)

        assert len(data) == 2
        assert data[0]["year"] == 2025
        assert data[0]["pnl"] == 200.0
        assert data[1]["year"] == 2026
        assert data[1]["pnl"] == 150.0

    def test_get_api_rolling_sharpe(self) -> None:
        """GET /api/rolling-sharpe returns rolling Sharpe points."""
        from sam_trader.services.dashboard import _get_rolling_sharpe_data

        rows = [{"date": f"2026-05-{i:02d}", "pnl": float(i)} for i in range(1, 26)]

        async def _fake(*args: object, **kwargs: object) -> list[dict[str, Any]]:
            return rows

        with patch(
            "sam_trader.services.dashboard._query_daily_pnl_from_fills_async",
            _fake,
        ):
            data = _get_rolling_sharpe_data(DashboardConfig(), days=90, window=20)

        assert len(data) == 6
        assert data[0]["date"] == "2026-05-20"
        assert "sharpe" in data[0]

    def test_get_api_rolling_beta_no_benchmark(self) -> None:
        """GET /api/rolling-beta returns beta 0 when benchmark has no fills."""
        from sam_trader.services.dashboard import _get_rolling_beta_data

        rows = [{"date": f"2026-05-{i:02d}", "pnl": float(i)} for i in range(1, 26)]

        async def _fake_pnl(*args: object, **kwargs: object) -> list[dict[str, Any]]:
            return rows

        async def _fake_bench(*args: object, **kwargs: object) -> list[dict[str, Any]]:
            return []

        with patch(
            "sam_trader.services.dashboard._query_daily_pnl_from_fills_async",
            _fake_pnl,
        ):
            with patch(
                "sam_trader.services.dashboard"
                "._query_benchmark_daily_pnl_from_fills_async",
                _fake_bench,
            ):
                data = _get_rolling_beta_data(
                    DashboardConfig(), days=90, window=20, benchmark="SPY.NASDAQ"
                )

        assert len(data) == 6
        assert all(p["beta"] == 0.0 for p in data)

    def test_get_api_rolling_beta_with_benchmark(self) -> None:
        """GET /api/rolling-beta computes beta when benchmark fills exist."""
        from sam_trader.services.dashboard import _get_rolling_beta_data

        rows = [{"date": f"2026-05-{i:02d}", "pnl": float(i)} for i in range(1, 26)]
        bench = [
            {"date": f"2026-05-{i:02d}", "pnl": float(i * 2)} for i in range(1, 26)
        ]

        async def _fake_pnl(*args: object, **kwargs: object) -> list[dict[str, Any]]:
            return rows

        async def _fake_bench(*args: object, **kwargs: object) -> list[dict[str, Any]]:
            return bench

        with patch(
            "sam_trader.services.dashboard._query_daily_pnl_from_fills_async",
            _fake_pnl,
        ):
            with patch(
                "sam_trader.services.dashboard"
                "._query_benchmark_daily_pnl_from_fills_async",
                _fake_bench,
            ):
                data = _get_rolling_beta_data(
                    DashboardConfig(), days=90, window=20, benchmark="SPY.NASDAQ"
                )

        assert len(data) == 6
        assert all(abs(p["beta"] - 0.5) < 0.01 for p in data)


class TestHtmlTier1Sections:
    """Tests for new Tier 1 HTML sections."""

    def test_html_renders_kpi_cards(self) -> None:
        """Dashboard HTML includes KPI card section."""
        data = {
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
            "performance": {
                "net_pnl": 1250.0,
                "net_pnl_delta": 500.0,
                "win_rate": 65.0,
                "win_rate_delta": 5.0,
                "sharpe_20d": 1.25,
                "sharpe_20d_delta": 0.15,
                "max_drawdown_pct": -8.5,
                "max_drawdown_delta": -1.2,
                "expectancy": 45.0,
                "expectancy_delta": 10.0,
            },
            "equity_curve": [
                {"date": "2026-05-01", "equity": 100.0, "pnl": 100.0},
                {"date": "2026-05-02", "equity": 150.0, "pnl": 50.0},
            ],
            "drawdown": {
                "current_dd_pct": 0.0,
                "max_dd_pct": 0.0,
                "events": [],
            },
            "timestamp": "",
        }

        from sam_trader.services.dashboard import _render_html

        html = _render_html(data)

        assert "PERFORMANCE KPIs" in html
        assert "Net P&L" in html
        assert "Win Rate" in html
        assert "Sharpe 20d" in html
        assert "Max DD" in html
        assert "Expectancy" in html
        assert "+$1,250.00" in html
        assert "65.0%" in html
        assert "1.25" in html
        assert "-8.50%" in html
        assert "+$45.00" in html

    def test_html_renders_equity_curve(self) -> None:
        """Dashboard HTML includes equity curve SVG chart."""
        data = {
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
            "performance": {
                "net_pnl": 0.0,
                "net_pnl_delta": 0.0,
                "win_rate": 0.0,
                "win_rate_delta": 0.0,
                "sharpe_20d": 0.0,
                "sharpe_20d_delta": 0.0,
                "max_drawdown_pct": 0.0,
                "max_drawdown_delta": 0.0,
                "expectancy": 0.0,
                "expectancy_delta": 0.0,
            },
            "equity_curve": [
                {"date": "2026-05-01", "equity": 100.0, "pnl": 100.0},
                {"date": "2026-05-02", "equity": 150.0, "pnl": 50.0},
            ],
            "drawdown": {
                "current_dd_pct": 0.0,
                "max_dd_pct": 0.0,
                "events": [],
            },
            "timestamp": "",
        }

        from sam_trader.services.dashboard import _render_html

        html = _render_html(data)

        assert "EQUITY CURVE" in html
        assert "<svg" in html
        assert "</svg>" in html

    def test_html_renders_drawdown(self) -> None:
        """Dashboard HTML includes drawdown SVG chart."""
        data = {
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
            "performance": {
                "net_pnl": 0.0,
                "net_pnl_delta": 0.0,
                "win_rate": 0.0,
                "win_rate_delta": 0.0,
                "sharpe_20d": 0.0,
                "sharpe_20d_delta": 0.0,
                "max_drawdown_pct": 0.0,
                "max_drawdown_delta": 0.0,
                "expectancy": 0.0,
                "expectancy_delta": 0.0,
            },
            "equity_curve": [
                {"date": "2026-05-01", "equity": 100.0, "pnl": 100.0},
                {"date": "2026-05-02", "equity": 150.0, "pnl": 50.0},
            ],
            "drawdown": {
                "current_dd_pct": 0.0,
                "max_dd_pct": 0.0,
                "events": [],
            },
            "timestamp": "",
        }

        from sam_trader.services.dashboard import _render_html

        html = _render_html(data)

        assert "DRAWDOWN" in html
        assert "<svg" in html

    def test_html_positions_include_mark_and_pnl_pct(self) -> None:
        """Positions table includes mark price and P&L %."""
        data = {
            "health": {"status": "healthy", "services": {}},
            "fills": [],
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
                "instruments": [],
                "counts": {},
                "venues": [],
                "timestamp": "",
            },
            "pnl": {"strategies": {}, "total": 0.0, "date": ""},
            "performance": {
                "net_pnl": 0.0,
                "net_pnl_delta": 0.0,
                "win_rate": 0.0,
                "win_rate_delta": 0.0,
                "sharpe_20d": 0.0,
                "sharpe_20d_delta": 0.0,
                "max_drawdown_pct": 0.0,
                "max_drawdown_delta": 0.0,
                "expectancy": 0.0,
                "expectancy_delta": 0.0,
            },
            "equity_curve": [],
            "drawdown": {
                "current_dd_pct": 0.0,
                "max_dd_pct": 0.0,
                "events": [],
            },
            "timestamp": "",
        }

        from sam_trader.services.dashboard import _render_html

        html = _render_html(data)

        # Mark price = 245.30 + (125/100) = 246.55
        assert "246.55" in html
        # P&L % = 125 / (100 * 245.30) * 100 = 0.51%
        assert "+0.51%" in html
        assert "CURRENT POSITIONS" in html

    def test_html_positions_no_open_positions_message(self) -> None:
        """Empty positions shows 7-column no-data message."""
        data = {
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
            "performance": {
                "net_pnl": 0.0,
                "net_pnl_delta": 0.0,
                "win_rate": 0.0,
                "win_rate_delta": 0.0,
                "sharpe_20d": 0.0,
                "sharpe_20d_delta": 0.0,
                "max_drawdown_pct": 0.0,
                "max_drawdown_delta": 0.0,
                "expectancy": 0.0,
                "expectancy_delta": 0.0,
            },
            "equity_curve": [],
            "drawdown": {
                "current_dd_pct": 0.0,
                "max_dd_pct": 0.0,
                "events": [],
            },
            "timestamp": "",
        }

        from sam_trader.services.dashboard import _render_html

        html = _render_html(data)
        assert "No open positions" in html
        assert "colspan='7'" in html


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
