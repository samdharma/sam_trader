"""Bundle validation — schema check + strategy class check + backtest gate."""

from __future__ import annotations

import dataclasses
import importlib
import logging
import os
import re
from typing import Any

import yaml
from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import AccountType, OmsType
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.instruments import Equity
from nautilus_trader.model.objects import Currency, Money, Price, Quantity
from nautilus_trader.trading.config import ImportableStrategyConfig, StrategyFactory
from nautilus_trader.trading.strategy import Strategy

from sam_trader.bundle_loader import (
    VALID_VENUES,
    _derive_config_path,
    _nautilus_to_futu_code,
)

logger = logging.getLogger(__name__)

_SEMVER_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
_FAMILY_RE = re.compile(r"^[A-Za-z0-9_]+$")


# Synthetic backtest timestamps — 2024-01-01 00:00:00 UTC in nanoseconds
_BACKTEST_BASE_TS = 1_704_067_200_000_000_000
_BACKTEST_BAR_INTERVAL_NS = 300_000_000_000  # 5 minutes


@dataclasses.dataclass(frozen=True)
class BundleCheckResult:
    """Result of validating a single bundle."""

    bundle_id: str
    passed: bool
    errors: list[str]
    warnings: list[str]


@dataclasses.dataclass(frozen=True)
class ValidationResult:
    """Aggregate result of validating all bundles in a file."""

    all_passed: bool
    bundles: list[BundleCheckResult]
    summary: str


def _validate_bundle_schema(bundle: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Validate the structure of a single bundle dict.

    Returns
    -------
    tuple[list[str], list[str]]
        (errors, warnings)

    """
    errors: list[str] = []
    warnings: list[str] = []

    # id
    bundle_id = bundle.get("id")
    if bundle_id is None:
        errors.append("Missing required field: id")
    elif not isinstance(bundle_id, str):
        errors.append("Field 'id' must be a string")

    # enabled
    enabled = bundle.get("enabled")
    if enabled is not None and not isinstance(enabled, bool):
        errors.append("Field 'enabled' must be a boolean")

    # venue
    venue = bundle.get("venue")
    if venue is None:
        errors.append("Missing required field: venue")
    elif venue not in VALID_VENUES:
        errors.append(f"Unknown venue: {venue!r}")

    # market (optional, defaults to "US" — must be US or HK if present)
    market = bundle.get("market")
    if market is not None and market not in ("US", "HK"):
        errors.append(f"Field 'market' must be 'US' or 'HK', got: {market!r}")

    # strategy
    strategy = bundle.get("strategy")
    if not strategy:
        errors.append("Missing required field: strategy")
    elif not isinstance(strategy, dict):
        errors.append("Field 'strategy' must be a mapping")
    else:
        path = strategy.get("path")
        if not path:
            errors.append("Missing required field: strategy.path")
        elif not isinstance(path, str):
            errors.append("Field 'strategy.path' must be a string")

        config = strategy.get("config")
        if config is None:
            errors.append("Missing required field: strategy.config")
        elif not isinstance(config, dict):
            errors.append("Field 'strategy.config' must be a mapping")
        else:
            if "instrument_id" not in config:
                errors.append("Missing required field: strategy.config.instrument_id")
            elif not isinstance(config.get("instrument_id"), str):
                errors.append("Field 'strategy.config.instrument_id' must be a string")

            if "bar_type" not in config:
                errors.append("Missing required field: strategy.config.bar_type")
            elif not isinstance(config.get("bar_type"), str):
                errors.append("Field 'strategy.config.bar_type' must be a string")

        config_path = strategy.get("config_path")
        if config_path is not None and not isinstance(config_path, str):
            errors.append("Field 'strategy.config_path' must be a string")

    # bracket
    bracket = bundle.get("bracket")
    if bracket is not None and not isinstance(bracket, dict):
        errors.append("Field 'bracket' must be a mapping")

    # risk
    risk = bundle.get("risk")
    if risk is not None and not isinstance(risk, dict):
        errors.append("Field 'risk' must be a mapping")

    # metadata: family
    family = bundle.get("family")
    if family is not None:
        if not isinstance(family, str):
            errors.append("Field 'family' must be a string")
        elif not _FAMILY_RE.match(family):
            errors.append("Field 'family' must be alphanumeric with underscores only")

    # metadata: version (semver)
    version = bundle.get("version")
    if version is not None:
        if not isinstance(version, str):
            errors.append("Field 'version' must be a string")
        elif not _SEMVER_RE.match(version):
            errors.append("Field 'version' must be a valid semver string (x.y.z)")

    # metadata: variant
    variant = bundle.get("variant")
    if variant is not None and not isinstance(variant, str):
        errors.append("Field 'variant' must be a string")

    return errors, warnings


def _validate_strategy_class(strategy_path: str) -> tuple[bool, list[str]]:
    """Check that *strategy_path* points to an importable Strategy subclass.

    Returns
    -------
    tuple[bool, list[str]]
        (ok, errors)

    """
    errors: list[str] = []
    try:
        module_path, class_name = strategy_path.split(":", 1)
    except ValueError:
        errors.append(f"Invalid strategy path format: {strategy_path!r}")
        return False, errors

    try:
        module = importlib.import_module(module_path)
    except ImportError as exc:
        errors.append(f"Cannot import module for {strategy_path!r}: {exc}")
        return False, errors

    try:
        cls = getattr(module, class_name)
    except AttributeError as exc:
        errors.append(f"Cannot find class {strategy_path!r}: {exc}")
        return False, errors

    if not issubclass(cls, Strategy):
        errors.append(f"{strategy_path} is not a Strategy subclass")
        return False, errors

    # Verify config class exists
    config_path = _derive_config_path(strategy_path)
    try:
        cfg_module_path, cfg_class_name = config_path.split(":", 1)
        cfg_module = (
            module
            if cfg_module_path == module_path
            else importlib.import_module(cfg_module_path)
        )
        getattr(cfg_module, cfg_class_name)
    except (ValueError, ImportError, AttributeError) as exc:
        errors.append(f"Cannot find config class {config_path!r}: {exc}")
        return False, errors

    return True, errors


def _make_instrument(instrument_id_str: str) -> Equity:
    """Build a minimal Equity instrument for the backtest gate."""
    instrument_id = InstrumentId.from_str(instrument_id_str)
    return Equity(
        instrument_id=instrument_id,
        raw_symbol=instrument_id.symbol,
        currency=Currency.from_str("USD"),
        price_precision=2,
        price_increment=Price.from_str("0.01"),
        lot_size=Quantity.from_int(1),
        ts_event=0,
        ts_init=0,
    )


def _make_synthetic_bars(bar_type_str: str, count: int = 20) -> list[Bar]:
    """Generate flat synthetic bars for the backtest gate."""
    bar_type = BarType.from_str(bar_type_str)
    bars: list[Bar] = []
    for i in range(count):
        ts = _BACKTEST_BASE_TS + i * _BACKTEST_BAR_INTERVAL_NS
        bars.append(
            Bar(
                bar_type=bar_type,
                open=Price.from_str("150.00"),
                high=Price.from_str("151.00"),
                low=Price.from_str("149.00"),
                close=Price.from_str("150.50"),
                volume=Quantity.from_int(1000),
                ts_event=ts,
                ts_init=ts + 1,
            )
        )
    return bars


def _run_backtest_gate_in_process(
    strategy_path: str,
    config_path: str,
    config: dict[str, Any],
) -> tuple[bool, list[str]]:
    """Internal worker that runs inside a subprocess.

    Must be a module-level function so it is picklable by
    ``multiprocessing``.

    """
    errors: list[str] = []
    engine: BacktestEngine | None = None

    instrument_id_str = config.get("instrument_id")
    bar_type_str = config.get("bar_type")

    if not instrument_id_str:
        errors.append("Cannot run backtest gate: missing instrument_id in config")
        return False, errors
    if not bar_type_str:
        errors.append("Cannot run backtest gate: missing bar_type in config")
        return False, errors

    try:
        engine = BacktestEngine()

        instrument_id = InstrumentId.from_str(instrument_id_str)
        venue = instrument_id.venue

        engine.add_venue(
            venue=venue,
            oms_type=OmsType.NETTING,
            account_type=AccountType.MARGIN,
            starting_balances=[Money(100_000, Currency.from_str("USD"))],
        )

        equity = _make_instrument(instrument_id_str)
        engine.add_instrument(equity)

        isc = ImportableStrategyConfig(
            strategy_path=strategy_path,
            config_path=config_path,
            config=config,
        )
        strategy = StrategyFactory.create(isc)
        engine.add_strategy(strategy)

        bars = _make_synthetic_bars(bar_type_str)
        engine.add_data(bars)

        engine.run()
        return True, errors
    except Exception as exc:
        errors.append(f"Backtest gate failed: {exc}")
        return False, errors
    finally:
        if engine is not None:
            try:
                engine.dispose()
            except Exception:
                pass


def _backtest_gate_worker(
    q: Any,
    strategy_path: str,
    config_path: str,
    config: dict[str, Any],
) -> None:
    """Module-level worker for multiprocessing.

    Must be picklable — therefore defined at module scope.

    """
    ok, errors = _run_backtest_gate_in_process(strategy_path, config_path, config)
    q.put((ok, errors))


def _run_backtest_gate(isc: ImportableStrategyConfig) -> tuple[bool, list[str]]:
    """Run a minimal backtest to verify the strategy doesn't crash.

    This is a *smoke test* — it does not validate profitability or
    correctness, only that the strategy can be instantiated and run
    through a small number of bars without raising an exception.

    The backtest is executed in a fresh subprocess to avoid global-state
    conflicts when multiple BacktestEngine instances are created in the
    same process (NautilusTrader v1.227.0 limitation).

    Returns
    -------
    tuple[bool, list[str]]
        (ok, errors)

    """
    import multiprocessing

    ctx = multiprocessing.get_context("spawn")
    queue: Any = ctx.Queue()

    p = ctx.Process(
        target=_backtest_gate_worker,
        args=(queue, isc.strategy_path, isc.config_path, dict(isc.config)),
    )
    p.start()
    p.join(timeout=60)

    if p.is_alive():
        p.terminate()
        p.join()
        return False, ["Backtest gate timed out after 60 seconds"]

    if p.exitcode != 0:
        return False, [f"Backtest gate process exited with code {p.exitcode}"]

    try:
        ok, errors = queue.get(timeout=5)
        return ok, errors
    except Exception as exc:
        return False, [f"Backtest gate result unreadable: {exc}"]


def validate_bundles(
    path: str | os.PathLike[str],
    *,
    backtest_gate: bool = True,
) -> ValidationResult:
    """Validate all bundles in a YAML file.

    Every bundle (enabled or disabled) receives a schema check.
    Enabled bundles additionally receive a strategy-class existence
    check and, unless *backtest_gate* is ``False``, a minimal backtest.

    Parameters
    ----------
    path : str | os.PathLike[str]
        Path to the bundles YAML file.
    backtest_gate : bool, default True
        Whether to run the backtest smoke test for enabled bundles.

    Returns
    -------
    ValidationResult
        Aggregate validation result.

    """
    path_str = os.fspath(path)

    # Parse YAML directly so we can validate disabled bundles too
    try:
        with open(path_str, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)
    except yaml.YAMLError as exc:
        return ValidationResult(
            all_passed=False,
            bundles=[],
            summary=f"Failed to parse YAML: {exc}",
        )
    except FileNotFoundError:
        return ValidationResult(
            all_passed=False,
            bundles=[],
            summary=f"Bundles file not found: {path_str}",
        )

    if raw is None:
        return ValidationResult(
            all_passed=True,
            bundles=[],
            summary="No bundles defined (empty file)",
        )

    if not isinstance(raw, dict):
        return ValidationResult(
            all_passed=False,
            bundles=[],
            summary="Bundles file must contain a mapping",
        )

    bundles_raw = raw.get("bundles", [])
    if not isinstance(bundles_raw, list):
        return ValidationResult(
            all_passed=False,
            bundles=[],
            summary="'bundles' must be a list",
        )

    bundle_results: list[BundleCheckResult] = []

    for bundle in bundles_raw:
        if not isinstance(bundle, dict):
            bundle_results.append(
                BundleCheckResult(
                    bundle_id="unknown",
                    passed=False,
                    errors=["Each bundle must be a mapping"],
                    warnings=[],
                )
            )
            continue

        bundle_id = bundle.get("id", "unknown")
        enabled = bundle.get("enabled", True)

        schema_errors, schema_warnings = _validate_bundle_schema(bundle)
        all_errors = list(schema_errors)

        if enabled and not schema_errors:
            strategy_path = bundle.get("strategy", {}).get("path", "")
            if strategy_path:
                ok, strat_errors = _validate_strategy_class(strategy_path)
                all_errors.extend(strat_errors)

                if ok and backtest_gate:
                    # Build ImportableStrategyConfig manually so we can run
                    # the backtest gate without relying on load_bundles
                    config_path = bundle.get("strategy", {}).get(
                        "config_path"
                    ) or _derive_config_path(strategy_path)
                    config: dict[str, Any] = dict(
                        bundle.get("strategy", {}).get("config", {})
                    )
                    for key, value in bundle.get("bracket", {}).items():
                        config.setdefault(key, value)
                    for key, value in bundle.get("risk", {}).items():
                        config.setdefault(key, value)

                    venue = bundle.get("venue", "IB")
                    if venue == "FUTU":
                        instrument_id = config.get("instrument_id")
                        if instrument_id and isinstance(instrument_id, str):
                            try:
                                config["futu_code"] = _nautilus_to_futu_code(
                                    instrument_id
                                )
                            except ValueError:
                                all_errors.append(
                                    f"Invalid instrument_id for Futu: {instrument_id}"
                                )
                    if venue == "IB":
                        config.setdefault("exchange", "SMART")
                    market = bundle.get("market", "US")
                    config.setdefault("market", market)
                    config.setdefault("venue", venue)
                    config.setdefault("bundle_id", bundle_id)

                    if not all_errors:
                        isc = ImportableStrategyConfig(
                            strategy_path=strategy_path,
                            config_path=config_path,
                            config=config,
                        )
                        bt_ok, bt_errors = _run_backtest_gate(isc)
                        all_errors.extend(bt_errors)

        passed = not all_errors
        bundle_results.append(
            BundleCheckResult(
                bundle_id=bundle_id,
                passed=passed,
                errors=all_errors,
                warnings=schema_warnings,
            )
        )

    all_passed = all(r.passed for r in bundle_results)
    total = len(bundle_results)
    passed_count = sum(1 for r in bundle_results if r.passed)
    summary = f"{passed_count}/{total} bundles passed validation"

    return ValidationResult(
        all_passed=all_passed,
        bundles=bundle_results,
        summary=summary,
    )
