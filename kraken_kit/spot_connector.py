from typing import Any, Self

import pandas as pd

from .formatting import format_price, parse_timeframe, truncate_qty
from .spot_client import SpotClient


class SpotConnector:
    """Connector for the Kraken Spot REST API.

    Two construction modes:
        - ``SpotConnector(api_key, api_secret)`` — authenticated.
        - ``SpotConnector.public()`` — public endpoints only.

    https://docs.kraken.com/api/docs/guides/spot-rest-auth/
    """

    def __init__(self, api_key: str, api_secret: str, tier: str = "starter") -> None:
        """
        Args:
            api_key: Kraken API key.
            api_secret: Kraken API secret (base64-encoded).
            tier: Verification tier for rate limits — ``"starter"``,
                ``"intermediate"``, or ``"pro"``.
        """
        self._client = SpotClient(api_key, api_secret, tier)
        self._symbol_info_cache: dict[str, dict[str, Any]] = {}

    @classmethod
    def public(cls) -> Self:
        """Create an unauthenticated connector for public endpoints only."""
        instance = object.__new__(cls)
        instance._client = SpotClient.public()
        instance._symbol_info_cache = {}
        return instance

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def amend_order(self, txid: str, **kwargs: Any) -> str:
        """Modify an order in-place, preserving queue priority.

        Cannot amend orders with conditional close.

        Args:
            txid: Transaction ID.
            **kwargs: ``order_qty``, ``display_qty``, ``limit_price``,
                ``trigger_price``, ``post_only``, ``deadline``.

        Returns:
            Amend ID.
        """
        data: dict[str, Any] = {"txid": txid}
        for key in (
            "order_qty", "display_qty", "limit_price",
            "trigger_price", "post_only", "deadline",
        ):
            if key in kwargs:
                val = kwargs[key]
                if isinstance(val, bool):
                    data[key] = str(val).lower()
                else:
                    data[key] = str(val)

        result = self._client.private_request("AmendOrder", data)
        return result.get("amend_id", "")

    def cancel_all(self) -> dict[str, Any]:
        """Cancel all open orders."""
        return self._client.private_request("CancelAll")

    def cancel_all_after(self, timeout_seconds: int) -> dict[str, Any]:
        """Dead man's switch — cancel all orders after timeout.

        Send periodically (every 15–30 s) with a 60 s timeout to keep orders
        alive. Pass ``0`` to deactivate.

        Args:
            timeout_seconds: Seconds until cancellation (``0`` to disable).
        """
        return self._client.private_request(
            "CancelAllOrdersAfter", {"timeout": timeout_seconds}
        )

    def cancel_order(self, txid_or_cl_ord_id: str) -> dict[str, Any]:
        """Cancel a single open order.

        Args:
            txid_or_cl_ord_id: Transaction ID or client order ID.
        """
        return self._client.private_request("CancelOrder", {"txid": txid_or_cl_ord_id})

    def cancel_order_batch(self, ids: list[str]) -> dict[str, Any]:
        """Cancel up to 50 orders.

        Args:
            ids: Transaction IDs, client order IDs, or user-ref IDs.
        """
        if len(ids) > 50:
            raise ValueError("Maximum 50 orders per batch cancel")
        self._client.acquire_private(1)
        data: dict[str, Any] = {"orders": [{"txid": id_} for id_ in ids]}
        return self._client.private_request_json("CancelOrderBatch", data)

    def close(self) -> None:
        """Close the underlying HTTP session."""
        self._client.close()

    def edit_order(
        self,
        txid: str,
        symbol: str,
        *,
        limit_price: float | None = None,
        trigger_price: float | None = None,
        volume: float | None = None,
        oflags: str | None = None,
        deadline: str | None = None,
        cancel_response: bool | None = None,
        newuserref: str | None = None,
    ) -> str:
        """Cancel-and-replace an existing order. Returns a new txid.

        Cannot edit orders with conditional close.

        Args:
            txid: Transaction ID of the order to edit.
            symbol: Trading symbol (required by Kraken).
            limit_price: New limit price.
            trigger_price: New trigger/stop price (for stop-loss-limit,
                take-profit-limit order types).
            volume: New order volume.
            oflags: Order flags.
            deadline: Deadline for the order.
            cancel_response: Whether to include cancel info in response.
            newuserref: New user reference ID.

        Returns:
            New transaction ID.
        """
        self._client.acquire_trading(symbol, 7)

        data: dict[str, Any] = {"txid": txid, "pair": symbol}
        if limit_price is not None:
            data["price"] = self.format_price(symbol, limit_price)
        if trigger_price is not None:
            data["price2"] = self.format_price(symbol, trigger_price)
        if volume is not None:
            data["volume"] = self.format_qty(symbol, volume)
        if oflags is not None:
            data["oflags"] = oflags
        if deadline is not None:
            data["deadline"] = deadline
        if cancel_response is not None:
            data["cancel_response"] = str(cancel_response).lower()
        if newuserref is not None:
            data["newuserref"] = newuserref

        result = self._client.private_request("EditOrder", data)
        return result.get("txid", "")

    def get_balance(self) -> dict[str, Any]:
        """Fetch account balances."""
        self._client.acquire_private(1)
        return self._client.private_request("Balance")

    def get_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1h",
    ) -> pd.DataFrame:
        """Fetch the latest OHLCV candles as a DataFrame.

        Kraken spot returns up to 720 of the most recent candles.
        Historical data beyond that window is not available through
        this endpoint.

        Args:
            symbol: Trading symbol (e.g. ``"XBTUSDC"``).
            timeframe: Candle interval — ``1m``, ``5m``, ``15m``, ``30m``,
                ``1h``, ``4h``, ``1d``, ``1w``, ``1M``.
        """
        _, interval = parse_timeframe(timeframe)
        params: dict[str, Any] = {"pair": symbol, "interval": interval}
        result = self._client.public_request("OHLC", params)
        candles = _first_value(result, exclude={"last"})
        if not candles:
            return pd.DataFrame(
                columns=["open", "high", "low", "close", "vwap", "volume", "count"]
            )
        df = pd.DataFrame(
            candles,
            columns=["time", "open", "high", "low", "close", "vwap", "volume", "count"],
        )
        df["time"] = pd.to_datetime(df["time"], unit="s")
        for col in ("open", "high", "low", "close", "vwap", "volume"):
            df[col] = pd.to_numeric(df[col])
        df["count"] = df["count"].astype(int)
        return df.set_index("time")

    def get_order_book(self, symbol: str, depth: int = 10) -> dict[str, Any]:
        """Fetch order book depth.

        Args:
            symbol: Trading symbol (e.g. ``"XBTUSDC"``).
            depth: Number of ask/bid levels (max 500).
        """
        result = self._client.public_request("Depth", {"pair": symbol, "count": depth})
        return _first_value(result)

    def get_spreads(self, symbol: str) -> list[list]:
        """Fetch recent spread data (bid/ask history).

        Args:
            symbol: Trading symbol (e.g. ``"XBTUSDC"``).
        """
        result = self._client.public_request("Spread", {"pair": symbol})
        return _first_value(result, exclude={"last"})

    def get_recent_trades(self, symbol: str, count: int = 20) -> pd.DataFrame:
        """Fetch recent trades.

        Args:
            symbol: Trading symbol (e.g. ``"XBTUSDC"``).
            count: Number of trades to return (max 1000).
        """
        result = self._client.public_request("Trades", {"pair": symbol, "count": count})
        trades = _first_value(result, exclude={"last"})
        df = pd.DataFrame(
            trades,
            columns=["price", "volume", "time", "side", "type", "misc", "trade_id"],
        )
        df["time"] = pd.to_datetime(df["time"], unit="s")
        df["price"] = pd.to_numeric(df["price"])
        df["volume"] = pd.to_numeric(df["volume"])
        df["side"] = df["side"].map({"b": "buy", "s": "sell"})
        df["type"] = df["type"].map({"l": "limit", "m": "market"})
        return df.set_index("time")

    def get_symbol_info(self, symbol: str) -> dict[str, Any]:
        """Fetch trading requirements for a symbol.

        Args:
            symbol: Trading symbol (e.g. ``"XBTUSD"``).

        Returns:
            Dict with keys: ``qty_decimals``, ``price_decimals``,
            ``tick_size``, ``order_min``, ``cost_min``.
        """
        return self._get_symbol_info(symbol)

    def _get_symbol_info(self, symbol: str) -> dict[str, Any]:
        if symbol not in self._symbol_info_cache:
            result = self._client.public_request("AssetPairs", {"pair": symbol})
            raw = _first_value(result)
            self._symbol_info_cache[symbol] = {
                "qty_decimals": int(raw["lot_decimals"]),
                "price_decimals": int(raw["pair_decimals"]),
                "tick_size": float(raw["tick_size"]),
                "order_min": float(raw["ordermin"]),
                "cost_min": float(raw["costmin"]),
            }
        return self._symbol_info_cache[symbol]

    def format_qty(self, symbol: str, qty: float) -> str:
        """Truncate ``qty`` to the pair's allowed precision.

        Args:
            symbol: Spot pair (e.g. ``"XBTUSDC"``).
            qty: Raw quantity to format.

        Returns:
            Quantity as a string at the pair's ``qty_decimals``.
        """
        info = self._get_symbol_info(symbol)
        return str(truncate_qty(qty, info["qty_decimals"]))

    def format_price(self, symbol: str, price: float) -> str:
        """Round ``price`` to the pair's tick size.

        Args:
            symbol: Spot pair (e.g. ``"XBTUSDC"``).
            price: Raw price.

        Returns:
            Price as a string aligned to the pair's ``tick_size``.
        """
        info = self._get_symbol_info(symbol)
        return format_price(price, info["tick_size"])

    def get_asset_pairs(self, pair: str | None = None) -> dict[str, Any]:
        """Fetch available trading pairs.

        Args:
            pair: Optional comma-separated pair filter (e.g. ``"XBTUSDC,ETHUSDC"``).
                  If ``None``, returns all pairs.
        """
        params = {"pair": pair} if pair else {}
        return self._client.public_request("AssetPairs", params)

    def get_server_time(self) -> dict[str, Any]:
        """Fetch the server time."""
        return self._client.public_request("Time")

    def get_ticker(self, symbol: str) -> dict[str, Any]:
        """Fetch ticker data with human-readable keys.

        Args:
            symbol: Trading symbol (e.g. ``"XBTUSD"``).

        Returns:
            Dict with keys: ``ask``, ``bid``, ``last``, ``volume_today``,
            ``volume_24h``, ``vwap_today``, ``vwap_24h``, ``trades_today``,
            ``trades_24h``, ``low_today``, ``low_24h``, ``high_today``,
            ``high_24h``, ``open``, ``mid``.
        """
        raw = self.get_ticker_data(symbol)
        ask = float(raw["a"][0])
        bid = float(raw["b"][0])
        return {
            "ask": ask,
            "bid": bid,
            "mid": (ask + bid) / 2,
            "last": float(raw["c"][0]),
            "volume_today": float(raw["v"][0]),
            "volume_24h": float(raw["v"][1]),
            "vwap_today": float(raw["p"][0]),
            "vwap_24h": float(raw["p"][1]),
            "trades_today": int(raw["t"][0]),
            "trades_24h": int(raw["t"][1]),
            "low_today": float(raw["l"][0]),
            "low_24h": float(raw["l"][1]),
            "high_today": float(raw["h"][0]),
            "high_24h": float(raw["h"][1]),
            "open": float(raw["o"]),
        }

    def get_ticker_data(self, symbol: str) -> dict[str, Any]:
        """Fetch raw ticker data as returned by the Kraken API.

        Args:
            symbol: Trading symbol (e.g. ``"XBTUSD"``).
        """
        result = self._client.public_request("Ticker", {"pair": symbol})
        return _first_value(result)

    def place_order(
        self,
        symbol: str,
        side: str,
        volume: float,
        price: str | float | None = None,
        *,
        ordertype: str | None = None,
        trigger_price: str | float | None = None,
        time_in_force: str = "GTC",
        cl_ord_id: str | None = None,
        leverage: str | None = None,
        validate: bool = False,
        **kwargs: Any,
    ) -> str:
        """Place a new order.

        If *price* is given the order defaults to ``limit``; otherwise ``market``.

        Args:
            symbol: Trading symbol.
            side: ``"buy"`` or ``"sell"``.
            volume: Order volume.
            price: Limit price (``None`` → market order).
            ordertype: Override the order type (``market``, ``limit``,
                ``stop-loss``, ``take-profit``, ``trailing-stop``, etc.).
            trigger_price: Secondary price for compound order types
                (e.g. limit price for ``stop-loss-limit``).
            time_in_force: ``GTC``, ``IOC``, or ``GTD``.
            cl_ord_id: Client order ID.
            leverage: Leverage amount (e.g. ``"2:1"``).
            validate: Validate only, do not submit.
            **kwargs: Extra Kraken params — ``oflags``, ``starttm``,
                ``expiretm``, ``userref``, ``displayvol``, ``trigger``,
                ``deadline``.

        Returns:
            Transaction ID (or description string when *validate* is ``True``).
        """
        self._client.acquire_trading(symbol, 1)

        if ordertype is None:
            ordertype = "limit" if price is not None else "market"

        data: dict[str, Any] = {
            "pair": symbol,
            "type": side,
            "ordertype": ordertype,
            "volume": self.format_qty(symbol, volume),
        }
        if price is not None:
            data["price"] = (
                str(price) if isinstance(price, str)
                else self.format_price(symbol, price)
            )
        if trigger_price is not None:
            data["price2"] = (
                str(trigger_price) if isinstance(trigger_price, str)
                else self.format_price(symbol, trigger_price)
            )
        if ordertype != "market":
            data["timeinforce"] = time_in_force
        if cl_ord_id is not None:
            data["cl_ord_id"] = cl_ord_id
        if leverage is not None:
            data["leverage"] = leverage
        if validate:
            data["validate"] = "true"

        for key in (
            "oflags", "starttm", "expiretm",
            "userref", "displayvol", "trigger", "deadline",
        ):
            if key in kwargs:
                data[key] = str(kwargs[key])

        result = self._client.private_request("AddOrder", data)
        if validate:
            return result.get("descr", {}).get("order", "validated")
        return result["txid"][0]

    def place_order_batch(
        self, orders: list[dict[str, Any]], validate: bool = False
    ) -> dict[str, Any]:
        """Place a batch of 2–15 orders for a single symbol.

        All orders must share the same symbol. Orders are independent — no OCO,
        no linking. If one order fails post-submission, others still execute.

        Args:
            orders: Order dicts with keys ``ordertype``, ``type``, ``volume``,
                ``price``, etc. The ``symbol`` from the first order is used.
            validate: Validate only, do not submit.

        Returns:
            Batch result with per-order statuses.
        """
        if len(orders) < 2 or len(orders) > 15:
            raise ValueError("Batch requires 2–15 orders")

        symbol = orders[0].get("symbol", "")
        self._client.acquire_trading(symbol, len(orders) * 2)

        clean = [{k: v for k, v in o.items() if k != "symbol"} for o in orders]
        data: dict[str, Any] = {"pair": symbol, "orders": clean}
        if validate:
            data["validate"] = True
        return self._client.private_request_json("AddOrderBatch", data)

    def place_order_with_close(
        self,
        symbol: str,
        side: str,
        volume: float,
        close_type: str,
        close_price: str | float,
        price: float | None = None,
        close_limit_price: str | float | None = None,
        **kwargs: Any,
    ) -> str:
        """Place an order with a conditional close (TP or SL).

        Only one conditional close per order. The close volume and direction
        are automatically the opposite of the primary order.

        Args:
            symbol: Trading symbol.
            side: ``"buy"`` or ``"sell"``.
            volume: Order volume.
            close_type: Close order type — ``limit``, ``stop-loss``,
                ``stop-loss-limit``, ``take-profit``, ``take-profit-limit``,
                ``trailing-stop``, or ``trailing-stop-limit``.
            close_price: Close trigger price. Supports relative offsets
                (``"-5%"``, ``"#10"``).
            price: Limit price for the primary order (``None`` → market).
            close_limit_price: Limit price for compound close types
                (e.g. ``stop-loss-limit``, ``take-profit-limit``).
            **kwargs: ``ordertype``, ``time_in_force``, ``cl_ord_id``,
                ``leverage``, ``validate``, ``oflags``, ``trigger``,
                ``deadline``.

        Returns:
            Transaction ID (or description string when *validate* is ``True``).
        """
        self._client.acquire_trading(symbol, 1)

        ordertype = kwargs.pop("ordertype", None)
        if ordertype is None:
            ordertype = "limit" if price is not None else "market"

        close_price_str = (
            str(close_price) if isinstance(close_price, str)
            else self.format_price(symbol, close_price)
        )
        data: dict[str, Any] = {
            "pair": symbol,
            "type": side,
            "ordertype": ordertype,
            "volume": self.format_qty(symbol, volume),
            "close[ordertype]": close_type,
            "close[price]": close_price_str,
        }
        if price is not None:
            data["price"] = self.format_price(symbol, price)
        if close_limit_price is not None:
            close_limit_price_str = (
                str(close_limit_price) if isinstance(close_limit_price, str)
                else self.format_price(symbol, close_limit_price)
            )
            data["close[price2]"] = close_limit_price_str
        if ordertype != "market":
            data["timeinforce"] = kwargs.pop("time_in_force", "GTC")

        validate = kwargs.pop("validate", False)
        if validate:
            data["validate"] = "true"

        for key in ("cl_ord_id", "leverage", "oflags", "trigger", "deadline"):
            if key in kwargs:
                data[key] = str(kwargs[key])

        result = self._client.private_request("AddOrder", data)
        if validate:
            return result.get("descr", {}).get("order", "validated")
        return result["txid"][0]


def _first_value(result: dict[str, Any], exclude: set[str] | None = None) -> Any:
    exclude = exclude or set()
    for key, value in result.items():
        if key not in exclude:
            return value
    return result
