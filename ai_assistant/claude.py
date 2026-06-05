import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import requests
from pydantic import BaseModel, ValidationError

from kraken_kit.exceptions import APIError


logger = logging.getLogger(__name__)

URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
WEB_SEARCH_TOOL_TYPE = "web_search_20250305"
REQUEST_TIMEOUT_SECONDS = 60

ToolDispatch = Callable[[str, dict[str, Any]], str]


class AIResponseError(Exception):
    """Raised when the Claude response does not match the expected format."""


@dataclass
class AgentRun:
    """A completed agent loop. ``forced`` is true when the output was compelled
    after the gathering budget ran out rather than offered by Claude on its own;
    ``gathering_turns`` counts the gathering turns used, excluding any forced
    synthesis turn."""

    output: BaseModel
    tool_calls: list[dict[str, Any]]
    web_searches: list[dict[str, Any]]
    forced: bool
    gathering_turns: int
    token_usage: list[dict[str, Any]]


def send_request(
    prompt: str,
    api_key: str,
    *,
    model: str,
    max_tokens: int,
    tools: list[dict[str, Any]],
    dispatch: ToolDispatch,
    output_schema: dict[str, Any],
    output_model: type[BaseModel],
    web_search: dict[str, Any] | None = None,
    max_gathering_turns: int = 10,
) -> AgentRun:
    """Run the agent loop against Claude and return the final output and tool calls.

    Claude is given the provided tools plus web search and a terminal output
    tool. For up to ``max_gathering_turns`` it may fetch data and decide on its
    own; once that budget is spent without an output, a final turn forces the
    output tool so an answer is always produced.

    Args:
        prompt: The user prompt sent to Claude.
        api_key: Anthropic API key.
        model: Claude model name (e.g. ``"claude-sonnet-4-5-20250929"``).
        max_tokens: Maximum tokens Claude may generate per turn.
        tools: Tool schemas Claude may call, each with ``name``,
            ``description`` and ``input_schema``.
        dispatch: Runs a tool by name with Claude's arguments and returns the
            result as a string.
        output_schema: Tool schema of the terminal output tool; calling it ends
            the loop.
        output_model: Pydantic model the output tool's payload must validate
            against.
        web_search: Options for Anthropic's web-search server tool (e.g.
            ``max_uses``, ``allowed_domains``, ``blocked_domains``). ``None``
            disables web search.
        max_gathering_turns: Turns Claude may gather data before the output
            is forced.

    Returns:
        ``AgentRun`` with the validated output, the tool calls
        (``{"name", "input"}``) Claude made in order, the web searches
        (``{"query", "urls"}``) it ran, and whether the output was forced.

    Raises:
        APIError: Anthropic returned a non-success HTTP status.
        AIResponseError: Claude produced neither a tool call nor an output
            during gathering, or the forced output failed validation.
    """
    output_name = output_schema["name"]
    all_tools = _build_tools(tools, web_search, output_schema)
    messages: list[dict[str, Any]] = [{"role": "user", "content": prompt}]
    tool_calls: list[dict[str, Any]] = []
    web_searches: list[dict[str, Any]] = []
    token_usage: list[dict[str, Any]] = []
    queries_by_id: dict[str, str] = {}

    for turn in range(1, max_gathering_turns + 1):
        blocks, stop_reason, usage = _post(messages, all_tools, api_key, model, max_tokens)
        token_usage.append({"turn": turn, **usage})
        searches = _web_searches(blocks, queries_by_id)
        for search in searches:
            search["turn"] = turn
        web_searches.extend(searches)

        if stop_reason == "pause_turn":
            messages.append({"role": "assistant", "content": blocks})
            continue

        output_block = _find_output(blocks, output_name)
        if output_block is not None:
            output = _validate_output(output_block, output_model)
            return AgentRun(
                output,
                tool_calls,
                web_searches,
                forced=False,
                gathering_turns=turn,
                token_usage=token_usage,
            )

        tool_uses = _client_tool_uses(blocks, output_name)
        if not tool_uses:
            raise AIResponseError(f"Claude returned no tool call and no output: {blocks}")

        tool_calls.extend(
            {"turn": turn, "name": b["name"], "input": b.get("input", {})} for b in tool_uses
        )
        messages.append({"role": "assistant", "content": blocks})
        messages.append({"role": "user", "content": _run_tools(tool_uses, dispatch)})

    forced_choice = {"type": "tool", "name": output_name}
    blocks, _, usage = _post(messages, all_tools, api_key, model, max_tokens, tool_choice=forced_choice)
    token_usage.append({"turn": "forced", **usage})
    web_searches.extend(_web_searches(blocks, queries_by_id))
    output_block = _find_output(blocks, output_name)
    if output_block is None:
        raise AIResponseError(f"Forced output turn produced no output: {blocks}")
    return AgentRun(
        _validate_output(output_block, output_model),
        tool_calls,
        web_searches,
        forced=True,
        gathering_turns=max_gathering_turns,
        token_usage=token_usage,
    )


def _post(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    api_key: str,
    model: str,
    max_tokens: int,
    tool_choice: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], str | None, dict[str, Any]]:
    headers = {
        "x-api-key": api_key,
        "anthropic-version": ANTHROPIC_VERSION,
        "content-type": "application/json",
    }
    payload: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "tools": tools,
        "messages": messages,
    }
    if tool_choice is not None:
        payload["tool_choice"] = tool_choice
    response = requests.post(
        URL, headers=headers, data=json.dumps(payload), timeout=REQUEST_TIMEOUT_SECONDS
    )
    if not response.ok:
        raise APIError(f"Anthropic API {response.status_code}: {response.text}")
    data = response.json()
    if data.get("stop_reason") == "max_tokens":
        raise AIResponseError(
            f"Response truncated by the max_tokens cap ({max_tokens}); raise max_tokens in the config"
        )
    usage = data.get("usage", {})
    trimmed = {
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
    }
    searches = usage.get("server_tool_use", {}).get("web_search_requests", 0)
    if searches:
        trimmed["web_search_requests"] = searches
    return data.get("content", []), data.get("stop_reason"), trimmed


def _build_tools(
    tools: list[dict[str, Any]],
    web_search: dict[str, Any] | None,
    output_schema: dict[str, Any],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    if web_search is not None:
        result.append(
            {"type": WEB_SEARCH_TOOL_TYPE, "name": "web_search", **web_search}
        )
    result.extend(tools)
    result.append(output_schema)
    return result


def _web_searches(
    blocks: list[dict[str, Any]], queries_by_id: dict[str, str]
) -> list[dict[str, Any]]:
    for block in blocks:
        if block.get("type") == "server_tool_use" and block.get("name") == "web_search":
            queries_by_id[block.get("id")] = block.get("input", {}).get("query", "")

    searches = []
    for block in blocks:
        if block.get("type") != "web_search_tool_result":
            continue
        results = block.get("content", [])
        urls = [r.get("url", "") for r in results if isinstance(r, dict)]
        searches.append({"query": queries_by_id.get(block.get("tool_use_id"), ""), "urls": urls})
    return searches


def _find_output(blocks: list[dict[str, Any]], output_name: str) -> dict[str, Any] | None:
    for block in blocks:
        if block.get("type") == "tool_use" and block.get("name") == output_name:
            return block
    return None


def _client_tool_uses(
    blocks: list[dict[str, Any]], output_name: str
) -> list[dict[str, Any]]:
    return [
        b
        for b in blocks
        if b.get("type") == "tool_use" and b.get("name") != output_name
    ]


def _run_tools(tool_uses: list[dict[str, Any]], dispatch: ToolDispatch) -> list[dict[str, Any]]:
    results = []
    for block in tool_uses:
        name = block["name"]
        arguments = block.get("input", {})
        logger.info("Claude called %s %s", name, arguments)
        output = dispatch(name, arguments)
        results.append(
            {"type": "tool_result", "tool_use_id": block["id"], "content": output}
        )
    return results


def _validate_output(block: dict[str, Any], output_model: type[BaseModel]) -> BaseModel:
    try:
        return output_model(**block.get("input", {}))
    except ValidationError as e:
        raise AIResponseError(f"Invalid output structure: {e}") from e
