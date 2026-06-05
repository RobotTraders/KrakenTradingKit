import base64
import hashlib
import hmac
import time
import urllib.parse
from typing import Any, Self

import requests

from .exceptions import APIError, AuthError
from .rate_limiter import RateLimiter, FUTURES_LIMITS


class FuturesClient:
    """Transport and authentication layer for the Kraken Futures REST API."""

    LIVE_BASE_URL = "https://futures.kraken.com/derivatives/api/v3"
    DEMO_BASE_URL = "https://demo-futures.kraken.com/derivatives/api/v3"
    CHARTS_URL = "https://futures.kraken.com/api/charts/v1"

    def __init__(self, api_key: str, api_secret: str, *, demo: bool = False) -> None:
        self._api_key = api_key
        self._api_secret = api_secret
        self._authenticated = True
        self._base_url = self.DEMO_BASE_URL if demo else self.LIVE_BASE_URL
        self._session = requests.Session()
        self._limiter = RateLimiter(**FUTURES_LIMITS["default"])

    @classmethod
    def public(cls, *, demo: bool = False) -> Self:
        """Create an unauthenticated client for public endpoints only."""
        instance = object.__new__(cls)
        instance._api_key = None
        instance._api_secret = None
        instance._authenticated = False
        instance._base_url = cls.DEMO_BASE_URL if demo else cls.LIVE_BASE_URL
        instance._session = requests.Session()
        instance._limiter = RateLimiter(**FUTURES_LIMITS["default"])
        return instance

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def close(self) -> None:
        """Close the underlying HTTP session."""
        self._session.close()

    @property
    def session(self) -> requests.Session:
        """Read-only access to the underlying HTTP session."""
        return self._session

    def acquire(self, cost: float = 1) -> None:
        """Acquire tokens from the rate limiter."""
        self._limiter.acquire(cost)

    def authenticated_request(
        self,
        method: str,
        endpoint: str,
        data: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Sign and execute an authenticated request, returning the parsed result."""
        self._require_auth()
        nonce = str(int(time.time() * 1000))
        endpoint_path = f"/api/v3/{endpoint}"
        url = f"{self._base_url}/{endpoint}"

        if method.upper() == "GET":
            postdata = urllib.parse.urlencode(params) if params else ""
        else:
            postdata = urllib.parse.urlencode(data) if data else ""

        headers = {
            "APIKey": self._api_key,
            "Authent": self._sign(endpoint_path, postdata, nonce),
            "Nonce": nonce,
        }

        resp = self._session.request(
            method, url, params=params, data=data, headers=headers
        )
        return _handle_response(resp)

    def public_get(
        self, endpoint: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """GET a public endpoint and return the parsed result."""
        url = f"{self._base_url}/{endpoint}"
        resp = self._session.get(url, params=params)
        return _handle_response(resp)

    def raw_get(
        self, url: str, params: dict[str, Any] | None = None
    ) -> requests.Response:
        """Execute a raw GET request and return the response."""
        return self._session.get(url, params=params)

    def _require_auth(self) -> None:
        if not self._authenticated:
            raise AuthError("This method requires API credentials")

    def _sign(self, endpoint_path: str, postdata: str, nonce: str) -> str:
        message = postdata + nonce + endpoint_path
        sha256_hash = hashlib.sha256(message.encode()).digest()
        secret = base64.b64decode(self._api_secret)
        mac = hmac.new(secret, sha256_hash, hashlib.sha512)
        return base64.b64encode(mac.digest()).decode()


def _handle_response(resp: requests.Response) -> dict[str, Any]:
    try:
        body = resp.json()
    except Exception:
        resp.raise_for_status()
        raise APIError(f"Unexpected response: {resp.text[:200]}")
    result = body.get("result")
    if result != "success":
        error = body.get("error", body.get("errors", "Unknown error"))
        raise APIError(error)
    return body
