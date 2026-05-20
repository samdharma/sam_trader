#!/usr/bin/env python3
"""Simple metrics viewer for Ralph Wiggum loop data.

Reads logs/ralph_metrics.jsonl and renders basic stats:
- iterations per day
- pass/fail rate
- time spent per tier
- task type distribution

Usage:
    python scripts/ralph/ralph_metrics_viewer.py [--output text|html] [--save PATH]

Environment variables:
    RALPH_METRICS_FILE - Override metrics jsonl path
    RALPH_PROJECT_DIR  - Override project directory
"""

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional

PROJECT_DIR = os.environ.get(
    "RALPH_PROJECT_DIR",
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
)


def parse_iso_ts(ts_str: str) -> datetime:
    """Parse ISO timestamp with optional Z suffix."""
    ts_str = ts_str.replace("Z", "+00:00")
    return datetime.fromisoformat(ts_str)


def load_metrics(path: Optional[str] = None) -> List[Dict[str, Any]]:
    metrics_file = path or os.path.join(PROJECT_DIR, "logs", "ralph_metrics.jsonl")
    events: List[Dict[str, Any]] = []
    if not os.path.exists(metrics_file):
        return events
    with open(metrics_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return events


def analyze_metrics(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Compute summary statistics from raw events."""
    iter_start: Dict[str, Dict[str, Any]] = {}
    iter_end: Dict[str, Dict[str, Any]] = {}
    iter_checkpoint: Dict[str, Dict[str, Any]] = {}

    for ev in events:
        eid = ev.get("iteration", "")
        tid = ev.get("task_id", "")
        key = f"{tid}:{eid}"
        event_type = ev.get("event", "")
        if event_type == "iteration_start":
            iter_start[key] = ev
        elif event_type == "iteration_end":
            iter_end[key] = ev
        elif event_type in ("checkpoint_cleared", "checkpoint_retained"):
            iter_checkpoint[key] = ev

    daily_counts: Dict[str, int] = defaultdict(int)
    passes = 0
    fails = 0
    tier_durations: Dict[str, List[float]] = defaultdict(list)
    task_type_counts: Dict[str, int] = defaultdict(int)
    total_duration_sec = 0.0
    matched_iterations = 0

    for key, start_ev in iter_start.items():
        end_ev = iter_end.get(key)
        checkpoint_ev = iter_checkpoint.get(key)

        task_type = start_ev.get("task_type", "unknown")
        task_type_counts[task_type] += 1

        if checkpoint_ev:
            reason = checkpoint_ev.get("reason", "")
            if reason == "clean_worktree":
                passes += 1
            else:
                fails += 1
        else:
            fails += 1

        if end_ev:
            start_ts = parse_iso_ts(start_ev["timestamp"])
            end_ts = parse_iso_ts(end_ev["timestamp"])
            duration = (end_ts - start_ts).total_seconds()
            total_duration_sec += duration
            matched_iterations += 1

            tier = start_ev.get("tier", "unknown")
            tier_durations[tier].append(duration)

            day_str = end_ts.strftime("%Y-%m-%d")
            daily_counts[day_str] += 1

    total = passes + fails if (passes + fails) > 0 else 1
    pass_rate = (passes / total) * 100.0
    fail_rate = (fails / total) * 100.0

    avg_duration_sec = total_duration_sec / matched_iterations if matched_iterations > 0 else 0.0

    tier_stats = {}
    for tier, durations in tier_durations.items():
        total_sec = sum(durations)
        avg_sec = total_sec / len(durations)
        tier_stats[tier] = {
            "count": len(durations),
            "total_sec": total_sec,
            "avg_sec": avg_sec,
            "avg_min": avg_sec / 60.0,
        }

    return {
        "daily_counts": dict(daily_counts),
        "passes": passes,
        "fails": fails,
        "pass_rate": pass_rate,
        "fail_rate": fail_rate,
        "total_iterations": passes + fails,
        "avg_duration_sec": avg_duration_sec,
        "avg_duration_min": avg_duration_sec / 60.0,
        "tier_stats": tier_stats,
        "task_type_counts": dict(task_type_counts),
    }


def render_text(stats: Dict[str, Any]) -> str:
    lines: List[str] = []
    lines.append("=" * 60)
    lines.append("Ralph Wiggum Loop Metrics")
    lines.append("=" * 60)
    lines.append("")
    lines.append(f"Total iterations : {stats['total_iterations']}")
    pass_str = (
        f"Pass rate        : {stats['passes']} passed, {stats['fails']} failed"
        f" ({stats['pass_rate']:.1f}% / {stats['fail_rate']:.1f}%)"
    )
    lines.append(pass_str)
    lines.append(f"Avg duration     : {stats['avg_duration_min']:.1f} minutes")
    lines.append("")

    lines.append("-" * 40)
    lines.append("Iterations per day")
    lines.append("-" * 40)
    for day in sorted(stats["daily_counts"]):
        lines.append(f"  {day} : {stats['daily_counts'][day]}")
    lines.append("")

    lines.append("-" * 40)
    lines.append("Time spent per tier")
    lines.append("-" * 40)
    for tier in sorted(stats["tier_stats"]):
        t = stats["tier_stats"][tier]
        total_min = t["avg_min"] * t["count"]
        lines.append(
            f"  {tier:12s} : {t['count']:3d} runs,"
            f" avg {t['avg_min']:.1f} min, total {total_min:.1f} min"
        )
    lines.append("")

    lines.append("-" * 40)
    lines.append("Task type distribution")
    lines.append("-" * 40)
    for tt in sorted(stats["task_type_counts"]):
        lines.append(f"  {tt:12s} : {stats['task_type_counts'][tt]}")
    lines.append("")

    return "\n".join(lines)


def render_html(stats: Dict[str, Any]) -> str:
    daily_rows = "\n".join(
        f"<tr><td>{day}</td><td>{stats['daily_counts'][day]}</td></tr>"
        for day in sorted(stats["daily_counts"])
    )
    tier_rows = "\n".join(
        (
            f"<tr><td>{tier}</td>"
            f"<td>{stats['tier_stats'][tier]['count']}</td>"
            f"<td>{stats['tier_stats'][tier]['avg_min']:.1f}</td>"
            f"<td>"
            f"{stats['tier_stats'][tier]['avg_min'] * stats['tier_stats'][tier]['count']:.1f}"
            f"</td></tr>"
        )
        for tier in sorted(stats["tier_stats"])
    )
    type_rows = "\n".join(
        f"<tr><td>{tt}</td><td>{stats['task_type_counts'][tt]}</td></tr>"
        for tt in sorted(stats["task_type_counts"])
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Ralph Wiggum Loop Metrics</title>
<style>
body {{ font-family: system-ui, -apple-system, sans-serif;"
        " background:#0f172a; color:#e2e8f0; padding:2rem; }}
h1 {{ color:#38bdf8; }}
h2 {{ color:#94a3b8; border-bottom:1px solid #334155; padding-bottom:.25rem; }}
table {{ border-collapse:collapse; width:100%; max-width:600px; margin-bottom:1.5rem; }}
th, td {{ padding:.5rem .75rem; text-align:left; border-bottom:1px solid #334155; }}
th {{ color:#94a3b8; }}
.metric {{ font-size:1.25rem; font-weight:600; color:#38bdf8; }}
</style>
</head>
<body>
<h1>Ralph Wiggum Loop Metrics</h1>
<p>Total iterations: <span class="metric">{stats['total_iterations']}</span></p>
<p>Pass rate: <span class="metric">{stats['passes']} passed</span> /"
        " <span class=\"metric\">{stats['fails']} failed</span>"
        " ({stats['pass_rate']:.1f}% / {stats['fail_rate']:.1f}%)</p>
<p>Average duration: <span class="metric">{stats['avg_duration_min']:.1f} minutes</span></p>

<h2>Iterations per day</h2>
<table>
<thead><tr><th>Date</th><th>Iterations</th></tr></thead>
<tbody>
{daily_rows}
</tbody>
</table>

<h2>Time spent per tier</h2>
<table>
<thead><tr><th>Tier</th><th>Runs</th><th>Avg (min)</th><th>Total (min)</th></tr></thead>
<tbody>
{tier_rows}
</tbody>
</table>

<h2>Task type distribution</h2>
<table>
<thead><tr><th>Type</th><th>Count</th></tr></thead>
<tbody>
{type_rows}
</tbody>
</table>
</body>
</html>"""


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Ralph Wiggum loop metrics viewer")
    parser.add_argument(
        "--metrics-file",
        default=os.path.join(PROJECT_DIR, "logs", "ralph_metrics.jsonl"),
        help="Path to ralph_metrics.jsonl",
    )
    parser.add_argument(
        "--output",
        choices=["text", "html"],
        default="text",
        help="Output format",
    )
    parser.add_argument(
        "--save",
        default=None,
        help="Write output to file instead of stdout",
    )
    args = parser.parse_args(argv)

    events = load_metrics(args.metrics_file)
    if not events:
        print("No metrics found.", file=sys.stderr)
        return 1

    stats = analyze_metrics(events)

    if args.output == "html":
        out = render_html(stats)
    else:
        out = render_text(stats)

    if args.save:
        with open(args.save, "w", encoding="utf-8") as f:
            f.write(out)
        print(f"Saved to {args.save}")
    else:
        print(out)

    return 0


if __name__ == "__main__":
    sys.exit(main())
