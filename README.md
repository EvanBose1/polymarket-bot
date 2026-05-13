# polymarket-bot

Automated [Polymarket](https://polymarket.com) trading bot with a ledger, a backtester, and a small live dashboard.

> Personal research / paper-trading tool. Not investment advice. Polymarket is restricted in some jurisdictions including the US - make sure you are permitted to use it.

## What's in here

- `bot.py` - async run loop with two strategies (copy-trade and edge), risk-managed `Portfolio`, dry-run by default.
- `ledger.py` - SQLite-backed ledger for every signal and (simulated or real) fill. The single source of truth.
- `smart_filter.py` - smarter copy-trade filter: ranks wallets by Sharpe-like risk-adjusted return and only fires when **multiple curated wallets agree** on the same trade within a short window.
- `backtest.py` - replay strategies against historical Polymarket trade data and report PnL, Sharpe, and win rate.
- `server.py` + `web/` - FastAPI + a tiny static page that shows live positions, recent signals, recent fills, realised P&L and top markets. Refreshes every 5 s.
- `tests/` - unit tests.

## Safety defaults

- `DRY_RUN=1` by default. Logs would-be trades but does not place orders.
- Per-position cap `max_position_usd=$25`, total exposure cap `max_total_exposure_usd=$200`.
- Copy-trader requires **2 curated wallets to agree** within 10 minutes before mirroring.
- Stale-signal filter drops fills older than 5 minutes.

## Setup

```bash
git clone https://github.com/EvanBose1/polymarket-bot.git
cd polymarket-bot
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# edit .env: POLY_PK, POLY_FUNDER
```

## Run

**Paper trade** (recommended - days, not hours):

```bash
set -a; source .env; set +a
python bot.py
```

**Dashboard** (in a second terminal):

```bash
uvicorn server:app --reload --port 8000
# open http://localhost:8000
```

**Backtest** a single market:

```bash
python backtest.py --token-id 0xYOUR_TOKEN_ID --strategy edge --min-edge 0.04
```

**Go live** - only after you have watched several days of dry-run logs *and* backtested the strategy on multiple markets:

```bash
DRY_RUN=0 python bot.py
```

## Configuration

| Env var | Default | Notes |
|---|---|---|
| `POLY_PK` | (required) | Polygon private key holding USDC |
| `POLY_FUNDER` | (required) | Your Polymarket proxy/funder address |
| `DRY_RUN` | 1 | Set to 0 for live trading |
| `WATCH` | (empty) | Comma-separated wallets to copy. Empty = auto-curate from leaderboard |
| `SMART_FILTER` | 1 | Require multi-wallet agreement before copying |
| `LEDGER_PATH` | `state/ledger.db` | SQLite file |

In-code knobs on `Config` (top of `bot.py`): `max_position_usd`, `max_total_exposure_usd`, `min_edge`, `poll_seconds`, `min_agree_wallets`.

## How the strategies work

**Copy-trader.** Polls `data-api.polymarket.com` for recent fills from each wallet in a curated set. The curated set is rebuilt hourly: candidates come from the leaderboard, but only wallets that pass thresholds on Sharpe, win-rate, and sample size make it in. A signal is only emitted when `min_agree_wallets` of them take the same side on the same token within `agree_window_seconds`. Then the bot posts a limit order one tick worse than top of book.

**Edge strategy.** For each active market, computes `edge = fair_value - mid`. The default `fair_value` is just last-trade price - **this is a placeholder, not a real model**. Replace it with a model that has actual predictive power (sports model, news classifier, cross-market arb, your own research) before going live.

## Why this is structured for measurement, not just trading

Every signal and fill gets logged. The dashboard lets you watch the bot in real time and the backtester lets you replay a strategy variant on historical trades. So before you flip `DRY_RUN=0`, you can answer:

- Does this strategy actually have positive expected value on the markets I care about?
- How often does it trade?
- What is the realised win rate and per-trade return?

If the answer to the first question is "no" or "I don't know", do **not** trade live.

## Tests

```bash
pip install pytest
pytest -q
```

## Roadmap

- Websocket order-book feed instead of polling.
- Domain-specific fair-value plug-ins (sports, crypto, politics).
- Auto-flatten / kill-switch on max drawdown.
- Telegram or Discord alerts on fills.

## License

MIT - use at your own risk.
