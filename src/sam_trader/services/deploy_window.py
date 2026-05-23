"""Deployment window checker for SAM Trader V3.

Reads DEPLOY_WINDOW (HH:MM-HH:MM, default 05:00-08:00 HKT)
and logs whether the current time falls inside the window.
"""

from __future__ import annotations

import argparse
import datetime
import logging
import os
import sys

DEPLOY_WINDOW: str = os.getenv("DEPLOY_WINDOW", "05:00-08:00")
TZ: str = os.getenv("TZ", "Asia/Hong_Kong")

logger = logging.getLogger("sam_trader.deploy_window")


def is_in_window(
    window: str,
    now: datetime.datetime | None = None,
) -> bool:
    """Return True if *now* lies inside the given HH:MM-HH:MM window.

    Supports overnight windows (e.g. 23:00-02:00).
    """
    if now is None:
        now = datetime.datetime.now()

    try:
        start_str, end_str = window.split("-")
    except ValueError:
        logger.error("Invalid DEPLOY_WINDOW format: %s (expected HH:MM-HH:MM)", window)
        return False

    def _parse(t: str) -> datetime.time:
        h, m = map(int, t.strip().split(":"))
        return datetime.time(h, m)

    start = _parse(start_str)
    end = _parse(end_str)
    current = now.time()

    if start <= end:
        return start <= current <= end
    # Overnight window
    return current >= start or current <= end


def check_window(window: str = DEPLOY_WINDOW, tz: str = TZ) -> bool:
    """Log window status and return the boolean result."""
    active = is_in_window(window)
    if active:
        logger.info("Deployment window ACTIVE: %s (%s)", window, tz)
        print(f"deploy_window_active=true window={window} tz={tz}")
    else:
        logger.info("Deployment window INACTIVE: %s (%s)", window, tz)
        print(f"deploy_window_active=false window={window} tz={tz}")
    return active


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(description="SAM Trader V3 Deployment Window")
    parser.add_argument(
        "--window",
        default=DEPLOY_WINDOW,
        help="Deployment window in HH:MM-HH:MM format",
    )
    args = parser.parse_args()
    active = check_window(args.window)
    return 0 if active else 0  # always exit 0; caller checks stdout if needed


if __name__ == "__main__":
    sys.exit(main())
