#!/usr/bin/env python3
"""
Detect affected test paths based on git diff and TEST_MAP.yaml.

Usage: python scripts/ralph/detect_affected_tests.py [--git-diff-args="HEAD~1"]
Output: space-separated pytest test paths (suitable for xargs)

Environment variables:
    RALPH_PROJECT_DIR - Override project directory
    RALPH_TEST_MAP    - Override TEST_MAP.yaml path
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(
    os.environ.get(
        "RALPH_PROJECT_DIR",
        Path(__file__).parent.parent.parent.resolve(),
    )
)
TEST_MAP_PATH = Path(
    os.environ.get(
        "RALPH_TEST_MAP",
        PROJECT_ROOT / "config" / "TEST_MAP.yaml",
    )
)


def parse_test_map_simple(path: Path) -> dict:
    """Minimal YAML parser sufficient for our TEST_MAP format."""
    text = path.read_text(encoding="utf-8")
    mappings = []
    default_tests = []
    current = None
    in_mappings = False
    in_defaults = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or not stripped:
            continue
        if stripped == "mappings:":
            in_mappings = True
            in_defaults = False
            continue
        if stripped == "default_tests:":
            in_mappings = False
            in_defaults = True
            continue
        if in_mappings:
            if stripped == "- source:":
                current = {"source": None, "tests": []}
                mappings.append(current)
            elif stripped.startswith('- source: "'):
                val = stripped.split('"', 2)[1]
                current = {"source": val, "tests": []}
                mappings.append(current)
            elif stripped.startswith("tests: ["):
                arr = stripped.split("[", 1)[1].rsplit("]", 1)[0]
                tests = [t.strip().strip('"').strip("'") for t in arr.split(",")]
                current["tests"] = tests
            elif stripped.startswith("-") and "tests:" in stripped:
                pass
        elif in_defaults:
            if stripped.startswith("-"):
                val = stripped.lstrip("-").strip().strip('"').strip("'")
                default_tests.append(val)
    return {"mappings": mappings, "default_tests": default_tests}


def parse_test_map(path: Path) -> dict:
    try:
        import yaml

        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    except ImportError:
        return parse_test_map_simple(path)


def get_modified_files(git_diff_args: str = "HEAD") -> list[str]:
    """Return list of modified Python files from git."""
    result = subprocess.run(
        ["git", "diff", "--name-only", "--diff-filter=ACM", git_diff_args],
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
    )
    modified = [f.strip() for f in result.stdout.splitlines() if f.strip().endswith(".py")]

    result2 = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard"],
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
    )
    untracked = [f.strip() for f in result2.stdout.splitlines() if f.strip().endswith(".py")]

    return list(set(modified + untracked))


def map_files_to_tests(modified_files: list[str], test_map: dict) -> list[str]:
    mappings = test_map.get("mappings", [])
    defaults = test_map.get("default_tests", ["tests/unit/"])

    matched_tests = set()
    any_match = False

    for f in modified_files:
        f_norm = f.replace("\\", "/")
        found = False
        for m in mappings:
            src = m.get("source", "").replace("\\", "/")
            matched = False
            if f_norm == src:
                matched = True
            elif "/" in src and (f_norm.endswith("/" + src) or src.endswith("/" + f_norm)):
                matched = True
            if matched:
                for t in m.get("tests", []):
                    matched_tests.add(t)
                found = True
                any_match = True
        if not found:
            if f_norm.startswith("tests/"):
                matched_tests.add(f_norm)
                any_match = True

    if not any_match:
        return defaults

    return sorted(matched_tests)


def main() -> int:
    parser = argparse.ArgumentParser(description="Detect affected tests from git diff")
    parser.add_argument(
        "--git-diff-args",
        default="HEAD",
        help="Args passed to git diff (default: HEAD)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output JSON array instead of space-separated paths",
    )
    args = parser.parse_args()

    if not TEST_MAP_PATH.exists():
        print("tests/unit/", file=sys.stdout)
        return 0

    test_map = parse_test_map(TEST_MAP_PATH)
    modified = get_modified_files(args.git_diff_args)

    if not modified:
        if args.json:
            print("[]")
        return 0

    tests = map_files_to_tests(modified, test_map)

    if args.json:
        print(json.dumps(tests))
    else:
        print(" ".join(tests))

    return 0


if __name__ == "__main__":
    sys.exit(main())
