"""SAM Trader dashboard — read-only observability page.

Serves a single HTML page on port 8080 with auto-refresh (meta tag).
Data sources: PostgreSQL (fills, positions), Redis (P&L), docker (health).
"""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Awaitable, TypeVar

from sam_trader.services.db_schema import validate_schema

T = TypeVar("T")

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# HTML template (dark terminal theme)
# ---------------------------------------------------------------------------

_DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta http-equiv="refresh" content="30">
<title>SAM Trader Dashboard</title>
<style>
:root {
  --bg:#0d1117; --fg:#c9d1d9; --accent:#58a6ff;
  --green:#3fb950; --red:#f85149; --muted:#8b949e;
  --border:#30363d;
}
* { box-sizing:border-box; }
body {
  margin:0; padding:1rem;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  background:var(--bg); color:var(--fg); font-size:14px;
}
h1 {
  margin:0 0 1rem; font-size:1.4rem; color:var(--accent);
  border-bottom:1px solid var(--border); padding-bottom:.5rem;
}
h2 { margin:1.5rem 0 .5rem; font-size:1.1rem; color:var(--accent); }
.status {
  display:inline-block; width:10px; height:10px;
  border-radius:50%; margin-right:.4rem;
}
.up { background:var(--green); }
.down { background:var(--red); }
table { width:100%; border-collapse:collapse; margin-top:.5rem; }
th, td {
  padding:.4rem .6rem; text-align:left;
  border-bottom:1px solid var(--border);
}
th { color:var(--muted); font-weight:600; }
tr:hover { background:#161b22; }
.buy { color:var(--green); }
.sell { color:var(--red); }
.positive { color:var(--green); }
.negative { color:var(--red); }
.card {
  border:1px solid var(--border); border-radius:6px;
  padding:1rem; margin-bottom:1rem;
}
.health-grid {
  display:grid;
  grid-template-columns:repeat(auto-fit,minmax(140px,1fr));
  gap:.75rem;
}
.health-item { display:flex; align-items:center; }
.footer {
  margin-top:2rem; font-size:.75rem; color:var(--muted);
  border-top:1px solid var(--border); padding-top:.5rem;
}
@media (max-width:600px) {
  body { padding:.5rem; font-size:12px; }
  th,td { padding:.3rem .4rem; }
}
</style>
</head>
<body>
<h1>🚀 SAM Trader Dashboard</h1>

<div class="card">
<h2>SYSTEM HEALTH</h2>
<div class="health-grid">
  <div class="health-item">
    <span class="status {{pg_status_class}}"></span>PG: {{pg_status}}
  </div>
  <div class="health-item">
    <span class="status {{redis_status_class}}"></span>Redis: {{redis_status}}
  </div>
  <div class="health-item">
    <span class="status {{futu_status_class}}"></span>Futu OpenD: {{futu_status}}
  </div>
  <div class="health-item">
    <span class="status {{trader_status_class}}"></span>sam-trader: {{trader_status}}
  </div>
</div>
</div>

<div class="card">
<h2>TODAY'S FILLS (last 20)</h2>
<table>
<thead>
  <tr>
    <th>Time</th><th>Symbol</th><th>Side</th><th>Qty</th>
    <th>Price</th><th>Venue</th><th>Slippage</th><th>Strategy</th>
  </tr>
</thead>
<tbody>
{{fills_rows}}
</tbody>
</table>
</div>

<div class="card">
<h2>CURRENT POSITIONS</h2>
<table>
<thead>
  <tr>
    <th>Symbol</th><th>Venue</th><th>Net Qty</th>
    <th>Avg Px</th><th>Unrealized P&L</th><th>Strategy</th>
  </tr>
</thead>
<tbody>
{{positions_rows}}
</tbody>
</table>
</div>

<div class="card">
<h2>P&L SUMMARY</h2>
<table>
<thead><tr><th>Strategy</th><th>Realized P&L</th></tr></thead>
<tbody>
{{pnl_rows}}
</tbody>
<tfoot>
  <tr>
    <td><strong>TOTAL REALIZED</strong></td>
    <td class="{{total_pnl_class}}"><strong>{{total_pnl}}</strong></td>
  </tr>
</tfoot>
</table>
</div>

<div class="footer">
Refreshed: {{now}} UTC &nbsp;|&nbsp; Auto-refresh every 30s
</div>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DashboardConfig:
    """Dashboard runtime configuration from environment."""

    host: str = "0.0.0.0"
    port: int = 8080
    pg_host: str = "sam-postgres"
    pg_port: int = 5432
    pg_db: str = "sam_trader"
    pg_user: str = "sam"
    pg_password: str = "sam_secret"
    redis_host: str = "sam-redis"
    redis_port: int = 6379
    redis_password: str = ""
    futu_container: str = "sam-futu-opend"
    trader_container: str = "sam-trader"


# ---------------------------------------------------------------------------
# Health helpers
# ---------------------------------------------------------------------------


def _docker_container_status(container_name: str) -> dict[str, Any]:
    """Return container status via docker inspect, or DOWN on error."""
    try:
        result = subprocess.run(
            [
                "sudo",
                "docker",
                "inspect",
                "--format={{.State.Status}} {{.State.Health.Status}}",
                container_name,
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            parts = result.stdout.strip().split()
            status = parts[0] if parts else "unknown"
            health = parts[1] if len(parts) > 1 else "unknown"
            return {"status": status, "health": health}
    except Exception as exc:
        logger.debug("docker inspect %s failed: %s", container_name, exc)
    return {"status": "down", "health": "unknown"}


def _pg_status(config: DashboardConfig) -> dict[str, Any]:
    """Check PostgreSQL health."""
    try:
        import asyncpg

        async def _ping() -> bool:
            conn = await asyncpg.connect(
                host=config.pg_host,
                port=config.pg_port,
                database=config.pg_db,
                user=config.pg_user,
                password=config.pg_password,
                timeout=5,
            )
            try:
                row = await conn.fetchrow("SELECT 1")
                return row is not None
            finally:
                await conn.close()

        loop = asyncio.new_event_loop()
        try:
            ok = loop.run_until_complete(_ping())
        finally:
            loop.close()
        return {"status": "UP" if ok else "DOWN"}
    except Exception as exc:
        logger.debug("pg health check failed: %s", exc)
        return {"status": "DOWN"}


def _redis_client(config: DashboardConfig) -> Any:
    """Return a synchronous Redis client."""
    import redis as _redis  # type: ignore[import-untyped]

    return _redis.Redis(
        host=config.redis_host,
        port=config.redis_port,
        password=config.redis_password or None,
        socket_connect_timeout=5,
        decode_responses=True,
    )


def _redis_status(config: DashboardConfig) -> dict[str, Any]:
    """Check Redis health."""
    try:
        client = _redis_client(config)
        return {"status": "UP" if client.ping() else "DOWN"}
    except Exception as exc:
        logger.debug("redis health check failed: %s", exc)
        return {"status": "DOWN"}


def check_all_services(config: DashboardConfig | None = None) -> dict[str, Any]:
    """Return health status for all monitored services."""
    cfg = config or DashboardConfig()
    pg = _pg_status(cfg)
    redis = _redis_status(cfg)
    futu = _docker_container_status(cfg.futu_container)
    trader = _docker_container_status(cfg.trader_container)

    return {
        "status": (
            "healthy"
            if all(
                s["status"] in ("UP", "running", "healthy")
                for s in (pg, redis, futu, trader)
            )
            else "degraded"
        ),
        "services": {
            "postgres": pg,
            "redis": redis,
            "futu_opend": futu,
            "sam_trader": trader,
        },
    }


# ---------------------------------------------------------------------------
# Data queries
# ---------------------------------------------------------------------------


def _run_async(coro: Awaitable[T]) -> T:
    """Run an async coroutine synchronously."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


async def _query_fills_async(config: DashboardConfig) -> list[dict[str, Any]]:
    """Fetch last 20 fills from PostgreSQL."""
    import asyncpg

    conn = await asyncpg.connect(
        host=config.pg_host,
        port=config.pg_port,
        database=config.pg_db,
        user=config.pg_user,
        password=config.pg_password,
        timeout=10,
    )
    try:
        rows = await conn.fetch("""
            SELECT
                to_char(ts_event, 'HH24:MI:SS') AS time,
                instrument_id AS symbol,
                side,
                qty::text,
                price::text,
                venue,
                slippage::text,
                strategy_id AS strategy
            FROM fills
            WHERE ts_event >= CURRENT_DATE
            ORDER BY ts_event DESC
            LIMIT 20
            """)
        return [dict(r) for r in rows]
    finally:
        await conn.close()


async def _query_positions_async(config: DashboardConfig) -> list[dict[str, Any]]:
    """Fetch current positions from PostgreSQL."""
    import asyncpg

    conn = await asyncpg.connect(
        host=config.pg_host,
        port=config.pg_port,
        database=config.pg_db,
        user=config.pg_user,
        password=config.pg_password,
        timeout=10,
    )
    try:
        rows = await conn.fetch("""
            SELECT
                instrument_id AS symbol,
                venue,
                net_quantity::text AS net_qty,
                avg_px::text,
                unrealized_pnl::text,
                strategy_id AS strategy
            FROM positions
            WHERE net_quantity != 0
            ORDER BY updated_at DESC
            """)
        return [dict(r) for r in rows]
    finally:
        await conn.close()


def query_fills(config: DashboardConfig | None = None) -> list[dict[str, Any]]:
    """Synchronous wrapper for fills query."""
    cfg = config or DashboardConfig()
    try:
        return _run_async(_query_fills_async(cfg))
    except Exception as exc:
        logger.warning("fills query failed: %s", exc)
        return []


def query_positions(config: DashboardConfig | None = None) -> list[dict[str, Any]]:
    """Synchronous wrapper for positions query."""
    cfg = config or DashboardConfig()
    try:
        return _run_async(_query_positions_async(cfg))
    except Exception as exc:
        logger.warning("positions query failed: %s", exc)
        return []


def query_pnl_from_redis(config: DashboardConfig | None = None) -> dict[str, Any]:
    """Read per-strategy realized P&L from Redis."""
    cfg = config or DashboardConfig()
    try:
        client = _redis_client(cfg)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        pnl: dict[str, float] = {}
        for key in client.scan_iter(match=f"sam:pnl:*:{today}"):
            val = client.get(key)
            if val is not None:
                try:
                    strategy_id = key.split(":")[2]
                    pnl[strategy_id] = float(val)
                except (IndexError, ValueError):
                    continue
        return {
            "strategies": pnl,
            "total": round(sum(pnl.values()), 2),
            "date": today,
        }
    except Exception as exc:
        logger.warning("pnl query failed: %s", exc)
        return {"strategies": {}, "total": 0.0, "date": "", "error": str(exc)}


def get_dashboard_data(config: DashboardConfig | None = None) -> dict[str, Any]:
    """Aggregate all dashboard data sources."""
    cfg = config or DashboardConfig()
    return {
        "health": check_all_services(cfg),
        "fills": query_fills(cfg),
        "positions": query_positions(cfg),
        "pnl": query_pnl_from_redis(cfg),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# HTML rendering
# ---------------------------------------------------------------------------


def _fmt_num(v: str | None) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v):,.2f}"
    except ValueError:
        return str(v)


def _fmt_pnl(v: float) -> str:
    sign = "+" if v >= 0 else ""
    return f"{sign}${v:,.2f}"


def _pnl_class(v: float) -> str:
    return "positive" if v >= 0 else "negative"


def _render_html(data: dict[str, Any]) -> str:
    """Substitute data into the HTML template."""
    health = data.get("health", {})
    services = health.get("services", {})

    def _svc_status(name: str) -> str:
        s: dict[str, Any] = services.get(name, {})
        st: str = s.get("status", "unknown")
        return st.upper()

    def _svc_class(name: str) -> str:
        s: dict[str, Any] = services.get(name, {})
        st: str = s.get("status", "")
        return "up" if st in ("UP", "running", "healthy") else "down"

    # Fills rows
    fills_rows: list[str] = []
    for f in data.get("fills", []):
        side_cls = "buy" if f.get("side") == "BUY" else "sell"
        slip = _fmt_num(f.get("slippage"))
        fills_rows.append(
            f"<tr>"
            f"<td>{f.get('time', '')}</td>"
            f"<td>{f.get('symbol', '')}</td>"
            f"<td class='{side_cls}'>{f.get('side', '')}</td>"
            f"<td>{_fmt_num(f.get('qty'))}</td>"
            f"<td>{_fmt_num(f.get('price'))}</td>"
            f"<td>{f.get('venue', '')}</td>"
            f"<td>{slip}</td>"
            f"<td>{f.get('strategy', '')}</td>"
            f"</tr>"
        )

    # Positions rows
    positions_rows: list[str] = []
    for p in data.get("positions", []):
        upnl = p.get("unrealized_pnl")
        upnl_str = _fmt_num(upnl)
        upnl_cls = _pnl_class(float(upnl) if upnl is not None else 0.0)
        positions_rows.append(
            f"<tr>"
            f"<td>{p.get('symbol', '')}</td>"
            f"<td>{p.get('venue', '')}</td>"
            f"<td>{_fmt_num(p.get('net_qty'))}</td>"
            f"<td>{_fmt_num(p.get('avg_px'))}</td>"
            f"<td class='{upnl_cls}'>{upnl_str}</td>"
            f"<td>{p.get('strategy', '')}</td>"
            f"</tr>"
        )

    # P&L rows
    pnl_data = data.get("pnl", {})
    pnl_rows: list[str] = []
    for strategy, val in (pnl_data.get("strategies") or {}).items():
        pnl_rows.append(
            f"<tr>"
            f"<td>{strategy}</td>"
            f"<td class='{_pnl_class(val)}'>{_fmt_pnl(val)}</td>"
            f"</tr>"
        )
    if not pnl_rows:
        pnl_rows.append("<tr><td colspan='2'>No P&L data</td></tr>")

    total_pnl = pnl_data.get("total", 0.0)

    return (
        _DASHBOARD_HTML.replace("{{pg_status}}", _svc_status("postgres"))
        .replace("{{pg_status_class}}", _svc_class("postgres"))
        .replace("{{redis_status}}", _svc_status("redis"))
        .replace("{{redis_status_class}}", _svc_class("redis"))
        .replace("{{futu_status}}", _svc_status("futu_opend"))
        .replace("{{futu_status_class}}", _svc_class("futu_opend"))
        .replace("{{trader_status}}", _svc_status("sam_trader"))
        .replace("{{trader_status_class}}", _svc_class("sam_trader"))
        .replace(
            "{{fills_rows}}",
            (
                "\n".join(fills_rows)
                if fills_rows
                else "<tr><td colspan='8'>No fills today</td></tr>"
            ),
        )
        .replace(
            "{{positions_rows}}",
            (
                "\n".join(positions_rows)
                if positions_rows
                else "<tr><td colspan='6'>No open positions</td></tr>"
            ),
        )
        .replace("{{pnl_rows}}", "\n".join(pnl_rows))
        .replace("{{total_pnl}}", _fmt_pnl(total_pnl))
        .replace("{{total_pnl_class}}", _pnl_class(total_pnl))
        .replace(
            "{{now}}",
            datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        )
    )


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------


class DashboardHandler(BaseHTTPRequestHandler):
    """Handle GET /health, GET /api/dashboard, and serve dashboard.html."""

    def log_message(self, format: str, *args: Any) -> None:
        logger.debug(format, *args)

    def _send_json(self, status: int, data: dict[str, Any]) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def _send_html(self, status: int, html: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode())

    def do_GET(self) -> None:  # noqa: N802
        path = self.path
        if path == "/health":
            health = check_all_services()
            self._send_json(200, health)
        elif path == "/api/dashboard":
            data = get_dashboard_data()
            self._send_json(200, data)
        else:
            # Serve dashboard HTML for any other path
            data = get_dashboard_data()
            html = _render_html(data)
            self._send_html(200, html)


# ---------------------------------------------------------------------------
# Server entry point
# ---------------------------------------------------------------------------


def run_server(config: DashboardConfig | None = None) -> None:
    """Start the blocking HTTP server."""
    cfg = config or DashboardConfig()
    server = HTTPServer((cfg.host, cfg.port), DashboardHandler)
    logger.info("Dashboard server listening on http://%s:%d", cfg.host, cfg.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down dashboard server")
    finally:
        server.server_close()


def main() -> int:
    """Entry point for ``python -m sam_trader.services.dashboard``."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    if not validate_schema():
        # Do not start the dashboard when the schema is missing —
        # this surfaces the init failure immediately rather than
        # generating repeated WARNING logs every 30 seconds.
        return 1
    run_server()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
