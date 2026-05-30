# flake8: noqa: E501  — template strings (HTML/CSS/JS) exceed line length
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
from datetime import date, datetime, time, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Awaitable, TypeVar
from urllib.parse import parse_qs, urlparse

from sam_trader.services.backtest.dashboard_api import (
    handle_backtest_catalog_instruments,
    handle_backtest_catalog_status,
    handle_backtest_compare,
    handle_backtest_run,
    handle_backtest_run_status,
    handle_backtest_runs,
    handle_backtest_runs_detail,
)
from sam_trader.services.dashboard_analytics import (
    EquityPoint,
    compute_annual_returns,
    compute_drawdown,
    compute_equity_curve,
    compute_kpis,
    compute_monthly_returns,
    compute_rolling_beta,
    compute_rolling_sharpe,
    render_drawdown_svg,
    render_equity_curve_svg,
)
from sam_trader.services.db_schema import validate_schema
from sam_trader.services.market_calendar import MarketCalendarService
from sam_trader.services.restart_orchestrator import RestartOrchestrator

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
.schedule-section { margin-bottom:1rem; }
.schedule-banner {
  padding:.75rem 1rem; border-radius:6px; margin-bottom:.5rem;
  font-weight:600;
}
.schedule-banner.holiday { background:#d29922; color:#0d1117; }
.schedule-banner.early { background:#d29922; color:#0d1117; }
.schedule-indicator { font-size:.9rem; margin-bottom:.25rem; }
.schedule-indicator.open { color:var(--green); }
.schedule-countdown { color:var(--muted); font-size:.85rem; }
.kpi-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(160px,1fr));
  gap:.75rem; margin-bottom:1rem; }
.kpi-card { border:1px solid var(--border); border-radius:6px; padding:.75rem;
  text-align:center; }
.kpi-label { color:var(--muted); font-size:.75rem; text-transform:uppercase;
  margin-bottom:.25rem; }
.kpi-value { font-size:1.25rem; font-weight:700; }
.kpi-delta { font-size:.8rem; margin-top:.25rem; }
.chart-container { margin-top:.5rem; overflow-x:auto; }
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
.fresh { color:var(--green); }
.stale { color:#d29922; }
.old { color:var(--red); }
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
/* --- Backtest Dashboard --- */
.bt-tabs { display:flex; gap:.25rem; margin-bottom:1rem; border-bottom:1px solid var(--border); padding-bottom:0; }
.bt-tab { background:none; color:var(--muted); border:none; padding:.5rem 1rem; cursor:pointer; font-family:inherit; font-size:.85rem; border-bottom:2px solid transparent; transition:color .2s; }
.bt-tab:hover { color:var(--fg); }
.bt-tab.active { color:var(--accent); border-bottom-color:var(--accent); }
.bt-panel { display:none; }
.bt-panel.active { display:block; }
.bt-form-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(200px,1fr)); gap:.75rem; margin-bottom:.75rem; }
.bt-form-field { display:flex; flex-direction:column; gap:.25rem; }
.bt-form-field label { font-size:.75rem; color:var(--muted); text-transform:uppercase; }
.bt-form-field input,.bt-form-field select { background:#161b22; color:var(--fg); border:1px solid var(--border); border-radius:4px; padding:.4rem .5rem; font-family:inherit; font-size:.85rem; }
.bt-form-field select[multiple] { height:110px; }
.bt-toggles { display:flex; gap:1.5rem; margin-bottom:.75rem; align-items:center; }
.bt-toggles label { display:flex; align-items:center; gap:.35rem; font-size:.85rem; cursor:pointer; color:var(--muted); }
.bt-toggles input[type="checkbox"] { accent-color:var(--accent); }
.bt-sweep-params { margin-bottom:.75rem; display:none; }
.bt-sweep-params.active { display:block; }
.bt-sweep-row { display:flex; gap:.5rem; margin-bottom:.35rem; align-items:center; }
.bt-sweep-row input { background:#161b22; color:var(--fg); border:1px solid var(--border); border-radius:4px; padding:.25rem .4rem; font-family:inherit; font-size:.8rem; width:80px; }
.bt-sweep-row .bt-sweep-key { width:150px; }
.bt-sweep-row .bt-sweep-vals { width:250px; }
.bt-run-btn { background:var(--accent); color:#fff; border:none; border-radius:4px; padding:.5rem 1.5rem; font-family:inherit; font-size:.9rem; cursor:pointer; font-weight:600; }
.bt-run-btn:hover { opacity:.85; }
.bt-run-btn:disabled { opacity:.5; cursor:not-allowed; }
.bt-progress { margin-top:.75rem; background:#161b22; border-radius:4px; height:22px; position:relative; overflow:hidden; }
.bt-progress-bar { height:100%; background:var(--accent); width:0%; transition:width .3s; }
.bt-progress-text { position:absolute; top:0; left:50%; transform:translateX(-50%); font-size:.7rem; color:#fff; line-height:22px; text-shadow:0 0 3px rgba(0,0,0,.5); }
.bt-table-controls { display:flex; gap:.5rem; margin-bottom:.75rem; align-items:center; flex-wrap:wrap; }
.bt-table-controls input { flex:1; min-width:180px; background:#161b22; color:var(--fg); border:1px solid var(--border); border-radius:4px; padding:.4rem .5rem; font-family:inherit; font-size:.85rem; }
.bt-btn-small { background:var(--border); color:var(--fg); border:none; border-radius:4px; padding:.35rem .75rem; font-family:inherit; font-size:.8rem; cursor:pointer; white-space:nowrap; }
.bt-btn-small:hover { background:var(--muted); color:#fff; }
.bt-btn-small.primary { background:var(--accent); color:#fff; }
th.sortable { cursor:pointer; user-select:none; }
th.sortable:hover { color:var(--accent); }
th.sortable::after { content:' \21D5'; font-size:.7rem; }
th.sortable.asc::after { content:' \2191'; }
th.sortable.desc::after { content:' \2193'; }
.bt-run-checkbox { width:16px; height:16px; accent-color:var(--accent); }
.bt-compare-metric-table { margin-bottom:1rem; }
.bt-chart-container { position:relative; width:100%; height:300px; background:#161b22; border:1px solid var(--border); border-radius:6px; overflow:hidden; margin-bottom:.75rem; cursor:crosshair; }
.bt-chart-container canvas { display:block; width:100%; height:100%; }
.bt-chart-tooltip { position:absolute; background:#0d1117; border:1px solid var(--accent); border-radius:4px; padding:.35rem .6rem; font-size:.75rem; color:var(--fg); pointer-events:none; display:none; z-index:10; white-space:nowrap; }
.bt-trade-list { max-height:400px; overflow-y:auto; }
.bt-status-badge { display:inline-block; padding:.15rem .5rem; border-radius:3px; font-size:.75rem; font-weight:600; }
.bt-status-badge.completed { background:rgba(63,185,80,.15); color:var(--green); }
.bt-status-badge.running { background:rgba(88,166,255,.15); color:var(--accent); }
.bt-status-badge.failed { background:rgba(248,81,73,.15); color:var(--red); }
.bt-status-badge.started { background:rgba(139,148,158,.15); color:var(--muted); }
.bt-modal-overlay { position:fixed; top:0; left:0; right:0; bottom:0; background:rgba(0,0,0,.7); z-index:100; overflow-y:auto; padding:2rem; }
.bt-modal-content { max-width:900px; margin:0 auto; background:var(--bg); border:1px solid var(--border); border-radius:8px; padding:1.5rem; }
.bt-metric-grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(140px,1fr)); gap:.5rem; margin-bottom:1rem; }
.bt-metric-card { border:1px solid var(--border); border-radius:4px; padding:.5rem; text-align:center; }
.bt-metric-card .bt-metric-label { font-size:.7rem; color:var(--muted); text-transform:uppercase; }
.bt-metric-card .bt-metric-value { font-size:1.1rem; font-weight:700; }
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

{{schedule_banner}}

<div class="card">
<h2>PERFORMANCE KPIs</h2>
<div class="kpi-grid">
  <div class="kpi-card">
    <div class="kpi-label">Net P&L</div>
    <div class="kpi-value {{kpi_net_pnl_class}}">{{kpi_net_pnl}}</div>
    <div class="kpi-delta {{kpi_net_pnl_delta_class}}">{{kpi_net_pnl_delta}}</div>
  </div>
  <div class="kpi-card">
    <div class="kpi-label">Win Rate</div>
    <div class="kpi-value">{{kpi_win_rate}}</div>
    <div class="kpi-delta {{kpi_win_rate_delta_class}}">{{kpi_win_rate_delta}}</div>
  </div>
  <div class="kpi-card">
    <div class="kpi-label">Sharpe 20d</div>
    <div class="kpi-value">{{kpi_sharpe}}</div>
    <div class="kpi-delta {{kpi_sharpe_delta_class}}">{{kpi_sharpe_delta}}</div>
  </div>
  <div class="kpi-card">
    <div class="kpi-label">Max DD</div>
    <div class="kpi-value negative">{{kpi_max_dd}}</div>
    <div class="kpi-delta {{kpi_max_dd_delta_class}}">{{kpi_max_dd_delta}}</div>
  </div>
  <div class="kpi-card">
    <div class="kpi-label">Expectancy</div>
    <div class="kpi-value {{kpi_expectancy_class}}">{{kpi_expectancy}}</div>
    <div class="kpi-delta {{kpi_expectancy_delta_class}}">{{kpi_expectancy_delta}}</div>
  </div>
</div>
</div>

<div class="card">
<h2>EQUITY CURVE</h2>
<div class="chart-container">
{{equity_curve_svg}}
</div>
</div>

<div class="card">
<h2>DRAWDOWN</h2>
<div class="chart-container">
{{drawdown_svg}}
</div>
</div>

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

<div class="card" id="market-data-card">
<h2 style="cursor:pointer;" onclick="toggleMarketData()">
  MARKET DATA <span id="md-toggle">▼</span>
</h2>
<div id="market-data-summary" style="display:none;">
  <span id="md-summary-text">{{market_data_summary}}</span>
</div>
<div id="market-data-detail" style="display:none;">
<table>
<thead>
  <tr>
    <th>Instrument</th><th>Last Bar</th><th>Count Today</th><th>Staleness</th>
  </tr>
</thead>
<tbody>
{{market_data_rows}}
</tbody>
</table>
{{venue_conn_rows}}
<div id="recent-bars-section" style="margin-top:1rem;">
  <h3 style="font-size:1rem; color:var(--accent);">Recent Bars</h3>
  <table id="recent-bars-table">
    <thead>
      <tr>
        <th>Instrument</th><th>Time</th><th>Open</th>
        <th>High</th><th>Low</th><th>Close</th><th>Volume</th>
      </tr>
    </thead>
    <tbody><tr><td colspan="7">Click to load details...</td></tr></tbody>
  </table>
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
    <th>Symbol</th><th>Venue</th><th>Qty</th>
    <th>Avg Px</th><th>Mark</th><th>Unrealized P&L</th><th>P&L %</th>
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

<!-- ====== BACKTEST DASHBOARD ====== -->
<div class="card" id="backtest-card">
<h2>📊 BACKTEST</h2>
<div class="bt-tabs">
  <button class="bt-tab active" onclick="switchBtTab('runner')">▶ Runner</button>
  <button class="bt-tab" onclick="switchBtTab('results')">📋 Results</button>
  <button class="bt-tab" onclick="switchBtTab('compare')">📊 Compare</button>
</div>

<!-- RUNNER -->
<div id="bt-panel-runner" class="bt-panel active">
  <div class="bt-form-grid">
    <div class="bt-form-field">
      <label>Instruments (Ctrl/Cmd+click to multi-select)</label>
      <select id="bt-instruments" multiple></select>
    </div>
    <div class="bt-form-field">
      <label>Strategy ID</label>
      <input type="text" id="bt-strategy-id" placeholder="e.g., tsla-orb-15m-futu">
    </div>
    <div class="bt-form-field">
      <label>Start Date</label>
      <input type="date" id="bt-start">
    </div>
    <div class="bt-form-field">
      <label>End Date</label>
      <input type="date" id="bt-end">
    </div>
  </div>
  <div class="bt-toggles">
    <label><input type="checkbox" id="bt-sweep-toggle" onchange="toggleSweepParams()"> Parameter Sweep</label>
    <label><input type="checkbox" id="bt-wf-toggle"> Walk-Forward</label>
  </div>
  <div id="bt-sweep-params" class="bt-sweep-params">
    <div class="bt-sweep-row"><input class="bt-sweep-key" placeholder="param (e.g. stop_loss_ticks)"><input class="bt-sweep-vals" placeholder="values (e.g. 5,10,15)"><button class="bt-btn-small" onclick="removeSweepRow(this)">✕</button></div>
    <button class="bt-btn-small" onclick="addSweepRow()" style="margin-top:.25rem;">+ Add Parameter</button>
  </div>
  <div id="bt-wf-params" style="display:none; margin-bottom:.75rem;">
    <div class="bt-form-grid" style="grid-template-columns:repeat(auto-fit,minmax(120px,1fr));">
      <div class="bt-form-field"><label>Train Days</label><input type="number" id="bt-wf-train" value="90" min="10"></div>
      <div class="bt-form-field"><label>Test Days</label><input type="number" id="bt-wf-test" value="30" min="5"></div>
    </div>
  </div>
  <div style="display:flex; gap:.75rem; align-items:center;">
    <button id="bt-run-btn" class="bt-run-btn" onclick="submitBacktest()">▶ Run Backtest</button>
    <span id="bt-run-error" style="color:var(--red); font-size:.85rem; display:none;"></span>
  </div>
  <div id="bt-progress" class="bt-progress" style="display:none;">
    <div id="bt-progress-bar" class="bt-progress-bar" style="width:0%;"></div>
    <span id="bt-progress-text" class="bt-progress-text">0%</span>
  </div>
</div>

<!-- RESULTS -->
<div id="bt-panel-results" class="bt-panel">
  <div class="bt-table-controls">
    <input type="text" id="bt-results-filter" placeholder="Filter by strategy, instrument, or run ID..." oninput="filterResultsTable()">
    <button class="bt-btn-small" onclick="loadResults()">🔄 Refresh</button>
    <button id="bt-select-compare" class="bt-btn-small primary" onclick="addSelectedToCompare()" style="display:none;">📊 Add to Compare</button>
    <button class="bt-btn-small" onclick="exportResultsCSV()">⬇ CSV</button>
    <button class="bt-btn-small" onclick="exportResultsJSON()">⬇ JSON</button>
  </div>
  <div style="overflow-x:auto;">
  <table id="bt-results-table">
    <thead>
      <tr>
        <th><input type="checkbox" id="bt-select-all" class="bt-run-checkbox" onchange="toggleSelectAll()" title="Select all"></th>
        <th class="sortable" onclick="sortResultsTable('run_id')">Run ID</th>
        <th class="sortable" onclick="sortResultsTable('strategy_id')">Strategy</th>
        <th class="sortable" onclick="sortResultsTable('instrument_id')">Instrument</th>
        <th class="sortable" onclick="sortResultsTable('bar_type')">Bar Type</th>
        <th class="sortable" onclick="sortResultsTable('start_date')">Start</th>
        <th class="sortable" onclick="sortResultsTable('end_date')">End</th>
        <th class="sortable" onclick="sortResultsTable('status')">Status</th>
        <th class="sortable" onclick="sortResultsTable('elapsed_secs')">Elapsed</th>
        <th>Actions</th>
      </tr>
    </thead>
    <tbody id="bt-results-tbody">
      <tr><td colspan="10" style="color:var(--muted);">Loading...</td></tr>
    </tbody>
  </table>
  </div>
</div>

<!-- COMPARE -->
<div id="bt-panel-compare" class="bt-panel">
  <div class="bt-table-controls">
    <span id="bt-compare-count" style="color:var(--muted); font-size:.85rem;">Select runs from Results tab, then click "Add to Compare"</span>
    <button class="bt-btn-small primary" onclick="runComparison()" id="bt-compare-btn" style="display:none;">▶ Run Comparison</button>
    <button class="bt-btn-small" onclick="clearCompare()">✕ Clear</button>
  </div>
  <div id="bt-compare-metric-table"></div>
  <div id="bt-compare-charts"></div>
</div>
</div>

<!-- DETAIL MODAL -->
<div id="bt-detail-modal" class="bt-modal-overlay" style="display:none;">
  <div class="bt-modal-content">
    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:1rem;">
      <h3 id="bt-detail-title" style="margin:0; color:var(--accent);">Run Detail</h3>
      <button class="bt-btn-small" onclick="closeDetail()" style="font-size:1.2rem; padding:.25rem .6rem;">✕</button>
    </div>
    <div id="bt-detail-body"></div>
  </div>
</div>

<div class="footer">
Refreshed: {{now}} UTC &nbsp;|&nbsp; Auto-refresh every 30s
</div>
<script>
(function(){
  var mdExpanded = sessionStorage.getItem('mdExpanded') === 'true';
  var summary = document.getElementById('market-data-summary');
  var detail = document.getElementById('market-data-detail');
  var toggle = document.getElementById('md-toggle');
  if (mdExpanded) {
    summary.style.display = 'none';
    detail.style.display = 'block';
    toggle.textContent = '▲';
    loadRecentBars();
    window.mdRefreshInterval = setInterval(loadRecentBars, 10000);
  } else {
    summary.style.display = 'block';
    detail.style.display = 'none';
  }
})();

function toggleMarketData() {
  var summary = document.getElementById('market-data-summary');
  var detail = document.getElementById('market-data-detail');
  var toggle = document.getElementById('md-toggle');
  var isHidden = detail.style.display === 'none';
  detail.style.display = isHidden ? 'block' : 'none';
  summary.style.display = isHidden ? 'none' : 'block';
  toggle.textContent = isHidden ? '▲' : '▼';
  sessionStorage.setItem('mdExpanded', isHidden ? 'true' : 'false');
  if (isHidden) {
    loadRecentBars();
    if (!window.mdRefreshInterval) {
      window.mdRefreshInterval = setInterval(loadRecentBars, 10000);
    }
  } else {
    if (window.mdRefreshInterval) {
      clearInterval(window.mdRefreshInterval);
      window.mdRefreshInterval = null;
    }
  }
}

async function loadRecentBars() {
  var tbody = document.querySelector('#recent-bars-table tbody');
  try {
    var resp = await fetch('/api/bars/recent?seconds=300');
    var data = await resp.json();
    if (!data.bars || data.bars.length === 0) {
      tbody.innerHTML = '<tr><td colspan="7">No recent bars</td></tr>';
      return;
    }
    tbody.innerHTML = data.bars.map(function(b) {
      return '<tr>' +
        '<td>' + (b.instrument_id || '') + '</td>' +
        '<td>' + ((b.ts || '').split('+')[0]) + '</td>' +
        '<td>' + (b.open || '') + '</td>' +
        '<td>' + (b.high || '') + '</td>' +
        '<td>' + (b.low || '') + '</td>' +
        '<td>' + (b.close || '') + '</td>' +
        '<td>' + (b.volume || '') + '</td>' +
      '</tr>';
    }).join('');
  } catch (e) {
    tbody.innerHTML = '<tr><td colspan="7">Error loading bars: ' +
      (e.message || e) + '</td></tr>';
  }
}

// ===== BACKTEST DASHBOARD =====
var _btCompareRunIds = [];
var _btResultsAll = [];
var _btResultsSortCol = 'created_at';
var _btResultsSortDir = 'desc';
var _btPollTimer = null;

function switchBtTab(name) {
  document.querySelectorAll('#backtest-card .bt-tab').forEach(function(t){t.classList.remove('active');});
  document.querySelectorAll('#backtest-card .bt-panel').forEach(function(p){p.classList.remove('active');});
  var btn = document.querySelector('#backtest-card .bt-tab:nth-child(' +
    ({runner:1,results:2,compare:3}[name]||1) + ')');
  if(btn) btn.classList.add('active');
  var panel = document.getElementById('bt-panel-'+name);
  if(panel) panel.classList.add('active');
  if(name==='results') loadResults();
  if(name==='compare') refreshCompareUI();
}

// --- Instruments ---
(function(){fetch('/api/backtest/catalog/instruments').then(function(r){return r.json();}).then(function(d){
  var s=document.getElementById('bt-instruments');s.innerHTML='';
  (d||[]).forEach(function(i){var o=document.createElement('option');
    o.value=i.instrument_id;o.textContent=i.instrument_id+'  ('+(i.bar_types||[]).join(', ')+')';s.appendChild(o);});
}).catch(function(){});})();

// --- Sweep Params ---
function toggleSweepParams(){
  document.getElementById('bt-sweep-params').classList.toggle('active',document.getElementById('bt-sweep-toggle').checked);
}
document.getElementById('bt-wf-toggle').addEventListener('change',function(){
  document.getElementById('bt-wf-params').style.display=this.checked?'block':'none';
});
function addSweepRow(){
  var c=document.getElementById('bt-sweep-params');
  var r=document.createElement('div');r.className='bt-sweep-row';
  r.innerHTML='<input class="bt-sweep-key" placeholder="param"><input class="bt-sweep-vals" placeholder="values"><button class="bt-btn-small" onclick="removeSweepRow(this)">✕</button>';
  c.insertBefore(r,c.lastElementChild);
}
function removeSweepRow(b){b.parentElement.remove();}

// --- Submit ---
function submitBacktest(){
  var btn=document.getElementById('bt-run-btn');
  var err=document.getElementById('bt-run-error');
  var prog=document.getElementById('bt-progress');
  var sel=document.getElementById('bt-instruments');
  var instIds=[];for(var i=0;i<sel.options.length;i++){if(sel.options[i].selected)instIds.push(sel.options[i].value);}
  var sid=document.getElementById('bt-strategy-id').value.trim();
  var start=document.getElementById('bt-start').value;
  var end=document.getElementById('bt-end').value;
  err.style.display='none';
  if(!sid){err.textContent='Strategy ID is required';err.style.display='block';return;}
  if(instIds.length===0){err.textContent='Select at least one instrument';err.style.display='block';return;}
  if(!start||!end){err.textContent='Start and End dates are required';err.style.display='block';return;}
  btn.disabled=true;btn.textContent='Starting...';
  prog.style.display='block';document.getElementById('bt-progress-bar').style.width='0%';
  document.getElementById('bt-progress-text').textContent='0%';
  var body={strategy_id:sid,instrument_ids:instIds,start:start,end:end};
  var sweepCheck=document.getElementById('bt-sweep-toggle').checked;
  var wfCheck=document.getElementById('bt-wf-toggle').checked;
  fetch('/api/backtest/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})
    .then(function(r){return r.json();})
    .then(function(d){
      if(d.error){err.textContent=d.error;err.style.display='block';btn.disabled=false;btn.textContent='▶ Run Backtest';prog.style.display='none';return;}
      pollBacktest(d.run_id);
    })
    .catch(function(e){err.textContent='Network error: '+e.message;err.style.display='block';btn.disabled=false;btn.textContent='▶ Run Backtest';prog.style.display='none';});
}

function pollBacktest(runId){
  if(_btPollTimer) clearInterval(_btPollTimer);
  var btn=document.getElementById('bt-run-btn');
  _btPollTimer=setInterval(function(){
    fetch('/api/backtest/run/'+runId+'/status')
      .then(function(r){return r.json();})
      .then(function(d){
        var pct=d.progress_pct||0;
        document.getElementById('bt-progress-bar').style.width=pct+'%';
        document.getElementById('bt-progress-text').textContent=pct+'%';
        if(d.status==='completed'){
          clearInterval(_btPollTimer);_btPollTimer=null;
          btn.disabled=false;btn.textContent='▶ Run Backtest';
          document.getElementById('bt-run-error').style.display='none';
          document.getElementById('bt-progress-text').textContent='Done!';
          loadResults();
        }else if(d.status==='failed'){
          clearInterval(_btPollTimer);_btPollTimer=null;
          btn.disabled=false;btn.textContent='▶ Run Backtest';
          document.getElementById('bt-run-error').textContent='Failed: '+(d.error||'unknown error');
          document.getElementById('bt-run-error').style.display='block';
          document.getElementById('bt-progress-text').textContent='Failed';
        }
      }).catch(function(){});
  },1500);
}

// --- Results Table ---
function loadResults(){
  fetch('/api/backtest/runs?limit=200')
    .then(function(r){return r.json();})
    .then(function(d){
      _btResultsAll=d||[];
      renderResultsTable(_btResultsAll);
    }).catch(function(){});
}

function renderResultsTable(rows){
  var tbody=document.getElementById('bt-results-tbody');
  if(!rows.length){tbody.innerHTML='<tr><td colspan="10" style="color:var(--muted);">No backtest runs found</td></tr>';return;}
  tbody.innerHTML=rows.map(function(r){
    var badge='<span class="bt-status-badge '+escHtml(r.status||'')+'">'+(r.status||'?')+'</span>';
    return '<tr>'+
      '<td><input type="checkbox" class="bt-run-checkbox bt-cb" value="'+escHtml(r.run_id||'')+'" onchange="onCheckboxChange()"></td>'+
      '<td>'+escHtml(r.run_id||'')+'</td>'+
      '<td>'+escHtml(r.strategy_id||'')+'</td>'+
      '<td>'+escHtml(r.instrument_id||'')+'</td>'+
      '<td>'+escHtml(r.bar_type||'')+'</td>'+
      '<td>'+escHtml(r.start_date||'')+'</td>'+
      '<td>'+escHtml(r.end_date||'')+'</td>'+
      '<td>'+badge+'</td>'+
      '<td>'+(r.elapsed_secs!=null?parseFloat(r.elapsed_secs).toFixed(1)+'s':'—')+'</td>'+
      '<td><button class="bt-btn-small" onclick="showRunDetail(\''+escHtml(r.run_id||'')+'\')">Detail</button></td>'+
    '</tr>';
  }).join('');
}

function escHtml(s){if(!s)return'';return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');}

function onCheckboxChange(){
  var cbs=document.querySelectorAll('.bt-cb:checked');
  document.getElementById('bt-select-compare').style.display=cbs.length>=2?'inline-block':'none';
}

function toggleSelectAll(){
  var checked=document.getElementById('bt-select-all').checked;
  document.querySelectorAll('.bt-cb').forEach(function(c){c.checked=checked;});
  onCheckboxChange();
}

function addSelectedToCompare(){
  var ids=[];
  document.querySelectorAll('.bt-cb:checked').forEach(function(c){ids.push(c.value);});
  ids.forEach(function(id){if(_btCompareRunIds.indexOf(id)===-1)_btCompareRunIds.push(id);});
  document.getElementById('bt-compare-count').textContent=_btCompareRunIds.length+' run(s) selected';
  document.getElementById('bt-compare-btn').style.display='inline-block';
}

function filterResultsTable(){
  var q=document.getElementById('bt-results-filter').value.toLowerCase();
  var filtered=_btResultsAll.filter(function(r){
    return (r.run_id||'').toLowerCase().indexOf(q)!==-1||
           (r.strategy_id||'').toLowerCase().indexOf(q)!==-1||
           (r.instrument_id||'').toLowerCase().indexOf(q)!==-1||
           (r.bar_type||'').toLowerCase().indexOf(q)!==-1;
  });
  renderResultsTable(filtered);
}

function sortResultsTable(col){
  if(_btResultsSortCol===col){_btResultsSortDir=_btResultsSortDir==='asc'?'desc':'asc';}
  else{_btResultsSortCol=col;_btResultsSortDir='asc';}
  document.querySelectorAll('#bt-results-table th.sortable').forEach(function(th){th.classList.remove('asc','desc');});
  var th=document.querySelector('#bt-results-table th.sortable[onclick*="'+col+'"]');
  if(th)th.classList.add(_btResultsSortDir);
  var sorted=_btResultsAll.slice().sort(function(a,b){
    var va=a[col]||'',vb=b[col]||'';
    if(col==='elapsed_secs'){va=parseFloat(va)||0;vb=parseFloat(vb)||0;}
    if(va<vb)return _btResultsSortDir==='asc'?-1:1;
    if(va>vb)return _btResultsSortDir==='asc'?1:-1;
    return 0;
  });
  renderResultsTable(sorted);
}

// --- Export ---
function exportResultsJSON(){
  var blob=new Blob([JSON.stringify(_btResultsAll,null,2)],{type:'application/json'});
  downloadBlob(blob,'backtest_results.json');
}
function exportResultsCSV(){
  var headers=['run_id','strategy_id','instrument_id','bar_type','start_date','end_date','status','elapsed_secs','strategy_family','created_at'];
  var lines=[headers.join(',')];
  _btResultsAll.forEach(function(r){lines.push(headers.map(function(h){return csvEscape(r[h]||'');}).join(','));});
  var blob=new Blob([lines.join('\n')],{type:'text/csv'});
  downloadBlob(blob,'backtest_results.csv');
}
function csvEscape(v){v=String(v);if(v.indexOf(',')!==-1||v.indexOf('"')!==-1||v.indexOf('\n')!==-1){return'"'+v.replace(/"/g,'""')+'"';}return v;}
function downloadBlob(blob,name){var a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=name;a.click();URL.revokeObjectURL(a.href);}

// --- Run Detail Modal ---
function showRunDetail(runId){
  fetch('/api/backtest/runs/'+runId)
    .then(function(r){return r.json();})
    .then(function(d){
      if(d.error){alert(d.error);return;}
      document.getElementById('bt-detail-title').textContent='Run: '+runId;
      var body=document.getElementById('bt-detail-body');
      var sr=d.stats_returns||{};
      var sp=d.stats_pnls||{};
      var ec=d.equity_curve||[];
      var metrics=[
        ['Sharpe Ratio',fmtNum(sr.sharpe_ratio,3)],
        ['Sortino',fmtNum(sr.sortino_ratio,3)],
        ['Max DD',fmtPct(sr.max_drawdown)],
        ['Win Rate',fmtPct(sr.win_rate)],
        ['Profit Factor',fmtNum(sr.profit_factor,2)],
        ['Expectancy',fmtNum(sr.expectancy,4)],
        ['Total P&L',fmtDollar(sr.total_pnl)],
        ['CAGR',fmtPct(sr.cagr)],
        ['Calmar',fmtNum(sr.calmar_ratio,3)],
        ['Volatility',fmtPct(sr.volatility)],
        ['Total Events',d.total_events||'—'],
        ['Total Orders',d.total_orders||'—'],
        ['Elapsed',d.elapsed_secs!=null?d.elapsed_secs+'s':'—']
      ];
      body.innerHTML='<div class="bt-metric-grid">'+metrics.map(function(m){
        return '<div class="bt-metric-card"><div class="bt-metric-label">'+m[0]+'</div><div class="bt-metric-value">'+m[1]+'</div></div>';
      }).join('')+'</div>'+
        '<h4 style="margin:1rem 0 .5rem; color:var(--accent);">Equity Curve</h4>'+
        '<div class="bt-chart-container" id="bt-detail-chart"><canvas id="bt-detail-canvas"></canvas><div class="bt-chart-tooltip" id="bt-detail-tt"></div></div>'+
        '<div style="display:flex; gap:.5rem; margin-top:.25rem;">'+
          '<button class="bt-btn-small" onclick="exportRunJSON(\''+escHtml(runId)+'\')">⬇ JSON</button>'+
          '<button class="bt-btn-small" onclick="exportRunCSV(\''+escHtml(runId)+'\')">⬇ CSV</button>'+
        '</div>';
      document.getElementById('bt-detail-modal').style.display='block';
      setTimeout(function(){drawEquityCurve('bt-detail-canvas','bt-detail-tt',ec);},100);
    }).catch(function(e){alert('Error: '+e.message);});
}
function closeDetail(){document.getElementById('bt-detail-modal').style.display='none';}
function fmtNum(v,dec){if(v==null)return'—';return Number(v).toFixed(dec);}
function fmtPct(v){if(v==null)return'—';return (Number(v)*100).toFixed(2)+'%';}
function fmtDollar(v){if(v==null)return'—';return'$'+Number(v).toLocaleString('en-US',{minimumFractionDigits:2,maximumFractionDigits:2});}

function exportRunJSON(runId){
  fetch('/api/backtest/runs/'+runId).then(function(r){return r.json();}).then(function(d){
    var blob=new Blob([JSON.stringify(d,null,2)],{type:'application/json'});
    downloadBlob(blob,'backtest_'+runId+'.json');
  });
}
function exportRunCSV(runId){
  fetch('/api/backtest/runs/'+runId).then(function(r){return r.json();}).then(function(d){
    var sr=d.stats_returns||{};
    var ec=d.equity_curve||[];
    var lines=['metric,value'];
    for(var k in sr){lines.push(k+','+sr[k]);}
    lines.push('');lines.push('date,equity');
    (Array.isArray(ec)?ec:[]).forEach(function(p){
      if(p&&p.length>=2)lines.push(p[0]+','+p[1]);
    });
    var blob=new Blob([lines.join('\n')],{type:'text/csv'});
    downloadBlob(blob,'backtest_'+runId+'.csv');
  });
}

// --- Compare ---
function clearCompare(){_btCompareRunIds=[];refreshCompareUI();}
function refreshCompareUI(){
  document.getElementById('bt-compare-count').textContent=_btCompareRunIds.length+' run(s) selected';
  document.getElementById('bt-compare-btn').style.display=_btCompareRunIds.length>=2?'inline-block':'none';
  document.getElementById('bt-compare-metric-table').innerHTML='';
  document.getElementById('bt-compare-charts').innerHTML='';
}

function runComparison(){
  if(_btCompareRunIds.length<2)return;
  var url='/api/backtest/compare?runs='+_btCompareRunIds.join(',');
  fetch(url).then(function(r){return r.json();}).then(function(d){
    renderComparison(d);
  }).catch(function(e){console.error(e);});
}

function renderComparison(data){
  // Metric table
  var comparison=data.comparison||[];
  var runIds=[];for(var k in (data.runs||{})){runIds.push(k);}
  var metricTable='<div style="overflow-x:auto;"><table><thead><tr><th>Metric</th>'+runIds.map(function(id){return'<th>'+escHtml(id)+'</th>';}).join('')+'</tr></thead><tbody>';
  comparison.forEach(function(row){
    metricTable+='<tr><td>'+escHtml(row.metric)+'</td>';
    runIds.forEach(function(id){
      var v=row[id];
      var display=v!=null?String(v):'—';
      if(['sharpe_ratio','sortino_ratio','calmar_ratio'].indexOf(row.metric)!==-1)display=fmtNum(v,3);
      if(['max_drawdown','win_rate','cagr','volatility'].indexOf(row.metric)!==-1)display=fmtPct(v);
      if(['total_pnl','expectancy'].indexOf(row.metric)!==-1)display=fmtDollar(v);
      if(row.metric==='elapsed_secs')display=v!=null?Number(v).toFixed(1)+'s':'—';
      metricTable+='<td>'+display+'</td>';
    });
    metricTable+='</tr>';
  });
  metricTable+='</tbody></table></div>';
  document.getElementById('bt-compare-metric-table').innerHTML=metricTable;

  // Equity curve charts (overlaid)
  var chartsHtml='<h3 style="color:var(--accent); margin:1rem 0 .5rem;">Equity Curves Overlay</h3>';
  chartsHtml+='<div class="bt-chart-container" id="bt-compare-chart" style="height:350px;"><canvas id="bt-compare-canvas"></canvas><div class="bt-chart-tooltip" id="bt-compare-tt"></div></div>';
  document.getElementById('bt-compare-charts').innerHTML=chartsHtml;

  // Draw overlaid equity curves
  setTimeout(function(){
    var curves=[];
    var colors=['#58a6ff','#3fb950','#f85149','#d29922','#bc8cff','#79c0ff'];
    for(var i=0;i<runIds.length;i++){
      var r=data.runs[runIds[i]];
      var ec=r&&r.equity_curve?r.equity_curve:[];
      if(ec.length)curves.push({label:runIds[i],data:ec,color:colors[i%colors.length]});
    }
    if(curves.length)drawMultiEquityCurve('bt-compare-canvas','bt-compare-tt',curves);
  },100);
}

// --- Canvas Chart (single equity curve) ---
function drawEquityCurve(canvasId,ttId,equityCurve){
  var canvas=document.getElementById(canvasId);
  var tt=document.getElementById(ttId);
  if(!canvas||!equityCurve||!equityCurve.length)return;
  var container=canvas.parentElement;
  var W=container.clientWidth,H=container.clientHeight;
  canvas.width=W*2;canvas.height=H*2;
  canvas.style.width=W+'px';canvas.style.height=H+'px';
  var ctx=canvas.getContext('2d');ctx.scale(2,2);

  var points=parseEquityPoints(equityCurve);
  if(!points.length)return;
  var pad={top:20,right:20,bottom:40,left:70};
  var pw=W-pad.left-pad.right,ph=H-pad.top-pad.bottom;
  var xs=points.map(function(p){return p.ts;});
  var ys=points.map(function(p){return p.equity;});
  var xMin=Math.min.apply(null,xs),xMax=Math.max.apply(null,xs);
  var yMin=Math.min.apply(null,ys),yMax=Math.max.apply(null,ys);
  if(xMin===xMax)xMax+=1;if(yMin===yMax)yMax+=1;

  // Drawdown
  var peak=-Infinity;var ddPoints=[];
  points.forEach(function(p){if(p.equity>peak)peak=p.equity;ddPoints.push({ts:p.ts,dd:peak>0?(peak-p.equity)/peak:0});});
  ctx.fillStyle='rgba(248,81,73,0.15)';
  ddPoints.forEach(function(p,i){
    var x=pad.left+(p.ts-xMin)/(xMax-xMin)*pw;
    var y0=pad.top+ph-(0/(xMax-xMin)*ph);
    var yh=(p.dd||0)*(ph*0.5);
    if(i>0){
      var prev=ddPoints[i-1];
      var px1=pad.left+(prev.ts-xMin)/(xMax-xMin)*pw;
      ctx.fillRect(px1,pad.top+ph-yh,p.x-px1,yh);
    }
  });

  // Grid
  ctx.strokeStyle='rgba(48,54,61,0.5)';ctx.lineWidth=0.5;
  var gridLines=5;
  for(var i=0;i<=gridLines;i++){
    var gy=pad.top+ph-(ph/gridLines)*i;
    ctx.beginPath();ctx.moveTo(pad.left,gy);ctx.lineTo(W-pad.right,gy);ctx.stroke();
  }

  // Equity line
  ctx.strokeStyle='#3fb950';ctx.lineWidth=2;ctx.beginPath();
  points.forEach(function(p,i){
    var x=pad.left+(p.ts-xMin)/(xMax-xMin)*pw;
    var y=pad.top+ph-(p.equity-yMin)/(yMax-yMin)*ph;
    if(i===0)ctx.moveTo(x,y);else ctx.lineTo(x,y);
  });
  ctx.stroke();

  // Axes
  ctx.fillStyle='var(--muted)';ctx.font='10px monospace';ctx.fillStyle='#8b949e';
  for(var i=0;i<=gridLines;i++){
    var v=yMin+(yMax-yMin)/gridLines*i;
    ctx.fillText('$'+v.toFixed(0),2,pad.top+ph-(ph/gridLines)*i+3);
  }
  // X-axis dates
  var xSteps=Math.min(6,points.length);
  for(var i=0;i<=xSteps;i++){
    var idx=Math.floor(points.length*i/xSteps);if(idx>=points.length)idx=points.length-1;
    var p=points[idx];
    var x=pad.left+(p.ts-xMin)/(xMax-xMin)*pw;
    ctx.fillText(formatDate(p.ts),x-25,pad.top+ph+16);
  }
  ctx.fillText('Equity Curve',pad.left+10,16);

  // Hover
  canvas.onmousemove=function(e){
    var rect=canvas.getBoundingClientRect();
    var mx=e.clientX-rect.left,mx=e.clientY-rect.top;
    var closest=null,minDist=Infinity;
    points.forEach(function(p){
      var x=pad.left+(p.ts-xMin)/(xMax-xMin)*pw;
      var dist=Math.abs(mx-x);
      if(dist<minDist&&dist<30){minDist=dist;closest=p;}
    });
    if(closest){
      tt.style.display='block';tt.style.left=(pad.left+(closest.ts-xMin)/(xMax-xMin)*pw+10)+'px';
      tt.style.top=(pad.top+ph-(closest.equity-yMin)/(yMax-yMin)*ph-30)+'px';
      tt.textContent=formatDate(closest.ts)+'  $'+closest.equity.toLocaleString('en-US',{minimumFractionDigits:2});
    }else{tt.style.display='none';}
  };
  canvas.onmouseleave=function(){tt.style.display='none';};

  // Zoom/Pan support via mouse wheel
  var zoomX=[xMin,xMax],zoomY=[yMin,yMax];
  canvas.onwheel=function(e){e.preventDefault();/* Zoom placeholder */};
}

// --- Canvas Chart (multi equity curve) ---
function drawMultiEquityCurve(canvasId,ttId,curves){
  var canvas=document.getElementById(canvasId);
  var tt=document.getElementById(ttId);
  if(!canvas||!curves.length)return;
  var container=canvas.parentElement;
  var W=container.clientWidth,H=container.clientHeight;
  canvas.width=W*2;canvas.height=H*2;
  canvas.style.width=W+'px';canvas.style.height=H+'px';
  var ctx=canvas.getContext('2d');ctx.scale(2,2);
  var pad={top:20,right:20,bottom:40,left:70};
  var pw=W-pad.left-pad.right,ph=H-pad.top-pad.bottom;

  var allY=[];
  var curvesData=curves.map(function(c){var pts=parseEquityPoints(c.data);pts.forEach(function(p){allY.push(p.equity);});return{label:c.label,points:pts,color:c.color};});
  if(!allY.length)return;
  var yMin=Math.min.apply(null,allY),yMax=Math.max.apply(null,allY);
  if(yMin===yMax)yMax+=1;
  var xMin=Infinity,xMax=-Infinity;
  curvesData.forEach(function(cd){cd.points.forEach(function(p){if(p.ts<xMin)xMin=p.ts;if(p.ts>xMax)xMax=p.ts;});});
  if(xMin===xMax)xMax+=1;

  // Grid
  ctx.strokeStyle='rgba(48,54,61,0.5)';ctx.lineWidth=0.5;
  var gridLines=5;
  for(var i=0;i<=gridLines;i++){
    var gy=pad.top+ph-(ph/gridLines)*i;
    ctx.beginPath();ctx.moveTo(pad.left,gy);ctx.lineTo(W-pad.right,gy);ctx.stroke();
  }
  // Lines
  curvesData.forEach(function(cd){
    ctx.strokeStyle=cd.color;ctx.lineWidth=2;ctx.beginPath();
    cd.points.forEach(function(p,i){
      var x=pad.left+(p.ts-xMin)/(xMax-xMin)*pw;
      var y=pad.top+ph-(p.equity-yMin)/(yMax-yMin)*ph;
      if(i===0)ctx.moveTo(x,y);else ctx.lineTo(x,y);
    });
    ctx.stroke();
  });
  // Labels
  ctx.font='10px monospace';var ly=pad.top+14;
  curvesData.forEach(function(cd){
    ctx.fillStyle=cd.color;ctx.fillText(cd.label,pad.left+pw-100,ly);ly+=14;
  });
  // Axis
  ctx.fillStyle='#8b949e';
  for(var i=0;i<=gridLines;i++){
    var v=yMin+(yMax-yMin)/gridLines*i;
    ctx.fillText('$'+v.toFixed(0),2,pad.top+ph-(ph/gridLines)*i+3);
  }
}

function parseEquityPoints(data){
  if(!data||!data.length)return[];
  return data.map(function(p){
    var ts=0,equity=0;
    if(Array.isArray(p)){ts=p[0];equity=p[1];}
    else if(typeof p==='object'){ts=p.ts||p.date||p.time||p[0]||0;equity=p.equity||p.value||p.cum_pnl||p[1]||0;}
    if(typeof ts==='string'){var dt=new Date(ts);ts=isNaN(dt.getTime())?0:dt.getTime();}
    return{ts:Number(ts),equity:Number(equity)};
  }).filter(function(p){return !isNaN(p.ts)&&!isNaN(p.equity);});
}

function formatDate(ts){var d=new Date(Number(ts));return d.toISOString().split('T')[0];}
</script>
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


async def _query_daily_pnl_from_fills_async(
    config: DashboardConfig, days: int = 30
) -> list[dict[str, Any]]:
    """Aggregate daily cash-flow P&L from the fills table.

    Uses signed notional minus commission as a proxy for realized P&L.
    BUY fills = negative cash flow, SELL fills = positive cash flow.
    """
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
        rows = await conn.fetch(
            """
            SELECT
                DATE(ts_event) AS date,
                SUM(
                    CASE WHEN side = 'SELL'
                        THEN qty * price
                        ELSE -qty * price
                    END
                ) - SUM(COALESCE(commission, 0)) AS pnl
            FROM fills
            WHERE ts_event >= CURRENT_DATE - $1::int * INTERVAL '1 day'
            GROUP BY DATE(ts_event)
            ORDER BY date
            """,
            days,
        )
        return [dict(r) for r in rows]
    finally:
        await conn.close()


async def _query_benchmark_daily_pnl_from_fills_async(
    config: DashboardConfig,
    benchmark_instrument: str,
    days: int = 30,
) -> list[dict[str, Any]]:
    """Aggregate daily P&L for a single benchmark instrument from fills."""
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
        rows = await conn.fetch(
            """
            SELECT
                DATE(ts_event) AS date,
                SUM(
                    CASE WHEN side = 'SELL'
                        THEN qty * price
                        ELSE -qty * price
                    END
                ) - SUM(COALESCE(commission, 0)) AS pnl
            FROM fills
            WHERE ts_event >= CURRENT_DATE - $1::int * INTERVAL '1 day'
              AND instrument_id = $2
            GROUP BY DATE(ts_event)
            ORDER BY date
            """,
            days,
            benchmark_instrument,
        )
        return [dict(r) for r in rows]
    finally:
        await conn.close()


def _query_daily_pnl_from_redis(
    config: DashboardConfig, days: int = 30
) -> list[dict[str, Any]]:
    """Read per-strategy daily P&L from Redis and aggregate by date.

    Keys are ``sam:pnl:{strategy_id}:{YYYY-MM-DD}``.
    Returns rows with ``date`` and ``pnl``.
    """
    try:
        client = _redis_client(config)
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime(
            "%Y-%m-%d"
        )
        daily: dict[str, float] = {}
        for key in client.scan_iter(match="sam:pnl:*"):
            key_str = key if isinstance(key, str) else key.decode()
            parts = key_str.split(":")
            if len(parts) < 4:
                continue
            date_str = parts[3]
            if date_str < cutoff:
                continue
            val = client.get(key)
            if val is None:
                continue
            try:
                pnl = float(val if isinstance(val, str) else val.decode())
            except ValueError:
                continue
            daily[date_str] = daily.get(date_str, 0.0) + pnl

        return [{"date": d, "pnl": round(v, 2)} for d, v in sorted(daily.items())]
    except Exception as exc:
        logger.debug("redis daily pnl query failed: %s", exc)
        return []


def _get_equity_curve_data(
    config: DashboardConfig, days: int = 30
) -> list[dict[str, Any]]:
    """Return equity curve points (date, equity, pnl).

    Prefers Redis ``sam:pnl:*`` keys; falls back to fills table.
    """
    redis_pnl = _query_daily_pnl_from_redis(config, days)
    if redis_pnl:
        curve = compute_equity_curve(redis_pnl)
    else:
        try:
            fills_pnl = _run_async(_query_daily_pnl_from_fills_async(config, days))
            curve = compute_equity_curve(fills_pnl)
        except Exception as exc:
            logger.warning("equity curve query failed: %s", exc)
            curve = []
    return [
        {"date": p.date, "equity": round(p.equity, 2), "pnl": round(p.pnl, 2)}
        for p in curve
    ]


def _get_drawdown_data(config: DashboardConfig, days: int = 30) -> dict[str, Any]:
    """Return drawdown stats and events."""
    equity_data = _get_equity_curve_data(config, days)
    from sam_trader.services.dashboard_analytics import EquityPoint

    points = [
        EquityPoint(date=d["date"], equity=d["equity"], pnl=d["pnl"])
        for d in equity_data
    ]
    dd = compute_drawdown(points)
    return {
        "current_dd_pct": dd["current_dd_pct"],
        "max_dd_pct": dd["max_dd_pct"],
        "events": [
            {
                "start_date": e.start_date,
                "trough_date": e.trough_date,
                "end_date": e.end_date,
                "depth_pct": e.depth_pct,
                "recovery_days": e.recovery_days,
            }
            for e in dd["events"]
        ],
    }


def _get_performance_data(config: DashboardConfig, days: int = 30) -> dict[str, Any]:
    """Return 5 KPIs with deltas."""
    equity_data = _get_equity_curve_data(config, days)
    from sam_trader.services.dashboard_analytics import EquityPoint

    points = [
        EquityPoint(date=d["date"], equity=d["equity"], pnl=d["pnl"])
        for d in equity_data
    ]
    kpis = compute_kpis(points, lookback_days=days)
    return {
        "net_pnl": kpis.net_pnl,
        "net_pnl_delta": kpis.net_pnl_delta,
        "win_rate": kpis.win_rate,
        "win_rate_delta": kpis.win_rate_delta,
        "sharpe_20d": kpis.sharpe_20d,
        "sharpe_20d_delta": kpis.sharpe_20d_delta,
        "max_drawdown_pct": kpis.max_drawdown_pct,
        "max_drawdown_delta": kpis.max_drawdown_delta,
        "expectancy": kpis.expectancy,
        "expectancy_delta": kpis.expectancy_delta,
    }


def _get_monthly_returns_data(
    config: DashboardConfig, days: int = 365
) -> list[dict[str, Any]]:
    """Return monthly returns from fills."""
    try:
        daily = _run_async(_query_daily_pnl_from_fills_async(config, days))
        return compute_monthly_returns(daily)
    except Exception as exc:
        logger.warning("monthly returns query failed: %s", exc)
        return []


def _get_annual_returns_data(
    config: DashboardConfig, days: int = 730
) -> list[dict[str, Any]]:
    """Return annual returns from fills."""
    try:
        daily = _run_async(_query_daily_pnl_from_fills_async(config, days))
        return compute_annual_returns(daily)
    except Exception as exc:
        logger.warning("annual returns query failed: %s", exc)
        return []


def _get_rolling_sharpe_data(
    config: DashboardConfig, days: int = 90, window: int = 20
) -> list[dict[str, Any]]:
    """Return rolling Sharpe from fills."""
    try:
        daily = _run_async(_query_daily_pnl_from_fills_async(config, days))
        return compute_rolling_sharpe(daily, window=window)
    except Exception as exc:
        logger.warning("rolling sharpe query failed: %s", exc)
        return []


def _get_rolling_beta_data(
    config: DashboardConfig,
    days: int = 90,
    window: int = 20,
    benchmark: str = "SPY.NASDAQ",
) -> list[dict[str, Any]]:
    """Return rolling Beta from fills against a benchmark instrument."""
    try:
        daily = _run_async(_query_daily_pnl_from_fills_async(config, days))
        bench = _run_async(
            _query_benchmark_daily_pnl_from_fills_async(config, benchmark, days)
        )
        return compute_rolling_beta(daily, benchmark_pnl=bench, window=window)
    except Exception as exc:
        logger.warning("rolling beta query failed: %s", exc)
        return []


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


def query_market_data_from_redis(
    config: DashboardConfig | None = None,
) -> dict[str, Any]:
    """Read bar telemetry and venue connection state from Redis."""
    cfg = config or DashboardConfig()
    try:
        client = _redis_client(cfg)
        now = datetime.now(timezone.utc)
        today = now.strftime("%Y-%m-%d")

        # Bars: last received timestamp per instrument
        instruments: list[dict[str, Any]] = []
        for key in client.scan_iter(match="sam:bars:last:*"):
            instrument_id = (
                key.split(":", 3)[3]
                if isinstance(key, str)
                else key.decode().split(":", 3)[3]
            )
            last_ts_str = client.get(key)
            if not last_ts_str:
                continue
            try:
                last_ts = datetime.fromisoformat(last_ts_str)
            except ValueError:
                continue
            age_seconds = int((now - last_ts).total_seconds())
            if age_seconds < 120:
                staleness = "fresh"
            elif age_seconds < 300:
                staleness = "stale"
            else:
                staleness = "old"
            instruments.append(
                {
                    "instrument_id": instrument_id,
                    "last_ts": last_ts.strftime("%H:%M:%S"),
                    "age_seconds": age_seconds,
                    "staleness": staleness,
                }
            )
        instruments.sort(key=lambda x: x["instrument_id"])

        # Bars: today's count per instrument
        counts: dict[str, int] = {}
        count_hash = f"sam:bars:count:{today}"
        raw_counts = client.hgetall(count_hash)
        if raw_counts:
            for k, v in raw_counts.items():
                instr = k if isinstance(k, str) else k.decode()
                try:
                    counts[instr] = int(v)
                except ValueError:
                    counts[instr] = 0

        # Venue connection state
        venues: list[dict[str, Any]] = []
        for key in client.scan_iter(match="sam:venue:conn:*"):
            venue_name = (
                key.split(":", 3)[3]
                if isinstance(key, str)
                else key.decode().split(":", 3)[3]
            )
            val = client.get(key)
            if val:
                val_str = val if isinstance(val, str) else val.decode()
                status, _, ts_str = val_str.partition(":")
                venues.append(
                    {
                        "venue": venue_name,
                        "status": status,
                        "last_change": ts_str.split("+")[0] if ts_str else "",
                    }
                )
        venues.sort(key=lambda x: x["venue"])

        return {
            "instruments": instruments,
            "counts": counts,
            "venues": venues,
            "timestamp": now.isoformat(),
        }
    except Exception as exc:
        logger.warning("market data query failed: %s", exc)
        return {"instruments": [], "counts": {}, "venues": [], "timestamp": ""}


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


def _handle_bars_recent(path: str, config: DashboardConfig) -> dict[str, Any]:
    """Handle GET /api/bars/recent?instrument=X&seconds=300.

    Reads ``sam:bars:recent:{instrument_id}`` from Redis, filters
    entries older than *seconds*, and returns a JSON-compatible dict.
    """
    parsed = urlparse(path)
    params = parse_qs(parsed.query)
    instrument = params.get("instrument", [None])[0]
    try:
        seconds = int(params.get("seconds", ["300"])[0])
    except ValueError:
        seconds = 300

    cutoff = datetime.now(timezone.utc) - timedelta(seconds=seconds)
    results: list[dict[str, Any]] = []

    try:
        client = _redis_client(config)
        if instrument:
            keys = [f"sam:bars:recent:{instrument}"]
        else:
            keys = []
            for key in client.scan_iter(match="sam:bars:recent:*"):
                key_str = key if isinstance(key, str) else key.decode()
                keys.append(key_str)

        for key in keys:
            instr_id = key.split(":", 3)[3] if ":" in key else key
            items = client.lrange(key, 0, 99)
            for item in items:
                try:
                    data = json.loads(item if isinstance(item, str) else item.decode())
                    ts = datetime.fromisoformat(data["ts"])
                    if ts >= cutoff:
                        data["instrument_id"] = instr_id
                        results.append(data)
                except (ValueError, KeyError, TypeError):
                    continue
    except Exception as exc:
        logger.warning("bars recent query failed: %s", exc)

    results.sort(key=lambda x: x["ts"], reverse=True)
    return {"bars": results, "seconds": seconds, "count": len(results)}


def get_market_schedule_info(config: DashboardConfig | None = None) -> dict[str, Any]:
    """Query MarketCalendarService and return schedule banner fragments.

    Data is sourced from Redis cache when available, falling back to
    ``MarketCalendarService`` hardcoded/library holidays on cache miss.
    """
    cfg = config or DashboardConfig()
    try:
        redis_client = _redis_client(cfg)
    except Exception as exc:
        logger.debug("market schedule redis unavailable: %s", exc)
        redis_client = None

    svc = MarketCalendarService(redis_client=redis_client)
    today = date.today()
    banners: list[str] = []
    indicators: list[str] = []
    countdowns: list[str] = []

    for market in ("US", "HK"):
        try:
            if svc.is_holiday(market, today):
                name = svc.holiday_name(market, today) or "Holiday"
                banners.append(
                    f"🚫 {market} Market Holiday: {name} \u2014 Markets Closed"
                )
            elif svc.is_early_close(market, today):
                _open, close_t = svc.market_hours(market, today)
                banners.append(
                    f"\u26a0\ufe0f Early Close Today ({market}): "
                    f"{close_t.strftime('%H:%M')}"
                )
            else:
                indicators.append(f"\u2705 {market} Markets Open Today")

            next_day = svc.next_trading_day(market, today)
            tz_name = svc.market_timezone(market)
            try:
                from zoneinfo import ZoneInfo

                tz: Any = ZoneInfo(tz_name)
            except Exception:
                tz = timezone.utc
            now_local = datetime.now(tz)
            next_open_local = datetime.combine(next_day, time(9, 30), tzinfo=tz)
            hours_until = int((next_open_local - now_local).total_seconds() / 3600)
            if hours_until < 0:
                hours_until = 0
            countdowns.append(
                f"Next {market} session: {next_day.isoformat()} in {hours_until}h"
            )
        except Exception as exc:
            logger.debug("market schedule query failed for %s: %s", market, exc)

    return {
        "banners": banners,
        "indicators": indicators,
        "countdowns": countdowns,
    }


def get_dashboard_data(config: DashboardConfig | None = None) -> dict[str, Any]:
    """Aggregate all dashboard data sources."""
    cfg = config or DashboardConfig()
    return {
        "health": check_all_services(cfg),
        "market_data": query_market_data_from_redis(cfg),
        "fills": query_fills(cfg),
        "positions": query_positions(cfg),
        "pnl": query_pnl_from_redis(cfg),
        "schedule": get_market_schedule_info(cfg),
        "equity_curve": _get_equity_curve_data(cfg),
        "drawdown": _get_drawdown_data(cfg),
        "performance": _get_performance_data(cfg),
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


def _render_schedule_html(schedule: dict[str, Any]) -> str:
    """Render schedule banner HTML from MarketCalendarService result."""
    parts: list[str] = []
    for banner in schedule.get("banners", []):
        cls = "holiday" if "Holiday" in banner else "early"
        parts.append(f'<div class="schedule-banner {cls}">{banner}</div>')
    for indicator in schedule.get("indicators", []):
        parts.append(f'<div class="schedule-indicator open">{indicator}</div>')
    for countdown in schedule.get("countdowns", []):
        parts.append(f'<div class="schedule-countdown">{countdown}</div>')
    if not parts:
        parts.append(
            '<div class="schedule-indicator open">' "\u2705 Markets Open Today" "</div>"
        )
    return '<div class="schedule-section">\n' + "\n".join(parts) + "\n</div>"


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

    # Market data rows
    market_data = data.get("market_data", {})
    market_data_rows: list[str] = []
    counts = market_data.get("counts", {})
    for instr in market_data.get("instruments", []):
        instr_id = instr.get("instrument_id", "")
        count = counts.get(instr_id, 0)
        stale_cls = instr.get("staleness", "old")
        stale_label = {
            "fresh": "● <2min",
            "stale": "● <5min",
            "old": "● >5min",
        }.get(stale_cls, "● unknown")
        market_data_rows.append(
            f"<tr>"
            f"<td>{instr_id}</td>"
            f"<td>{instr.get('last_ts', '')}</td>"
            f"<td>{count}</td>"
            f"<td class='{stale_cls}'>{stale_label}</td>"
            f"</tr>"
        )

    # Market data summary for collapsed view
    md_summary: str
    if not market_data.get("instruments"):
        md_summary = "No instruments"
    else:
        count = len(market_data["instruments"])
        min_age = min(i.get("age_seconds", 9999) for i in market_data["instruments"])
        md_summary = (
            f"{count} instrument{'s' if count != 1 else ''} | last bar {min_age}s ago"
        )

    venue_conn_rows: list[str] = []
    venues = market_data.get("venues", [])
    if venues:
        venue_conn_rows.append(
            "<div style='margin-top:.5rem; font-size:.85rem; color:var(--muted);'>"
        )
        venue_conn_rows.append("Venues: ")
        parts = []
        for v in venues:
            v_cls = "up" if v.get("status") == "UP" else "down"
            parts.append(f"<span class='status {v_cls}'></span>{v.get('venue', '')}")
        venue_conn_rows.append(" ".join(parts))
        venue_conn_rows.append("</div>")

    # Positions rows
    positions_rows: list[str] = []
    for p in data.get("positions", []):
        upnl = p.get("unrealized_pnl")
        upnl_str = _fmt_num(upnl)
        upnl_cls = _pnl_class(float(upnl) if upnl is not None else 0.0)
        qty = float(p.get("net_qty") or 0)
        avg_px = float(p.get("avg_px") or 0)
        if qty != 0 and avg_px != 0:
            mark_price = avg_px + (float(upnl or 0) / qty)
            pnl_pct = (float(upnl or 0) / (abs(qty) * avg_px)) * 100
        else:
            mark_price = 0.0
            pnl_pct = 0.0
        positions_rows.append(
            f"<tr>"
            f"<td>{p.get('symbol', '')}</td>"
            f"<td>{p.get('venue', '')}</td>"
            f"<td>{_fmt_num(p.get('net_qty'))}</td>"
            f"<td>{_fmt_num(p.get('avg_px'))}</td>"
            f"<td>{_fmt_num(str(mark_price))}</td>"
            f"<td class='{upnl_cls}'>{upnl_str}</td>"
            f"<td class='{upnl_cls}'>{pnl_pct:+.2f}%</td>"
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

    schedule_html = _render_schedule_html(data.get("schedule", {}))

    # KPI cards
    perf = data.get("performance", {})
    kpi_net_pnl = perf.get("net_pnl", 0.0)
    kpi_net_pnl_delta = perf.get("net_pnl_delta", 0.0)
    kpi_win_rate = perf.get("win_rate", 0.0)
    kpi_win_rate_delta = perf.get("win_rate_delta", 0.0)
    kpi_sharpe = perf.get("sharpe_20d", 0.0)
    kpi_sharpe_delta = perf.get("sharpe_20d_delta", 0.0)
    kpi_max_dd = perf.get("max_drawdown_pct", 0.0)
    kpi_max_dd_delta = perf.get("max_drawdown_delta", 0.0)
    kpi_expectancy = perf.get("expectancy", 0.0)
    kpi_expectancy_delta = perf.get("expectancy_delta", 0.0)

    def _delta_str(v: float) -> str:
        sign = "+" if v >= 0 else ""
        return f"{sign}{v:.2f} vs prior"

    def _delta_cls(v: float) -> str:
        return "positive" if v >= 0 else "negative"

    # Charts
    equity_points = [
        EquityPoint(date=d["date"], equity=d["equity"], pnl=d["pnl"])
        for d in data.get("equity_curve", [])
    ]
    equity_svg = render_equity_curve_svg(equity_points)
    drawdown_svg = render_drawdown_svg(equity_points)

    return (
        _DASHBOARD_HTML.replace("{{schedule_banner}}", schedule_html)
        .replace("{{pg_status}}", _svc_status("postgres"))
        .replace("{{pg_status_class}}", _svc_class("postgres"))
        .replace("{{redis_status}}", _svc_status("redis"))
        .replace("{{redis_status_class}}", _svc_class("redis"))
        .replace("{{futu_status}}", _svc_status("futu_opend"))
        .replace("{{futu_status_class}}", _svc_class("futu_opend"))
        .replace("{{trader_status}}", _svc_status("sam_trader"))
        .replace("{{trader_status_class}}", _svc_class("sam_trader"))
        .replace(
            "{{market_data_rows}}",
            (
                "\n".join(market_data_rows)
                if market_data_rows
                else "<tr><td colspan='4'>No bar telemetry</td></tr>"
            ),
        )
        .replace("{{market_data_summary}}", md_summary)
        .replace("{{venue_conn_rows}}", "\n".join(venue_conn_rows))
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
                else "<tr><td colspan='7'>No open positions</td></tr>"
            ),
        )
        .replace("{{pnl_rows}}", "\n".join(pnl_rows))
        .replace("{{total_pnl}}", _fmt_pnl(total_pnl))
        .replace("{{total_pnl_class}}", _pnl_class(total_pnl))
        .replace("{{kpi_net_pnl}}", _fmt_pnl(kpi_net_pnl))
        .replace("{{kpi_net_pnl_class}}", _pnl_class(kpi_net_pnl))
        .replace("{{kpi_net_pnl_delta}}", _delta_str(kpi_net_pnl_delta))
        .replace("{{kpi_net_pnl_delta_class}}", _delta_cls(kpi_net_pnl_delta))
        .replace("{{kpi_win_rate}}", f"{kpi_win_rate:.1f}%")
        .replace("{{kpi_win_rate_delta}}", _delta_str(kpi_win_rate_delta))
        .replace("{{kpi_win_rate_delta_class}}", _delta_cls(kpi_win_rate_delta))
        .replace("{{kpi_sharpe}}", f"{kpi_sharpe:.2f}")
        .replace("{{kpi_sharpe_delta}}", _delta_str(kpi_sharpe_delta))
        .replace("{{kpi_sharpe_delta_class}}", _delta_cls(kpi_sharpe_delta))
        .replace("{{kpi_max_dd}}", f"{kpi_max_dd:.2f}%")
        .replace("{{kpi_max_dd_delta}}", _delta_str(kpi_max_dd_delta))
        .replace("{{kpi_max_dd_delta_class}}", _delta_cls(kpi_max_dd_delta))
        .replace("{{kpi_expectancy}}", _fmt_pnl(kpi_expectancy))
        .replace("{{kpi_expectancy_class}}", _pnl_class(kpi_expectancy))
        .replace("{{kpi_expectancy_delta}}", _delta_str(kpi_expectancy_delta))
        .replace("{{kpi_expectancy_delta_class}}", _delta_cls(kpi_expectancy_delta))
        .replace("{{equity_curve_svg}}", equity_svg)
        .replace("{{drawdown_svg}}", drawdown_svg)
        .replace(
            "{{now}}",
            datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        )
    )


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------


class DashboardHandler(BaseHTTPRequestHandler):
    """Handle GET /health, GET /api/dashboard, backtest API, and
    serve dashboard.html."""

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

    def do_POST(self) -> None:  # noqa: N802
        path = self.path
        if path == "/api/backtest/run":
            try:
                content_length = int(self.headers.get("Content-Length", 0))
                body_raw = self.rfile.read(content_length)
                body = json.loads(body_raw) if body_raw else {}
            except (json.JSONDecodeError, ValueError) as exc:
                self._send_json(400, {"error": f"Invalid JSON: {exc}"})
                return
            data = handle_backtest_run(body)
            self._send_json(200 if "error" not in data else 400, data)
        else:
            self._send_json(404, {"error": "Not found"})

    def do_GET(self) -> None:  # noqa: N802
        path = self.path
        cfg = DashboardConfig()
        if path == "/health":
            health = check_all_services()
            self._send_json(200, health)
        elif path == "/api/dashboard":
            data = get_dashboard_data()
            self._send_json(200, data)
        elif path.startswith("/api/bars/recent"):
            data = _handle_bars_recent(path, cfg)
            self._send_json(200, data)
        elif path.startswith("/api/equity-curve"):
            parsed = urlparse(path)
            params = parse_qs(parsed.query)
            try:
                days = int(params.get("days", ["30"])[0])
            except ValueError:
                days = 30
            data = {"points": _get_equity_curve_data(cfg, days)}
            self._send_json(200, data)
        elif path.startswith("/api/drawdown"):
            parsed = urlparse(path)
            params = parse_qs(parsed.query)
            try:
                days = int(params.get("days", ["30"])[0])
            except ValueError:
                days = 30
            data = _get_drawdown_data(cfg, days)
            self._send_json(200, data)
        elif path == "/api/performance":
            data = _get_performance_data(cfg)
            self._send_json(200, data)
        elif path == "/api/monthly-returns":
            parsed = urlparse(path)
            params = parse_qs(parsed.query)
            try:
                days = int(params.get("days", ["365"])[0])
            except ValueError:
                days = 365
            data = {"months": _get_monthly_returns_data(cfg, days)}
            self._send_json(200, data)
        elif path == "/api/annual-returns":
            parsed = urlparse(path)
            params = parse_qs(parsed.query)
            try:
                days = int(params.get("days", ["730"])[0])
            except ValueError:
                days = 730
            data = {"years": _get_annual_returns_data(cfg, days)}
            self._send_json(200, data)
        elif path.startswith("/api/rolling-sharpe"):
            parsed = urlparse(path)
            params = parse_qs(parsed.query)
            try:
                window = int(params.get("window", ["20"])[0])
            except ValueError:
                window = 20
            try:
                days = int(params.get("days", ["90"])[0])
            except ValueError:
                days = 90
            data = {"points": _get_rolling_sharpe_data(cfg, days, window)}
            self._send_json(200, data)
        elif path.startswith("/api/rolling-beta"):
            parsed = urlparse(path)
            params = parse_qs(parsed.query)
            try:
                window = int(params.get("window", ["20"])[0])
            except ValueError:
                window = 20
            try:
                days = int(params.get("days", ["90"])[0])
            except ValueError:
                days = 90
            benchmark = params.get("benchmark", ["SPY.NASDAQ"])[0]
            data = {"points": _get_rolling_beta_data(cfg, days, window, benchmark)}
            self._send_json(200, data)
        elif path.startswith("/api/backtest/run/"):
            # /api/backtest/run/<id>/status
            parts = path.split("/")
            if len(parts) >= 5 and parts[-1] == "status":
                run_id = parts[-2]
                data = handle_backtest_run_status(run_id)
                status_code = 200 if "error" not in data else 404
                self._send_json(status_code, data)
            else:
                self._send_json(404, {"error": "Not found"})
        elif path == "/api/backtest/runs":
            parsed = urlparse(path)
            params = parse_qs(parsed.query)
            try:
                limit = int(params.get("limit", ["50"])[0])
            except ValueError:
                limit = 50
            data = handle_backtest_runs(limit=limit)  # type: ignore[assignment]
            self._send_json(200, data)
        elif path.startswith("/api/backtest/runs/"):
            # /api/backtest/runs/<id>
            parts = path.split("/")
            if len(parts) == 5:
                run_id = parts[-1]
                data = handle_backtest_runs_detail(run_id)
                status_code = 200 if "error" not in data else 404
                self._send_json(status_code, data)
            else:
                self._send_json(404, {"error": "Not found"})
        elif path.startswith("/api/backtest/compare"):
            parsed = urlparse(path)
            params = parse_qs(parsed.query)
            runs_raw = params.get("runs", [""])[0]
            run_ids = [r.strip() for r in runs_raw.split(",") if r.strip()]
            data = (
                handle_backtest_compare(run_ids)
                if run_ids
                else {"error": "Missing runs parameter"}
            )
            self._send_json(200 if "error" not in data else 400, data)
        elif path == "/api/backtest/catalog/instruments":
            data = handle_backtest_catalog_instruments()  # type: ignore[assignment]
            self._send_json(200, data)
        elif path == "/api/backtest/catalog/status":
            data = handle_backtest_catalog_status()
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

    orchestrator = RestartOrchestrator()
    orchestrator.start()
    try:
        run_server()
    finally:
        orchestrator.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
