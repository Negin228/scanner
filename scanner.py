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
SCAN_INTERVAL_SECS = 300

# Advanced Tab Settings (Institutional Logic)
ACCOUNT_SIZE = 100000
KELLY_FRACTION = 0.25
TARGET_WIN_RATE = 0.85
REAL_FILL_SLIPPAGE = 0.02
TREND_FAST_MA = 20
TREND_SLOW_MA = 50
MIN_RSI = 40
MAX_RSI = 75
MIN_IVR = 30
EXPECTED_MOVE_MULTIPLIER = 1.0

HEADERS = {"Authorization": f"Bearer {TRADIER_API_KEY}", "Accept": "application/json"}
OUTPUT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "signals.json")

# ─────────────────────────────────────────────
# ADVANCED LOGIC FUNCTIONS
# ─────────────────────────────────────────────

def get_history(symbol, days=120):
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
    
    # RSI Calculation
    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0 for d in deltas[-14:]]
    losses = [abs(d) if d < 0 else 0 for d in deltas[-14:]]
    avg_gain = sum(gains) / 14
    avg_loss = sum(losses) / 14
    rsi = 100 - (100 / (1 + (avg_gain/avg_loss))) if avg_loss != 0 else 100

    return {"bullish": (price > ma_fast > ma_slow and MIN_RSI <= rsi <= MAX_RSI), "rsi": rsi}

def detect_market_regime(vix, spy_bullish):
    if vix >= 40: return "crisis"
    if vix >= 30: return "high_volatility"
    if spy_bullish and vix < 20: return "bull"
    if not spy_bullish and vix > 20: return "bear"
    return "neutral"

def calculate_kelly_qty(net_credit, max_loss):
    rr = net_credit / max_loss
    kelly = TARGET_WIN_RATE - ((1 - TARGET_WIN_RATE) / rr)
    adjusted_kelly = max(0, kelly * KELLY_FRACTION)
    risk_dollars = ACCOUNT_SIZE * adjusted_kelly
    return max(1, int(risk_dollars / (max_loss * 100)))

# ─────────────────────────────────────────────
# SPREAD FINDERS
# ─────────────────────────────────────────────

def find_advanced_spreads(symbol, price, puts, expiration, regime, trend):
    advanced_signals = []
    dte = (datetime.datetime.strptime(expiration, "%Y-%m-%d").date() - datetime.date.today()).days
    by_strike = {float(p["strike"]): p for p in puts}

    for strike, short in by_strike.items():
        long = by_strike.get(strike - SPREAD_WIDTH)
        if not long: continue

        delta, _, theta, iv = extract_greeks(short)
        if not delta or abs(delta) > 0.15: continue
        
        # Expected Move Filter
        exp_move = price * iv * math.sqrt(dte / 365)
        if (price - strike) < (exp_move * EXPECTED_MOVE_MULTIPLIER): continue

        # Realistic Fill
        net_credit = ((float(short['bid']) + float(short['ask']))/2) - \
                     ((float(long['bid']) + float(long['ask']))/2) - REAL_FILL_SLIPPAGE
        
        if net_credit < 0.15: continue
        
        max_loss = SPREAD_WIDTH - net_credit
        pot = abs(delta) * 2 # Probability of Touch
        
        # Institutional Scoring
        score = (net_credit/max_loss * 40) + (max(0, 0.25 - pot) * 80) + (min(abs(theta or 0)*10, 10))

        advanced_signals.append({
            "symbol": symbol, "expiration": expiration, "dte": dte,
            "short_strike": strike, "net_credit": round(net_credit, 2),
            "qty": calculate_kelly_qty(net_credit, max_loss),
            "score": round(score, 2), "regime": regime, "rsi": round(trend['rsi'], 1),
            "pot": round(pot * 100, 1)
        })
    return advanced_signals

# ─────────────────────────────────────────────
# CORE EXECUTION
# ─────────────────────────────────────────────

def run_combined_scan():
    pt_tz = timezone(timedelta(hours=-7))
    now = datetime.datetime.now(pt_tz)
    
    vix = get_quote("VIX")
    spy_price = get_quote("SPY")
    spy_trend = trend_analysis("SPY", spy_price)
    regime = detect_market_regime(vix, spy_trend["bullish"] if spy_trend else False)

    all_signals = []
    advanced_signals = []

    for symbol in SYMBOLS:
        price = get_quote(symbol)
        if not price: continue
        
        trend = trend_analysis(symbol, price)
        expirations = get_expirations(symbol)
        
        for exp in expirations:
            dte = (datetime.datetime.strptime(exp, "%Y-%m-%d").date() - datetime.date.today()).days
            if not (7 <= dte <= 45): continue
            
            puts = get_puts(symbol, exp)
            # Original Logic
            all_signals.extend(find_spreads(symbol, price, puts, exp))
            # Advanced Logic (Only if trend is bullish and not in crisis)
            if trend and trend["bullish"] and regime != "crisis":
                advanced_signals.extend(find_advanced_spreads(symbol, price, puts, exp, regime, trend))

    # Top 10 filter for the original logic
    top10 = sorted([s for s in all_signals if s.get("top10_eligible")], key=lambda x: x["top10_score"], reverse=True)[:10]

    output = {
        "last_updated": now.strftime("%Y-%m-%d %H:%M:%S"),
        "vix": vix,
        "market_regime": regime,
        "signals": all_signals,
        "top10": top10,
        "advanced": sorted(advanced_signals, key=lambda x: x["score"], reverse=True)
    }

    with open(OUTPUT_FILE, "w") as f:
        json.dump(output, f, indent=2)

# ─────────────────────────────────────────────
# EXECUTION
# ─────────────────────────────────────────────

if __name__ == "__main__":
    try:
        # We remove the while True loop. 
        # This function now runs ONCE and finishes.
        run_combined_scan()
        print("Success: JSON updated.")
    except Exception as e:
        print(f"Error: {e}")
        exit(1) # Tell GitHub the run failed
