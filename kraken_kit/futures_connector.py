import json
import re
import time
from typing import Any, Self

import pandas as pd

from .exceptions import APIError
from .formatting import format_price, parse_date, parse_timeframe, truncate_qty
from .futures_client import FuturesClient, _handle_response

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I
)

_SEND_ORDER_SUCCESS = "placed"
_EDIT_ORDER_SUCCESS = "edited"

_CHARTS_MAX_CANDLES = 2000


def _require_send_success(result: dict[str, Any]) -> str:
    """Extract order_id from a sendorder response, raising on rejection."""
    send_status = result.get("sendStatus", {})
    status = send_status.get("status", "")
    if status != _SEND_ORDER_SUCCESS:
        reason = send_status.get("reason", status)
        raise APIError(f"Order rejected: {reason}")
    order_id = send_status.get("order_id", "")
    if not order_id:
        raise APIError("Order placed but no order_id returned")
    return order_id


def _require_edit_success(result: dict[str, Any]) -> dict[str, Any]:
    """Validate an editorder response, raising on rejection."""
    edit_status = result.get("editStatus", {})
    status = edit_status.get("status", "")
    if status != _EDIT_ORDER_SUCCESS:
        reason = edit_status.get("reason", status)
        raise APIError(f"Edit rejected: {reason}")
    return result


def _require_batch_success(result: dict[str, Any]) -> dict[str, Any]:
    """Validate a batchorder response, raising on any rejected operation."""
    batch_status = result.get("batchStatus", [])
    failures: list[str] = []
    for i, entry in enumerate(batch_status):
        # Send operations have sendStatus, edit operations have editStatus
        send = entry.get("sendStatus", {})
        edit = entry.get("editStatus", {})
        if send:
            status = send.get("status", "")
            if status != _SEND_ORDER_SUCCESS:
                reason = send.get("reason", status)
                failures.append(f"order {i}: {reason}")
        if edit:
            status = edit.get("status", "")
            if status != _EDIT_ORDER_SUCCESS:
                reason = edit.get("reason", status)
                failures.append(f"order {i}: {reason}")
    if failures:
        raise APIError(f"Batch rejected: {'; '.join(failures)}")
    return result


class FuturesConnector:
    """Connector for the Kraken Futures REST API.

    Two construction modes:
        - ``FuturesConnector(api_key, api_secret)`` — authenticated.
        - ``FuturesConnector.public()`` — public endpoints only.

    Futures API keys are separate from spot keys and are generated at
    ``futures.kraken.com``.

    https://docs.kraken.com/api/docs/guides/futures-rest/
    """

    TICKER_CACHE_TTL = 5.0

    def __init__(self, api_key: str, api_secret: str, *, demo: bool = False) -> None:
        """
        Args:
            api_key: Kraken Futures API key.
            api_secret: Kraken Futures API secret (base64-encoded).
            demo: Route trading to the demo platform (``demo-futures.kraken.com``)
                instead of live.
        """
        self._client = FuturesClient(api_key, api_secret, demo=demo)
        self._tickers_cache: list[dict[str, Any]] = []
        self._tickers_cache_time: float = 0.0
        self._instrument_cache: dict[str, dict[str, Any]] = {}

    @classmethod
    def public(cls, *, demo: bool = False) -> Self:
        """Create an unauthenticated connector for public endpoints only."""
        instance = object.__new__(cls)
        instance._client = FuturesClient.public(demo=demo)
        instance._tickers_cache = []
        instance._tickers_cache_time = 0.0
        instance._instrument_cache = {}
        return instance

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def _get_instrument_info(self, symbol: str) -> dict[str, Any]:
        if symbol not in self._instrument_cache:
            instruments = self.get_instruments()
            for inst in instruments:
                sym = inst.get("symbol", "")
                self._instrument_cache[sym] = {
                    "qty_decimals": int(inst.get("contractValueTradePrecision", 8)),
                    "tick_size": float(inst.get("tickSize", 0.01)),
                }
            if symbol not in self._instrument_cache:
                raise APIError(f"Instrument not found: {symbol}")
        return self._instrument_cache[symbol]

    def format_qty(self, symbol: str, qty: float) -> str:
        """Truncate ``qty`` to the instrument's allowed precision.

        Args:
            symbol: Futures symbol (e.g. ``"PF_XBTUSD"``).
            qty: Raw quantity to format.

        Returns:
            Quantity as a string at the instrument's ``qty_decimals``.
        """
        info = self._get_instrument_info(symbol)
        return str(truncate_qty(qty, info["qty_decimals"]))

    def format_price(self, symbol: str, price: float | str) -> str:
        """Round ``price`` to the instrument's tick size.

        Args:
            symbol: Futures symbol (e.g. ``"PF_XBTUSD"``).
            price: Raw price; pre-formatted strings are returned as-is.

        Returns:
            Price as a string aligned to the instrument's ``tick_size``.
        """
        if isinstance(price, str):
            return price
        info = self._get_instrument_info(symbol)
        return format_price(price, info["tick_size"])

    def cancel_all(self, symbol: str | None = None) -> dict[str, Any]:
        """Cancel all open orders, optionally filtered by symbol.

        Args:
            symbol: Cancel only orders for this symbol.
        """
        data: dict[str, Any] = {}
        if symbol:
            data["symbol"] = symbol
        return self._client.authenticated_request("POST", "cancelallorders", data)

    def cancel_all_after(self, timeout_seconds: int) -> dict[str, Any]:
        """Dead man's switch — cancel all orders after timeout.

        Recommended: call every 15–20 s with a 60 s timeout.
        Pass ``0`` to deactivate.

        Args:
            timeout_seconds: Seconds until cancellation (``0`` to disable).
        """
        return self._client.authenticated_request(
            "POST", "cancelallordersafter", {"timeout": timeout_seconds}
        )

    def cancel_order(self, order_id_or_cl_ord_id: str) -> dict[str, Any]:
        """Cancel a single open order.

        Args:
            order_id_or_cl_ord_id: Order ID (UUID) or client order ID.
        """
        if _UUID_RE.match(order_id_or_cl_ord_id):
            data: dict[str, Any] = {"order_id": order_id_or_cl_ord_id}
        else:
            data = {"cliOrdId": order_id_or_cl_ord_id}
        return self._client.authenticated_request("POST", "cancelorder", data)

    def close(self) -> None:
        """Close the underlying HTTP session."""
        self._client.close()

    def edit_order(
        self, order_id: str, *, symbol: str | None = None, **kwargs: Any
    ) -> dict[str, Any]:
        """Modify an existing order.

        Args:
            order_id: Order ID to edit.
            symbol: Futures symbol (required when ``size``, ``limit_price``,
                or ``stop_price`` are given, so values can be formatted).
            **kwargs: ``size``, ``limit_price``, ``stop_price``,
                ``trailing_stop_deviation_unit``,
                ``trailing_stop_max_deviation``, ``cl_ord_id``.

        Returns:
            Edit result from the API.
        """
        self._client.acquire(1)
        data: dict[str, Any] = {"orderId": order_id}

        needs_fmt = {"size", "limit_price", "stop_price"}
        if needs_fmt & kwargs.keys() and symbol is None:
            raise ValueError(
                "symbol is required when editing size, limit_price, or stop_price"
            )

        if "size" in kwargs:
            data["size"] = self.format_qty(symbol, kwargs["size"])  # type: ignore[arg-type]
        if "limit_price" in kwargs:
            data["limitPrice"] = self.format_price(symbol, kwargs["limit_price"])  # type: ignore[arg-type]
        if "stop_price" in kwargs:
            data["stopPrice"] = self.format_price(symbol, kwargs["stop_price"])  # type: ignore[arg-type]

        passthrough = {
            "trailing_stop_deviation_unit": "trailingStopDeviationUnit",
            "trailing_stop_max_deviation": "trailingStopMaxDeviation",
            "cl_ord_id": "cliOrdId",
        }
        for py_key, api_key in passthrough.items():
            if py_key in kwargs:
                data[api_key] = kwargs[py_key]

        result = self._client.authenticated_request("POST", "editorder", data)
        return _require_edit_success(result)

    def get_accounts(self) -> dict[str, Any]:
        """Fetch account balances and margin info."""
        self._client.acquire(1)
        return self._client.authenticated_request("GET", "accounts")

    def get_balance(self, currency: str = "USDC") -> dict[str, Any]:
        """Fetch balance for a currency from the multi-collateral account."""
        response = self.get_accounts()
        accounts = response.get("accounts", response)
        flex = accounts.get("flex", {})
        return flex.get("currencies", {}).get(currency, {})

    def get_account_capital(self) -> float:
        """Fetch total account capital in USD, excluding unrealized PnL."""
        response = self.get_accounts()
        accounts = response.get("accounts", response)
        return float(accounts["flex"]["balanceValue"])

    def get_funding_rate(self, symbol: str) -> dict[str, Any]:
        """Fetch the current funding rate and the next predicted rate.

        Args:
            symbol: Futures symbol (e.g. ``"PF_XBTUSD"``).

        Returns:
            Dict with ``current`` and ``predicted`` floats. Positive means
            longs pay shorts (perp trading above spot). Negative means
            shorts pay longs (perp trading below spot).
        """
        ticker = self.get_ticker(symbol)
        return {
            "current": float(ticker.get("fundingRate", 0)),
            "predicted": float(ticker.get("fundingRatePrediction", 0)),
        }

    def get_funding_rate_history(
        self,
        symbol: str,
        start_date: str | int | None = None,
        end_date: str | int | None = None,
    ) -> pd.DataFrame:
        """Fetch historical funding rates as a DataFrame.

        Args:
            symbol: Futures symbol (e.g. ``"PF_XBTUSD"``).
            start_date: Start date. Accepts ``"2025-01-01"`` or UNIX timestamp.
            end_date: End date. Accepts ``"2025-06-01"`` or UNIX timestamp.
                If ``None``, returns data up to now.

        Returns:
            DataFrame with columns ``fundingRate`` and ``relativeFundingRate``,
            indexed by a ``time`` DatetimeIndex sorted ascending.
        """
        url = "https://futures.kraken.com/derivatives/api/v4/historicalfundingrates"
        resp = self._client.raw_get(url, params={"symbol": symbol})
        result = _handle_response(resp)
        rates = result.get("rates", [])

        empty_columns = ["fundingRate", "relativeFundingRate"]
        if not rates:
            empty = pd.DataFrame(columns=empty_columns)
            empty.index = pd.DatetimeIndex([], name="time")
            return empty

        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["timestamp"])
        for col in ("fundingRate", "relativeFundingRate"):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col])
        df = df.set_index("time").sort_index()
        df = df[[c for c in empty_columns if c in df.columns]]

        if start_date is not None:
            start_ts = pd.to_datetime(parse_date(start_date), unit="s", utc=True)
            if df.index.tz is None:
                start_ts = start_ts.tz_localize(None)
            df = df[df.index >= start_ts]
        if end_date is not None:
            end_ts = pd.to_datetime(parse_date(end_date), unit="s", utc=True)
            if df.index.tz is None:
                end_ts = end_ts.tz_localize(None)
            df = df[df.index <= end_ts]

        return df

    def get_open_interest(self, symbol: str) -> float:
        """Fetch the current open interest.

        Args:
            symbol: Futures symbol (e.g. ``"PF_XBTUSD"``).

        Returns:
            Open interest as a float. Rising open interest on a price move
            signals new positions opening; falling open interest signals
            existing positions closing.
        """
        ticker = self.get_ticker(symbol)
        return float(ticker.get("openInterest", 0))

    def get_instrument_info(self, symbol: str) -> dict[str, Any]:
        """Fetch trading precision for a futures instrument.

        Args:
            symbol: Futures symbol (e.g. ``"PF_XBTUSD"``).

        Returns:
            Dict with keys: ``qty_decimals``, ``tick_size``.
        """
        return self._get_instrument_info(symbol)

    def get_instruments(self) -> list[dict[str, Any]]:
        """Fetch all available futures contracts."""
        result = self._client.public_get("instruments")
        return result.get("instruments", [])

    def get_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1h",
        start_date: str | int | None = None,
        end_date: str | int | None = None,
    ) -> pd.DataFrame:
        """Fetch OHLCV candles as a DataFrame.

        Automatically paginates to fetch the full requested range.

        Args:
            symbol: Futures symbol (e.g. ``"PF_XBTUSD"``).
            timeframe: Candle interval — ``1m``, ``5m``, ``15m``, ``30m``,
                ``1h``, ``4h``, ``12h``, ``1d``, ``1w``.
            start_date: Start date. Accepts ``"2025-01-01"`` or UNIX timestamp.
            end_date: End date. Accepts ``"2025-06-01"`` or UNIX timestamp.
                If ``None``, returns data up to now.
        """
        interval, minutes = parse_timeframe(timeframe)
        url = f"{self._client.CHARTS_URL}/trade/{symbol}/{interval}"
        window_seconds = _CHARTS_MAX_CANDLES * minutes * 60
        from_ts = parse_date(start_date) if start_date is not None else None
        end_ts = parse_date(end_date) if end_date is not None else None
        all_candles: list[dict] = []

        while True:
            params: dict[str, Any] = {}
            if from_ts is not None:
                params["from"] = from_ts
                page_end = from_ts + window_seconds
                params["to"] = min(page_end, end_ts) if end_ts is not None else page_end
            elif end_ts is not None:
                params["to"] = end_ts
            resp = self._client.raw_get(url, params=params)
            resp.raise_for_status()
            candles = resp.json().get("candles", [])
            if not candles:
                break
            all_candles.extend(candles)
            last_time = int(candles[-1]["time"]) // 1000
            if end_ts is not None and last_time >= end_ts:
                break
            if len(candles) < _CHARTS_MAX_CANDLES:
                break
            from_ts = last_time

        if not all_candles:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        df = pd.DataFrame(all_candles)
        df["time"] = pd.to_datetime(df["time"], unit="ms")
        for col in ("open", "high", "low", "close", "volume"):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col])
        df = df.set_index("time")
        return df[~df.index.duplicated(keep="last")]

    def get_open_positions(self) -> list[dict[str, Any]]:
        """Fetch all open positions."""
        self._client.acquire(1)
        result = self._client.authenticated_request("GET", "openpositions")
        return result.get("openPositions", [])

    def get_open_position(self, symbol: str) -> dict[str, Any] | None:
        """Fetch the open position for a specific symbol, or ``None``."""
        positions = self.get_open_positions()
        for p in positions:
            if p.get("symbol") == symbol:
                return p
        return None

    def get_order_book(self, symbol: str, depth: int | None = None) -> dict[str, Any]:
        """Fetch order book for a futures symbol.

        Args:
            symbol: Futures symbol (e.g. ``"PF_XBTUSD"``).
            depth: Number of ask/bid levels to return. If ``None``, returns all.
        """
        result = self._client.public_get("orderbook", {"symbol": symbol})
        book = result.get("orderBook", result)
        # Kraken futures sorts bids lowest-first; reverse so best bid is first
        if depth is not None:
            book = {**book, "asks": book["asks"][:depth], "bids": book["bids"][-depth:][::-1]}
        else:
            book["bids"] = book["bids"][::-1]
        return book

    def get_spreads(self, symbol: str, depth: int = 1) -> dict[str, Any]:
        """Fetch current spread from the order book.

        Args:
            symbol: Futures symbol (e.g. ``"PF_XBTUSD"``).
            depth: Number of levels to include.
        """
        book = self.get_order_book(symbol, depth=depth)
        return {
            "ask": book["asks"][0][0],
            "bid": book["bids"][0][0],
            "spread": book["asks"][0][0] - book["bids"][0][0],
        }

    def get_recent_trades(self, symbol: str, count: int = 20) -> pd.DataFrame:
        """Fetch recent trades for a futures symbol.

        Args:
            symbol: Futures symbol (e.g. ``"PF_XBTUSD"``).
            count: Number of trades to return.
        """
        result = self._client.public_get("history", {"symbol": symbol})
        trades = result.get("history", [])[:count]
        if not trades:
            return pd.DataFrame(columns=["price", "size", "side"])
        df = pd.DataFrame(trades)
        df["time"] = pd.to_datetime(df["time"])
        df["price"] = pd.to_numeric(df["price"])
        df["size"] = pd.to_numeric(df["size"])
        return df[["time", "price", "size", "side"]].set_index("time")

    def get_server_time(self) -> str:
        """Fetch server time (smoke test).

        Returns:
            ISO-8601 server timestamp.
        """
        result = self._client.public_get("tickers")
        return result.get("serverTime", "")

    def get_ticker(self, symbol: str) -> dict[str, Any]:
        """Fetch ticker data for a single symbol.

        Args:
            symbol: Futures symbol (e.g. ``"PF_XBTUSD"``).
        """
        if time.monotonic() - self._tickers_cache_time < self.TICKER_CACHE_TTL:
            tickers = self._tickers_cache
        else:
            tickers = self.get_tickers()
        for ticker in tickers:
            if ticker.get("symbol", "").upper() == symbol.upper():
                return ticker
        raise APIError(f"Symbol not found: {symbol}")

    def get_ticker_data(self, symbol: str) -> dict[str, Any]:
        """Fetch full ticker data for a futures symbol.

        Args:
            symbol: Futures symbol (e.g. ``"PF_XBTUSD"``).
        """
        return self.get_ticker(symbol)

    def get_tickers(self) -> list[dict[str, Any]]:
        """Fetch ticker data for all futures symbols."""
        result = self._client.public_get("tickers")
        self._tickers_cache = result.get("tickers", [])
        self._tickers_cache_time = time.monotonic()
        return self._tickers_cache

    def place_order(
        self,
        symbol: str,
        side: str,
        volume: float,
        price: float | str | None = None,
        *,
        reduce_only: bool = False,
        cl_ord_id: str | None = None,
        **kwargs: Any,
    ) -> str:
        """Place a new futures order.

        If *price* is given the order defaults to ``lmt``; otherwise ``mkt``.

        Args:
            symbol: Futures symbol (e.g. ``"PF_XBTUSD"``).
            side: ``"buy"`` or ``"sell"``.
            volume: Order volume in base currency.
            price: Limit price (``None`` → market order).
            reduce_only: Reduce-only flag.
            cl_ord_id: Client order ID.
            **kwargs: ``order_type``, ``trigger_signal``, ``stop_price``,
                ``trailing_stop_deviation_unit``,
                ``trailing_stop_max_deviation``.

        Returns:
            Order ID.
        """
        self._client.acquire(1)
        order_type = kwargs.pop("order_type", "lmt" if price is not None else "mkt")

        data: dict[str, Any] = {
            "orderType": order_type,
            "symbol": symbol,
            "side": side,
            "size": self.format_qty(symbol, volume),
        }
        if price is not None:
            data["limitPrice"] = self.format_price(symbol, price)
        if reduce_only:
            data["reduceOnly"] = "true"
        if cl_ord_id:
            data["cliOrdId"] = cl_ord_id

        param_map = {
            "trigger_signal": "triggerSignal",
            "trailing_stop_deviation_unit": "trailingStopDeviationUnit",
            "trailing_stop_max_deviation": "trailingStopMaxDeviation",
        }
        for py_key, api_key in param_map.items():
            if py_key in kwargs:
                data[api_key] = kwargs[py_key]

        stop_price = kwargs.get("stop_price")
        if stop_price is not None:
            data["stopPrice"] = self.format_price(symbol, stop_price)

        result = self._client.authenticated_request("POST", "sendorder", data)
        return _require_send_success(result)

    def place_order_batch(self, orders: list[dict[str, Any]]) -> dict[str, Any]:
        """Submit batch operations (send, cancel, edit).

        Orders are independent — no OCO, no linking.

        Args:
            orders: List of operation dicts. Each must have an ``"order"`` key
                (``"send"``, ``"cancel"``, or ``"edit"``) plus the relevant
                parameters for that operation type.

        Returns:
            Batch result with per-operation statuses.
        """
        self._client.acquire(len(orders))
        data: dict[str, Any] = {"json": json.dumps({"batchOrder": orders})}
        result = self._client.authenticated_request("POST", "batchorder", data)
        return _require_batch_success(result)

    def place_stop_loss(
        self,
        symbol: str,
        side: str,
        volume: float,
        stop_loss_price: float | str,
        trigger_signal: str = "last",
    ) -> str:
        """Place a stop-loss order (reduce-only forced).

        Args:
            symbol: Futures symbol.
            side: ``"buy"`` or ``"sell"``.
            volume: Order volume.
            stop_loss_price: Price at which the stop-loss triggers.
            trigger_signal: ``"last"``, ``"mark"``, or ``"spot"``.

        Returns:
            Order ID.
        """
        self._client.acquire(1)
        data: dict[str, Any] = {
            "orderType": "stp",
            "symbol": symbol,
            "side": side,
            "size": self.format_qty(symbol, volume),
            "stopPrice": self.format_price(symbol, stop_loss_price),
            "triggerSignal": trigger_signal,
            "reduceOnly": "true",
        }
        result = self._client.authenticated_request("POST", "sendorder", data)
        return _require_send_success(result)

    def place_take_profit(
        self,
        symbol: str,
        side: str,
        volume: float,
        take_profit_price: float | str,
        trigger_signal: str = "last",
    ) -> str:
        """Place a take-profit order (reduce-only forced).

        Args:
            symbol: Futures symbol.
            side: ``"buy"`` or ``"sell"``.
            volume: Order volume.
            take_profit_price: Price at which the take-profit triggers.
            trigger_signal: ``"last"``, ``"mark"``, or ``"spot"``.

        Returns:
            Order ID.
        """
        self._client.acquire(1)
        data: dict[str, Any] = {
            "orderType": "take_profit",
            "symbol": symbol,
            "side": side,
            "size": self.format_qty(symbol, volume),
            "stopPrice": self.format_price(symbol, take_profit_price),
            "triggerSignal": trigger_signal,
            "reduceOnly": "true",
        }
        result = self._client.authenticated_request("POST", "sendorder", data)
        return _require_send_success(result)

    def place_bracket_order(
        self,
        symbol: str,
        side: str,
        volume: float,
        price: float | str | None = None,
        *,
        stop_loss_price: float | str | None = None,
        take_profit_price: float | str | None = None,
        trailing_stop_deviation: float | None = None,
        trailing_stop_deviation_unit: str = "PERCENT",
        reduce_only: bool = False,
        cl_ord_id: str | None = None,
        trigger_signal: str = "last",
    ) -> dict[str, str]:
        """Place an entry order with optional stop-loss, take-profit, and trailing stop.

        The entry is a market order unless ``price`` is given (then limit).
        Bracket orders are sized at ``volume`` and forced reduce-only on the
        opposite side, so they cap to the actual filled position when they
        trigger and are rejected harmlessly if the entry never fills.

        Args:
            symbol: Futures symbol (e.g. ``"PF_XBTUSD"``).
            side: ``"buy"`` or ``"sell"`` for the entry.
            volume: Entry order volume; also used as the bracket size.
            price: Limit price (``None`` → market order).
            stop_loss_price: Optional stop-loss trigger price.
            take_profit_price: Optional take-profit trigger price.
            trailing_stop_deviation: Optional trailing-stop max deviation.
            trailing_stop_deviation_unit: ``"PERCENT"`` or ``"QUOTE_CURRENCY"``.
            reduce_only: Reduce-only flag for the entry.
            cl_ord_id: Client order ID for the entry.
            trigger_signal: Trigger signal for brackets
                (``"last"``, ``"mark"``, or ``"spot"``).

        Returns:
            Dict containing ``"entry"`` and, when placed, ``"stop_loss"``,
            ``"take_profit"``, and ``"trailing_stop"`` order IDs.
        """
        entry_id = self.place_order(
            symbol,
            side,
            volume,
            price,
            reduce_only=reduce_only,
            cl_ord_id=cl_ord_id,
        )
        result: dict[str, str] = {"entry": entry_id}

        exit_side = "sell" if side == "buy" else "buy"

        if stop_loss_price is not None:
            result["stop_loss"] = self.place_stop_loss(
                symbol,
                exit_side,
                volume,
                stop_loss_price,
                trigger_signal=trigger_signal,
            )
        if take_profit_price is not None:
            result["take_profit"] = self.place_take_profit(
                symbol,
                exit_side,
                volume,
                take_profit_price,
                trigger_signal=trigger_signal,
            )
        if trailing_stop_deviation is not None:
            result["trailing_stop"] = self.place_trailing_stop(
                symbol,
                exit_side,
                volume,
                trailing_stop_deviation,
                deviation_unit=trailing_stop_deviation_unit,
                trigger_signal=trigger_signal,
                reduce_only=True,
            )

        return result

    def place_trailing_stop(
        self,
        symbol: str,
        side: str,
        volume: float,
        # trailing_stop_price: float | str,
        max_deviation: float,
        *,
        deviation_unit: str = "PERCENT",
        trigger_signal: str = "last",
        reduce_only: bool = True,
    ) -> str:
        """Place a trailing-stop order.

        Args:
            symbol: Futures symbol.
            side: ``"buy"`` or ``"sell"``.
            volume: Order volume.
            trailing_stop_price: Initial stop price.
            max_deviation: Deviation value for the trailing mechanism.
            deviation_unit: ``"PERCENT"`` or ``"QUOTE_CURRENCY"``.
            trigger_signal: ``"last"``, ``"mark"``, or ``"spot"``.
            reduce_only: Reduce-only flag (default ``True``).

        Returns:
            Order ID.
        """
        self._client.acquire(1)
        data: dict[str, Any] = {
            "orderType": "trailing_stop",
            "symbol": symbol,
            "side": side,
            "size": self.format_qty(symbol, volume),
            # "stopPrice": self.format_price(symbol, trailing_stop_price),
            "trailingStopDeviationUnit": deviation_unit,
            "trailingStopMaxDeviation": max_deviation,
            "triggerSignal": trigger_signal,
        }
        if reduce_only:
            data["reduceOnly"] = "true"
        result = self._client.authenticated_request("POST", "sendorder", data)
        return _require_send_success(result)

    def get_leverage_preferences(self) -> list[dict[str, Any]]:
        """Fetch current leverage preferences.

        Symbols in the response are in isolated margin mode.
        Symbols absent from the response are in cross margin mode.
        """
        self._client.acquire(1)
        result = self._client.authenticated_request("GET", "leveragepreferences")
        return result.get("leveragePreferences", [])

    def set_leverage(self, symbol: str, leverage: float) -> dict[str, Any]:
        """Set isolated margin mode with a max leverage for a symbol.

        Args:
            symbol: Futures symbol (e.g. ``"PF_XBTUSD"``).
            leverage: Maximum leverage multiplier.
        """
        self._client.acquire(1)
        data: dict[str, Any] = {"symbol": symbol, "maxLeverage": str(leverage)}
        return self._client.authenticated_request("PUT", "leveragepreferences", data)

    def set_cross_margin(self, symbol: str) -> dict[str, Any]:
        """Switch a symbol back to cross margin mode.

        Args:
            symbol: Futures symbol (e.g. ``"PF_XBTUSD"``).
        """
        self._client.acquire(1)
        data: dict[str, Any] = {"symbol": symbol}
        return self._client.authenticated_request("PUT", "leveragepreferences", data)
