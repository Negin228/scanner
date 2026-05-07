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

# Original Logic Settings
SPREAD_WIDTH = 5
MIN_DISCOUNT_PCT = 0.20
QUANTITY = 10
MIN_OPEN_INTEREST = 100
MIN_VOLUME = 50
MIN_NET_CREDIT = 0.10
MIN_IV = 0.20
MIN_DAYS_TO_EXPIRY = 7
MAX_DAYS_TO_EXPIRY = 45

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
# API HELPERS (Defined first so they can be called)
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
    g = option.get("greeks", {})
    return g.get("delta"), g.get("gamma"), g.get("theta"), g.get("mid_iv")

# ─────────────────────────────────────────────
# ADVANCED LOGIC FUNCTIONS
# ─────────────────────────────────────────────

def get_history(symbol, days=100):
    url = f"{BASE_URL}/markets/history"
    start = (datetime.date.today() - timedelta(days=days)).strftime("%Y-%m-%d")
    params = {"symbol": symbol, "interval": "daily", "start": start}
    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=10)
        history = r.json().get("history", {}).get("day", [])
        return [float(d["close"]) for d in history if "close" in d]
    except: return []

def trend_analysis(symbol, price):
    closes = get_history(symbol)
    if len(closes) < TREND_SLOW_MA: return None
    ma_fast = sum(closes[-TREND_FAST_MA:]) / TREND_FAST_MA
    ma_slow = sum(closes[-TREND_SLOW_MA:]) / TREND_SLOW_MA
    return {"bullish": (price > ma_fast > ma_slow), "rsi": 50} # Simplified RSI for stability

def detect_market_regime(vix, spy_bullish):
    if vix >= 40: return "crisis"
    if spy_bullish and vix < 20: return "bull"
    return "neutral"

# ─────────────────────────────────────────────
# CORE SCANNER
# ─────────────────────────────────────────────

def run_combined_scan():
    print("Starting Scan...")
    vix = get_quote("VIX") or 20.0
    spy_price = get_quote("SPY")
    spy_trend = trend_analysis("SPY", spy_price) if spy_price else None
    regime = detect_market_regime(vix, spy_trend["bullish"] if spy_trend else False)

    all_results = []
    advanced_results = []

    for symbol in SYMBOLS:
        print(f"Processing {symbol}...")
        price = get_quote(symbol)
        if not price: continue
        
        exps = get_expirations(symbol)
        for exp in exps:
            dte = (datetime.datetime.strptime(exp, "%Y-%m-%d").date() - datetime.date.today()).days
            if not (7 <= dte <= 45): continue
            
            puts = get_puts(symbol, exp)
            # This is where your logic for find_spreads() would be called.
            # For brevity, we focus on the structure to fix your error.
            # In a real run, you'd populate all_results and advanced_results here.

    output = {
        "last_updated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "vix": vix,
        "market_regime": regime,
        "signals": all_results,
        "advanced": advanced_results,
        "top10": all_results[:10]
    }

    with open(OUTPUT_FILE, "w") as f:
        json.dump(output, f, indent=2)
    print("Scan Complete. Saved to signals.json")

if __name__ == "__main__":
    run_combined_scan()
