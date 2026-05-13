"""
Seed the ledger with realistic-looking demo data so the dashboard has
something to display before you have run the bot for real.

Run:  python seed_demo.py
Then: uvicorn server:app --port 8000  and open http://localhost:8000
"""
import random
import time
from ledger import Ledger

random.seed(42)

MARKETS = [
    ("0x11aa" + "0" * 60, "Will BTC close above 100k on Dec 31?", 0.62),
    ("0x22bb" + "0" * 60, "Fed cuts rates at next meeting?", 0.41),
    ("0x33cc" + "0" * 60, "Lakers make the playoffs?", 0.78),
    ("0x44dd" + "0" * 60, "Movie X opens above $80M?", 0.33),
    ("0x55ee" + "0" * 60, "AI lab Y releases model in Q4?", 0.55),
]


def main():
    ledger = Ledger("state/ledger.db")
    now = int(time.time())
    # Generate ~40 signals across the last 6 hours, ~70% become fills
    for i in range(40):
        token_id, _q, mid = random.choice(MARKETS)
        strategy = random.choice(["copy", "copy", "edge"])  # copy a bit more frequent
        side = random.choice(["BUY", "SELL"])
        ts_offset = random.randint(0, 6 * 3600)
        # Simulate intended price and edge
        edge = round(random.uniform(0.02, 0.08) * (1 if side == "BUY" else -1), 3)
        intended = round(max(0.02, min(0.98, mid - edge / 2)), 3)
        # Insert with a back-dated timestamp by temporarily monkey-patching time.time? Simpler:
        # we just call record_signal/fill with the default ts=now, but space them by inserting
        # raw rows for realism.
        with ledger._conn() as c:
            cur = c.execute(
                "INSERT INTO signals(ts, strategy, market_id, token_id, side, intended_price, edge, meta) VALUES(?,?,?,?,?,?,?,?)",
                (now - ts_offset, strategy, None, token_id, side, intended,
                 edge, "{}"),
            )
            sig_id = cur.lastrowid
            if random.random() < 0.7:
                # Simulated fill at slightly worse price
                fill_px = round(intended + (0.01 if side == "BUY" else -0.01), 3)
                shares = round(25.0 / fill_px, 2)
                c.execute(
                    "INSERT INTO fills(ts, signal_id, strategy, token_id, side, price, size_shares, usd, dry_run, order_id) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (now - ts_offset + 2, sig_id, strategy, token_id, side,
                     fill_px, shares, fill_px * shares, 1, None),
                )
    # Resolve one market YES (price=1) and one NO (price=0) to show realised PnL
    ledger.record_resolution(MARKETS[0][0], 1.0)
    ledger.record_resolution(MARKETS[3][0], 0.0)
    print("Seeded demo ledger at state/ledger.db")
    print("Open positions:", len(ledger.open_positions()))
    print("Realised PnL:  ", ledger.realised_pnl())


if __name__ == "__main__":
    main()
