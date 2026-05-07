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
EXPECTED_MOVE_MULTIPLIER = 1.0

HEADERS = {"Authorization": f"Bearer {TRADIER_API_KEY}", "Accept": "application/json"}
OUTPUT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "signals.json")

# ─────────────────────────────────────────────
# API HELPERS (With Timeouts & Rate Limit Handling)
# ─────────────────────────────────────────────

def get_quote(symbol):
    url = f"{BASE_URL}/markets/quotes"
    params = {"symbols": symbol}
    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=5)
        if r.status_code == 429:
            time.sleep(10)
            return None
        r.raise_for_status()
        quote = r.json().get("quotes", {}).get("quote", {})
        time.sleep(0.1) # Brief pause to respect rate limits
        last = quote.get("last") or quote.get("bid")
        return float(last) if last else None
    except Exception as e:
        print(f"  [!] Quote error for {symbol}: {e}", flush=True)
        return None

def get_expirations(symbol):
    url = f"{BASE_URL}/markets/options/expirations"
    params = {"symbol": symbol, "includeAllRoots": "true"}
    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=10)
        if r.status_code == 429:
            time.sleep(10)
            return []
        dates = r.json().get("expirations", {}).get("date", [])
        return [dates] if isinstance(dates, str) else dates
    except Exception as e:
        print(f"  [!] Expiration error for {symbol}: {e}", flush=True)
        return []

def get_puts(symbol, expiration):
    url = f"{BASE_URL}/markets/options/chains"
    params = {"symbol": symbol, "expiration": expiration, "greeks": "true"}
    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=10)
        if r.status_code == 429:
            print("  [!] Rate limited! Sleeping 10s...", flush=True)
            time.sleep(10)
            return []
        options = r.json().get("options", {}).get("option", [])
        if isinstance(options, dict): options = [options]
        return [o for o in options if o.get("option_type") == "put"]
    except Exception as e:
        print(f"  [!] Chain error for {symbol} ({expiration}): {e}", flush=True)
        return []

def get_history(symbol, days=120):
    url = f"{BASE_URL}/markets/history"
    start = (datetime.date.today() - timedelta(days=days)).strftime("%Y-%m-%d")
    params = {"symbol": symbol, "interval": "daily", "start": start}
    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=10)
        return [float(d["close"]) for d in r.json().get("history", {}).get("day", []) if "close" in d]
    except Exception as e:
        return []

# ─────────────────────────────────────────────
# DATA SAFETY HELPERS
# ─────────────────────────────────────────────

def safe_float(val, default=0.0):
    """Helper to safely convert API strings/None to float."""
    try:
        if val is None: return default
        return float(val)
    except (ValueError, TypeError):
        return default

def extract_greeks(option):
    """Safely extract greeks, preventing NoneType crashes."""
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
# LOGIC ENGINES
# ─────────────────────────────────────────────

def trend_analysis(symbol, price):
    closes = get_history(symbol)
    if len(closes) < TREND_SLOW_MA: return None
    ma_fast = sum(closes[-TREND_FAST_MA:]) / TREND_FAST_MA
    ma_slow = sum(closes[-TREND_SLOW_MA:]) / TREND_SLOW_MA
    
    # Calculate simple RSI
    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains = [d if d > 0 else 0 for d in deltas[-14:]]
    losses = [abs(d) if d < 0 else 0 for d in deltas[-14:]]
    avg_gain = sum(gains) / 14 if len(gains) >= 14 else 0
    avg_loss = sum(losses) / 14 if len(losses) >= 14 else 0
    rsi = 100 - (100 / (1 + (avg_gain/avg_loss))) if avg_loss != 0 else 100

    return {"bullish": (price > ma_fast > ma_slow), "rsi": round(rsi, 1)}

def find_spreads(symbol, price, puts, expiration):
    """Original Logic for All Signals and Top 10."""
    max_short = price * (1 - MIN_DISCOUNT_PCT)
    by_strike = {float(p["strike"]): p for p in puts if p.get("strike") is not None}
    signals = []

    for strike, short in by_strike.items():
        if strike > max_short: continue
        long = by_strike.get(strike - SPREAD_WIDTH)
        if not long: continue

        short_bid = safe_float(short.get("bid"))
        long_ask = safe_float(long.get("ask"))
        net_credit = short_bid - long_ask
        
        if net_credit < MIN_NET_CREDIT: continue

        short_oi = int(short.get("open_interest") or 0)
        if short_oi < MIN_OPEN_INTEREST: continue

        delta, theta, iv = extract_greeks(short)
        dte = (datetime.datetime.strptime(expiration, "%Y-%m-%d").date() - datetime.date.today()).days
        credit_pct = (net_credit / SPREAD_WIDTH) * 100
        score = (credit_pct * 2) + (iv * 50) + min(short_oi / 1000, 5)
        
        short_ba = safe_float(short.get("ask")) - short_bid
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

def find_advanced_spreads(symbol, price, puts, expiration, trend):
    """Advanced Logic using Institutional Scoring."""
    adv_signals = []
    dte = (datetime.datetime.strptime(expiration, "%Y-%m-%d").date() - datetime.date.today()).days
    by_strike = {float(p["strike"]): p for p in puts}

    for strike, short in by_strike.items():
        long = by_strike.get(strike - SPREAD_WIDTH)
        if not long: continue
        
        s_bid, s_ask = safe_float(short.get('bid')), safe_float(short.get('ask'))
        l_bid, l_ask = safe_float(long.get('bid')), safe_float(long.get('ask'))
        
        # Skip if completely illiquid
        if s_bid == 0 or s_ask == 0: continue 

        mid_short = (s_bid + s_ask) / 2
        mid_long = (l_bid + l_ask) / 2
        net_credit = mid_short - mid_long - REAL_FILL_SLIPPAGE
        
        if net_credit < 0.15: continue

        delta, theta, iv = extract_greeks(short)
        if delta is None or abs(delta) > 0.15: continue

        # Expected Move Filter
        exp_move = price * iv * math.sqrt(dte / 365)
        if (price - strike) < (exp_move * EXPECTED_MOVE_MULTIPLIER): continue

        max_loss = SPREAD_WIDTH - net_credit
        pot = abs(delta) * 2 
        
        # Scoring & Kelly Sizing
        score = (net_credit / max_loss * 40) + (max(0, 0.25 - pot) * 80) + (min(abs(theta or 0)*10, 10))
        
        rr = net_credit / max_loss
        kelly = TARGET_WIN_RATE - ((1 - TARGET_WIN_RATE) / rr) if rr > 0 else 0
        qty = max(1, int((ACCOUNT_SIZE * max(0, kelly * KELLY_FRACTION)) / (max_loss * 100)))

        adv_signals.append({
            "symbol": symbol, "expiration": expiration, "dte": dte,
            "short_strike": strike, "net_credit": round(net_credit, 2),
            "qty": qty, "score": round(score, 2), 
            "pot": round(pot * 100, 1), "rsi": trend['rsi'], "iv_rank": 50
        })
    return adv_signals

# ─────────────────────────────────────────────
# MAIN EXECUTION
# ─────────────────────────────────────────────

def run_combined_scan():
    print(f"Starting combined scan at {datetime.datetime.now(timezone.utc)}...", flush=True)
    
    vix = get_quote("VIX") or 18.0
    spy_price = get_quote("SPY")
    spy_trend = trend_analysis("SPY", spy_price) if spy_price else None
    
    if vix >= 40: regime = "crisis"
    elif spy_trend and spy_trend["bullish"] and vix < 20: regime = "bull"
    elif not spy_trend and vix > 20: regime = "bear"
    else: regime = "neutral"

    all_signals = []
    advanced_signals = []

    for symbol in SYMBOLS:
        print(f"Scanning {symbol}...", flush=True)
        price = get_quote(symbol)
        if not price: continue
        
        trend = trend_analysis(symbol, price)
        expirations = get_expirations(symbol)
        
        for exp in (expirations or []):
            try:
                dte = (datetime.datetime.strptime(exp, "%Y-%m-%d").date() - datetime.date.today()).days
                if not (MIN_DAYS_TO_EXPIRY <= dte <= MAX_DAYS_TO_EXPIRY): continue
                
                puts = get_puts(symbol, exp)
                if not puts: continue

                # 1. Standard Logic
                all_signals.extend(find_spreads(symbol, price, puts, exp))

                # 2. Advanced Logic
                if trend and trend["bullish"] and regime != "crisis":
                    advanced_signals.extend(find_advanced_spreads(symbol, price, puts, exp, trend))
            
            except Exception as e:
                print(f"  [!] Skipping exp {exp} for {symbol}: {e}", flush=True)

    top10 = sorted([s for s in all_signals if s.get("top10_eligible")], key=lambda x: x["top10_score"], reverse=True)[:10]

    output = {
        "last_updated": datetime.datetime.now(timezone(timedelta(hours=-7))).strftime("%Y-%m-%d %H:%M:%S PT"),
        "vix": round(vix, 2),
        "market_regime": regime,
        "signals": sorted(all_signals, key=lambda x: x["score"], reverse=True),
        "top10": top10,
        "advanced": sorted(advanced_signals, key=lambda x: x["score"], reverse=True)
    }

    with open(OUTPUT_FILE, "w") as f:
        json.dump(output, f, indent=2)
        
    print(f"Scan complete. {len(all_signals)} total signals saved.", flush=True)

if __name__ == "__main__":
    try:
        run_combined_scan()
    except Exception as e:
        print(f"FATAL ERROR: {e}", flush=True)
        exit(1)
