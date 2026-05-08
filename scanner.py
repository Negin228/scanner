"""
Put Credit Spread Scanner — Background Service (CLEAN REWRITE)

Fixes:
- New tab now properly broader (not constrained by MIN_DISCOUNT_PCT logic)
- Cleaner architecture
- Reduced duplicate filtering confusion
"""

import requests
import datetime as dt
import json
import os
import math
from datetime import timedelta, timezone

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────

BASE_URL = "https://api.tradier.com/v1"
TRADIER_API_KEY = os.getenv("TRADIER_API_KEY")

SYMBOLS = ["NVDA", "AMZN", "MSFT", "META", "GOOG", "NFLX",
           "PLTR", "TSLA", "SPY", "TQQQ", "SQQQ", "AMD", "ORCL"]

HEADERS = {
    "Authorization": f"Bearer {TRADIER_API_KEY}",
    "Accept": "application/json"
}

OUTPUT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "signalskelly.json")

# ─────────────────────────────────────────────
# CORE PARAMETERS
# ─────────────────────────────────────────────

SPREAD_WIDTH = 5
QUANTITY = 10

MIN_OI = 100
MIN_VOL = 50
MIN_CREDIT = 0.10
MIN_IV = 0.20

MIN_DTE = 7
MAX_DTE = 45

# OLD FILTER (All tab structure)
OTM_DISCOUNT = 0.20

# NEW TAB (FIXED — broader discovery)
NEW_MAX_DELTA = 0.15
NEW_MAX_OTM = 0.95   # allows near-ATM trades (IMPORTANT FIX)

# TOP 10 (execution quality)
TOP_OI = 500
TOP_VOL = 200
TOP_CREDIT_PCT = 8.0
TOP_MAX_DTE = 21
TOP_MAX_DELTA = 0.10
TOP_MAX_BA = 0.10


# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def get_quote(symbol):
    try:
        r = requests.get(
            f"{BASE_URL}/markets/quotes",
            headers=HEADERS,
            params={"symbols": symbol},
            timeout=10
        )
        q = r.json()["quotes"]["quote"]
        last = q.get("last") or q.get("bid")
        return float(last) if last else None
    except:
        return None


def get_expirations(symbol):
    try:
        r = requests.get(
            f"{BASE_URL}/markets/options/expirations",
            headers=HEADERS,
            params={"symbol": symbol},
            timeout=10
        )
        data = r.json()["expirations"]["date"]
        return data if isinstance(data, list) else [data]
    except:
        return []


def get_puts(symbol, exp):
    try:
        r = requests.get(
            f"{BASE_URL}/markets/options/chains",
            headers=HEADERS,
            params={"symbol": symbol, "expiration": exp, "greeks": "true"},
            timeout=10
        )
        opts = r.json()["options"]["option"]
        return [o for o in opts if o["option_type"] == "put"]
    except:
        return []


def dte(exp):
    exp = dt.datetime.strptime(exp, "%Y-%m-%d").date()
    return (exp - dt.date.today()).days


def greeks(opt):
    g = opt.get("greeks") or {}
    return (
        g.get("delta"),
        g.get("gamma"),
        g.get("theta"),
        g.get("mid_iv")
    )


# ─────────────────────────────────────────────
# ALL STRATEGY SCAN
# ─────────────────────────────────────────────

def scan_all(symbol, price, puts, exp):
    signals = []

    max_strike = price * (1 - OTM_DISCOUNT)
    by_strike = {float(p["strike"]): p for p in puts if p.get("strike")}

    for strike, short in by_strike.items():

        if strike > max_strike:
            continue

        long = by_strike.get(strike - SPREAD_WIDTH)
        if not long:
            continue

        delta, _, _, iv = greeks(short)
        if iv is None:
            iv = 0.0

        if iv < MIN_IV or delta is None:
            continue

        short_bid = float(short.get("bid") or 0)
        long_ask = float(long.get("ask") or 0)

        credit = short_bid - long_ask
        if credit < MIN_CREDIT:
            continue

        dte_val = dte(exp)

        signals.append({
            "symbol": symbol,
            "expiration": exp,
            "dte": dte_val,
            "strike": strike,
            "credit": round(credit, 2),
            "delta": round(delta, 4),
            "iv": float(iv),
            "score": credit * 100 + iv * 10
        })

    return signals


# ─────────────────────────────────────────────
# NEW TAB (FIXED — broader + true discovery)
# ─────────────────────────────────────────────

def scan_new(symbol, price, puts, exp):
    signals = []

    by_strike = {float(p["strike"]): p for p in puts if p.get("strike")}

    for strike, short in by_strike.items():

        delta, _, _, iv = greeks(short)
        if delta is None:
            continue

        # ✅ PRIMARY FILTER = DELTA ONLY
        if abs(delta) > NEW_MAX_DELTA:
            continue

        # ✅ FIX: allow broader OTM range (THIS WAS THE BUG)
        if strike > price * NEW_MAX_OTM:
            continue

        long = by_strike.get(strike - SPREAD_WIDTH)
        if not long:
            continue

        short_bid = float(short.get("bid") or 0)
        long_ask = float(long.get("ask") or 0)

        credit = short_bid - long_ask
        if credit < MIN_CREDIT:
            continue

        signals.append({
            "symbol": symbol,
            "expiration": exp,
            "strike": strike,
            "credit": round(credit, 2),
            "delta": round(delta, 4),
            "iv": float(iv or 0),
            "score": (0.15 - abs(delta)) * 100 + credit * 50
        })

    return signals


# ─────────────────────────────────────────────
# MAIN SCAN
# ─────────────────────────────────────────────

def run_scan():

    now = dt.datetime.now(timezone.utc)
    timestamp = now.strftime("%Y-%m-%d %H:%M:%S")

    all_signals = []
    new_signals = []

    for symbol in SYMBOLS:

        price = get_quote(symbol)
        if not price:
            continue

        exps = get_expirations(symbol)

        for exp in exps:
            if not (MIN_DTE <= dte(exp) <= MAX_DTE):
                continue

            puts = get_puts(symbol, exp)

            if not puts:
                continue

            all_signals.extend(scan_all(symbol, price, puts, exp))
            new_signals.extend(scan_new(symbol, price, puts, exp))

    # sort
    all_signals.sort(key=lambda x: x["score"], reverse=True)
    new_signals.sort(key=lambda x: x["score"], reverse=True)

    output = {
        "last_updated": timestamp,
        "signals": all_signals,
        "new_signals": new_signals
    }

    with open(OUTPUT_FILE, "w") as f:
        json.dump(output, f, indent=2)

    print(f"Done → All: {len(all_signals)} | New: {len(new_signals)}")


# ─────────────────────────────────────────────
# RUN
# ─────────────────────────────────────────────

if __name__ == "__main__":
    run_scan()
