"""Futu adapter configuration dataclasses."""

from __future__ import annotations

from nautilus_trader.live.config import LiveDataClientConfig, LiveExecClientConfig


class FutuDataClientConfig(LiveDataClientConfig, frozen=True):
    """Configuration for ``FutuLiveDataClient`` instances.

    Parameters
    ----------
    host : str
        The Futu OpenD host address (default: 'futu-opend').
    port : int
        The Futu OpenD port (default: 11111).
    trd_env : str
        The trading environment, e.g. 'SIMULATE' or 'REAL' (default: 'SIMULATE').
    trd_market : str
        The trading market code, e.g. 'US', 'HK', 'CN' (default: 'US').
    client_id : int
        The client identifier for this connection (default: 1).

    """

    host: str = "futu-opend"
    port: int = 11111
    trd_env: str = "SIMULATE"
    trd_market: str = "US"
    client_id: int = 1
    load_ids: frozenset | None = None
    keep_alive_interval_secs: int = 1800

    @property
    def client_key(self) -> tuple[str, int, str]:
        """Return the shared client cache key for this config.

        Returns
        -------
        tuple[str, int, str]
            (host, port, trd_env) tuple used to lookup shared Futu contexts.

        """
        return (self.host, self.port, self.trd_env)


class FutuExecClientConfig(LiveExecClientConfig, frozen=True):
    """Configuration for ``FutuLiveExecutionClient`` instances.

    Parameters
    ----------
    host : str
        The Futu OpenD host address (default: 'futu-opend').
    port : int
        The Futu OpenD port (default: 11111).
    trd_env : str
        The trading environment, e.g. 'SIMULATE' or 'REAL' (default: 'SIMULATE').
    trd_market : str
        The trading market code, e.g. 'US', 'HK', 'CN' (default: 'US').
    client_id : int
        The client identifier for this connection (default: 1).
    paper_acc_type : str
        Expected ``sim_acc_type`` for paper trading account discovery
        (e.g., ``"STOCK"`` for HK, ``"STOCK_AND_OPTION"`` for US).
        Used to filter ``get_acc_list()`` results in SIMULATE mode.
    unlock_pwd_md5 : str
        The MD5 hash of the trade unlock password (default: '').
        Required for REAL trading environment.

    """

    host: str = "futu-opend"
    port: int = 11111
    trd_env: str = "SIMULATE"
    trd_market: str = "US"
    client_id: int = 1
    paper_acc_type: str = "STOCK_AND_OPTION"
    unlock_pwd_md5: str = ""

    @property
    def client_key(self) -> tuple[str, int, str]:
        """Return the shared client cache key for this config.

        Returns
        -------
        tuple[str, int, str]
            (host, port, trd_env) tuple used to lookup shared Futu contexts.

        """
        return (self.host, self.port, self.trd_env)
