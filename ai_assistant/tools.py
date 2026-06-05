import json
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from kraken_kit.formatting import parse_timeframe
from kraken_kit.futures_connector import FuturesConnector


# =============================================================================
# get_ohlcv
# =============================================================================

def get_ohlcv(
    connector: FuturesConnector,
    symbol: str,
    timeframe: str = "1d",
    candles: int = 30,
) -> str:
    """Return the most recent OHLCV candles as a JSON string, oldest to newest.

    The last candle is the current, still-forming interval; all earlier candles
    are closed.

    Args:
        symbol: Futures symbol (e.g. ``PF_XBTUSD``).
        timeframe: Candle interval (``1m``, ``5m``, ``15m``, ``30m``, ``1h``,
            ``4h``, ``12h``, ``1d``, ``1w``).

    Returns:
        JSON array of ``{time, open, high, low, close, volume}`` records.
    """
    _, minutes = parse_timeframe(timeframe)
    start = datetime.now(timezone.utc) - timedelta(minutes=minutes * candles)
    df = connector.get_ohlcv(
        symbol, timeframe=timeframe, start_date=start.strftime("%Y-%m-%d %H:%M:%S")
    )
    if df.empty:
        return json.dumps([])
    recent = df.tail(candles).reset_index()
    recent["time"] = recent["time"].dt.strftime("%Y-%m-%d %H:%M:%S")
    return json.dumps(recent.to_dict(orient="records"))


GET_OHLCV_SCHEMA = {
    "name": "get_ohlcv",
    "description": (
        "Fetch OHLCV candles (open, high, low, close, volume) for a Kraken "
        "Futures perpetual, straight from the exchange. Candles are returned "
        "oldest to newest. The last candle is the current, still-forming "
        "interval: its close is the live price, and its high/low/volume cover "
        "only the elapsed part of the interval. All earlier candles are closed."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "timeframe": {
                "type": "string",
                "description": (
                    "Candle interval. One of 1m, 5m, 15m, 30m, 1h, 4h, 12h, "
                    "1d, 1w. Defaults to 1d if omitted."
                ),
                "default": "1d",
            },
            "candles": {
                "type": "integer",
                "description": "How many candles to return. Defaults to 30.",
                "default": 30,
            },
        },
        "required": [],
    },
}


# =============================================================================
# get_funding_rate
# =============================================================================

def get_funding_rate(
    connector: FuturesConnector,
    symbol: str,
    count: int = 30,
) -> str:
    """Return the most recent daily average funding rates as JSON.

    Kraken publishes funding hourly; rates are averaged per day. The last
    entry is the current day.

    Args:
        symbol: Futures symbol (e.g. ``PF_XBTUSD``).
        count: Number of recent days to return.

    Returns:
        JSON array of ``{time, fundingRate, relativeFundingRate}`` records.
    """
    df = connector.get_funding_rate_history(symbol)
    if df.empty:
        return json.dumps([])
    daily = df.resample("1D").mean().dropna()
    recent = daily.tail(count).reset_index()
    recent["time"] = recent["time"].dt.strftime("%Y-%m-%d")
    return json.dumps(recent.to_dict(orient="records"))


GET_FUNDING_RATE_SCHEMA = {
    "name": "get_funding_rate",
    "description": (
        "Fetch daily average funding rates for a Kraken Futures perpetual; "
        "the last entry is the current day. Positive means longs pay shorts "
        "(bullish positioning); negative means shorts pay longs (bearish "
        "positioning)."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "count": {
                "type": "integer",
                "description": "How many days to return. Defaults to 30.",
                "default": 30,
            },
        },
        "required": [],
    },
}


# =============================================================================
# Registry
# =============================================================================

TOOLS: dict[str, dict[str, Any]] = {
    "get_ohlcv": {"function": get_ohlcv, "schema": GET_OHLCV_SCHEMA},
    "get_funding_rate": {"function": get_funding_rate, "schema": GET_FUNDING_RATE_SCHEMA},
}


def build_tools(
    tools: dict[str, dict[str, Any]],
    connector: FuturesConnector,
    symbol: str,
) -> tuple[list[dict[str, Any]], Callable[[str, dict[str, Any]], str]]:
    """Resolve configured tools to their Claude schemas and a connector/symbol-bound dispatch.

    Each entry maps a tool name to ``default_<parameter>`` values used when Claude
    does not choose that parameter itself.
    """
    schemas = [TOOLS[name]["schema"] for name in tools]

    def dispatch(name: str, arguments: dict[str, Any]) -> str:
        defaults = {key.removeprefix("default_"): value for key, value in tools[name].items()}
        return TOOLS[name]["function"](connector, symbol, **{**defaults, **arguments})

    return schemas, dispatch
