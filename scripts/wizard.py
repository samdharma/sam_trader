#!/usr/bin/env python3
"""SAM Trader V3 — First-run wizard. Generates .env from .env.example template."""

from __future__ import annotations

import getpass
import hashlib
import os
import re
import stat
import sys
from collections.abc import Callable
from pathlib import Path

ENV_PATH = Path(".env")
TEMPLATE_PATH = Path(".env.example")

VALIDATORS = {
    "TRADER_ID": lambda v: (
        v
        if re.match(r"^[a-zA-Z0-9_]+$", v)
        else (_ for _ in ()).throw(ValueError("Only letters, numbers, underscores"))
    ),
    "SAM_ENV": lambda v: (
        v
        if v in ("paper", "live")
        else (_ for _ in ()).throw(ValueError("Must be 'paper' or 'live'"))
    ),
}

# ── helpers ──────────────────────────────────────────────────────────────────


def _md5(val: str) -> str:
    return hashlib.md5(val.encode()).hexdigest()


def _parse_template(lines: list[str]) -> dict[str, str]:
    """Parse KEY=VALUE pairs from template lines; ignore comments and blanks."""
    result: dict[str, str] = {}
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" in line:
            key, _, value = line.partition("=")
            result[key.strip()] = value
    return result


def _bool(val: str) -> str:
    v = val.strip().lower()
    if v in ("y", "yes", "1", "true"):
        return "true"
    if v in ("n", "no", "0", "false", ""):
        return "false"
    raise ValueError("Enter y/n")


def _prompt(
    label: str,
    default: str | None = None,
    validator: Callable[[str], str] | None = None,
    masked: bool = False,
) -> str:
    """Prompt with optional default, validation, and getpass masking."""
    display = f"{label} [{default}]: " if default else f"{label}: "

    for _attempt in range(3):
        try:
            raw = getpass.getpass(display) if masked else input(display)
        except EOFError:
            print("\nAborted.")
            sys.exit(1)

        if raw == "" and default is not None:
            val = default
        else:
            val = raw

        if validator is not None and val != default:
            try:
                return validator(val)
            except ValueError as e:
                print(f"Invalid: {e}")
                continue

        return val

    # Exhausted retries — fall back to default
    if default is not None:
        return default
    raise ValueError("Too many invalid attempts")


def _prompt_bool(label: str, default: bool = False) -> str:
    default_hint = "Y/n" if default else "y/N"
    try:
        raw = input(f"{label} [{default_hint}]: ")
    except EOFError:
        print("\nAborted.")
        sys.exit(1)
    return _bool(raw if raw else ("y" if default else "n"))


def _confirm_write(env_path: Path) -> bool:
    """Return True if it's safe to write env_path."""
    if not env_path.exists():
        return True
    try:
        ans = input(f"{env_path} already exists. Overwrite? [y/N]: ")
    except EOFError:
        print("\nAborted.")
        return False
    return ans.strip().lower() in ("y", "yes")


_PASSWORD_KEYS = {
    "POSTGRES_PASSWORD",
    "TWS_PASSWORD",
    "FUTU_ACCOUNT_PWD_MD5",
    "FUTU_UNLOCK_PWD_MD5",
    "REDIS_PASSWORD",
}


def _build_summary(updates: dict[str, str]) -> list[str]:
    lines: list[str] = []
    for key, value in updates.items():
        if key in _PASSWORD_KEYS:
            display = "********" if value else "(empty)"
        elif value == "":
            display = "(empty)"
        else:
            display = value
        lines.append(f"  {key}: {display}")
    return lines


def write_env(
    updates: dict[str, str],
    template: list[str],
    env_path: Path = ENV_PATH,
) -> None:
    """Write .env by merging updates into template lines (no confirmation)."""
    output: list[str] = []
    for line in template:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in line:
            key = line.split("=", 1)[0].strip()
            if key in updates:
                _, _, rest = line.partition("=")
                hash_idx = rest.find("#")
                if hash_idx > 0 and rest[hash_idx - 1] == " ":
                    comment_start = hash_idx
                    while comment_start > 0 and rest[comment_start - 1] == " ":
                        comment_start -= 1
                    output.append(f"{key}={updates[key]}{rest[comment_start:]}")
                else:
                    output.append(f"{key}={updates[key]}")
                continue
        output.append(line)

    env_path.write_text("\n".join(output) + "\n")
    os.chmod(env_path, stat.S_IRUSR | stat.S_IWUSR)


# ── wizard flow ──────────────────────────────────────────────────────────────


def run_wizard(
    template_path: Path = TEMPLATE_PATH,
) -> tuple[dict[str, str], list[str], dict[str, str]]:
    """Run interactive prompts and return (updates, template_lines, defaults)."""
    template_lines = template_path.read_text().splitlines()
    defaults = _parse_template(template_lines)
    updates: dict[str, str] = {}

    print("=" * 60)
    print("SAM Trader V3 — First Run Wizard")
    print("=" * 60)

    updates["TRADER_ID"] = _prompt(
        "Trader ID",
        default=defaults.get("TRADER_ID", "sam_trader"),
        validator=VALIDATORS["TRADER_ID"],
    )
    updates["SAM_ENV"] = _prompt(
        "Environment (paper/live)",
        default=defaults.get("SAM_ENV", "paper"),
        validator=VALIDATORS["SAM_ENV"],
    )

    # ── Futu broker ──
    futu_default = defaults.get("FUTU_ENABLED", "false") == "true"
    updates["FUTU_ENABLED"] = _prompt_bool("Enable Futu broker?", default=futu_default)
    if updates["FUTU_ENABLED"] == "true":
        updates["FUTU_ACCOUNT_ID"] = _prompt(
            "Futu account ID (email/phone)",
            default=defaults.get("FUTU_ACCOUNT_ID", ""),
        )
        raw_pwd = _prompt("Futu account password", masked=True)
        if raw_pwd:
            updates["FUTU_ACCOUNT_PWD_MD5"] = _md5(raw_pwd)
        raw_unlock = _prompt("Futu trade-unlock password (optional)", masked=True)
        if raw_unlock:
            updates["FUTU_UNLOCK_PWD_MD5"] = _md5(raw_unlock)

    # ── IB broker ──
    ib_default = defaults.get("IB_ENABLED", "false") == "true"
    updates["IB_ENABLED"] = _prompt_bool("Enable IBKR broker?", default=ib_default)
    if updates["IB_ENABLED"] == "true":
        updates["IB_ACCOUNT_ID"] = _prompt(
            "IB Account ID", default=defaults.get("IB_ACCOUNT_ID", "")
        )
        updates["TWS_USERID"] = _prompt(
            "TWS User ID", default=defaults.get("TWS_USERID", "")
        )
        updates["TWS_PASSWORD"] = _prompt("TWS Password", masked=True)

    updates["POSTGRES_PASSWORD"] = _prompt(
        "PostgreSQL password",
        default=defaults.get("POSTGRES_PASSWORD", "sam_secret"),
    )
    updates["REDIS_PASSWORD"] = _prompt(
        "Redis password (optional)", default=defaults.get("REDIS_PASSWORD", "")
    )

    print()  # blank line before summary
    return updates, template_lines, defaults


# ── CLI entry-point ──────────────────────────────────────────────────────────


def main() -> int:
    try:
        updates, template_lines, _defaults = run_wizard(TEMPLATE_PATH)

        if not _confirm_write(ENV_PATH):
            print("Aborted.")
            return 1

        write_env(updates, template_lines, ENV_PATH)
        print(f"INFO: .env written to {ENV_PATH.resolve()}")
        return 0
    except (KeyboardInterrupt, SystemExit):
        print("\nAborted.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
