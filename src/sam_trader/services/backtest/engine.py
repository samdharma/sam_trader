"""Backtest engine wrapper — Nautilus BacktestNode + BacktestRunConfig integration.

Provides a high-level Python wrapper around NautilusTrader's Rust/PyO3
backtesting stack: constructs BacktestRunConfig from strategy bundles +
catalog data, manages BacktestNode lifecycle, and returns BacktestResult.
"""

from __future__ import annotations

import logging

from nautilus_trader.backtest.config import (
    BacktestDataConfig,
    BacktestEngineConfig,
    BacktestRunConfig,
    BacktestVenueConfig,
)
from nautilus_trader.backtest.node import BacktestNode
from nautilus_trader.backtest.results import BacktestResult
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.trading.config import ImportableStrategyConfig

logger = logging.getLogger(__name__)


class BacktestEngineError(Exception):
    """Raised when a backtest run fails."""


class BacktestEngineWrapper:
    """High-level wrapper around NautilusTrader's BacktestNode.

    Constructs :class:`BacktestRunConfig` from strategy bundles + catalog
    parameters, manages the :class:`BacktestNode` lifecycle, and returns
    :class:`BacktestResult` objects.

    Usage::

        wrapper = BacktestEngineWrapper(catalog_path="data/catalog")
        result = wrapper.run(
            strategies=bundles,
            instrument_ids=["TSLA.NASDAQ"],
            bar_types=["TSLA.NASDAQ-5-MINUTE-LAST-EXTERNAL"],
            start="2024-01-01",
            end="2024-06-30",
        )
        print(result.stats_pnls)
        print(result.stats_returns)

    Parameters
    ----------
    catalog_path : str
        Path to the Nautilus ParquetDataCatalog directory.

    """

    def __init__(self, catalog_path: str = "data/catalog") -> None:
        self._catalog_path = catalog_path

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(
        self,
        strategies: list[ImportableStrategyConfig],
        instrument_ids: list[str],
        bar_types: list[str],
        start: str,
        end: str,
        *,
        run_analysis: bool = True,
        venue_name: str = "SIM",
        oms_type: str = "NETTING",
        account_type: str = "MARGIN",
        starting_balances: list[str] | None = None,
        trader_id: str = "BACKTEST-001",
        instance_id: str | None = None,
    ) -> BacktestResult:
        r"""Run a single backtest with the given strategies and data range.

        Constructs all required Nautilus config objects, builds the
        :class:`BacktestNode`, runs the backtest, and returns the result.

        Parameters
        ----------
        strategies : list[ImportableStrategyConfig]
            Strategy configurations to run (typically from bundle loader).
        instrument_ids : list[str]
            Instrument IDs such as ``["TSLA.NASDAQ", "AAPL.NASDAQ"]``.
        bar_types : list[str]
            Bar type strings that must exist in the catalog, e.g.
            ``["TSLA.NASDAQ-5-MINUTE-LAST-EXTERNAL"]``.
        start : str
            Backtest start date (ISO-format: ``"2024-01-01"``).
        end : str
            Backtest end date (ISO-format: ``"2024-06-30"``).
        run_analysis : bool
            When ``True`` (default), :class:`PortfolioAnalyzer` auto-computes
            all 17 Nautilus statistics (Sharpe, Sortino, CAGR, etc.).
        venue_name : str
            Backtest venue name (default ``"SIM"``).
        oms_type : str
            OMS type — ``"NETTING"`` or ``"HEDGING"``.
        account_type : str
            Account type — ``"MARGIN"`` or ``"CASH"``.
        starting_balances : list[str] | None
            Starting account balances (default ``["100000 USD"]``).
        trader_id : str
            Trader ID for the run (default ``"BACKTEST-001"``).
        instance_id : str | None
            Nautilus instance ID. Auto-generated if not provided.

        Returns
        -------
        BacktestResult
            Result object with ``stats_pnls``, ``stats_returns``, and
            execution metadata.

        Raises
        ------
        BacktestEngineError
            If the backtest fails to build or run.

        """
        run_config = self._build_run_config(
            strategies=strategies,
            instrument_ids=instrument_ids,
            bar_types=bar_types,
            start=start,
            end=end,
            run_analysis=run_analysis,
            venue_name=venue_name,
            oms_type=oms_type,
            account_type=account_type,
            starting_balances=starting_balances,
            trader_id=trader_id,
            instance_id=instance_id,
        )

        return self._run_node([run_config])[0]

    def run_multi(
        self,
        configs: list[BacktestRunConfig],
    ) -> list[BacktestResult]:
        """Run multiple backtest configurations in a single BacktestNode.

        Parameters
        ----------
        configs : list[BacktestRunConfig]
            Pre-built run configurations.

        Returns
        -------
        list[BacktestResult]
            One result per run config.

        Raises
        ------
        BacktestEngineError
            If the node fails to build or run.

        """
        return self._run_node(configs)

    def build_run_config(
        self,
        strategies: list[ImportableStrategyConfig],
        instrument_ids: list[str],
        bar_types: list[str],
        start: str,
        end: str,
        *,
        run_analysis: bool = True,
        venue_name: str = "SIM",
        oms_type: str = "NETTING",
        account_type: str = "MARGIN",
        starting_balances: list[str] | None = None,
        trader_id: str = "BACKTEST-001",
        instance_id: str | None = None,
    ) -> BacktestRunConfig:
        """Build a :class:`BacktestRunConfig` without executing.

        Useful for inspecting or extending the config before running.

        Parameters are identical to :meth:`run`.

        Returns
        -------
        BacktestRunConfig

        """
        return self._build_run_config(
            strategies=strategies,
            instrument_ids=instrument_ids,
            bar_types=bar_types,
            start=start,
            end=end,
            run_analysis=run_analysis,
            venue_name=venue_name,
            oms_type=oms_type,
            account_type=account_type,
            starting_balances=starting_balances,
            trader_id=trader_id,
            instance_id=instance_id,
        )

    # ------------------------------------------------------------------
    # Internal: config construction
    # ------------------------------------------------------------------

    @staticmethod
    def _derive_venues_from_instruments(
        instrument_ids: list[str],
    ) -> list[str]:
        """Derive unique venue names from instrument IDs.

        Returns a list of unique venue names as strings (e.g.
        ``["NASDAQ"]`` from ``["TSLA.NASDAQ", "AAPL.NASDAQ"]``),
        falling back to ``["SIM"]`` when instruments have no venue.

        """
        venues: set[str] = set()
        for iid_str in instrument_ids:
            try:
                venue = InstrumentId.from_str(iid_str).venue
                if venue:
                    venues.add(str(venue))
            except (ValueError, AttributeError):
                continue

        if not venues:
            return ["SIM"]

        return sorted(venues)

    def _build_venue_configs(
        self,
        venue_names: list[str],
        oms_type: str,
        account_type: str,
        starting_balances: list[str] | None,
    ) -> list[BacktestVenueConfig]:
        """Construct :class:`BacktestVenueConfig` objects for each venue.

        Nautilus ``BacktestNode`` validates that every instrument's venue
        has a matching ``BacktestVenueConfig``.  This method creates one
        config per unique venue derived from the instruments being tested.

        """
        if starting_balances is None:
            starting_balances = ["100000 USD"]

        return [
            BacktestVenueConfig(
                name=name,
                oms_type=oms_type,
                account_type=account_type,
                starting_balances=starting_balances,
            )
            for name in venue_names
        ]

    def _build_data_config(
        self,
        instrument_ids: list[str],
        bar_types: list[str],
        start: str,
        end: str,
    ) -> BacktestDataConfig:
        """Construct a :class:`BacktestDataConfig` pointing at the catalog."""
        return BacktestDataConfig(
            catalog_path=self._catalog_path,
            data_cls="nautilus_trader.model.data:Bar",
            instrument_ids=instrument_ids,
            bar_types=bar_types,
            start_time=start,
            end_time=end,
        )

    def _build_engine_config(
        self,
        strategies: list[ImportableStrategyConfig],
        run_analysis: bool,
        trader_id: str,
        instance_id: str | None,  # type: ignore[arg-type]  # pyo3 accepts str
    ) -> BacktestEngineConfig:
        """Construct a :class:`BacktestEngineConfig` with strategies."""
        return BacktestEngineConfig(
            strategies=strategies,
            run_analysis=run_analysis,
            trader_id=trader_id,
            instance_id=instance_id,  # type: ignore[arg-type]
        )

    def _build_run_config(
        self,
        strategies: list[ImportableStrategyConfig],
        instrument_ids: list[str],
        bar_types: list[str],
        start: str,
        end: str,
        *,
        run_analysis: bool,
        venue_name: str,
        oms_type: str,
        account_type: str,
        starting_balances: list[str] | None,
        trader_id: str,
        instance_id: str | None,
    ) -> BacktestRunConfig:
        """Assemble the full :class:`BacktestRunConfig`.

        Raises
        ------
        BacktestEngineError
            If no strategies are provided or required fields are missing.

        """
        if not strategies:
            raise BacktestEngineError(
                "At least one ImportableStrategyConfig is required"
            )
        if not instrument_ids:
            raise BacktestEngineError(
                "At least one instrument_id is required for data loading"
            )
        if not bar_types:
            raise BacktestEngineError(
                "At least one bar_type is required for data loading"
            )

        # Derive venue configs from instrument IDs so Nautilus validation
        # can match each instrument's venue to a BacktestVenueConfig.
        # Falls back to ["SIM"] when instruments have no venue.
        venue_names = self._derive_venues_from_instruments(instrument_ids)
        venue_configs = self._build_venue_configs(
            venue_names=venue_names,
            oms_type=oms_type,
            account_type=account_type,
            starting_balances=starting_balances,
        )

        data_config = self._build_data_config(
            instrument_ids=instrument_ids,
            bar_types=bar_types,
            start=start,
            end=end,
        )

        engine_config = self._build_engine_config(
            strategies=strategies,
            run_analysis=run_analysis,
            trader_id=trader_id,
            instance_id=instance_id,
        )

        return BacktestRunConfig(
            venues=venue_configs,
            data=[data_config],
            engine=engine_config,
        )

    # ------------------------------------------------------------------
    # Internal: node execution
    # ------------------------------------------------------------------

    def _run_node(
        self,
        configs: list[BacktestRunConfig],
    ) -> list[BacktestResult]:
        """Build and run the BacktestNode, returning results.

        Parameters
        ----------
        configs : list[BacktestRunConfig]
            One or more run configurations.

        Returns
        -------
        list[BacktestResult]
            Results in the same order as configs.

        Raises
        ------
        BacktestEngineError
            If build/run fails for any config.

        """
        if not configs:
            raise BacktestEngineError("No run configs provided")

        node: BacktestNode | None = None
        results: list[BacktestResult] = []

        try:
            node = BacktestNode(configs=configs)
            node.build()

            # Track which config IDs are known so we don't miss results
            expected_ids: set[str] = set()
            for cfg in configs:
                cid = getattr(cfg, "id", None) or str(cfg)
                expected_ids.add(cid)

            node.run()

            for cfg in configs:
                cid = getattr(cfg, "id", None) or str(cfg)
                try:
                    engine = node.get_engine(cid)
                except (KeyError, ValueError, RuntimeError) as exc:
                    raise BacktestEngineError(
                        f"Failed to get engine for run config {cid!r}: {exc}"
                    ) from exc
                if engine is None:
                    raise BacktestEngineError(
                        f"Backtest engine not found for run config {cid!r}"
                    )
                result: BacktestResult = engine.get_result()
                results.append(result)

        except BacktestEngineError:
            raise
        except Exception as exc:
            raise BacktestEngineError(f"Backtest node failed: {exc}") from exc
        finally:
            if node is not None:
                try:
                    node.dispose()
                except Exception:
                    logger.warning("Error disposing BacktestNode", exc_info=True)

        return results
