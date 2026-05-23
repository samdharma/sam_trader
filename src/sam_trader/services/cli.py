"""SAM Trader CLI — operations commands.

Intended to be invoked as ``python -m sam_trader.services.cli`` or via the
``sam-validate-bundles`` console script entry point.

Full ``sam`` command suite (status, health, backup, etc.) is implemented in
Phase 8 (sam-services container).

"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from sam_trader.bundle_validation import validate_bundles

logger = logging.getLogger(__name__)

DEFAULT_BUNDLES_PATH = Path("config/bundles.yaml")


def _cmd_validate_bundles(args: argparse.Namespace) -> int:
    """Run the validate-bundles command."""
    path = Path(args.path)
    if not path.exists():
        print(f"ERROR: Bundles file not found: {path}", file=sys.stderr)
        return 1

    result = validate_bundles(path, backtest_gate=not args.no_backtest)

    print(result.summary)
    print()

    for bundle in result.bundles:
        status = "PASS" if bundle.passed else "FAIL"
        print(f"[{status}] {bundle.bundle_id}")
        for error in bundle.errors:
            print(f"  ERROR: {error}")
        for warning in bundle.warnings:
            print(f"  WARN:  {warning}")

    return 0 if result.all_passed else 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sam",
        description="SAM Trader V3 — operations CLI",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # validate-bundles
    validate_parser = subparsers.add_parser(
        "validate-bundles",
        help="Validate bundle YAML: schema + strategy class + backtest gate",
    )
    validate_parser.add_argument(
        "--path",
        type=str,
        default=str(DEFAULT_BUNDLES_PATH),
        help=f"Path to bundles YAML (default: {DEFAULT_BUNDLES_PATH})",
    )
    validate_parser.add_argument(
        "--no-backtest",
        action="store_true",
        help="Skip the backtest smoke test",
    )
    validate_parser.set_defaults(func=_cmd_validate_bundles)

    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    logging.basicConfig(
        level=logging.WARNING,
        format="%(levelname)s: %(message)s",
    )
    parser = _build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
