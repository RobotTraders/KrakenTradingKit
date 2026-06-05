import re
from datetime import datetime, timezone
from typing import Any

import requests


_POSITION_COLOR = {
    "long": 0x00FF00,
    "short": 0xFF0000,
    "flat": 0x808080,
}

_ERROR_COLOR = 0xFFA500

_DEFAULT_TIMEOUT_SECONDS = 10
_DEFAULT_USERNAME = "AI Trading Assistant"
_WEBHOOK_PREFIX = "https://discord.com/api/webhooks/"
_FIELD_VALUE_MAX_LENGTH = 1024


class DiscordNotifier:
    """Webhook notifier that posts the AI outlook and the trading action to Discord."""

    def __init__(self, webhook_url: str, username: str | None = None) -> None:
        """Initialize the notifier.

        Args:
            webhook_url: Discord webhook URL.
            username: Display name to override the webhook's default.

        Raises:
            ValueError: ``webhook_url`` is empty or not a Discord webhook URL.
        """
        if not webhook_url or not webhook_url.startswith(_WEBHOOK_PREFIX):
            raise ValueError("Invalid Discord webhook URL")

        self._webhook_url = webhook_url
        self._username = username or _DEFAULT_USERNAME
        self._timeout = _DEFAULT_TIMEOUT_SECONDS

    def send_notification(
        self,
        title: str,
        outlook: dict[str, Any],
        action: dict[str, Any],
        web_searches: list[dict[str, Any]] | None = None,
        tool_calls: list[dict[str, Any]] | None = None,
        gathering_turns: int | None = None,
        forced: bool = False,
    ) -> None:
        """Post an embed showing the trading action, every field of the outlook, and
        what the agent did on each gathering turn.

        Args:
            title: Heading for the embed.
            outlook: AI outlook as a mapping; each field is shown as-is.
            action: Trading action, with ``action`` and ``position`` keys.
            web_searches: Web searches the AI ran, each ``{"turn", "query", "urls"}``.
            tool_calls: Tool calls the AI made, each ``{"turn", "name", "input"}``.
            gathering_turns: Gathering turns the agent loop used, if known.
            forced: Whether the outlook was forced after the gathering budget.

        Raises:
            requests.HTTPError: Discord returned a non-2xx status.
            requests.RequestException: The HTTP call itself failed.
        """
        embed = _build_embed(
            title, outlook, action, web_searches, tool_calls, gathering_turns, forced
        )
        payload = {"embeds": [embed], "username": self._username}

        response = requests.post(self._webhook_url, json=payload, timeout=self._timeout)
        response.raise_for_status()

    def send_error(self, title: str, error: Exception) -> None:
        """Post an embed reporting that a run stopped and why.

        Args:
            title: Heading for the embed.
            error: The exception that stopped the run.

        Raises:
            requests.HTTPError: Discord returned a non-2xx status.
            requests.RequestException: The HTTP call itself failed.
        """
        embed = {
            "title": f"\U0001F6A8 {title}",
            "color": _ERROR_COLOR,
            "fields": [
                {"name": "Run stopped", "value": str(error)[:_FIELD_VALUE_MAX_LENGTH], "inline": False}
            ],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        payload = {"embeds": [embed], "username": self._username}

        response = requests.post(self._webhook_url, json=payload, timeout=self._timeout)
        response.raise_for_status()


def _strip_citations(text: str) -> str:
    return re.sub(r"<cite[^>]*>|</cite>", "", text)


def _agent_activity(
    tool_calls: list[dict[str, Any]],
    web_searches: list[dict[str, Any]],
    gathering_turns: int,
    forced: bool,
) -> str:
    lines = []
    for turn in range(1, gathering_turns + 1):
        steps = []
        for call in tool_calls:
            if call.get("turn") == turn:
                arguments = ", ".join(f"{k}={v}" for k, v in call.get("input", {}).items())
                steps.append(f"{call['name']}({arguments})")
        for search in web_searches:
            if search.get("turn") == turn:
                steps.append(f'search "{search["query"]}"')
        if turn == gathering_turns and not forced:
            steps.append("outlook")
        if steps:
            lines.append(f"{turn}. " + " · ".join(steps))
    if forced:
        lines.append("forced. outlook")
    return "\n".join(lines)


def _build_embed(
    title: str,
    outlook: dict[str, Any],
    action: dict[str, Any],
    web_searches: list[dict[str, Any]] | None,
    tool_calls: list[dict[str, Any]] | None,
    gathering_turns: int | None,
    forced: bool,
) -> dict[str, Any]:
    position = action.get("position", "flat")
    fields = [
        {
            "name": "Action",
            "value": f"{action['action'].upper()} → {position.upper()}",
            "inline": False,
        }
    ]
    for key, value in outlook.items():
        fields.append(
            {
                "name": key.replace("_", " ").title(),
                "value": _strip_citations(str(value))[:_FIELD_VALUE_MAX_LENGTH],
                "inline": False,
            }
        )
    if gathering_turns is not None:
        activity = _agent_activity(tool_calls or [], web_searches or [], gathering_turns, forced)
        if activity:
            fields.append(
                {
                    "name": f"Gathering Turns ({gathering_turns})",
                    "value": activity[:_FIELD_VALUE_MAX_LENGTH],
                    "inline": False,
                }
            )
    urls = [url for search in web_searches or [] for url in search["urls"]]
    unique = list(dict.fromkeys(urls))
    if unique:
        listed = "\n".join(unique)[:_FIELD_VALUE_MAX_LENGTH]
        fields.append({"name": f"Sources ({len(unique)})", "value": listed, "inline": False})

    return {
        "title": title,
        "color": _POSITION_COLOR.get(position, _POSITION_COLOR["flat"]),
        "fields": fields,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
