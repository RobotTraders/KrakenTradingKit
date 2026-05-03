import base64
import hashlib
import hmac
import json
import time
import urllib.parse
from typing import Any, Self

import requests

from .exceptions import APIError, AuthError
from .rate_limiter import RateLimiter, SPOT_PRIVATE_LIMITS, SPOT_TRADING_LIMITS


class SpotClient:
    """Transport and authentication layer for the Kraken Spot REST API."""

    BASE_URL = "https://api.kraken.com"

    def __init__(self, api_key: str, api_secret: str, tier: str = "starter") -> None:
        self._api_key = api_key
        self._api_secret = api_secret
        self._authenticated = True
        self._tier = tier
        self._session = requests.Session()
        self._private_limiter = RateLimiter(**SPOT_PRIVATE_LIMITS[tier])
        self._trading_limiters: dict[str, RateLimiter] = {}

    @classmethod
    def public(cls) -> Self:
        """Create an unauthenticated client for public endpoints only."""
        instance = object.__new__(cls)
        instance._api_key = None
        instance._api_secret = None
        instance._authenticated = False
        instance._tier = "starter"
        instance._session = requests.Session()
        instance._private_limiter = None
        instance._trading_limiters = {}
        return instance

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def close(self) -> None:
        """Close the underlying HTTP session."""
        self._session.close()

    def acquire_private(self, cost: float = 1) -> None:
        """Acquire tokens from the private endpoint rate limiter."""
        self._private_limiter.acquire(cost)

    def acquire_trading(self, pair: str, cost: float = 1) -> None:
        """Acquire tokens from the per-pair trading rate limiter."""
        if pair not in self._trading_limiters:
            self._trading_limiters[pair] = RateLimiter(
                **SPOT_TRADING_LIMITS[self._tier]
            )
        self._trading_limiters[pair].acquire(cost)

    def private_request(
        self, endpoint: str, data: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Sign and POST to a private endpoint, returning the parsed result."""
        self._require_auth()
        urlpath = f"/0/private/{endpoint}"
        url = f"{self.BASE_URL}{urlpath}"
        if data is None:
            data = {}
        data["nonce"] = str(int(time.time() * 1000))
        headers = {
            "API-Key": self._api_key,
            "API-Sign": self._sign(urlpath, data),
        }
        resp = self._session.post(url, data=data, headers=headers)
        return _handle_response(resp)

    def private_request_json(
        self, endpoint: str, data: dict[str, Any]
    ) -> dict[str, Any]:
        """Sign and POST JSON to a private endpoint, returning the parsed result."""
        self._require_auth()
        urlpath = f"/0/private/{endpoint}"
        url = f"{self.BASE_URL}{urlpath}"
        data["nonce"] = int(time.time() * 1000)
        body = json.dumps(data)
        headers = {
            "API-Key": self._api_key,
            "API-Sign": self._sign(urlpath, body),
            "Content-Type": "application/json",
        }
        resp = self._session.post(url, data=body, headers=headers)
        return _handle_response(resp)

    def public_request(
        self, endpoint: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """GET a public endpoint and return the parsed result."""
        url = f"{self.BASE_URL}/0/public/{endpoint}"
        resp = self._session.get(url, params=params)
        return _handle_response(resp)

    def _require_auth(self) -> None:
        if not self._authenticated:
            raise AuthError("This method requires API credentials")

    def _sign(self, urlpath: str, data: dict[str, Any] | str) -> str:
        if isinstance(data, str):
            nonce = str(json.loads(data)["nonce"])
            postdata = data
        else:
            nonce = str(data["nonce"])
            postdata = urllib.parse.urlencode(data)
        encoded = (nonce + postdata).encode()
        message = urlpath.encode() + hashlib.sha256(encoded).digest()
        mac = hmac.new(base64.b64decode(self._api_secret), message, hashlib.sha512)
        return base64.b64encode(mac.digest()).decode()


def _handle_response(resp: requests.Response) -> dict[str, Any]:
    try:
        body = resp.json()
    except Exception:
        resp.raise_for_status()
        raise APIError(f"Unexpected response: {resp.text[:200]}")
    if body.get("error"):
        raise APIError(body["error"])
    return body["result"]
