"""
FastAPI dashboard for the bot.

Endpoints:
  GET /api/positions       -> current net positions from the ledger
  GET /api/recent-fills    -> latest simulated/real fills
  GET /api/recent-signals  -> latest signal evaluations
  GET /api/pnl             -> realised P&L (resolved markets only)
  GET /api/markets         -> current Polymarket active markets
  GET /                    -> static dashboard page

Run:  uvicorn server:app --reload --port 8000
"""
import os
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import httpx

from ledger import Ledger

app = FastAPI(title="polymarket-bot dashboard")
ledger = Ledger(os.environ.get("LEDGER_PATH", "state/ledger.db"))

GAMMA_API = "https://gamma-api.polymarket.com"


@app.get("/api/positions")
def positions():
    return {"positions": ledger.open_positions()}


@app.get("/api/recent-fills")
def recent_fills(limit: int = 50):
    return {"fills": ledger.recent_fills(limit=limit)}


@app.get("/api/recent-signals")
def recent_signals(limit: int = 50):
    return {"signals": ledger.recent_signals(limit=limit)}


@app.get("/api/pnl")
def pnl():
    return ledger.realised_pnl()


@app.get("/api/markets")
async def markets(limit: int = 30):
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(f"{GAMMA_API}/markets",
                        params={"active": "true", "closed": "false",
                                "limit": limit, "order": "volume24hr",
                                "ascending": "false"})
        r.raise_for_status()
        data = r.json()
    # Trim to fields the dashboard renders
    out = []
    for m in data:
        out.append({
            "id": m.get("id"),
            "question": m.get("question"),
            "slug": m.get("slug"),
            "lastTradePrice": m.get("lastTradePrice"),
            "volume24hr": m.get("volume24hr"),
            "liquidity": m.get("liquidity"),
            "clobTokenIds": m.get("clobTokenIds"),
        })
    return {"markets": out}


# Serve static dashboard files from ./web
if os.path.isdir("web"):
    app.mount("/static", StaticFiles(directory="web"), name="static")


@app.get("/")
def index():
    return FileResponse("web/index.html")
