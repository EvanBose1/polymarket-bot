"""
Backtester: replay a strategy against historical Polymarket trade data.

Usage:
    python backtest.py --token-id 0xabc... --strategy edge --min-edge 0.04

Pulls historical trades from the Polymarket data API, walks them in time order,
and at each tick asks the strategy whether it would have placed an order. If yes,
we simulate a fill at that trade's price and mark-to-market with the next tick.
Finally we use the market resolution (if available) to compute realised P&L.
"""
import argparse
import math
import statistics
import time
from dataclasses import dataclass
from typing import Callable, Optional

import httpx

DATA_API = "https://data-api.polymarket.com"
GAMMA_API = "https://gamma-api.polymarket.com"


@dataclass
class SimFill:
    ts: int
    side: str
    price: float
    shares: float


def fetch_trades(token_id: str, limit: int = 1000):
    """Fetch recent trades for a CLOB token id. Newest first; we reverse for replay."""
    with httpx.Client(timeout=20) as c:
        r = c.get(f"{DATA_API}/trades", params={"market": token_id, "limit": limit})
        r.raise_for_status()
        trades = r.json()
    trades.sort(key=lambda t: t.get("timestamp", 0))
    return trades


def fetch_resolution(market_id: str) -> Optional[float]:
    with httpx.Client(timeout=20) as c:
        r = c.get(f"{GAMMA_API}/markets/{market_id}")
        if r.status_code != 200:
            return None
        m = r.json()
    # Polymarket reports outcome prices for resolved markets as 0 or 1 per outcome.
    outcome_prices = m.get("outcomePrices")
    if not outcome_prices:
        return None
    try:
        prices = outcome_prices if isinstance(outcome_prices, list) else __import__("json").loads(outcome_prices)
        return float(prices[0])
    except Exception:
        return None


# ----- Strategies as pure functions of recent trade window -----

def edge_strategy(window, *, min_edge: float = 0.03) -> Optional[str]:
    """Buy if mid-of-recent-window is below a simple fair-value (5-trade EMA),
    sell if above. window is a list of trade dicts in chronological order.
    Returns "BUY" / "SELL" / None."""
    if len(window) < 6:
        return None
    last_price = float(window[-1]["price"])
    # "fair value" placeholder: EMA of prior 5 trades.
    prior = [float(t["price"]) for t in window[-6:-1]]
    alpha = 0.5
    fv = prior[0]
    for p in prior[1:]:
        fv = alpha * p + (1 - alpha) * fv
    edge = fv - last_price
    if edge >= min_edge:
        return "BUY"
    if -edge >= min_edge:
        return "SELL"
    return None


def momentum_strategy(window) -> Optional[str]:
    """Trade in the direction of the last 3 trades' net move > 2 cents."""
    if len(window) < 4:
        return None
    diff = float(window[-1]["price"]) - float(window[-4]["price"])
    if diff > 0.02:
        return "BUY"
    if diff < -0.02:
        return "SELL"
    return None


STRATEGIES = {
    "edge": edge_strategy,
    "momentum": momentum_strategy,
}


def run_backtest(token_id: str, strategy_name: str = "edge",
                 size_shares: float = 100.0,
                 resolved_price: Optional[float] = None,
                 **strat_kwargs):
    strat: Callable = STRATEGIES[strategy_name]
    trades = fetch_trades(token_id)
    if len(trades) < 10:
        return {"error": "not enough trades", "n": len(trades)}
    fills = []
    for i in range(5, len(trades) - 1):
        window = trades[: i + 1]
        sig = strat(window, **strat_kwargs) if strat_kwargs else strat(window)
        if not sig:
            continue
        # simulate fill at the NEXT trade's price (conservative)
        next_price = float(trades[i + 1]["price"])
        fills.append(SimFill(ts=int(trades[i + 1].get("timestamp", 0)),
                              side=sig, price=next_price, shares=size_shares))
    if not fills:
        return {"n_trades": len(trades), "n_fills": 0, "pnl": 0.0}
    # Net position and cost
    net = sum(f.shares if f.side == "BUY" else -f.shares for f in fills)
    cost = sum((f.shares if f.side == "BUY" else -f.shares) * f.price for f in fills)
    if resolved_price is None:
        # mark to last observed price
        last_p = float(trades[-1]["price"])
        mtm = net * last_p
        pnl = mtm - cost
        basis = "mark-to-last"
    else:
        pnl = net * resolved_price - cost
        basis = "resolved"
    # Per-fill returns
    fill_returns = []
    mark = resolved_price if resolved_price is not None else float(trades[-1]["price"])
    for f in fills:
        r = (mark - f.price) if f.side == "BUY" else (f.price - mark)
        fill_returns.append(r * f.shares)
    sharpe = (statistics.mean(fill_returns) /
              statistics.pstdev(fill_returns)) if len(fill_returns) > 1 and statistics.pstdev(fill_returns) > 0 else None
    return {
        "n_trades": len(trades),
        "n_fills": len(fills),
        "net_shares": net,
        "cost": cost,
        "pnl": pnl,
        "basis": basis,
        "sharpe_per_fill": sharpe,
        "win_rate": sum(1 for r in fill_returns if r > 0) / len(fill_returns),
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--token-id", required=True)
    p.add_argument("--strategy", default="edge", choices=list(STRATEGIES))
    p.add_argument("--min-edge", type=float, default=0.03)
    p.add_argument("--size", type=float, default=100.0)
    p.add_argument("--resolved", type=float, default=None,
                   help="Resolved price (0 or 1). If omitted, marks to last trade.")
    args = p.parse_args()
    kwargs = {"min_edge": args.min_edge} if args.strategy == "edge" else {}
    out = run_backtest(args.token_id, args.strategy, size_shares=args.size,
                       resolved_price=args.resolved, **kwargs)
    import json
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
