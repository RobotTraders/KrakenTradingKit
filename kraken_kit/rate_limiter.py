import threading
import time


class RateLimiter:
    """Counter-based rate limiter with configurable decay.

    The counter increases with each call and decays continuously over time.
    When a call would push the counter past the maximum, the limiter sleeps
    until enough headroom has decayed.

    https://docs.kraken.com/api/docs/guides/spot-rest-ratelimits/
    https://docs.kraken.com/api/docs/guides/spot-ratelimits/
    """

    def __init__(self, max_counter: float, decay_rate: float) -> None:
        """
        Args:
            max_counter: Counter ceiling before requests are throttled.
            decay_rate: Units the counter drops per second.
        """
        self._max_counter = max_counter
        self._decay_rate = decay_rate
        self._counter = 0.0
        self._last_update = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self, cost: float = 1) -> None:
        """Block until the counter can accommodate *cost*."""
        with self._lock:
            self._decay()
            if self._counter + cost > self._max_counter:
                deficit = self._counter + cost - self._max_counter
                wait = deficit / self._decay_rate
                time.sleep(wait)
                self._decay()
            self._counter += cost

    def _decay(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_update
        self._counter = max(0.0, self._counter - elapsed * self._decay_rate)
        self._last_update = now


SPOT_PRIVATE_LIMITS: dict[str, dict[str, float]] = {
    "starter":      {"max_counter": 15, "decay_rate": 0.33},
    "intermediate": {"max_counter": 20, "decay_rate": 0.5},
    "pro":          {"max_counter": 20, "decay_rate": 1.0},
}

SPOT_TRADING_LIMITS: dict[str, dict[str, float]] = {
    "starter":      {"max_counter": 60,  "decay_rate": 1.0},
    "intermediate": {"max_counter": 125, "decay_rate": 2.34},
    "pro":          {"max_counter": 180, "decay_rate": 3.75},
}

FUTURES_LIMITS: dict[str, dict[str, float]] = {
    "default": {"max_counter": 50, "decay_rate": 1.0},
}
