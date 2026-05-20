#!/usr/bin/env python3
"""Ralph Wiggum Daily/Weekly Report v1.

Generates operational summary reports from project data.

Usage:
    python scripts/ralph/ralph_report.py --daily [YYYY-MM-DD]
    python scripts/ralph/ralph_report.py --weekly [weeks_back]
    python scripts/ralph/ralph_report.py --output text|html [--save]

Environment variables:
    RALPH_PROJECT_DIR  - Override project directory
    RALPH_METRICS_FILE - Override metrics jsonl path
"""

import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

PROJECT_DIR = os.environ.get(
    "RALPH_PROJECT_DIR",
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
)
sys.path.insert(0, PROJECT_DIR)


def parse_ralph_metrics(start_dt: datetime, end_dt: datetime) -> Dict[str, Any]:
    """Parse Ralph metrics for a date range."""
    metrics_file = os.environ.get(
        "RALPH_METRICS_FILE",
        os.path.join(PROJECT_DIR, "logs", "ralph_metrics.jsonl"),
    )
    iterations = 0
    tasks: set = set()
    checkpoints_cleared = 0
    checkpoints_retained = 0
    has_data = False
    if not os.path.exists(metrics_file):
        return {}
    with open(metrics_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                ts_str = obj.get("timestamp", "")
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                if start_dt <= ts < end_dt:
                    has_data = True
                    event = obj.get("event", "")
                    if event == "iteration_end":
                        iterations += 1
                    if obj.get("task_id"):
                        tasks.add(obj["task_id"])
                    if event == "checkpoint_cleared":
                        checkpoints_cleared += 1
                    if event == "checkpoint_retained":
                        checkpoints_retained += 1
            except Exception:
                continue
    if not has_data:
        return {}
    return {
        "iterations": iterations,
        "unique_tasks": len(tasks),
        "checkpoints_cleared": checkpoints_cleared,
        "checkpoints_retained": checkpoints_retained,
    }


def generate_daily(report_date: date) -> Dict[str, Any]:
    start_dt = datetime.combine(report_date, datetime.min.time())
    end_dt = start_dt + timedelta(days=1)

    report: Dict[str, Any] = {
        "mode": "daily",
        "date": report_date.isoformat(),
        "generated_at": datetime.utcnow().isoformat() + "Z",
    }

    metrics = parse_ralph_metrics(start_dt, end_dt)
    if metrics:
        report["ralph_loop"] = metrics

    return report


def generate_weekly(weeks_back: int = 0) -> Dict[str, Any]:
    today = date.today()
    start_of_week = today - timedelta(days=today.weekday() + (weeks_back * 7))
    end_of_week = start_of_week + timedelta(days=7)

    report: Dict[str, Any] = {
        "mode": "weekly",
        "week_start": start_of_week.isoformat(),
        "week_end": (end_of_week - timedelta(days=1)).isoformat(),
        "generated_at": datetime.utcnow().isoformat() + "Z",
    }

    metrics = parse_ralph_metrics(
        datetime.combine(start_of_week, datetime.min.time()),
        datetime.combine(end_of_week, datetime.min.time()),
    )
    if metrics:
        report["ralph_loop"] = metrics

    return report


def render_text(report: Dict[str, Any]) -> str:
    lines: List[str] = []
    lines.append("=" * 60)
    lines.append("Ralph Wiggum Operational Report")
    lines.append("=" * 60)
    lines.append("")

    if report.get("mode") == "daily":
        lines.append(f"Mode: Daily")
        lines.append(f"Date: {report['date']}")
    else:
        lines.append(f"Mode: Weekly")
        lines.append(f"Week: {report['week_start']} to {report['week_end']}")

    lines.append(f"Generated: {report['generated_at']}")
    lines.append("")

    ralph = report.get("ralph_loop", {})
    if ralph:
        lines.append("-" * 40)
        lines.append("Ralph Loop Activity")
        lines.append("-" * 40)
        lines.append(f"  Iterations:       {ralph.get('iterations', 0)}")
        lines.append(f"  Unique tasks:     {ralph.get('unique_tasks', 0)}")
        lines.append(f"  Checkpoints OK:   {ralph.get('checkpoints_cleared', 0)}")
        lines.append(f"  Checkpoints kept: {ralph.get('checkpoints_retained', 0)}")
        lines.append("")
    else:
        lines.append("No Ralph loop activity in this period.")
        lines.append("")

    lines.append("=" * 60)
    return "\n".join(lines)


def render_html(report: Dict[str, Any]) -> str:
    ralph = report.get("ralph_loop", {})
    ralph_rows = ""
    if ralph:
        ralph_rows = f"""
        <tr><td>Iterations</td><td>{ralph.get('iterations', 0)}</td></tr>
        <tr><td>Unique tasks</td><td>{ralph.get('unique_tasks', 0)}</td></tr>
        <tr><td>Checkpoints OK</td><td>{ralph.get('checkpoints_cleared', 0)}</td></tr>
        <tr><td>Checkpoints kept</td><td>{ralph.get('checkpoints_retained', 0)}</td></tr>
        """

    period_label = (
        f"Date: {report['date']}" if report.get("mode") == "daily"
        else f"Week: {report['week_start']} to {report['week_end']}"
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Ralph Wiggum Report</title>
<style>
body {{ font-family: system-ui, -apple-system, sans-serif; background:#0f172a; color:#e2e8f0; padding:2rem; }}
h1 {{ color:#38bdf8; }}
h2 {{ color:#94a3b8; border-bottom:1px solid #334155; padding-bottom:.25rem; }}
table {{ border-collapse:collapse; width:100%; max-width:600px; margin-bottom:1.5rem; }}
th, td {{ padding:.5rem .75rem; text-align:left; border-bottom:1px solid #334155; }}
th {{ color:#94a3b8; }}
</style>
</head>
<body>
<h1>Ralph Wiggum Operational Report</h1>
<p>{period_label}</p>
<p>Generated: {report['generated_at']}</p>

<h2>Ralph Loop Activity</h2>
<table>
<thead><tr><th>Metric</th><th>Value</th></tr></thead>
<tbody>
{ralph_rows}
</tbody>
</table>
</body>
</html>"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Ralph Wiggum operational report")
    parser.add_argument("--daily", nargs="?", const="today", help="Generate daily report")
    parser.add_argument("--weekly", nargs="?", const="0", type=str, help="Generate weekly report")
    parser.add_argument("--output", choices=["text", "html"], default="text")
    parser.add_argument("--save", help="Save to file instead of stdout")
    args = parser.parse_args()

    if args.daily is not None:
        if args.daily == "today":
            report_date = date.today()
        else:
            report_date = date.fromisoformat(args.daily)
        report = generate_daily(report_date)
    elif args.weekly is not None:
        weeks_back = int(args.weekly)
        report = generate_weekly(weeks_back)
    else:
        parser.print_help()
        return 1

    if args.output == "html":
        out = render_html(report)
    else:
        out = render_text(report)

    if args.save:
        with open(args.save, "w", encoding="utf-8") as f:
            f.write(out)
        print(f"Saved to {args.save}")
    else:
        print(out)

    return 0


if __name__ == "__main__":
    sys.exit(main())
