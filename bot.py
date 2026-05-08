"""
Polymarket trading bot.

Two strategies:
  - copy_trader: mirrors recent fills from "smart money" wallets
  - edge: places limit orders when fair value diverges from book mid
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


def make_client(cfg: Config) -> ClobClient:
    c = ClobClient(CLOB_HOST, key=cfg.pk, chain_id=cfg.chain_id,
                   signature_type=2, funder=cfg.funder)
    c.set_api_creds(c.create_or_derive_api_creds())
    return c


# ---------- Market data helpers ----------
async def get_markets(client: httpx.AsyncClient, limit: int = 200):
    r = await client.get(f"{GAMMA_API}/markets",
                         params={"active": "true", "closed": "false", "limit": limit})
    r.raise_for_status()
    return r.json()


async def get_orderbook(client: httpx.AsyncClient, token_id: str):
    r = await client.get(f"{CLOB_HOST}/book", params={"token_id": token_id})
    r.raise_for_status()
    return r.json()


async def get_top_traders(client: httpx.AsyncClient, window: str = "1d", limit: int = 25):
    r = await client.get(f"{DATA_API}/leaderboard",
                         params={"window": window, "limit": limit, "sortBy": "profit"})
    r.raise_for_status()
    return r.json()


async def get_user_activity(client: httpx.AsyncClient, address: str, limit: int = 20):
    r = await client.get(f"{DATA_API}/activity",
                         params={"user": address, "limit": limit, "type": "TRADE"})
    r.raise_for_status()
    return r.json()


# ---------- Risk / sizing ----------
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
def place_limit(cfg: Config, client: ClobClient, token_id: str,
                side: str, price: float, size_shares: float):
    price = round(max(0.01, min(0.99, price)), 2)
    if cfg.dry_run:
        log.info(f"[DRY] {side} {size_shares:.2f} @ {price} on {token_id[:10]}...")
        return {"dry_run": True}
    args = OrderArgs(price=price, size=size_shares, side=side, token_id=token_id)
    signed = client.create_order(args)
    return client.post_order(signed, OrderType.GTC)


# ---------- Strategy: Copy-trader ----------
class CopyTrader:
    def __init__(self, cfg, clob, http, portfolio):
        self.cfg, self.clob, self.http, self.pf = cfg, clob, http, portfolio
        self.watch = set(a.lower() for a in cfg.watch_addresses)
        self.seen_tx: set = set()

    async def refresh_watchlist(self):
        if self.watch:
            return
        top = await get_top_traders(self.http, window="7d", limit=15)
        self.watch = {row["proxyWallet"].lower() for row in top
                      if row.get("profit", 0) > 0}
        log.info(f"Watching {len(self.watch)} top wallets")

    async def step(self):
        await self.refresh_watchlist()
        for addr in list(self.watch):
            try:
                acts = await get_user_activity(self.http, addr, limit=10)
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
                usd = min(self.cfg.max_position_usd,
                          float(t.get("usdcSize", 0)) * 0.01)
                if usd <= 0 or not self.pf.can_open(usd):
                    continue
                try:
                    book = await get_orderbook(self.http, token_id)
                except Exception as e:
                    log.warning(f"orderbook: {e}")
                    continue
                lvl = book["asks"] if side == BUY else book["bids"]
                if not lvl:
                    continue
                best = float(lvl[0]["price"])
                limit_price = best + (0.01 if side == BUY else -0.01)
                shares = usd / max(limit_price, 0.01)
                place_limit(self.cfg, self.clob, token_id, side, limit_price, shares)
                self.pf.book(token_id, usd)


# ---------- Strategy: Edge / fair-value ----------
class EdgeStrategy:
    def __init__(self, cfg, clob, http, portfolio):
        self.cfg, self.clob, self.http, self.pf = cfg, clob, http, portfolio

    def fair_value(self, market) -> Optional[float]:
        # Replace with your own model.
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
            if abs(edge) < self.cfg.min_edge:
                continue
            side = BUY if edge > 0 else SELL
            px = best_bid + 0.01 if side == BUY else best_ask - 0.01
            usd = self.cfg.max_position_usd
            if not self.pf.can_open(usd):
                continue
            shares = usd / max(px, 0.01)
            place_limit(self.cfg, self.clob, token_id, side, px, shares)
            self.pf.book(token_id, usd)


# ---------- Runner ----------
async def main():
    cfg = Config(
        pk=os.environ["POLY_PK"],
        funder=os.environ["POLY_FUNDER"],
        dry_run=os.environ.get("DRY_RUN", "1") == "1",
        watch_addresses=[a for a in os.environ.get("WATCH", "").split(",") if a],
    )
    clob = make_client(cfg)
    pf = Portfolio(cfg)
    async with httpx.AsyncClient(timeout=15) as http:
        copy = CopyTrader(cfg, clob, http, pf)
        edge = EdgeStrategy(cfg, clob, http, pf)
        while True:
            try:
                await copy.step()
                await edge.step()
            except Exception as e:
                log.exception(f"loop error: {e}")
            await asyncio.sleep(cfg.poll_seconds)


if __name__ == "__main__":
    asyncio.run(main())
