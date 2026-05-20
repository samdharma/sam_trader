#!/usr/bin/env python3
"""
Ralph Wiggum Spec Checker v1

Reads docs/agent/SPEC.md, runs each verification_command, and writes
a JSON report to docs/agent/spec_report.json.

Usage:
    python scripts/ralph/ralph_check_specs.py [--spec=path/to/SPEC.md]

Environment variables:
    RALPH_PROJECT_DIR - Override project directory
    RALPH_SPEC_PATH   - Override spec markdown path
    RALPH_REPORT_PATH - Override report json path
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(
    os.environ.get(
        "RALPH_PROJECT_DIR",
        Path(__file__).parent.parent.parent.resolve(),
    )
)
SPEC_PATH = Path(
    os.environ.get(
        "RALPH_SPEC_PATH",
        PROJECT_ROOT / "docs" / "agent" / "SPEC.md",
    )
)
REPORT_PATH = Path(
    os.environ.get(
        "RALPH_REPORT_PATH",
        PROJECT_ROOT / "docs" / "agent" / "spec_report.json",
    )
)


def parse_spec(md_path: Path) -> list[dict]:
    """Parse SPEC.md and return a list of spec items."""
    text = md_path.read_text(encoding="utf-8")
    items: list[dict] = []

    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        # Match bullet lines like: - [x] **ID** — Description
        m = re.match(r"^\s*-\s*\[[\s x]*\]\s*\*\*(?P<id>[^*]+)\*\*\s*—\s*(?P<desc>.+)$", line)
        if not m:
            i += 1
            continue

        spec_id = m.group("id").strip()
        description = m.group("desc").strip()
        i += 1

        item: dict[str, str | int | bool] = {
            "id": spec_id,
            "description": description,
            "verification_command": "",
            "expected_output_contains": "",
            "expected_exit_code": 0,
            "passes": False,
        }

        while i < len(lines) and lines[i].startswith("  "):
            kv = re.match(
                r"^\s*-\s+`(?P<key>[^`]+)`:\s*(?:`(?P<val_quoted>[^`]*)`|(?P<val_unquoted>[^\n]+))",
                lines[i],
            )
            if kv:
                key = kv.group("key").strip()
                val = (
                    kv.group("val_quoted")
                    if kv.group("val_quoted") is not None
                    else kv.group("val_unquoted").strip()
                )
                if key == "expected_exit_code":
                    try:
                        item[key] = int(val)
                    except ValueError:
                        item[key] = val
                elif key == "passes":
                    item[key] = val.lower() in ("true", "1", "yes")
                else:
                    item[key] = val
            i += 1

        items.append(item)

    return items


def _get_timeout(cmd: str, spec_id: str) -> int:
    """Return per-category timeout override."""
    lower_cmd = cmd.lower()
    if "regression" in lower_cmd or "e2e" in lower_cmd:
        return 180
    if spec_id.startswith(("RISK-", "E2E-")):
        return 180
    if "pytest tests/unit/" in lower_cmd and "-k" not in lower_cmd:
        return 120
    return 60


def run_command(cmd: str, spec_id: str = "") -> tuple[int, str, str]:
    """Run a shell command and return (exit_code, stdout, stderr)."""
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["PATH"] = f"{PROJECT_ROOT}/.venv/bin:{env.get('PATH', '')}"
    timeout = _get_timeout(cmd, spec_id)
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            env=env,
            timeout=timeout,
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode("utf-8") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = exc.stderr.decode("utf-8") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        return (
            -1,
            stdout,
            stderr + f"\n[TIMEOUT] Command exceeded {timeout} seconds",
        )


def evaluate_item(item: dict) -> dict:
    """Run the verification command and determine pass/fail."""
    cmd = item.get("verification_command", "")
    if not cmd:
        return {
            **item,
            "actual_exit_code": None,
            "actual_output": "",
            "passes": False,
            "reason": "No verification command",
        }

    exit_code, stdout, stderr = run_command(cmd, item.get("id", ""))
    combined_output = stdout + "\n" + stderr

    passed = True
    reasons: list[str] = []

    expected_exit = item.get("expected_exit_code")
    if isinstance(expected_exit, int):
        if exit_code != expected_exit:
            passed = False
            reasons.append(f"exit code {exit_code} != {expected_exit}")

    expected_contains = item.get("expected_output_contains", "")
    if expected_contains and expected_contains not in combined_output:
        passed = False
        reasons.append(f"output did not contain '{expected_contains}'")

    return {
        **item,
        "actual_exit_code": exit_code,
        "actual_output": combined_output.strip()[:2000],
        "passes": passed,
        "reason": "; ".join(reasons) if reasons else "",
    }


def main() -> int:
    spec_path = SPEC_PATH
    if len(sys.argv) > 1 and sys.argv[1].startswith("--spec="):
        spec_path = Path(sys.argv[1].split("=", 1)[1])

    if not spec_path.exists():
        print(f"[RALPH SPEC] ERROR: {spec_path} not found.")
        return 1

    items = parse_spec(spec_path)
    if not items:
        print(f"[RALPH SPEC] WARNING: No spec items found in {spec_path}.")

    results = []
    passed_count = 0
    failed_count = 0

    print(f"[RALPH SPEC] Checking {len(items)} spec items...")
    print("=" * 50)

    for item in items:
        evaluated = evaluate_item(item)
        results.append(evaluated)

        status = "PASS" if evaluated["passes"] else "FAIL"
        if evaluated["passes"]:
            passed_count += 1
        else:
            failed_count += 1

        print(f"[{status}] {evaluated['id']}: {evaluated['description']}")
        if not evaluated["passes"] and evaluated.get("reason"):
            print(f"       Reason: {evaluated['reason']}")

    report = {
        "total": len(results),
        "passed": passed_count,
        "failed": failed_count,
        "items": results,
    }

    REPORT_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"[RALPH SPEC] Report written to {REPORT_PATH}")
    print("=" * 50)
    print(f"[RALPH SPEC] Done. Passed: {passed_count}, Failed: {failed_count}")

    return 0 if failed_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
