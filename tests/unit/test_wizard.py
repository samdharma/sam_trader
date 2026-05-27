"""Tests for scripts/wizard.py — first-run .env generator.

Focuses on integration-level behavior: template merging, security (password
masking, permissions), wizard flows, and error handling.  Avoids testing
trivial one-line helpers in isolation.
"""

from __future__ import annotations

import getpass
import importlib.util
import stat
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from tests.helpers import patch_path_attrs

WIZARD_PATH = Path(__file__).resolve().parents[2] / "scripts" / "wizard.py"
spec = importlib.util.spec_from_file_location("wizard", WIZARD_PATH)
wizard = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
spec.loader.exec_module(wizard)  # type: ignore[union-attr]

# NOTE: patch.object breaks Path.exists() on importlib-loaded modules.
# When patching Path attributes (ENV_PATH, TEMPLATE_PATH) on `wizard`,
# always use `patch_path_attrs` from tests.helpers instead of
# `unittest.mock.patch.object`.  See tests/helpers.py for details.


# ── Template parsing (core data layer) ────────────────────────────────────────


class TestTemplateParsing:
    def test_extracts_key_value_pairs(self) -> None:
        env = wizard._parse_template(
            [
                "# comment",
                "TRADER_ID=sam_trader",
                "SAM_ENV=paper",
                "",
                "REDIS_PASSWORD=",
            ]
        )
        assert env == {
            "TRADER_ID": "sam_trader",
            "SAM_ENV": "paper",
            "REDIS_PASSWORD": "",
        }

    def test_ignores_comments_and_blank_lines(self) -> None:
        assert wizard._parse_template(["# foo", "", "KEY=value"]) == {"KEY": "value"}

    def test_preserves_inline_comment_in_value(self) -> None:
        env = wizard._parse_template(
            ["IB_TRADING_MODE=paper  # consumed by docker-compose"]
        )
        assert env["IB_TRADING_MODE"] == "paper  # consumed by docker-compose"


# ── Input validation ──────────────────────────────────────────────────────────


class TestValidation:
    def test_env_must_be_paper_or_live(self) -> None:
        with pytest.raises(ValueError, match="Must be 'paper' or 'live'"):
            wizard.VALIDATORS["SAM_ENV"]("staging")

    def test_trader_id_rejects_spaces(self) -> None:
        with pytest.raises(ValueError, match="Only letters, numbers, underscores"):
            wizard.VALIDATORS["TRADER_ID"]("sam trader")

    def test_bool_rejects_garbage(self) -> None:
        with pytest.raises(ValueError, match="Enter y/n"):
            wizard._bool("maybe")


# ── Prompt helpers (behavioural) ──────────────────────────────────────────────


class TestAutoDetectMarket:
    """Tests for _auto_detect_market() HKT time heuristic."""

    @patch.object(wizard, "datetime")
    def test_us_window_evening(self, mock_dt: object) -> None:
        """16:00 HKT → US market."""
        mock_dt.now.return_value = datetime(2026, 5, 27, 16, 0, 0)  # type: ignore
        assert wizard._auto_detect_market() == "US"

    @patch.object(wizard, "datetime")
    def test_us_window_midnight(self, mock_dt: object) -> None:
        """00:00 HKT → US market (crossing midnight)."""
        mock_dt.now.return_value = datetime(2026, 5, 27, 0, 0, 0)  # type: ignore
        assert wizard._auto_detect_market() == "US"

    @patch.object(wizard, "datetime")
    def test_us_window_late_night(self, mock_dt: object) -> None:
        """03:59 HKT → US market (boundary)."""
        mock_dt.now.return_value = datetime(2026, 5, 27, 3, 59, 0)  # type: ignore
        assert wizard._auto_detect_market() == "US"

    @patch.object(wizard, "datetime")
    def test_hk_window_morning(self, mock_dt: object) -> None:
        """04:00 HKT → HK market (boundary)."""
        mock_dt.now.return_value = datetime(2026, 5, 27, 4, 0, 0)  # type: ignore
        assert wizard._auto_detect_market() == "HK"

    @patch.object(wizard, "datetime")
    def test_hk_window_noon(self, mock_dt: object) -> None:
        """12:00 HKT → HK market."""
        mock_dt.now.return_value = datetime(2026, 5, 27, 12, 0, 0)  # type: ignore
        assert wizard._auto_detect_market() == "HK"

    @patch.object(wizard, "datetime")
    def test_hk_window_market_close(self, mock_dt: object) -> None:
        """15:59 HKT → HK market (just before US window)."""
        mock_dt.now.return_value = datetime(2026, 5, 27, 15, 59, 0)  # type: ignore
        assert wizard._auto_detect_market() == "HK"


class TestPromptBehavior:
    def test_validates_with_retry_fallback_to_default(self) -> None:
        with patch("builtins.input", side_effect=["bad", "worse", ""]):
            result = wizard._prompt(
                "Env", default="paper", validator=wizard.VALIDATORS["SAM_ENV"]
            )
        assert result == "paper"

    def test_masked_uses_getpass(self) -> None:
        with patch.object(getpass, "getpass", return_value="secret"):
            assert wizard._prompt("Pwd", masked=True) == "secret"

    def test_eof_aborts(self) -> None:
        with patch("builtins.input", side_effect=EOFError()), pytest.raises(SystemExit):
            wizard._prompt("Label")

    def test_bool_default_behavior(self) -> None:
        with patch("builtins.input", return_value=""):
            assert wizard._prompt_bool("Enable?", default=True) == "true"
            assert wizard._prompt_bool("Enable?", default=False) == "false"


# ── Confirm / overwrite protection ────────────────────────────────────────────


class TestConfirmWrite:
    def test_new_file_always_ok(self, tmp_path: Path) -> None:
        assert wizard._confirm_write(tmp_path / ".env") is True

    def test_overwrite_confirmed(self, tmp_path: Path) -> None:
        p = tmp_path / ".env"
        p.write_text("old")
        with patch("builtins.input", return_value="y"):
            assert wizard._confirm_write(p) is True

    def test_overwrite_denied(self, tmp_path: Path) -> None:
        p = tmp_path / ".env"
        p.write_text("old")
        with patch("builtins.input", return_value="n"):
            assert wizard._confirm_write(p) is False


# ── Summary (password masking — security) ─────────────────────────────────────


class TestBuildSummary:
    def test_masks_password_fields(self) -> None:
        lines = wizard._build_summary(
            {
                "POSTGRES_PASSWORD": "secret",
                "TWS_PASSWORD": "hunter2",
                "TRADER_ID": "sam",
            }
        )
        summary = "\n".join(lines)
        assert "secret" not in summary
        assert "hunter2" not in summary
        assert "********" in summary
        assert "sam" in summary

    def test_shows_empty_as_placeholder(self) -> None:
        assert "(empty)" in "\n".join(wizard._build_summary({"EMPTY_KEY": ""}))


# ── Write .env (template merge + permissions) ─────────────────────────────────


class TestWriteEnv:
    def test_merges_updates_into_template(self, tmp_path: Path) -> None:
        env_path = tmp_path / ".env"
        wizard.write_env(
            {"TRADER_ID": "custom", "SAM_ENV": "live"},
            ["TRADER_ID=sam_trader", "SAM_ENV=paper", "SECRET=default"],
            env_path=env_path,
        )
        content = env_path.read_text()
        assert "TRADER_ID=custom" in content
        assert "SAM_ENV=live" in content
        assert "SECRET=default" in content

    def test_preserves_inline_comments(self, tmp_path: Path) -> None:
        env_path = tmp_path / ".env"
        wizard.write_env(
            {"KEY": "new"}, ["# Header", "KEY=value  # inline"], env_path=env_path
        )
        content = env_path.read_text()
        assert "# Header" in content
        assert "KEY=new  # inline" in content

    def test_sets_owner_only_permissions(self, tmp_path: Path) -> None:
        env_path = tmp_path / ".env"
        wizard.write_env({}, ["KEY=value"], env_path=env_path)
        mode = env_path.stat().st_mode
        assert mode & stat.S_IRUSR and mode & stat.S_IWUSR
        assert not (mode & stat.S_IRGRP) and not (mode & stat.S_IROTH)


# ── Wizard flows (integration) ────────────────────────────────────────────────


class TestRunWizard:
    def test_minimal_flow_no_brokers(self, tmp_path: Path) -> None:
        template = tmp_path / ".env.example"
        template.write_text(
            "TRADER_ID=sam_trader\n"
            "SAM_ENV=paper\n"
            "MARKET=US\n"
            "FUTU_TRD_MARKET=US\n"
            "FUTU_ENABLED=false\n"
            "IB_ENABLED=false\n"
        )
        with patch(
            "builtins.input",
            side_effect=["my_trader", "paper", "US", "n", "n", "sam_secret", ""],
        ):
            updates, _lines, _defaults = wizard.run_wizard(template_path=template)
        assert updates == {
            "TRADER_ID": "my_trader",
            "SAM_ENV": "paper",
            "MARKET": "US",
            "FUTU_TRD_MARKET": "US",
            "FUTU_ENABLED": "false",
            "IB_ENABLED": "false",
            "POSTGRES_PASSWORD": "sam_secret",
            "REDIS_PASSWORD": "",
        }

    def test_futu_enabled_hashes_passwords(self, tmp_path: Path) -> None:
        template = tmp_path / ".env.example"
        template.write_text(
            "TRADER_ID=sam\nSAM_ENV=paper\nMARKET=US\nFUTU_TRD_MARKET=US\n"
            "FUTU_ENABLED=false\nFUTU_ACCOUNT_ID=\n"
            "FUTU_ACCOUNT_PWD_MD5=\nFUTU_UNLOCK_PWD_MD5=\nIB_ENABLED=false\n"
        )
        with patch(
            "builtins.input",
            side_effect=["sam", "paper", "US", "y", "user@ex.com", "n", "pgpass", ""],
        ):
            with patch.object(
                getpass, "getpass", side_effect=["rawpwd", "", "pgpass", ""]
            ):
                updates, _lines, _defaults = wizard.run_wizard(template_path=template)
        assert updates["FUTU_ENABLED"] == "true"
        assert updates["FUTU_ACCOUNT_ID"] == "user@ex.com"
        assert updates["FUTU_ACCOUNT_PWD_MD5"] == wizard._md5("rawpwd")
        assert "FUTU_UNLOCK_PWD_MD5" not in updates  # empty → omitted
        assert updates["MARKET"] == "US"
        assert updates["FUTU_TRD_MARKET"] == "US"

    def test_ib_enabled_collects_credentials(self, tmp_path: Path) -> None:
        template = tmp_path / ".env.example"
        template.write_text(
            "TRADER_ID=sam\nSAM_ENV=paper\nMARKET=US\nFUTU_TRD_MARKET=US\n"
            "FUTU_ENABLED=false\nIB_ENABLED=false\n"
            "IB_ACCOUNT_ID=\nTWS_USERID=\nTWS_PASSWORD=\n"
        )
        with patch(
            "builtins.input",
            side_effect=["sam", "paper", "US", "n", "y", "IB123", "twuser", "n", ""],
        ):
            with patch.object(
                getpass, "getpass", side_effect=["twpass", "sam_secret", ""]
            ):
                updates, _lines, _defaults = wizard.run_wizard(template_path=template)
        assert updates["IB_ENABLED"] == "true"
        assert updates["IB_ACCOUNT_ID"] == "IB123"
        assert updates["TWS_USERID"] == "twuser"
        assert updates["TWS_PASSWORD"] == "twpass"
        assert updates["MARKET"] == "US"
        assert updates["FUTU_TRD_MARKET"] == "US"

    def test_keyboard_interrupt_propagates(self, tmp_path: Path) -> None:
        template = tmp_path / ".env.example"
        template.write_text(
            "TRADER_ID=sam\nSAM_ENV=paper\nMARKET=US\nFUTU_TRD_MARKET=US\n"
            "FUTU_ENABLED=false\nIB_ENABLED=false\n"
        )
        with patch("builtins.input", side_effect=KeyboardInterrupt):
            with pytest.raises(KeyboardInterrupt):
                wizard.run_wizard(template_path=template)

    def test_market_auto_detect_dispatches(self, tmp_path: Path) -> None:
        """When 'auto' is entered, _auto_detect_market is called and result used."""
        template = tmp_path / ".env.example"
        template.write_text(
            "TRADER_ID=sam\nSAM_ENV=paper\nMARKET=US\nFUTU_TRD_MARKET=US\n"
            "FUTU_ENABLED=false\nIB_ENABLED=false\n"
        )
        with patch(
            "builtins.input",
            side_effect=["sam", "paper", "auto", "n", "n", "pgpass", ""],
        ):
            with patch.object(wizard, "_auto_detect_market", return_value="HK"):
                updates, _lines, _defaults = wizard.run_wizard(template_path=template)
        assert updates["MARKET"] == "HK"
        assert updates["FUTU_TRD_MARKET"] == "HK"

    def test_market_explicit_us(self, tmp_path: Path) -> None:
        """When 'US' is entered, MARKET=US without auto-detect."""
        template = tmp_path / ".env.example"
        template.write_text(
            "TRADER_ID=sam\nSAM_ENV=paper\nMARKET=auto\nFUTU_TRD_MARKET=US\n"
            "FUTU_ENABLED=false\nIB_ENABLED=false\n"
        )
        with patch(
            "builtins.input",
            side_effect=["sam", "paper", "US", "n", "n", "pgpass", ""],
        ):
            updates, _lines, _defaults = wizard.run_wizard(template_path=template)
        assert updates["MARKET"] == "US"
        assert updates["FUTU_TRD_MARKET"] == "US"

    def test_market_explicit_hk(self, tmp_path: Path) -> None:
        """When 'HK' is entered, MARKET=HK."""
        template = tmp_path / ".env.example"
        template.write_text(
            "TRADER_ID=sam\nSAM_ENV=paper\nMARKET=auto\nFUTU_TRD_MARKET=US\n"
            "FUTU_ENABLED=false\nIB_ENABLED=false\n"
        )
        with patch(
            "builtins.input",
            side_effect=["sam", "paper", "HK", "n", "n", "pgpass", ""],
        ):
            updates, _lines, _defaults = wizard.run_wizard(template_path=template)
        assert updates["MARKET"] == "HK"
        assert updates["FUTU_TRD_MARKET"] == "HK"

    def test_market_prompt_uses_template_default(self, tmp_path: Path) -> None:
        """MARKET prompt default comes from template, not hardcoded."""
        template = tmp_path / ".env.example"
        template.write_text(
            "TRADER_ID=sam\nSAM_ENV=paper\nMARKET=HK\nFUTU_TRD_MARKET=HK\n"
            "FUTU_ENABLED=false\nIB_ENABLED=false\n"
        )
        with patch(
            "builtins.input",
            side_effect=["sam", "paper", "", "n", "n", "pgpass", ""],
        ):
            with patch.object(
                wizard, "_auto_detect_market", return_value="US"
            ) as mock_detect:
                updates, _lines, _defaults = wizard.run_wizard(template_path=template)
        # Empty input → use default "HK" from template; not "auto", so no detect
        assert updates["MARKET"] == "HK"
        assert updates["FUTU_TRD_MARKET"] == "HK"
        mock_detect.assert_not_called()


# ── CLI entry point ───────────────────────────────────────────────────────────


class TestMain:
    def test_writes_env_on_success(self, tmp_path: Path) -> None:
        template = tmp_path / ".env.example"
        template.write_text(
            "TRADER_ID=sam\nSAM_ENV=paper\nMARKET=US\nFUTU_TRD_MARKET=US\n"
            "FUTU_ENABLED=false\nIB_ENABLED=false\n"
        )
        env_path = tmp_path / ".env"
        inputs = ["my_trader", "paper", "US", "n", "n", "pgpass", "", ""]
        with patch("builtins.input", side_effect=inputs):
            with patch.object(getpass, "getpass", side_effect=["pgpass", ""]):
                with patch_path_attrs(
                    wizard, ENV_PATH=env_path, TEMPLATE_PATH=template
                ):
                    assert wizard.main() == 0
        content = env_path.read_text()
        assert "TRADER_ID=my_trader" in content
        assert "MARKET=US" in content
        assert "FUTU_TRD_MARKET=US" in content

    def test_returns_1_when_user_denies_overwrite(self, tmp_path: Path) -> None:
        template = tmp_path / ".env.example"
        template.write_text(
            "TRADER_ID=sam\nSAM_ENV=paper\nMARKET=US\nFUTU_TRD_MARKET=US\n"
            "FUTU_ENABLED=false\nIB_ENABLED=false\n"
        )
        env_path = tmp_path / ".env"
        env_path.write_text("existing")
        inputs = ["my_trader", "paper", "US", "n", "n", "pgpass", "", "n"]
        with patch("builtins.input", side_effect=inputs):
            with patch.object(getpass, "getpass", side_effect=["pgpass", ""]):
                with patch_path_attrs(
                    wizard, ENV_PATH=env_path, TEMPLATE_PATH=template
                ):
                    assert wizard.main() == 1
        assert env_path.read_text() == "existing"

    def test_returns_1_on_keyboard_interrupt(self, tmp_path: Path) -> None:
        template = tmp_path / ".env.example"
        template.write_text(
            "TRADER_ID=sam\nSAM_ENV=paper\nMARKET=US\nFUTU_TRD_MARKET=US\n"
        )
        env_path = tmp_path / ".env"
        with patch("builtins.input", side_effect=KeyboardInterrupt):
            with patch_path_attrs(wizard, ENV_PATH=env_path, TEMPLATE_PATH=template):
                assert wizard.main() == 1
