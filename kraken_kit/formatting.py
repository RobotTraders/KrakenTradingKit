import math

import pandas as pd

TIMEFRAMES: dict[str, int] = {
    "1m": 1,
    "5m": 5,
    "15m": 15,
    "30m": 30,
    "1h": 60,
    "4h": 240,
    "12h": 720,
    "1d": 1440,
    "1w": 10080,
    "1M": 21600,
}


def parse_timeframe(timeframe: str) -> tuple[str, int]:
    """Validate a timeframe string and return (timeframe, minutes).

    Valid timeframes: ``1m``, ``5m``, ``15m``, ``30m``, ``1h``, ``4h``,
    ``12h``, ``1d``, ``1w``, ``1M``.

    Not all timeframes are supported by all endpoints — spot OHLC does
    not support ``12h``, futures charts does not support ``1M``.
    """
    if timeframe not in TIMEFRAMES:
        valid = ", ".join(TIMEFRAMES)
        raise ValueError(
            f"Invalid timeframe {timeframe!r}. Valid: {valid}"
        )
    return timeframe, TIMEFRAMES[timeframe]


def parse_date(value: str | int) -> int:
    """Convert a date string or UNIX timestamp to UNIX seconds.

    Accepts ``"2025-01-01"``, ``"2025-01-01 12:00:00"``, or an integer timestamp.
    Raises ``ValueError`` on unparseable strings.
    """
    if isinstance(value, int):
        return value
    try:
        return int(pd.Timestamp(value).timestamp())
    except Exception:
        raise ValueError(
            f"Invalid date {value!r}. Use 'YYYY-MM-DD' or a UNIX timestamp."
        ) from None


def truncate_qty(qty: float, decimals: int) -> float:
    """Truncate quantity to the allowed number of decimal places.

    Always truncates (floors) rather than rounding — placing more than
    you have is worse than placing slightly less.
    """
    factor = 10**decimals
    return math.floor(qty * factor) / factor


def format_price(price: float, tick_size: float) -> float:
    """Round price to the nearest valid tick."""
    return round(round(price / tick_size) * tick_size, 10)
