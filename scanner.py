import requests
import datetime
import json
import time
import math
import os
from datetime import timedelta, timezone

# ─────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────
BASE_URL = "https://api.tradier.com/v1"
TRADIER_API_KEY = os.getenv("TRADIER_API_KEY")

SYMBOLS = ["NVDA", "AMZN", "MSFT", "META", "GOOG", "NFLX", "PLTR", "TSLA", "SPY", "TQQQ", "SQQQ", "AMD", "ORCL"]

# Original Logic Settings (Powers "All Signals")
SPREAD_WIDTH = 5
MIN_DISCOUNT_PCT = 0.20
QUANTITY = 10
MIN_OPEN_INTEREST = 100
MIN_VOLUME = 50
MIN_NET_CREDIT = 0.10
MIN_IV = 0.20
MIN_DAYS_TO_EXPIRY = 7
MAX_DAYS_TO_EXPIRY = 45

# Top 10 tab criteria
TOP10_MIN_OI = 500
TOP10_MIN_VOLUME = 200
TOP10_MIN_CREDIT_PCT = 8.0
TOP10_MAX_DTE = 21
TOP10_MAX_DELTA = 0.10
TOP10_MAX_BA_SPREAD = 0.10

# Advanced Tab Settings
ACCOUNT_SIZE = 100000
KELLY_FRACTION = 0.25
TARGET_WIN_RATE = 0.85
REAL_FILL_SLIPPAGE = 0.02
TREND_FAST_MA = 20
TREND_SLOW_MA = 50

HEADERS = {"Authorization": f"Bearer {TRADIER_API_KEY}", "Accept": "application/json"}
OUTPUT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "signals.json")

# ─────────────────────────────────────────────
# API HELPERS
# ─────────────────────────────────────────────

def get_quote(symbol):
    url = f"{BASE_URL}/markets/quotes"
    params = {"symbols": symbol}
    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=10)
        quote = r.json().get("quotes", {}).get("quote", {})
        last = quote.get("last") or quote.get("bid")
        return float(last) if last else None
    except: return None

def get_expirations(symbol):
    url = f"{BASE_URL}/markets/options/expirations"
    params = {"symbol": symbol, "includeAllRoots": "true"}
    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=10)
        dates = r.json().get("expirations", {}).get("date", [])
        return [dates] if isinstance(dates, str) else dates
    except: return []

def get_puts(symbol, expiration):
    url = f"{BASE_URL}/markets/options/chains"
    params = {"symbol": symbol, "expiration": expiration, "greeks": "true"}
    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=10)
        options = r.json().get("options", {}).get("option", [])
        if isinstance(options, dict): options = [options]
        return [o for o in options if o.get("option_type") == "put"]
    except: return []

def extract_greeks(option):
    """Safely extract greeks even if the API returns null/None."""
    g = option.get("greeks")
    if not g or not isinstance(g, dict):
        return None, None, 0.0
    
    delta = g.get("delta")
    theta = g.get("theta")
    iv = g.get("mid_iv") or g.get("ask_iv") or g.get("bid_iv") or 0.0
    
    return (
        float(delta) if delta is not None else None,
        float(theta) if theta is not None else None,
        float(iv)
    )

# ─────────────────────────────────────────────
# ORIGINAL SPREAD FINDER (Powers "All Signals")
# ─────────────────────────────────────────────

def find_spreads(symbol, price, puts, expiration):
    max_short = price * (1 - MIN_DISCOUNT_PCT)
    by_strike = {float(p["strike"]): p for p in puts if p.get("strike") is not None}
    signals = []

    for strike, short in by_strike.items():
        if strike > max_short: continue
        long = by_strike.get(strike - SPREAD_WIDTH)
        if not long: continue

        short_oi = int(short.get("open_interest") or 0)
        if short_oi < MIN_OPEN_INTEREST: continue

        delta, theta, iv = extract_greeks(short)
        
        short_bid = float(short.get("bid") or 0)
        long_ask = float(long.get("ask") or 0)
        net_credit = short_bid - long_ask
        
        if net_credit < MIN_NET_CREDIT: continue

        dte = (datetime.datetime.strptime(expiration, "%Y-%m-%d").date() - datetime.date.today()).days
        credit_pct = (net_credit / SPREAD_WIDTH) * 100

        # Original Scoring Logic
        score = (credit_pct * 2) + (iv * 50) + min(short_oi / 1000, 5)
        
        # Top 10 Eligibility
        short_ba = float(short.get("ask") or 0) - short_bid
        top10_eligible = (
            short_oi >= TOP10_MIN_OI and 
            credit_pct >= TOP10_MIN_CREDIT_PCT and 
            dte <= TOP10_MAX_DTE and 
            short_ba <= TOP10_MAX_BA_SPREAD and
            (delta is None or abs(delta) <= TOP10_MAX_DELTA)
        )

        signals.append({
            "symbol": symbol, "expiration": expiration, "dte": dte,
            "short_strike": strike, "long_strike": strike - SPREAD_WIDTH,
            "current_price": round(price, 2), "net_credit": round(net_credit, 2),
            "score": round(score, 2), "top10_score": round(score * 1.2, 2),
            "top10_eligible": top10_eligible, "delta": round(delta, 4) if delta else None,
            "short_ba": round(short_ba, 3)
        })
    return signals

# ─────────────────────────────────────────────
# ADVANCED LOGIC (Powers "Advanced" Tab)
# ─────────────────────────────────────────────

def get_history(symbol):
    url = f"{BASE_URL}/markets/history"
    start = (datetime.date.today() - timedelta(days=100)).strftime("%Y-%m-%d")
    params = {"symbol": symbol, "interval": "daily", "start": start}
    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=10)
        return [float(d["close"]) for d in r.json().get("history", {}).get("day", [])]
    except: return []

def trend_analysis(symbol, price):
    closes = get_history(symbol)
    if len(closes) < TREND_SLOW_MA: return None
    ma_fast = sum(closes[-TREND_FAST_MA:]) / TREND_FAST_MA
    ma_slow = sum(closes[-TREND_SLOW_MA:]) / TREND_SLOW_MA
    return {"bullish": (price > ma_fast > ma_slow), "rsi": 55}

def find_advanced_spreads(symbol, price, puts, expiration, regime, trend):
    adv_signals = []
    dte = (datetime.datetime.strptime(expiration, "%Y-%m-%d").date() - datetime.date.today()).days
    by_strike = {float(p["strike"]): p for p in puts}

    for strike, short in by_strike.items():
        long = by_strike.get(strike - SPREAD_WIDTH)
        if not long: continue
        
        delta, theta, iv = extract_greeks(short)
        if delta is None or abs(delta) > 0.15: continue

        net_credit = ((float(short['bid']) + float(short['ask']))/2) - \
                     ((float(long['bid']) + float(long['ask']))/2) - REAL_FILL_SLIPPAGE
        if net_credit < 0.15: continue

        pot = round(abs(delta) * 200, 1) 
        score = (net_credit / (SPREAD_WIDTH - net_credit) * 40) + (max(0, 25 - pot/4) * 2)

        adv_signals.append({
            "symbol": symbol, "expiration": expiration, "dte": dte,
            "short_strike": strike, "net_credit": round(net_credit, 2),
            "qty": max(1, int(ACCOUNT_SIZE * 0.01 / (SPREAD_WIDTH * 100))),
            "score": round(score, 2), "pot": pot, "rsi": trend['rsi'], "iv_rank": 35
        })
    return adv_signals

# ─────────────────────────────────────────────
# MAIN EXECUTION
# ─────────────────────────────────────────────

def run_combined_scan():
    print("Starting combined scan...")
    vix = get_quote("VIX") or 18.0
    spy_price = get_quote("SPY")
    spy_trend = trend_analysis("SPY", spy_price) if spy_price else None
    regime = "bull" if (spy_trend and spy_trend["bullish"] and vix < 30) else "neutral"

    all_signals = []
    advanced_signals = []

    for symbol in SYMBOLS:
        print(f"Scanning {symbol}...")
        price = get_quote(symbol)
        if not price: continue
        
        trend = trend_analysis(symbol, price)
        expirations = get_expirations(symbol)
        
        for exp in (expirations or []):
            dte = (datetime.datetime.strptime(exp, "%Y-%m-%d").date() - datetime.date.today()).days
            if not (MIN_DAYS_TO_EXPIRY <= dte <= MAX_DAYS_TO_EXPIRY): continue
            
            puts = get_puts(symbol, exp)
            if not puts: continue

            # Populate All Signals Tab
            all_signals.extend(find_spreads(symbol, price, puts, exp))

            # Populate Advanced Tab
            if trend and trend["bullish"] and regime != "crisis":
                advanced_signals.extend(find_advanced_spreads(symbol, price, puts, exp, regime, trend))

    top10 = sorted([s for s in all_signals if s["top10_eligible"]], key=lambda x: x["top10_score"], reverse=True)[:10]

    output = {
        "last_updated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "vix": vix,
        "market_regime": regime,
        "signals": all_signals,
        "top10": top10,
        "advanced": sorted(advanced_signals, key=lambda x: x["score"], reverse=True)
    }

    with open(OUTPUT_FILE, "w") as f:
        json.dump(output, f, indent=2)
    print(f"Scan complete. {len(all_signals)} signals saved.")

if __name__ == "__main__":
    run_combined_scan()
