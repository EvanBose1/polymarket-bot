"""
Smarter copy-trading filter.

Naive: copy the wallets with the highest raw profit. Problem: high profit can come
from one lucky huge bet (survivorship + lottery effect), and you fill AFTER them at
worse prices.

This module ranks wallets by:
  1. Risk-adjusted return (Sharpe-like) over their last N trades.
  2. Sample size: must have at least MIN_TRADES closed positions.
  3. Win rate floor.
  4. Recency: trades within RECENCY_DAYS.

It then only emits a copy signal when MIN_AGREE_WALLETS from this curated set have
taken the same side on the same token within a short window. This dramatically cuts
false positives at the cost of fewer trades.
"""
import time
import statistics
from dataclasses import dataclass, field
from typing import Iterable, Optional

import httpx

DATA_API = "https://data-api.polymarket.com"


@dataclass
class WalletStats:
    address: str
    n_trades: int
    win_rate: float
    mean_return: float
    stdev_return: float
    sharpe: float
    last_trade_ts: int


@dataclass
class FilterConfig:
    min_trades: int = 25
    min_win_rate: float = 0.55
    min_sharpe: float = 0.5
    recency_days: int = 14
    min_agree_wallets: int = 2
    agree_window_seconds: int = 600  # wallets must agree within 10 minutes


def fetch_user_trades(http: httpx.Client, address: str, limit: int = 200):
    r = http.get(f"{DATA_API}/activity",
                 params={"user": address, "limit": limit, "type": "TRADE"})
    r.raise_for_status()
    return r.json()


def fetch_resolution(http: httpx.Client, token_id: str) -> Optional[float]:
    # Polymarket exposes resolution under /markets via gamma; here we accept the data-api shortcut.
    try:
        r = http.get(f"{DATA_API}/positions", params={"token": token_id, "limit": 1})
        r.raise_for_status()
        rows = r.json()
        if rows and rows[0].get("resolved"):
            return float(rows[0]["resolvedPrice"])
    except Exception:
        pass
    return None


def score_wallet(http: httpx.Client, address: str,
                 cfg: FilterConfig) -> Optional[WalletStats]:
    trades = fetch_user_trades(http, address, limit=300)
    cutoff = time.time() - cfg.recency_days * 86400
    trades = [t for t in trades if t.get("timestamp", 0) >= cutoff]
    if len(trades) < cfg.min_trades:
        return None
    # Estimate per-trade return: (resolved_price - entry) * direction, fall back to
    # mark-to-current if not yet resolved. Polymarket activity sometimes already
    # carries a realisedPnl/usdcSize pair; use that when present.
    rets = []
    wins = 0
    for t in trades:
        pnl = t.get("realisedPnl")
        size = float(t.get("usdcSize", 0) or 0)
        if pnl is None or size <= 0:
            continue
        r = float(pnl) / size
        rets.append(r)
        if r > 0:
            wins += 1
    if len(rets) < cfg.min_trades:
        return None
    mean = statistics.mean(rets)
    sd = statistics.pstdev(rets) or 1e-9
    sharpe = mean / sd
    return WalletStats(
        address=address.lower(),
        n_trades=len(rets),
        win_rate=wins / len(rets),
        mean_return=mean,
        stdev_return=sd,
        sharpe=sharpe,
        last_trade_ts=max(int(t.get("timestamp", 0)) for t in trades),
    )


def build_curated_set(http: httpx.Client, candidate_addresses: Iterable[str],
                      cfg: Optional[FilterConfig] = None) -> list:
    cfg = cfg or FilterConfig()
    out = []
    for a in candidate_addresses:
        try:
            s = score_wallet(http, a, cfg)
        except Exception:
            continue
        if s is None:
            continue
        if s.win_rate < cfg.min_win_rate:
            continue
        if s.sharpe < cfg.min_sharpe:
            continue
        out.append(s)
    out.sort(key=lambda w: w.sharpe, reverse=True)
    return out


@dataclass
class PendingSignal:
    token_id: str
    side: str
    wallets: set = field(default_factory=set)
    first_seen: int = 0


class AgreementTracker:
    """Buffers recent fills from curated wallets and emits a signal when
    >= min_agree_wallets agree on the same (token, side) within agree_window."""
    def __init__(self, cfg: FilterConfig):
        self.cfg = cfg
        self.pending: dict = {}
        self.emitted: set = set()  # (token_id, side, bucket) keys we already fired

    def _bucket(self, ts: int) -> int:
        return ts // self.cfg.agree_window_seconds

    def observe(self, *, wallet: str, token_id: str, side: str, ts: int):
        key = (token_id, side, self._bucket(ts))
        if key in self.emitted:
            return None
        ps = self.pending.setdefault(key, PendingSignal(token_id, side, set(), ts))
        ps.wallets.add(wallet.lower())
        if len(ps.wallets) >= self.cfg.min_agree_wallets:
            self.emitted.add(key)
            return ps
        return None

    def gc(self, now: Optional[int] = None):
        now = now or int(time.time())
        keep_bucket = self._bucket(now) - 2
        for k in list(self.pending):
            if k[2] < keep_bucket:
                self.pending.pop(k, None)
