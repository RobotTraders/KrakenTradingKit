from typing import Any

from kraken_kit.futures_connector import FuturesConnector

from ai_assistant.outlook import AIOutlook


CONNECTOR = FuturesConnector


def simple_ai_directional_outlook(
    connector: FuturesConnector,
    symbol: str,
    outlook: AIOutlook,
    *,
    dry_run: bool = False,
    min_confidence: str = "Medium",
    balance_fraction: float = 0.10,
) -> dict[str, Any]:
    """Go long on a Bullish outlook, short on Bearish; otherwise hold no position,
    closing out on Neutral or on a confidence below ``min_confidence``. Holds one
    position at a time, reversing into the opposite side on a flip. Sizes each entry
    at ``balance_fraction`` of account capital. Places the resulting orders unless
    ``dry_run`` is set."""
    confidence_levels = ["Low", "Medium", "High"]

    open_position = connector.get_open_position(symbol)
    position = open_position["side"] if open_position else None
    held_size = float(open_position["size"]) if open_position else 0.0
    close_long = {"side": "sell", "size": held_size, "reduce_only": True}
    close_short = {"side": "buy", "size": held_size, "reduce_only": True}

    if confidence_levels.index(outlook.confidence) < confidence_levels.index(min_confidence):
        if position is None:
            return {"action": "hold", "position": "flat", "orders": []}
        order = close_long if position == "long" else close_short
        if not dry_run:
            connector.place_order(symbol, order["side"], order["size"], reduce_only=order["reduce_only"])
        return {"action": "close", "position": "flat", "orders": [order]}

    price = float(connector.get_ticker(symbol)["markPrice"])
    entry_size = connector.get_account_capital() * balance_fraction / price
    open_long = {"side": "buy", "size": entry_size, "reduce_only": False}
    open_short = {"side": "sell", "size": entry_size, "reduce_only": False}

    match (position, outlook.interpretation):
        case ("long", "Bullish") | ("short", "Bearish") | (None, "Neutral"):
            action, orders = "hold", []
        case (None, "Bullish"):
            action, orders = "open", [open_long]
        case (None, "Bearish"):
            action, orders = "open", [open_short]
        case ("long", "Neutral"):
            action, orders = "close", [close_long]
        case ("short", "Neutral"):
            action, orders = "close", [close_short]
        case ("long", "Bearish"):
            action, orders = "reverse", [close_long, open_short]
        case ("short", "Bullish"):
            action, orders = "reverse", [close_short, open_long]

    if not dry_run:
        for order in orders:
            connector.place_order(symbol, order["side"], order["size"], reduce_only=order["reduce_only"])

    resulting = {"Bullish": "long", "Bearish": "short", "Neutral": "flat"}[outlook.interpretation]
    return {"action": action, "position": resulting, "orders": orders}
