#ChatGPT fixing Kelly adv = 1 for all, but very tight filters 
"""
Put Credit Spread Scanner — Debug-Clean Version
- Finds put credit spreads via Tradier
- Tracks rejection bottlenecks
- Outputs signals.json for dashboard use
"""

import os
import json
import datetime
import requests
from datetime import timedelta
from zoneinfo import ZoneInfo

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────

BASE_URL = "https://api.tradier.com/v1"
API_KEY = os.getenv("TRADIER_API_KEY")

SYMBOLS = ["NVDA", "AMZN", "MSFT", "META", "GOOG", "NFLX", "PLTR", "TSLA", "SPY", "TQQQ", "SQQQ"]

SESSION = requests.Session()
SESSION.headers.update({
    "Authorization": f"Bearer {API_KEY}",
    "Accept": "application/json"
})

SPREAD_WIDTH = 5

MIN_DTE = 7
MAX_DTE = 45

MIN_OI = 100
MIN_VOL = 50
MIN_CREDIT = 0.10
MIN_IV = 0.20

OUTPUT_FILE = "signals.json"

# ─────────────────────────────────────────────
# DEBUG TRACKING
# ─────────────────────────────────────────────

REJECT_REASONS = {
    "no_puts": 0,
    "no_long_leg": 0,
    "low_oi": 0,
    "low_vol": 0,
    "low_iv": 0,
    "low_credit": 0
}

def reject(reason):
    if reason in REJECT_REASONS:
        REJECT_REASONS[reason] += 1


# ─────────────────────────────────────────────
# API HELPERS
# ─────────────────────────────────────────────

def get(url, params=None):
    try:
        r = SESSION.get(url, params=params, timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print("API error:", e)
        return {}

def get_quote(symbol):
    data = get(f"{BASE_URL}/markets/quotes", {"symbols": symbol})
    q = data.get("quotes", {}).get("quote", {})
    price = q.get("last") or q.get("bid")
    return float(price) if price else None

def get_expirations(symbol):
    data = get(f"{BASE_URL}/markets/options/expirations", {
        "symbol": symbol,
        "includeAllRoots": "true"
    })
    exp = data.get("expirations", {}).get("date", [])
    return exp if isinstance(exp, list) else [exp]

def get_chain(symbol, expiration):
    data = get(f"{BASE_URL}/markets/options/chains", {
        "symbol": symbol,
        "expiration": expiration,
        "greeks": "true"
    })
    opts = data.get("options", {}).get("option", [])
    if isinstance(opts, dict):
        opts = [opts]
    return opts


def days_to_expiry(exp):
    return (datetime.datetime.strptime(exp, "%Y-%m-%d").date()
            - datetime.date.today()).days


def extract(option):
    g = option.get("greeks") or {}
    return (
        float(g.get("delta") or 0),
        float(g.get("gamma") or 0),
        float(g.get("theta") or 0),
        float(g.get("iv") or option.get("implied_volatility") or 0)
    )


# ─────────────────────────────────────────────
# SPREAD ENGINE
# ─────────────────────────────────────────────

def find_spreads(symbol, price, puts, exp):

    strikes = {
        float(p["strike"]): p
        for p in puts if p.get("strike")
    }

    results = []

    for short_strike, short in strikes.items():

        # ── find long leg ──
        long_strike = short_strike - SPREAD_WIDTH
        long = strikes.get(long_strike)

        if not long:
            reject("no_long_leg")
            continue

        # ── liquidity filters ──
        oi = int(short.get("open_interest") or 0)
        vol = int(short.get("volume") or 0)

        if oi < MIN_OI:
            reject("low_oi")
            continue

        if vol < MIN_VOL:
            reject("low_vol")
            continue

        # ── greeks ──
        delta, gamma, theta, iv = extract(short)

        if iv < MIN_IV:
            reject("low_iv")
            continue

        # ── pricing ──
        bid_s = float(short.get("bid") or 0)
        ask_s = float(short.get("ask") or 0)
        bid_l = float(long.get("bid") or 0)
        ask_l = float(long.get("ask") or 0)

        credit = bid_s - ask_l

        if credit < MIN_CREDIT:
            reject("low_credit")
            continue

        dte = days_to_expiry(exp)
        credit_pct = (credit / SPREAD_WIDTH) * 100

        score = (
            credit_pct * 2
            + iv * 50
            + oi / 100
            + vol / 50
        )

        results.append({
            "symbol": symbol,
            "expiration": exp,
            "dte": dte,
            "short_strike": short_strike,
            "long_strike": long_strike,
            "price": price,
            "credit": round(credit, 2),
            "credit_pct": round(credit_pct, 1),
            "iv": round(iv * 100, 1),
            "delta": delta,
            "theta": theta,
            "oi": oi,
            "vol": vol,
            "score": round(score, 2)
        })

    return results


# ─────────────────────────────────────────────
# ENGINE
# ─────────────────────────────────────────────

def run_scan():

    tz = ZoneInfo("America/Los_Angeles")
    now = datetime.datetime.now(tz)

    all_signals = []

    print(f"\nScan started: {now}")

    for symbol in SYMBOLS:

        price = get_quote(symbol)
        if not price:
            continue

        exps = get_expirations(symbol)

        for exp in exps:

            dte = days_to_expiry(exp)

            if dte < MIN_DTE or dte > MAX_DTE:
                continue

            puts = get_chain(symbol, exp)

            # ── FIXED DEBUG LOGIC ──
            if not puts:
                reject("no_puts")
                continue

            spreads = find_spreads(symbol, price, puts, exp)
            all_signals.extend(spreads)

        print(f"{symbol}: {price}")

    # sort results
    all_signals.sort(key=lambda x: x["score"], reverse=True)

    output = {
        "timestamp": now.isoformat(),
        "total_signals": len(all_signals),
        "signals": all_signals[:200]
    }

    with open(OUTPUT_FILE, "w") as f:
        json.dump(output, f, indent=2)

    print(f"\nDone → {len(all_signals)} signals")

    # ── DEBUG OUTPUT ──
    print("\n──────── REJECTION BREAKDOWN ────────")
    for k, v in REJECT_REASONS.items():
        print(f"{k:15}: {v}")


# ─────────────────────────────────────────────
# RUN
# ─────────────────────────────────────────────

if __name__ == "__main__":
    run_scan()
