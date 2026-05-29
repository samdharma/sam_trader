"""MarketConfig — frozen dataclass for per-market configuration.

Loaded from config/market_config.yaml at startup by SamTraderConfig.from_env().
Each market entry defines timezone, trading hours, routing venues, and pipeline timings.

See: docs/user/DYNAMIC_MULTI_MARKET_PLAN.md §3.2
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

# Regex for HH:MM time format validation (empty string allowed for lunch fields)
_HHMM_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


@dataclass(frozen=True)
class MarketConfig:
    """Per-market configuration loaded from market_config.yaml.

    Each market (US, HK) has its own entry with timezone-aware session hours,
    venue routing, broker enablement, and pipeline timing parameters.

    Attributes
    ----------
    futu_trd_market : str
        Futu trade market identifier (e.g., "US", "HK").
    default_time_in_force : str
        Default order time-in-force for this market. One of "DAY", "GTC",
        "IOC".  Overridable per-strategy via bundles.yaml or per-deployment
        via DEFAULT_TIME_IN_FORCE env var.  Defaults to "DAY" because Futu
        SIMULATE (paper trading) rejects GTC orders.
    futu_paper_acc_type : str
        Expected ``sim_acc_type`` for paper trading account discovery
        (e.g., ``"STOCK"`` for HK, ``"STOCK_AND_OPTION"`` for US).
    futu_routing_venues : list[str]
        Exchange venues for routing (e.g., ["NASDAQ", "NYSE"] for US).
    ib_enabled : bool
        Whether IBKR is enabled for this market.
    session_timezone : str
        IANA timezone name (e.g., "America/New_York").
    session_open : str
        Market open time in HH:MM format (market local time).
    session_close : str
        Market close time in HH:MM format (market local time).
    lunch_start : str
        Lunch break start in HH:MM, or empty string if no lunch break.
    lunch_end : str
        Lunch break end in HH:MM, or empty string if no lunch break.
    premarket_pipeline_time : str
        When the pre-market pipeline runs, HH:MM format (market local time).
    sod_readiness_time : str
        When the start-of-day readiness check runs, HH:MM format.
    eod_report_time : str
        When the end-of-day report is generated, HH:MM format.

    Raises
    ------
    ValueError
        If any time field is not in valid HH:MM format (empty string allowed
        for lunch_start and lunch_end).

    """

    futu_trd_market: str
    default_time_in_force: str = "DAY"
    futu_paper_acc_type: str = "STOCK_AND_OPTION"
    futu_routing_venues: list[str] = field(default_factory=list)
    ib_enabled: bool = False
    session_timezone: str = "America/New_York"
    session_open: str = "09:30"
    session_close: str = "16:00"
    lunch_start: str = ""
    lunch_end: str = ""
    premarket_pipeline_time: str = "08:30"
    sod_readiness_time: str = "08:00"
    eod_report_time: str = "16:05"

    def __post_init__(self) -> None:
        """Validate all time fields are in HH:MM format or empty (lunch fields only)."""
        time_fields = {
            "session_open": self.session_open,
            "session_close": self.session_close,
            "lunch_start": self.lunch_start,
            "lunch_end": self.lunch_end,
            "premarket_pipeline_time": self.premarket_pipeline_time,
            "sod_readiness_time": self.sod_readiness_time,
            "eod_report_time": self.eod_report_time,
        }

        for field_name, value in time_fields.items():
            if _HHMM_RE.match(value):
                continue
            if field_name in ("lunch_start", "lunch_end") and value == "":
                continue
            raise ValueError(
                f"Invalid {field_name}='{value}' — must be HH:MM format "
                f"(or empty string for lunch_start/lunch_end)"
            )

    @classmethod
    def from_yaml(cls, path: str | Path) -> dict[str, MarketConfig]:
        """Load per-market configurations from a YAML file.

        Parameters
        ----------
        path : str or Path
            Path to the market_config.yaml file.

        Returns
        -------
        dict[str, MarketConfig]
            Dict mapping market names (e.g., "US", "HK") to MarketConfig.

        Raises
        ------
        FileNotFoundError
            If the YAML file does not exist.
        ValueError
            If the YAML is malformed or missing the 'markets' key.
        TypeError
            If any market entry has unexpected field types.

        """
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Market config file not found: {path}")

        with open(path, "r") as f:
            raw = yaml.safe_load(f)

        if not isinstance(raw, dict) or "markets" not in raw:
            raise ValueError(
                f"Invalid market config: expected top-level 'markets' key in {path}"
            )

        markets_raw = raw["markets"]
        if not isinstance(markets_raw, dict):
            raise ValueError(
                f"Invalid market config: 'markets' must be a dict, "
                f"got {type(markets_raw).__name__}"
            )

        result: dict[str, MarketConfig] = {}
        for market_name, entry in markets_raw.items():
            if not isinstance(entry, dict):
                raise TypeError(
                    f"Market '{market_name}' entry must be a dict, "
                    f"got {type(entry).__name__}"
                )
            result[market_name] = cls(**entry)

        return result

    @classmethod
    def get_market(
        cls, market: str, path: str | Path = "config/market_config.yaml"
    ) -> MarketConfig:
        """Load config and return the entry for a specific market.

        Parameters
        ----------
        market : str
            Market identifier (e.g., "US", "HK").
        path : str or Path
            Path to the market_config.yaml file.

        Returns
        -------
        MarketConfig
            Configuration for the requested market.

        Raises
        ------
        ValueError
            If the requested market is not found in the config.

        """
        all_markets = cls.from_yaml(path)
        if market not in all_markets:
            available = ", ".join(sorted(all_markets.keys()))
            raise ValueError(f"Unknown market '{market}'. Available: {available}")
        return all_markets[market]
