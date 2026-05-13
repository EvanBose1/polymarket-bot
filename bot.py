"""
Polymarket trading bot.

Strategies:
  - CopyTrader: mirrors recent fills from a curated set of wallets, with optional
    multi-wallet agreement filter (smart_filter.AgreementTracker).
  - EdgeStrategy: places passive limit orders when book mid diverges from fair value.

All signals and fills are logged to a SQLite ledger so the dashboard and backtester
can read them. Defaults to DRY_RUN=1 (no real orders placed).
"""
import os
import time
import logging
import asyncio
from dataclasses import dataclass, field
from typing import Optional

import httpx
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType
from py_clob_client.order_builder.constants import BUY, SELL

from ledger import Ledger
from smart_filter import (FilterConfig, AgreementTracker, build_curated_set,
                          fetch_user_trades)

log = logging.getLogger("pmbot")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

CLOB_HOST = "https://clob.polymarket.com"
DATA_API  = "https://data-api.polymarket.com"
GAMMA_API = "https://gamma-api.polymarket.com"


@dataclass
class Config:
    pk: str
    funder: str
    chain_id: int = 137
    max_position_usd: float = 25.0
    max_total_exposure_usd: float = 200.0
    min_edge: float = 0.03
    poll_seconds: int = 20
    dry_run: bool = True
    watch_addresses: list = field(default_factory=list)
    use_smart_filter: bool = True
    min_agree_wallets: int = 2


def make_client(cfg: Config) -> ClobClient:
    c = ClobClient(CLOB_HOST, key=cfg.pk, chain_id=cfg.chain_id,
                   signature_type=2, funder=cfg.funder)
    c.set_api_creds(c.create_or_derive_api_creds())
    return c


# ---------- Market data ----------
async def get_markets(client: httpx.AsyncClient, limit: int = 200):
    r = await client.get(f"{GAMMA_API}/markets",
                         params={"active": "true", "closed": "false", "limit": limit})
    r.raise_for_status()
    return r.json()


async def get_orderbook(client: httpx.AsyncClient, token_id: str):
    r = await client.get(f"{CLOB_HOST}/book", params={"token_id": token_id})
    r.raise_for_status()
    return r.json()


async def get_top_traders(client: httpx.AsyncClient, window: str = "7d", limit: int = 50):
    r = await client.get(f"{DATA_API}/leaderboard",
                         params={"window": window, "limit": limit, "sortBy": "profit"})
    r.raise_for_status()
    return r.json()


# ---------- Portfolio ----------
class Portfolio:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.exposure = 0.0
        self.positions: dict = {}

    def can_open(self, usd: float) -> bool:
        return (usd <= self.cfg.max_position_usd
                and self.exposure + usd <= self.cfg.max_total_exposure_usd)

    def book(self, token_id: str, usd: float):
        self.positions[token_id] = self.positions.get(token_id, 0.0) + usd
        self.exposure += usd


# ---------- Order placement ----------
def place_limit(cfg: Config, client, ledger: Ledger, *, strategy: str,
                token_id: str, side: str, price: float, size_shares: float,
                signal_id: Optional[int] = None):
    price = round(max(0.01, min(0.99, price)), 2)
    if cfg.dry_run:
        log.info(f"[DRY] {strategy} {side} {size_shares:.2f} @ {price} on {token_id[:10]}...")
        ledger.record_fill(signal_id=signal_id, strategy=strategy, token_id=token_id,
                           side=side, price=price, size_shares=size_shares,
                           dry_run=True)
        return {"dry_run": True}
    args = OrderArgs(price=price, size=size_shares, side=side, token_id=token_id)
    signed = client.create_order(args)
    resp = client.post_order(signed, OrderType.GTC)
    ledger.record_fill(signal_id=signal_id, strategy=strategy, token_id=token_id,
                       side=side, price=price, size_shares=size_shares,
                       dry_run=False, order_id=str(resp.get("orderID", "")))
    return resp


# ---------- Copy-trader with smart filter ----------
class CopyTrader:
    def __init__(self, cfg, clob, http, portfolio, ledger):
        self.cfg, self.clob, self.http, self.pf, self.ledger = cfg, clob, http, portfolio, ledger
        self.curated = set(a.lower() for a in cfg.watch_addresses)
        self.tracker = AgreementTracker(FilterConfig(min_agree_wallets=cfg.min_agree_wallets))
        self.seen_tx: set = set()
        self._last_curated_refresh = 0

    async def refresh_curated(self):
        if self.cfg.watch_addresses:
            return  # user supplied list; trust it
        if time.time() - self._last_curated_refresh < 3600:
            return
        # Use a sync httpx client inside smart_filter (it expects one).
        log.info("Refreshing curated wallet set...")
        top = await get_top_traders(self.http, window="7d", limit=50)
        candidates = [row["proxyWallet"] for row in top if row.get("profit", 0) > 0]
        with httpx.Client(timeout=15) as sync_http:
            stats = build_curated_set(sync_http, candidates)
        self.curated = {s.address for s in stats}
        log.info(f"Curated set size: {len(self.curated)} (from {len(candidates)} candidates)")
        self._last_curated_refresh = time.time()

    async def step(self):
        await self.refresh_curated()
        for addr in list(self.curated):
            try:
                acts = await self.http.get(
                    f"{DATA_API}/activity",
                    params={"user": addr, "limit": 5, "type": "TRADE"}
                )
                acts.raise_for_status()
                acts = acts.json()
            except Exception as e:
                log.warning(f"activity {addr[:8]}: {e}")
                continue
            for t in acts:
                tx = t.get("transactionHash")
                if not tx or tx in self.seen_tx:
                    continue
                self.seen_tx.add(tx)
                if time.time() - t.get("timestamp", 0) > 300:
                    continue
                token_id = t["asset"]
                side = BUY if t["side"].upper() == "BUY" else SELL
                if self.cfg.use_smart_filter:
                    sig = self.tracker.observe(wallet=addr, token_id=token_id,
                                               side=side, ts=int(t["timestamp"]))
                    if sig is None:
                        continue
                    log.info(f"AGREE {len(sig.wallets)} wallets -> {side} {token_id[:10]}")
                await self._maybe_trade(token_id, side)
        self.tracker.gc()

    async def _maybe_trade(self, token_id: str, side: str):
        if not self.pf.can_open(self.cfg.max_position_usd):
            return
        try:
            book = await get_orderbook(self.http, token_id)
        except Exception as e:
            log.warning(f"orderbook: {e}")
            return
        levels = book.get("asks" if side == BUY else "bids") or []
        if not levels:
            return
        best = float(levels[0]["price"])
        limit_price = best + (0.01 if side == BUY else -0.01)
        usd = self.cfg.max_position_usd
        shares = usd / max(limit_price, 0.01)
        sig_id = self.ledger.record_signal(
            strategy="copy", token_id=token_id, side=side,
            intended_price=limit_price, edge=None,
            meta={"book_best": best})
        place_limit(self.cfg, self.clob, self.ledger, strategy="copy",
                    token_id=token_id, side=side, price=limit_price,
                    size_shares=shares, signal_id=sig_id)
        self.pf.book(token_id, usd)


# ---------- Edge strategy ----------
class EdgeStrategy:
    def __init__(self, cfg, clob, http, portfolio, ledger):
        self.cfg, self.clob, self.http, self.pf, self.ledger = cfg, clob, http, portfolio, ledger

    def fair_value(self, market) -> Optional[float]:
        try:
            return float(market.get("lastTradePrice"))
        except (TypeError, ValueError):
            return None

    async def step(self):
        markets = await get_markets(self.http, limit=100)
        for m in markets:
            tokens = m.get("clobTokenIds") or []
            if not tokens:
                continue
            fv = self.fair_value(m)
            if fv is None:
                continue
            token_id = tokens[0]
            try:
                book = await get_orderbook(self.http, token_id)
            except Exception:
                continue
            if not book.get("bids") or not book.get("asks"):
                continue
            best_bid = float(book["bids"][0]["price"])
            best_ask = float(book["asks"][0]["price"])
            mid = (best_bid + best_ask) / 2
            edge = fv - mid
            sig_side = BUY if edge > 0 else SELL
            # Always log the evaluation so the dashboard can see passes too:
            if abs(edge) < self.cfg.min_edge:
                continue
            px = best_bid + 0.01 if sig_side == BUY else best_ask - 0.01
            usd = self.cfg.max_position_usd
            if not self.pf.can_open(usd):
                continue
            shares = usd / max(px, 0.01)
            sig_id = self.ledger.record_signal(
                strategy="edge", token_id=token_id, side=sig_side,
                intended_price=px, edge=edge,
                market_id=str(m.get("id")),
                meta={"fv": fv, "mid": mid})
            place_limit(self.cfg, self.clob, self.ledger, strategy="edge",
                        token_id=token_id, side=sig_side, price=px,
                        size_shares=shares, signal_id=sig_id)
            self.pf.book(token_id, usd)


# ---------- Runner ----------
async def main():
    cfg = Config(
        pk=os.environ["POLY_PK"],
        funder=os.environ["POLY_FUNDER"],
        dry_run=os.environ.get("DRY_RUN", "1") == "1",
        watch_addresses=[a for a in os.environ.get("WATCH", "").split(",") if a],
        use_smart_filter=os.environ.get("SMART_FILTER", "1") == "1",
    )
    clob = make_client(cfg)
    pf = Portfolio(cfg)
    ledger = Ledger(os.environ.get("LEDGER_PATH", "state/ledger.db"))
    async with httpx.AsyncClient(timeout=15) as http:
        copy = CopyTrader(cfg, clob, http, pf, ledger)
        edge = EdgeStrategy(cfg, clob, http, pf, ledger)
        while True:
            try:
                await copy.step()
                await edge.step()
            except Exception as e:
                log.exception(f"loop error: {e}")
            await asyncio.sleep(cfg.poll_seconds)


if __name__ == "__main__":
    asyncio.run(main())
