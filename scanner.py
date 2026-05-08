"""
Put Credit Spread Scanner — Background Service

Output fields match dashboard.html expectations exactly:
  signals[]        → symbol, expiration, dte, short_strike, long_strike, current_price,
                      otm_pct, net_credit, credit_pct, breakeven, iv_pct, delta, theta,
                      short_ba, short_oi, short_vol, total_credit, total_risk, score
  new_signals[]    → symbol, expiration, strike, credit, delta, iv, score
  top10[]          → same shape as signals[] (subset with tighter filters)
  tickers{}        → price, signal_count, top10_count per symbol
"""

import requests
import datetime as dt
import json
import os
from datetime import timezone

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

SPREAD_WIDTH   = 5
QUANTITY       = 10

MIN_OI         = 100
MIN_VOL        = 50
MIN_CREDIT     = 0.10
MIN_IV         = 0.20   # as decimal (0.20 = 20%)

MIN_DTE        = 7
MAX_DTE        = 45

# All Signals tab — short strike must be ≥20% OTM
OTM_DISCOUNT   = 0.20

# New tab — broader, delta-only filter
NEW_MAX_DELTA  = 0.15
NEW_MAX_OTM    = 0.95   # strike ≤ 95% of price

# Top 10 — tighter execution quality filters
TOP_OI         = 500
TOP_VOL        = 200
TOP_CREDIT_PCT = 8.0
TOP_MAX_DTE    = 21
TOP_MAX_DELTA  = 0.10
TOP_MAX_BA     = 0.10


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


def calc_dte(exp_str):
    exp = dt.datetime.strptime(exp_str, "%Y-%m-%d").date()
    return (exp - dt.date.today()).days


def get_greeks(opt):
    g = opt.get("greeks") or {}
    return (
        g.get("delta"),   # negative for puts
        g.get("gamma"),
        g.get("theta"),
        g.get("mid_iv")   # decimal e.g. 0.45 = 45%
    )


def midpoint(opt):
    bid = float(opt.get("bid") or 0)
    ask = float(opt.get("ask") or 0)
    return (bid + ask) / 2 if (bid + ask) > 0 else 0


def ba_spread(opt):
    bid = float(opt.get("bid") or 0)
    ask = float(opt.get("ask") or 0)
    return round(ask - bid, 3) if ask > bid else None


# ─────────────────────────────────────────────
# ALL SIGNALS SCAN
# Outputs fields matching dashboard "All Signals" table
# ─────────────────────────────────────────────

def scan_all(symbol, price, puts, exp, dte_val):
    signals = []

    max_strike = price * (1 - OTM_DISCOUNT)
    by_strike = {float(p["strike"]): p for p in puts if p.get("strike")}

    for short_strike, short in by_strike.items():

        # Must be ≥20% OTM
        if short_strike > max_strike:
            continue

        long_strike = short_strike - SPREAD_WIDTH
        long = by_strike.get(long_strike)
        if not long:
            continue

        delta, _, theta, iv = get_greeks(short)
        if iv is None or delta is None:
            continue
        if iv < MIN_IV:
            continue

        short_bid  = float(short.get("bid") or 0)
        long_ask   = float(long.get("ask") or 0)
        net_credit = short_bid - long_ask

        if net_credit < MIN_CREDIT:
            continue

        short_oi  = int(short.get("open_interest") or 0)
        short_vol = int(short.get("volume") or 0)

        if short_oi < MIN_OI or short_vol < MIN_VOL:
            continue

        credit_pct   = (net_credit / SPREAD_WIDTH) * 100          # % of width
        otm_pct      = round((1 - short_strike / price) * 100, 1) # % below current price
        breakeven    = round(short_strike - net_credit, 2)
        total_credit = round(net_credit * QUANTITY * 100, 2)
        total_risk   = round((SPREAD_WIDTH - net_credit) * QUANTITY * 100, 2)
        iv_pct       = round(iv * 100, 1)                          # convert decimal → %

        score = (net_credit * 100) + (iv * 10) + (1 / max(abs(delta), 0.001) * 0.1)

        signals.append({
            "symbol":       symbol,
            "expiration":   exp,
            "dte":          dte_val,
            "short_strike": short_strike,
            "long_strike":  long_strike,
            "current_price": round(price, 2),
            "otm_pct":      otm_pct,
            "net_credit":   round(net_credit, 2),
            "credit_pct":   round(credit_pct, 1),
            "breakeven":    breakeven,
            "iv_pct":       iv_pct,
            "delta":        round(delta, 4),
            "theta":        round(theta, 4) if theta else None,
            "short_ba":     ba_spread(short),
            "short_oi":     short_oi,
            "short_vol":    short_vol,
            "total_credit": total_credit,
            "total_risk":   total_risk,
            "score":        round(score, 2),
        })

    return signals


# ─────────────────────────────────────────────
# NEW SIGNALS SCAN
# Broader discovery: delta-only filter, no OTM% floor
# ─────────────────────────────────────────────

def scan_new(symbol, price, puts, exp, dte_val):
    signals = []

    by_strike = {float(p["strike"]): p for p in puts if p.get("strike")}

    for short_strike, short in by_strike.items():

        delta, _, _, iv = get_greeks(short)
        if delta is None:
            continue

        # Primary filter: delta only
        if abs(delta) > NEW_MAX_DELTA:
            continue

        # Broad OTM: strike must be ≤ 95% of price
        if short_strike > price * NEW_MAX_OTM:
            continue

        long_strike = short_strike - SPREAD_WIDTH
        long = by_strike.get(long_strike)
        if not long:
            continue

        short_bid  = float(short.get("bid") or 0)
        long_ask   = float(long.get("ask") or 0)
        credit     = round(short_bid - long_ask, 2)

        if credit < MIN_CREDIT:
            continue

        score = (NEW_MAX_DELTA - abs(delta)) * 100 + credit * 50

        signals.append({
            "symbol":     symbol,
            "expiration": exp,
            "dte":        dte_val,
            "strike":     short_strike,
            "credit":     credit,
            "delta":      round(delta, 4),
            "iv":         round(iv * 100, 1) if iv else 0.0,   # store as % for display
            "score":      round(score, 2),
        })

    return signals


# ─────────────────────────────────────────────
# BUILD TOP 10 FROM ALL SIGNALS
# ─────────────────────────────────────────────

def build_top10(all_signals):
    candidates = []
    for s in all_signals:
        if s["short_oi"] < TOP_OI:
            continue
        if s["short_vol"] < TOP_VOL:
            continue
        if s["credit_pct"] < TOP_CREDIT_PCT:
            continue
        if s["dte"] > TOP_MAX_DTE:
            continue
        if s["delta"] is not None and abs(s["delta"]) > TOP_MAX_DELTA:
            continue
        if s["short_ba"] is not None and s["short_ba"] > TOP_MAX_BA:
            continue
        candidates.append(s)
    candidates.sort(key=lambda x: x["score"], reverse=True)
    return candidates[:10]


# ─────────────────────────────────────────────
# BUILD TICKER SUMMARY
# ─────────────────────────────────────────────

def build_tickers(prices, all_signals, top10):
    top10_syms = {}
    for s in top10:
        top10_syms[s["symbol"]] = top10_syms.get(s["symbol"], 0) + 1

    sig_counts = {}
    for s in all_signals:
        sig_counts[s["symbol"]] = sig_counts.get(s["symbol"], 0) + 1

    tickers = {}
    for sym, price in prices.items():
        tickers[sym] = {
            "price":        round(price, 2),
            "signal_count": sig_counts.get(sym, 0),
            "top10_count":  top10_syms.get(sym, 0),
        }
    return tickers


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

def run_scan():
    now = dt.datetime.now(timezone.utc)
    timestamp = now.strftime("%Y-%m-%d %H:%M:%S UTC")

    all_signals = []
    new_signals = []
    prices      = {}

    for symbol in SYMBOLS:
        print(f"Scanning {symbol}…")

        price = get_quote(symbol)
        if not price:
            print(f"  ✗ no quote")
            continue

        prices[symbol] = price
        exps = get_expirations(symbol)

        for exp in exps:
            dte_val = calc_dte(exp)
            if not (MIN_DTE <= dte_val <= MAX_DTE):
                continue

            puts = get_puts(symbol, exp)
            if not puts:
                continue

            all_signals.extend(scan_all(symbol, price, puts, exp, dte_val))
            new_signals.extend(scan_new(symbol, price, puts, exp, dte_val))

    # Sort by score descending
    all_signals.sort(key=lambda x: x["score"], reverse=True)
    new_signals.sort(key=lambda x: x["score"], reverse=True)

    top10   = build_top10(all_signals)
    tickers = build_tickers(prices, all_signals, top10)

    output = {
        "last_updated":    timestamp,
        "next_scan_secs":  300,
        "signals":         all_signals,
        "new_signals":     new_signals,
        "top10":           top10,
        "tickers":         tickers,
        # advanced_signals left empty — not implemented in this version
        "advanced_signals": [],
        "config": {
            "spread_width":       SPREAD_WIDTH,
            "min_otm_pct":        int(OTM_DISCOUNT * 100),
            "quantity":           QUANTITY,
            "min_oi":             MIN_OI,
            "min_volume":         MIN_VOL,
            "dte_range":          [MIN_DTE, MAX_DTE],
            "top10_min_oi":       TOP_OI,
            "top10_min_vol":      TOP_VOL,
            "top10_min_credit_pct": TOP_CREDIT_PCT,
            "top10_max_delta":    TOP_MAX_DELTA,
        }
    }

    with open(OUTPUT_FILE, "w") as f:
        json.dump(output, f, indent=2)

    print(f"\n✓ Done → All: {len(all_signals)} | New: {len(new_signals)} | Top10: {len(top10)}")
    print(f"  Written to: {OUTPUT_FILE}")


if __name__ == "__main__":
    run_scan()
