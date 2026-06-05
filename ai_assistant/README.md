# AI Assistant

A Claude-driven trading assistant for Kraken Futures. Each run executes one cycle of its configuration: Claude gathers what the configured tools expose, produces a structured outlook, and the configured strategy acts on it. One TOML file under `bots/` at the repository root fully describes one assistant.

## Install

Dependencies are installed once at the repository root, which also creates the `.venv` the assistant runs from:

```bash
cd KrakenTradingKit
uv sync
```

## Setup

An assistant is created by copying the shipped example config and the secrets template, then editing both. From the repository root:

```bash
cp bots/daily_active_trader.toml bots/my_assistant.toml
cp bots/daily_active_trader_secrets_example.toml bots/my_assistant_secrets.toml
```

The secrets file holds `kraken_api_key`, `kraken_api_secret`, `anthropic_api_key`, and an optional `discord_webhook_url`; the config references it by file stem (`secrets = "my_assistant_secrets"`). Secrets files are gitignored and never reach the repository.

## Modes

Two top-level flags in the config TOML, alongside `crypto_name` and `kraken_symbol`:

- `demo = true` routes orders to `demo-futures.kraken.com` (separate API keys, generated there).
- `dry_run = true` decides and logs without placing any order.
- **Both flags default to off when absent: the assistant trades live with real orders.**

## Run once

From the repository root:

```bash
uv run python -m ai_assistant my_assistant
```

`uv run` executes the command inside the project's `.venv`. The argument is the config file stem. Logs land in `logs/<config>_<YYYY-MM>.jsonl` at the repository root, one JSON line per event with monthly rotation; the `event: "decision"` lines carry the outlook, sources, token usage, and orders.

## Automate with cron

Cron schedules run in the server's timezone:

```bash
timedatectl
```

On a UTC server, one line per assistant, daily at 00:05 UTC just after the daily candle closes:

```cron
5 0 * * * cd $HOME/KrakenTradingKit && $HOME/KrakenTradingKit/.venv/bin/python -m ai_assistant my_assistant >> $HOME/KrakenTradingKit/ai-assistant-cron.log 2>&1
```

Cron sets `$HOME` to the crontab owner's home, so the same line works as `root` on a VPS (`/root`) or as `ubuntu`/`ec2-user` on AWS (`/home/ubuntu`); a repository cloned elsewhere needs the full path instead. Cron's own PATH is minimal and has no `uv`, which is why the line addresses the python binary inside the venv directly: it is the same interpreter `uv run` resolves to.
