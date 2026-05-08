# polymarket-bot

Automated [Polymarket](https://polymarket.com) trading bot with two pluggable strategies:

1. **Copy-trader** - mirrors recent fills from a list of "smart money" wallets (or auto-pulls top wallets from the leaderboard).
2. **Edge / fair-value** - places passive limit orders when book midpoint diverges from a fair-value model.

> This is software for personal research and paper-trading. It is not investment advice. You are responsible for any funds you connect to it. Polymarket is restricted in some jurisdictions (including the US) - make sure you are permitted to use it.

## Architecture

- `bot.py` - single-file entry point with both strategies, a risk-managed `Portfolio`, and an async run-loop.
- `tests/` - unit tests for risk caps and config defaults.
- `.env.example` - template for the env vars the bot reads.

Key safety defaults:

- `DRY_RUN=1` by default (logs trades instead of placing them).
- Per-position cap (`max_position_usd`, default $25) and total exposure cap (`max_total_exposure_usd`, default $200).
- Stale-signal filter on copied trades (drops fills older than 5 minutes).

## Setup

```bash
git clone https://github.com/EvanBose1/polymarket-bot.git
cd polymarket-bot
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# edit .env and fill in POLY_PK and POLY_FUNDER
```

Get your funder/proxy address and API credentials from Polymarket: Profile -> API. Fund the wallet with USDC on Polygon.

## Run

Paper trade (recommended first):

```bash
set -a; source .env; set +a
python bot.py
```

Go live (only after watching dry-run logs and trusting the behaviour):

```bash
DRY_RUN=0 python bot.py
```

## Configuration

All knobs live on the `Config` dataclass at the top of `bot.py`:

| Field | Default | Notes |
|-------|---------|-------|
| `max_position_usd` | 25 | Max USD per single order |
| `max_total_exposure_usd` | 200 | Hard cap across all open positions |
| `min_edge` | 0.03 | Required EV in cents before edge strategy fires |
| `poll_seconds` | 20 | How often each strategy runs |
| `watch_addresses` | env `WATCH` | Comma-separated wallets to copy. Empty = auto top traders |

## Strategies

### Copy-trader

Polls Polymarket data API for recent TRADE activity from each watched wallet, dedupes by transaction hash, and posts a same-side limit order one tick worse than top of book. Order size is scaled down vs. the source trade.

### Edge

Iterates active markets, computes `edge = fair_value - mid`, and posts a passive order on the favourable side when `abs(edge) >= min_edge`. The default `fair_value` is just the last trade price - **replace it with a real model** (sports prediction, news classifier, cross-market arb, etc.) before going live.

## Tests

```bash
pip install pytest
pytest -q
```

## Roadmap

- Websocket streaming instead of polling.
- Sharpe-weighted leaderboard filter for copy-trader.
- Plug-in fair-value modules (sports, crypto, politics).
- SQLite-backed position/trade ledger.
- Telegram/Discord alerts.

## License

MIT - use at your own risk.
