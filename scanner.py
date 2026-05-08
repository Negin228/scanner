import requests
import datetime
import json
import time
import os
import math
from datetime import timedelta, timezone

# ─────────────────────────────────────────────
# CONFIGURATION & INSTITUTIONAL SETTINGS
# ─────────────────────────────────────────────
BASE_URL = "https://api.tradier.com/v1"
TRADIER_API_KEY = os.getenv("TRADIER_API_KEY")

SYMBOLS = ["NVDA", "AMZN", "MSFT", "META", "GOOG", "NFLX", "PLTR", "TSLA", "SPY", "AMD", "ORCL"]
# Standard benchmark for beta-weighting
BENCHMARK = "SPY"

# Risk Parameters
SPREAD_WIDTH        = 5
MIN_PROB_PROFIT     = 80.0  # Percentage
ADV_ACCOUNT_SIZE    = 100000
RISK_PER_TRADE_PCT  = 0.02  # 2% of account
SLIPPAGE_ADJUST     = 0.02  # Deduct from mid-price to simulate real fill

HEADERS = {
    "Authorization": f"Bearer {TRADIER_API_KEY}",
    "Accept": "application/json"
}

# ─────────────────────────────────────────────
# MATH & RISK ENGINES
# ─────────────────────────────────────────────

def calculate_ev(short_delta, net_credit, max_loss):
    """
    Calculates Expected Value: (P_win * Credit) - (P_loss * Max_Loss).
    We use Delta as a proxy for P_loss.
    """
    p_loss = abs(short_delta)
    p_win = 1 - p_loss
    ev = (p_win * net_credit) - (p_loss * max_loss)
    return round(ev, 2), round(p_win * 100, 1)

def get_beta_weighted_delta(price, benchmark_price, delta, qty, beta=1.2):
    """
    Normalizes risk to SPY. Formula: Position Delta * (Price / SPY_Price) * Beta
    """
    pos_delta = delta * qty * 100
    weighted = pos_delta * (price / benchmark_price) * beta
    return round(weighted, 2)

def calculate_correlation(hist1, hist2):
    """
    Simple Pearson Correlation between two lists of historical closes.
    """
    if not hist1 or not hist2 or len(hist1) != len(hist2):
        return 0
    n = len(hist1)
    mu1 = sum(hist1) / n
    mu2 = sum(hist2) / n
    ss1 = sum((x - mu1)**2 for x in hist1)
    ss2 = sum((x - mu2)**2 for x in hist2)
    sc = sum((hist1[i] - mu1) * (hist2[i] - mu2) for i in range(n))
    if ss1 * ss2 == 0: return 0
    return round(sc / math.sqrt(ss1 * ss2), 2)

# ─────────────────────────────────────────────
# DATA ACQUISITION (Tradier API Wrappers)
# ─────────────────────────────────────────────

def get_quote(symbol):
    url = f"{BASE_URL}/markets/quotes"
    try:
        r = requests.get(url, headers=HEADERS, params={"symbols": symbol, "greeks": "true"}, timeout=10)
        quote = r.json().get("quotes", {}).get("quote", {})
        return quote
    except: return None

def get_history(symbol, days=30):
    url = f"{BASE_URL}/markets/history"
    start = (datetime.date.today() - timedelta(days=days)).strftime("%Y-%m-%d")
    try:
        r = requests.get(url, headers=HEADERS, params={"symbol": symbol, "interval": "daily", "start": start}, timeout=10)
        history = r.json().get("history", {}).get("day", [])
        return [float(d["close"]) for d in history]
    except: return []

def get_puts(symbol, expiration):
    url = f"{BASE_URL}/markets/options/chains"
    try:
        r = requests.get(url, headers=HEADERS, params={"symbol": symbol, "expiration": expiration, "greeks": "true"}, timeout=10)
        return r.json().get("options", {}).get("option", [])
    except: return []

# ─────────────────────────────────────────────
# CORE SCANNER LOGIC
# ─────────────────────────────────────────────

def run_institutional_scan():
    print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] Initializing Institutional Scan...")
    
    # 1. Fetch Benchmark Data
    spy_quote = get_quote(BENCHMARK)
    spy_price = float(spy_quote.get("last", 500))
    spy_hist = get_history(BENCHMARK)
    
    all_signals = []
    
    for symbol in SYMBOLS:
        quote = get_quote(symbol)
        if not quote: continue
        
        price = float(quote.get("last"))
        hist = get_history(symbol)
        correlation_to_spy = calculate_correlation(hist, spy_hist)
        
        # Get expirations (skipping helper for brevity, assuming next monthly)
        # In production, loop through expirations 7-45 DTE
        exp = (datetime.date.today() + timedelta(days=30)).strftime("%Y-%m-%d")
        options = get_puts(symbol, exp)
        
        for opt in options:
            if opt.get("option_type") != "put": continue
            
            strike = float(opt["strike"])
            # Only look for Spreads: Find the 'Long' leg 5 points below
            long_leg = next((o for o in options if float(o["strike"]) == strike - SPREAD_WIDTH), None)
            
            if not long_leg: continue

            # Liquidity & Greek Check
            delta = float(opt.get("greeks", {}).get("delta", 0))
            if abs(delta) > 0.20: continue # Too close to money
            
            mid_credit = (float(opt["bid"]) + float(opt["ask"]))/2 - (float(long_leg["bid"]) + float(long_leg["ask"]))/2
            net_credit = mid_credit - SLIPPAGE_ADJUST
            max_loss = SPREAD_WIDTH - net_credit
            
            if net_credit < 0.15: continue
            
            # Institutional Metrics
            ev, pop = calculate_ev(delta, net_credit, max_loss)
            beta_delta = get_beta_weighted_delta(price, spy_price, delta, 1, beta=1.5) # Default beta 1.5

            if pop < MIN_PROB_PROFIT or ev <= 0: continue

            all_signals.append({
                "symbol": symbol,
                "strike": f"{strike}/{strike-SPREAD_WIDTH}",
                "net_credit": round(net_credit, 2),
                "ev": ev,
                "pop": f"{pop}%",
                "spy_delta": beta_delta,
                "correlation_spy": correlation_to_spy,
                "score": round(ev * correlation_to_spy, 2) # Penalize uncorrelated or low EV
            })

    # Sort by Expected Value
    all_signals.sort(key=lambda x: x["ev"], reverse=True)
    
    # Save to JSON
    output = {
        "timestamp": datetime.datetime.now().isoformat(),
        "spy_price": spy_price,
        "signals": all_signals[:20]
    }
    
    with open("institutional_signals.json", "w") as f:
        json.dump(output, f, indent=4)
    
    print(f"Scan Complete. {len(all_signals)} institutional signals identified.")

if __name__ == "__main__":
    run_institutional_scan()
