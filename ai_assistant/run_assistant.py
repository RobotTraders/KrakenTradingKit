from pathlib import Path
from typing import Any

import requests

from common.discord_notifier import DiscordNotifier
from common.logging_setup import configure_logging, log_event
from common.secrets import get_secrets
from strategies import get_strategy

from .claude import send_request
from .config import get_config
from .outlook import AIOutlook, OUTLOOK_SCHEMA
from .tools import build_tools


LOGS_DIR = Path(__file__).resolve().parents[1] / "logs"

REQUIRED_SECRETS = ("kraken_api_key", "kraken_api_secret", "anthropic_api_key")


def run_assistant(config_name: str) -> dict[str, Any]:
    """Run one cycle of a configuration: ask Claude for an outlook, act on it with
    the configured strategy, and log the run.

    Every run is logged to the structured JSON log; a failure anywhere in the cycle
    is logged with its traceback and posted to Discord when a webhook is configured,
    then re-raised.

    Args:
        config_name: File stem of a configuration under ``bots/``.
    """
    logger = configure_logging(config_name, LOGS_DIR)
    logger.info("Starting run for config %r", config_name)
    notifier = None
    title = config_name

    try:
        config = get_config(config_name)
        secrets = get_secrets(config.secrets, required=REQUIRED_SECRETS)
        title = f"{config.crypto_name} - {config_name}"
        mode = "demo" if config.demo else "live"
        if "discord_webhook_url" in secrets:
            notifier = DiscordNotifier(
                secrets["discord_webhook_url"], username=config.discord_bot_name
            )

        strategy = get_strategy(config.strategy)
        connector = strategy.connector(
            secrets["kraken_api_key"],
            secrets["kraken_api_secret"],
            demo=config.demo,
        )

        schemas, dispatch = build_tools(config.tools, connector, config.kraken_symbol)
        run = send_request(
            config.text,
            secrets["anthropic_api_key"],
            model=config.model,
            max_tokens=config.max_tokens,
            tools=schemas,
            dispatch=dispatch,
            output_schema=OUTLOOK_SCHEMA,
            output_model=AIOutlook,
            web_search=config.web_search,
            max_gathering_turns=config.max_gathering_turns,
        )

        action = strategy.run(
            connector,
            config.kraken_symbol,
            run.output,
            dry_run=config.dry_run,
            **config.strategy_params,
        )
        reference_price = float(connector.get_ticker(config.kraken_symbol)["markPrice"])

        log_event(
            logger,
            "Run complete",
            event="decision",
            crypto_name=config.crypto_name,
            symbol=config.kraken_symbol,
            mode=mode,
            model=config.model,
            reference_price=reference_price,
            account_capital=connector.get_account_capital(),
            tool_calls=run.tool_calls,
            web_searches=run.web_searches,
            interpretation=run.output.interpretation,
            confidence=run.output.confidence,
            reasons=run.output.reasons,
            forced=run.forced,
            gathering_turns=run.gathering_turns,
            token_usage=run.token_usage,
            total_input_tokens=sum(u.get("input_tokens") or 0 for u in run.token_usage),
            total_output_tokens=sum(u.get("output_tokens") or 0 for u in run.token_usage),
            action=action,
        )

    except Exception as error:
        logger.exception("Run failed for config %r", config_name)
        if notifier is not None:
            try:
                notifier.send_error(title, error)
            except requests.RequestException:
                logger.warning("Discord error notification failed", exc_info=True)
        raise

    if notifier is not None:
        try:
            notifier.send_notification(
                title,
                outlook=run.output.model_dump(),
                action=action,
                web_searches=run.web_searches,
                tool_calls=run.tool_calls,
                gathering_turns=run.gathering_turns,
                forced=run.forced,
            )
        except requests.RequestException:
            logger.warning("Discord notification failed", exc_info=True)

    return action
