"""
Put Credit Spread Scanner — DEBUG VERSION
Adds full diagnostic tracking to identify why signals = 0
"""

import requests
import datetime
import json
import os
import math
from datetime import timedelta, timezone

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────

BASE_URL = "https://api.tradier.com/v1"
TRADIER_API_KEY = os.getenv("TRADIER_API_KEY", "").strip()

DEBUG_MODE = True

SYMBOLS = ["NVDA", "AMZN", "MSFT", "META", "GOOG",
           "NFLX", "PLTR", "TSLA", "SPY", "TQQQ",
           "SQQQ", "AMD", "ORCL"]

SPREAD_WIDTH = 5
MIN_DISCOUNT_PCT = 0.20
MIN_OPEN_INTEREST = 100
MIN_VOLUME = 50
MIN_IV = 0.20
MIN_RETURN_ON_RISK = 0.20

OUTPUT_FILE = os.path.join(os.path.dirname(__file__), "signalskelly.json")

HEADERS = {
    "Authorization": f"Bearer {TRADIER_API_KEY}",
    "Accept": "application/json"
}

# ─────────────────────────────────────────────
# DEBUG TRACKING
# ─────────────────────────────────────────────

REJECT = {
    "no_puts": 0,
    "low_oi": 0,
    "low_vol": 0,
    "low_iv": 0,
    "high_delta": 0,
    "high_pot": 0,
    "expected_move": 0,
    "low_credit": 0,
    "low_return_on_risk": 0,
    "strike_filter": 0,
    "no_long_leg": 0,
    "other": 0,
    "passed": 0
}

STAGES = {
    "symbols_scanned": 0,
    "puts_found": 0,
    "strikes_checked": 0,
    "passed_all_filters": 0
}

SYMBOL_DEBUG = {}

def reject(reason, symbol=None):
    REJECT[reason] = REJECT.get(reason, 0) + 1

    if symbol:
        SYMBOL_DEBUG.setdefault(symbol, {})
        SYMBOL_DEBUG[symbol][reason] = SYMBOL_DEBUG[symbol].get(reason, 0) + 1


# ─────────────────────────────────────────────
# API HELPERS
# ─────────────────────────────────────────────

def get_quote(symbol):
    try:
        r = requests.get(
            f"{BASE_URL}/markets/quotes",
            headers=HEADERS,
            params={"symbols": symbol},
            timeout=10
        )
        r.raise_for_status()
        q = r.json().get("quotes", {}).get("quote", {})
        return float(q.get("last") or q.get("bid") or 0)
    except Exception as e:
        print(f"[QUOTE ERROR] {symbol}: {e}")
        return None


def get_expirations(symbol):
    try:
        r = requests.get(
            f"{BASE_URL}/markets/options/expirations",
            headers=HEADERS,
            params={"symbol": symbol},
            timeout=10
        )
        r.raise_for_status()
        d = r.json().get("expirations", {}).get("date", [])
        return [d] if isinstance(d, str) else (d or [])
    except Exception:
        return []


def get_puts(symbol, expiration):
    try:
        r = requests.get(
            f"{BASE_URL}/markets/options/chains",
            headers=HEADERS,
            params={"symbol": symbol, "expiration": expiration, "greeks": "true"},
            timeout=10
        )
        r.raise_for_status()
        opts = r.json().get("options", {}).get("option", [])
        if isinstance(opts, dict):
            opts = [opts]
        return [o for o in opts if o.get("option_type") == "put"]
    except Exception:
        return []


def extract_greeks(opt):
    g = opt.get("greeks") or {}
    return (
        float(g.get("delta")) if g.get("delta") else None,
        float(g.get("gamma")) if g.get("gamma") else None,
        float(g.get("theta")) if g.get("theta") else None,
        float(g.get("mid_iv") or g.get("ask_iv") or g.get("bid_iv") or 0) or None
    )


def days_to_expiry(exp):
    return (datetime.datetime.strptime(exp, "%Y-%m-%d").date()
            - datetime.date.today()).days


# ─────────────────────────────────────────────
# CORE SCANNER
# ─────────────────────────────────────────────

def find_spreads(symbol, price, puts, expiration):
    signals = []
    STAGES["puts_found"] += 1

    by_strike = {
        float(p["strike"]): p
        for p in puts if p.get("strike") is not None
    }

    if not by_strike:
        reject("no_puts", symbol)
        return []

    for strike, short in by_strike.items():

        STAGES["strikes_checked"] += 1

        if strike > price * (1 - MIN_DISCOUNT_PCT):
            reject("strike_filter", symbol)
            continue

        long = by_strike.get(strike - SPREAD_WIDTH)
        if not long:
            reject("no_long_leg", symbol)
            continue

        short_oi = int(short.get("open_interest") or 0)
        short_vol = int(short.get("volume") or 0)

        if short_oi < MIN_OPEN_INTEREST:
            reject("low_oi", symbol)
            continue

        if short_vol < MIN_VOLUME:
            reject("low_vol", symbol)
            continue

        delta, gamma, theta, iv = extract_greeks(short)

        if iv is None or iv < MIN_IV:
            reject("low_iv", symbol)
            continue

        short_bid = float(short.get("bid") or 0)
        short_ask = float(short.get("ask") or 0)
        long_bid = float(long.get("bid") or 0)
        long_ask = float(long.get("ask") or 0)

        net_credit = (short_bid + short_ask)/2 - (long_bid + long_ask)/2

        if net_credit < 0.15:
            reject("low_credit", symbol)
            continue

        max_loss = SPREAD_WIDTH - net_credit

        if max_loss <= 0:
            reject("other", symbol)
            continue

        return_on_risk = net_credit / max_loss

        if return_on_risk < MIN_RETURN_ON_RISK:
            reject("low_return_on_risk", symbol)
            continue

        REJECT["passed"] += 1
        STAGES["passed_all_filters"] += 1

        signals.append({
               "symbol":        symbol,
               "expiration":    expiration,
               "dte":           days_to_expiry(expiration),
               "short_strike":  strike,
               "long_strike":   strike - SPREAD_WIDTH,
               "current_price": round(price, 2),
               "otm_pct":       round((price - strike) / price * 100, 1),
               "net_credit":    round(net_credit, 2),
               "credit_pct":    round(net_credit / SPREAD_WIDTH * 100, 1),
               "breakeven":     round(strike - net_credit, 2),
               "iv_pct":        round((iv or 0) * 100, 1),
               "delta":         round(delta, 3) if delta else None,
               "theta":         round(theta, 3) if theta else None,
               "short_ba":      round(short_ask - short_bid, 3),
               "short_oi":      short_oi,
               "short_vol":     short_vol,
               "total_credit":  round(net_credit * 100 * 10, 2),   # 10 contracts
               "total_risk":    round(max_loss  * 100 * 10, 2),
               "score":         round(return_on_risk * 100 + (iv or 0) * 10, 1),})

    return signals


# ─────────────────────────────────────────────
# RUN SCAN
# ─────────────────────────────────────────────

def run_scan():

    all_signals = []

    for symbol in SYMBOLS:

        STAGES["symbols_scanned"] += 1

        price = get_quote(symbol)
        if not price:
            continue

        exps = get_expirations(symbol)

        for exp in exps:

            puts = get_puts(symbol, exp)
            if not puts:
                reject("no_puts", symbol)
                continue

            sigs = find_spreads(symbol, price, puts, exp)
            all_signals.extend(sigs)

    # ── OUTPUT ──

    output = {
        "signals": all_signals,
        "debug_rejects": REJECT,
        "debug_stages": STAGES,
        "debug_symbol": SYMBOL_DEBUG
    }

    with open(OUTPUT_FILE, "w") as f:
        json.dump(output, f, indent=2)

    # ── PRINT SUMMARY ──

    print("\n──────── DEBUG SUMMARY ────────")
    for k, v in REJECT.items():
        print(f"{k:25}: {v}")

    print("\n──────── STAGES ───────────────")
    for k, v in STAGES.items():
        print(f"{k:25}: {v}")

    print("\nTOTAL SIGNALS:", len(all_signals))


# ─────────────────────────────────────────────
# ENTRY
# ─────────────────────────────────────────────

if __name__ == "__main__":
    run_scan()
