import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


BOTS_DIR = Path(__file__).resolve().parents[1] / "bots"


@dataclass
class Config:
    """An assistant configuration: the prompt, the Claude settings, the market,
    and the strategy it runs."""

    text: str
    model: str
    max_tokens: int
    max_gathering_turns: int
    tools: dict[str, dict[str, Any]]
    web_search: dict[str, Any] | None
    strategy: str
    strategy_params: dict[str, Any]
    crypto_name: str
    kraken_symbol: str
    demo: bool
    dry_run: bool
    secrets: str
    discord_bot_name: str | None


def get_config(name: str) -> Config:
    """Load an assistant configuration from ``bots/<name>.toml``.

    The prompt body may reference any field of the file via ``str.format``
    (e.g. ``{crypto_name}``).

    Args:
        name: File stem of the configuration (without ``.toml``).

    Raises:
        FileNotFoundError: The configuration file does not exist.
        ValueError: The file is not valid TOML.
        KeyError: A required field is missing, or the prompt body references a
            field not present in the file.
    """
    path = BOTS_DIR / f"{name}.toml"
    if not path.exists():
        raise FileNotFoundError(f"Config '{name}' not found at {path}")

    with open(path, "rb") as f:
        try:
            data = tomllib.load(f)
        except tomllib.TOMLDecodeError as e:
            raise ValueError(f"Config file {path} is not valid TOML: {e}") from e

    try:
        rendered = data["prompt"].strip().format(**data)
    except KeyError as e:
        raise KeyError(f"Missing field {e} referenced by the prompt in '{name}'") from e

    try:
        strategy = dict(data["strategy"])
        return Config(
            text=rendered,
            model=data["model"],
            max_tokens=data["max_tokens"],
            max_gathering_turns=data["max_gathering_turns"],
            tools=data["tools"],
            web_search=data.get("web_search"),
            strategy=strategy.pop("name"),
            strategy_params=strategy,
            crypto_name=data["crypto_name"],
            kraken_symbol=data["kraken_symbol"],
            demo=data.get("demo", False),
            dry_run=data.get("dry_run", False),
            secrets=data["secrets"],
            discord_bot_name=data.get("discord_bot_name"),
        )
    except KeyError as e:
        raise KeyError(f"Config '{name}' is missing required field {e}") from e
