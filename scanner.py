

"""
Put Credit Spread Scanner — Background Service
Polls Tradier API every 5 minutes and writes results to signalskelly.json
Open dashboard.html in your browser to view live results.
"""

import requests
import datetime
import json
import time
import os
import math
import datetime
from datetime import timedelta, timezone

# ─────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────
BASE_URL = "https://api.tradier.com/v1"
# Sandbox: "https://sandbox.tradier.com/v1"
TRADIER_API_KEY = os.getenv("TRADIER_API_KEY")

SYMBOLS = ["NVDA", "AMZN", "MSFT", "META", "GOOG", "NFLX", "PLTR", "TSLA", "SPY", "TQQQ", "SQQQ", "AMD", "ORCL"]
#SYMBOLS = ["GOOGL", "SPY", "TQQQ", "SQQQ", "SOXL", "GOOG", "AMZN", "AAPL", "MSFT", "META", "NVDA", "TSLA", "AVGO", "ASML", "TSM", "ORCL", "CRM", "ADBE", "NFLX", "INTU", "AMD", "QCOM", "TXN", "AMAT", "LRCX", "MU", "PANW", "SNPS", "BRK-B", "JPM", "V", "MA", "BAC", "WFC", "MS", "GS", "BLK", "AXP", "UNH", "JNJ", "LLY", "ABBV", "MRK", "PFE", "TMO", "DHR", "ABT", "ISRG", "XOM", "CVX", "COP", "PG", "KO", "PEP", "COST", "WMT", "HD", "MCD", "NKE", "SBUX", "LOW", "LIN", "HON", "UPS", "CAT", "GE", "RTX", "BA", "NEE", "DUK", "SO", "PLD", "AMT", "TMUS", "VZ", "UBER", "SHOP", "MELI", "PDD", "IBKR", "KKR", "BX", "APO", "CMCSA", "DIS", "DELL", "PLTR", "CRWD", "ARM", "MRVL"]

# ── New tab criteria ─────────────────────────
NEW_MAX_DELTA = 0.15

SPREAD_WIDTH       = 5
MIN_DISCOUNT_PCT   = 0.20
QUANTITY           = 10
MIN_OPEN_INTEREST  = 100
MIN_VOLUME         = 50
MIN_NET_CREDIT     = 0.10
MIN_IV             = 0.20
MIN_DAYS_TO_EXPIRY = 7
MAX_DAYS_TO_EXPIRY = 45
SCAN_INTERVAL_SECS = 300   # 5 minutes

# ── Top 10 tab criteria ──────────────────────
# These stricter filters identify the best spreads
# for fast $0.01 decay exit strategy
TOP10_MIN_OI         = 500    # high liquidity
TOP10_MIN_VOLUME     = 200    # active trading today
TOP10_MIN_CREDIT_PCT = 8.0    # at least 8% of spread width as credit
TOP10_MAX_DTE        = 21     # 7-21 DTE sweet spot for fast theta decay
TOP10_MAX_DELTA      = 0.10   # short put delta <= 0.10 (far OTM, safer)
TOP10_MAX_BA_SPREAD  = 0.10   # tight bid/ask so you can close quickly

OUTPUT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "signalskelly.json")

HEADERS = {
    "Authorization": f"Bearer {TRADIER_API_KEY}",
    "Accept": "application/json"
}


# ─────────────────────────────────────────────
# API HELPERS
# ─────────────────────────────────────────────

def get_quote(symbol):
    url = f"{BASE_URL}/markets/quotes"
    params = {"symbols": symbol, "greeks": "false"}
    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=10)
        r.raise_for_status()
        quote = r.json().get("quotes", {}).get("quote", {})
        last = quote.get("last") or quote.get("bid")
        return float(last) if last else None
    except Exception as e:
        print(f"  [ERROR] Quote {symbol}: {e}")
        return None


def get_expirations(symbol):
    url = f"{BASE_URL}/markets/options/expirations"
    params = {"symbol": symbol, "includeAllRoots": "true", "strikes": "false"}
    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=10)
        r.raise_for_status()
        dates = r.json().get("expirations", {}).get("date", [])
        return [dates] if isinstance(dates, str) else (dates or [])
    except Exception as e:
        print(f"  [ERROR] Expirations {symbol}: {e}")
        return []


def get_puts(symbol, expiration):
    url = f"{BASE_URL}/markets/options/chains"
    params = {"symbol": symbol, "expiration": expiration, "greeks": "true"}
    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=10)
        r.raise_for_status()
        options = r.json().get("options", {}).get("option", [])
        if isinstance(options, dict):
            options = [options]
        return [o for o in options if o.get("option_type") == "put"]
    except Exception as e:
        print(f"  [ERROR] Chain {symbol} {expiration}: {e}")
        return []


def days_to_expiry(exp_str):
    # Use datetime.datetime because we imported the module
    exp = datetime.datetime.strptime(exp_str, "%Y-%m-%d").date()
    return (exp - datetime.date.today()).days


def extract_greeks(option):
    """Safely pull delta, gamma, theta, IV from a contract's greeks block."""
    greeks = option.get("greeks")
    if not greeks or not isinstance(greeks, dict):
        return None, None, None, None
    delta = greeks.get("delta")
    gamma = greeks.get("gamma")
    theta = greeks.get("theta")
    iv    = greeks.get("mid_iv") or greeks.get("ask_iv") or greeks.get("bid_iv")
    return (
        float(delta) if delta is not None else None,
        float(gamma) if gamma is not None else None,
        float(theta) if theta is not None else None,
        float(iv)    if iv    is not None else None,
    )


# ─────────────────────────────────────────────
# SPREAD FINDER
# ─────────────────────────────────────────────

def find_spreads(symbol, price, puts, expiration):
    max_short = price * (1 - MIN_DISCOUNT_PCT)
    by_strike = {float(p["strike"]): p for p in puts if p.get("strike") is not None}
    signals = []

    for strike, short in by_strike.items():
        if strike > max_short:
            continue
        long = by_strike.get(strike - SPREAD_WIDTH)
        if long is None:
            continue

        short_oi  = int(short.get("open_interest") or 0)
        short_vol = int(short.get("volume") or 0)
        long_oi   = int(long.get("open_interest") or 0)
        long_vol  = int(long.get("volume") or 0)

        if short_oi < MIN_OPEN_INTEREST or long_oi < MIN_OPEN_INTEREST:
            continue
        if short_vol < MIN_VOLUME or long_vol < MIN_VOLUME:
            continue

        delta, gamma, theta, iv = extract_greeks(short)
        if iv is None:
            iv = short.get("implied_volatility")
        iv = float(iv) if iv is not None else 0.0
        if iv < MIN_IV:
            continue

        short_bid = float(short.get("bid") or 0)
        short_ask = float(short.get("ask") or 0)
        long_bid  = float(long.get("bid")  or 0)
        long_ask  = float(long.get("ask")  or 0)

        net_credit     = short_bid - long_ask
        short_ba_spread = short_ask - short_bid

        if net_credit < MIN_NET_CREDIT:
            continue

        max_loss   = SPREAD_WIDTH - net_credit
        dte        = days_to_expiry(expiration)
        credit_pct = (net_credit / SPREAD_WIDTH) * 100

        # ── General score (all-signals tab) ──
        score = (
            credit_pct * 2
            + iv * 100 * 0.5
            + min(short_oi / 1000, 5)
            + min(short_vol / 500, 3)
            + max(0, (30 - abs(dte - 21)) / 30) * 3
        )

        # ── Top 10 score ──
        # Rewards tight spreads (easy to close), high credit%,
        # low DTE (fast theta), low delta (far OTM safety), high OI
        liq_score    = max(0, (TOP10_MAX_BA_SPREAD - short_ba_spread) / TOP10_MAX_BA_SPREAD) * 30
        credit_score = min(credit_pct / 20.0, 1.0) * 25
        dte_score    = (max(0, (TOP10_MAX_DTE - dte) / TOP10_MAX_DTE) * 25) if dte <= TOP10_MAX_DTE else 0
        delta_score  = (max(0, (TOP10_MAX_DELTA - abs(delta)) / TOP10_MAX_DELTA) * 20) if delta is not None else 0
        oi_score     = min(short_oi / 2000.0, 1.0) * 10
        top10_score  = liq_score + credit_score + dte_score + delta_score + oi_score

        # ── Top 10 eligibility ──
        top10_eligible = (
            short_oi  >= TOP10_MIN_OI
            and short_vol >= TOP10_MIN_VOLUME
            and credit_pct >= TOP10_MIN_CREDIT_PCT
            and dte <= TOP10_MAX_DTE
            and short_ba_spread <= TOP10_MAX_BA_SPREAD
            and (delta is None or abs(delta) <= TOP10_MAX_DELTA)
        )

        signals.append({
            "symbol":         symbol,
            "expiration":     expiration,
            "dte":            dte,
            "short_strike":   strike,
            "long_strike":    strike - SPREAD_WIDTH,
            "current_price":  round(price, 2),
            "otm_pct":        round((1 - strike / price) * 100, 1),
            "short_bid":      round(short_bid, 2),
            "short_ask":      round(short_ask, 2),
            "long_bid":       round(long_bid, 2),
            "long_ask":       round(long_ask, 2),
            "short_ba":       round(short_ba_spread, 3),
            "net_credit":     round(net_credit, 2),
            "max_loss":       round(max_loss, 2),
            "credit_pct":     round(credit_pct, 1),
            "breakeven":      round(strike - net_credit, 2),
            "iv_pct":         round(iv * 100, 1),
            "delta":          round(delta, 4) if delta is not None else None,
            "gamma":          round(gamma, 4) if gamma is not None else None,
            "theta":          round(theta, 4) if theta is not None else None,
            "short_oi":       short_oi,
            "short_vol":      short_vol,
            "long_oi":        long_oi,
            "long_vol":       long_vol,
            "qty":            QUANTITY,
            "total_credit":   round(net_credit * QUANTITY * 100, 0),
            "total_risk":     round(max_loss   * QUANTITY * 100, 0),
            "score":          round(score, 2),
            "top10_score":    round(top10_score, 2),
            "top10_eligible": top10_eligible,
        })

    signals.sort(key=lambda x: x["score"], reverse=True)
    return signals

# ─────────────────────────────────────────────
# NEW TAB SCANNER
# Uses delta instead of MIN_DISCOUNT_PCT
# ─────────────────────────────────────────────

def find_new_spreads(symbol, price, puts, expiration):

    by_strike = {
        float(p["strike"]): p
        for p in puts
        if p.get("strike") is not None
    }

    signals = []

    for strike, short in by_strike.items():

        # ── NEW FILTER ───────────────────────
        delta, gamma, theta, iv = extract_greeks(short)

        if delta is None:
            continue

        if abs(delta) > NEW_MAX_DELTA:
            continue

        # ─────────────────────────────────────

        long = by_strike.get(strike - SPREAD_WIDTH)

        if long is None:
            continue

        short_oi  = int(short.get("open_interest") or 0)
        short_vol = int(short.get("volume") or 0)
        long_oi   = int(long.get("open_interest") or 0)
        long_vol  = int(long.get("volume") or 0)

        if short_oi < MIN_OPEN_INTEREST or long_oi < MIN_OPEN_INTEREST:
            continue

        if short_vol < MIN_VOLUME or long_vol < MIN_VOLUME:
            continue

        if iv is None:
            iv = short.get("implied_volatility")

        iv = float(iv) if iv is not None else 0.0

        if iv < MIN_IV:
            continue

        short_bid = float(short.get("bid") or 0)
        short_ask = float(short.get("ask") or 0)

        long_bid = float(long.get("bid") or 0)
        long_ask = float(long.get("ask") or 0)

        net_credit = short_bid - long_ask

        if net_credit < MIN_NET_CREDIT:
            continue

        short_ba_spread = short_ask - short_bid

        max_loss   = SPREAD_WIDTH - net_credit
        dte        = days_to_expiry(expiration)
        credit_pct = (net_credit / SPREAD_WIDTH) * 100

        score = (
            credit_pct * 2
            + iv * 100 * 0.5
            + min(short_oi / 1000, 5)
            + min(short_vol / 500, 3)
            + (NEW_MAX_DELTA - abs(delta)) * 100
        )

        signals.append({
            "symbol":         symbol,
            "expiration":     expiration,
            "dte":            dte,
            "short_strike":   strike,
            "long_strike":    strike - SPREAD_WIDTH,
            "current_price":  round(price, 2),

            "otm_pct": round((1 - strike / price) * 100, 1),

            "short_bid": round(short_bid, 2),
            "short_ask": round(short_ask, 2),

            "long_bid": round(long_bid, 2),
            "long_ask": round(long_ask, 2),

            "short_ba": round(short_ba_spread, 3),

            "net_credit": round(net_credit, 2),
            "max_loss": round(max_loss, 2),

            "credit_pct": round(credit_pct, 1),

            "breakeven": round(strike - net_credit, 2),

            "iv_pct": round(iv * 100, 1),

            "delta": round(delta, 4) if delta is not None else None,
            "gamma": round(gamma, 4) if gamma is not None else None,
            "theta": round(theta, 4) if theta is not None else None,

            "short_oi": short_oi,
            "short_vol": short_vol,

            "long_oi": long_oi,
            "long_vol": long_vol,

            "qty": QUANTITY,

            "total_credit": round(net_credit * QUANTITY * 100, 0),
            "total_risk": round(max_loss * QUANTITY * 100, 0),

            "score": round(score, 2),
        })

    signals.sort(key=lambda x: x["score"], reverse=True)

    return signals

# ─────────────────────────────────────────────
# ADVANCED SCANNER — CONFIG + HELPERS
# ─────────────────────────────────────────────

ADV_MAX_DELTA            = 0.10
ADV_MAX_PROBABILITY_TOUCH = 0.25
ADV_EXPECTED_MOVE_MULT   = 1.0
ADV_REAL_FILL_SLIPPAGE   = 0.02
ADV_MIN_CREDIT           = 0.15
ADV_TOP10_MAX_BA         = 0.10

ADV_ACCOUNT_SIZE   = 100_000
ADV_KELLY_FRACTION = 0.25
ADV_TARGET_WIN_RATE = 0.85

ADV_TREND_FAST_MA = 20
ADV_TREND_SLOW_MA = 50
ADV_MIN_RSI = 40
ADV_MAX_RSI = 75
ADV_MIN_IVR = 30

ADV_VIX_LOW    = 15
ADV_VIX_HIGH   = 30
ADV_VIX_CRISIS = 40


def adv_moving_average(values, period):
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def adv_calculate_rsi(closes, period=14):
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, period + 1):
        diff = closes[-i] - closes[-i - 1]
        if diff >= 0:
            gains.append(diff); losses.append(0)
        else:
            gains.append(0); losses.append(abs(diff))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def adv_expected_move(price, iv, dte):
    return price * iv * math.sqrt(dte / 365)


def adv_probability_of_touch(delta):
    if delta is None:
        return None
    return min(abs(delta) * 2, 1.0)


def adv_calculate_iv_rank(current_iv, iv_history):
    if not iv_history:
        return 50
    low, high = min(iv_history), max(iv_history)
    if high == low:
        return 50
    return ((current_iv - low) / (high - low)) * 100


def adv_kelly_size(win_rate, credit, max_loss):
    # Standard Kelly goes deeply negative for credit spreads (high win rate,
    # low reward-to-risk ratio), so we use fixed-fractional sizing instead:
    # risk a base 2% of account per signal, scaled by win-rate confidence.
    if max_loss <= 0:
        return 1
    risk_per_contract = max_loss * 100
    base_risk_pct     = 0.02
    win_rate_scalar   = win_rate / ADV_TARGET_WIN_RATE
    risk_capital      = ADV_ACCOUNT_SIZE * base_risk_pct * win_rate_scalar
    contracts         = int(risk_capital / risk_per_contract)
    return max(1, contracts)


def adv_get_history(symbol, days=120):
    url = f"{BASE_URL}/markets/history"
    end = datetime.date.today()
    start = end - timedelta(days=days)
    try:
        r = requests.get(url, headers=HEADERS, params={
            "symbol": symbol, "interval": "daily",
            "start": start.strftime("%Y-%m-%d"),
            "end":   end.strftime("%Y-%m-%d")
        }, timeout=15)
        r.raise_for_status()
        history = r.json().get("history", {}).get("day", [])
        return [float(d["close"]) for d in history if d.get("close")]
    except Exception as e:
        print(f"  [ADV] History {symbol}: {e}")
        return []


def adv_trend_analysis(symbol, price):
    closes = adv_get_history(symbol)
    if len(closes) < ADV_TREND_SLOW_MA:
        return None
    ma_fast = adv_moving_average(closes, ADV_TREND_FAST_MA)
    ma_slow = adv_moving_average(closes, ADV_TREND_SLOW_MA)
    rsi = adv_calculate_rsi(closes)
    bullish = (price > ma_fast and ma_fast > ma_slow
               and ADV_MIN_RSI <= rsi <= ADV_MAX_RSI)
    return {"bullish": bullish, "ma_fast": ma_fast, "ma_slow": ma_slow, "rsi": rsi}


def adv_detect_regime(vix, spy_bullish):
    if vix >= ADV_VIX_CRISIS:
        return "crisis"
    if vix >= ADV_VIX_HIGH:
        return "high_volatility"
    if spy_bullish and vix < 20:
        return "bull"
    if not spy_bullish and vix > 20:
        return "bear"
    return "neutral"


def adv_find_spreads(symbol, price, puts, expiration, regime, trend_data):
    signals = []
    by_strike = {float(p["strike"]): p for p in puts if p.get("strike") is not None}
    dte = days_to_expiry(expiration)   # reuse existing helper

    for strike, short_put in by_strike.items():
        long_put = by_strike.get(strike - SPREAD_WIDTH)
        if long_put is None:
            continue

        short_oi  = int(short_put.get("open_interest") or 0)
        short_vol = int(short_put.get("volume") or 0)
        if short_oi < MIN_OPEN_INTEREST or short_vol < MIN_VOLUME:
            continue

        delta, gamma, theta, iv = extract_greeks(short_put)   # reuse existing
        if iv < MIN_IV:
            continue

        pot = adv_probability_of_touch(delta)
        if pot is not None and pot > ADV_MAX_PROBABILITY_TOUCH:
            continue
        if delta is not None and abs(delta) > ADV_MAX_DELTA:
            continue

        exp_move = adv_expected_move(price, iv, dte)
        if (price - strike) < (exp_move * ADV_EXPECTED_MOVE_MULT):
            continue

        short_bid = float(short_put.get("bid") or 0)
        short_ask = float(short_put.get("ask") or 0)
        long_bid  = float(long_put.get("bid")  or 0)
        long_ask  = float(long_put.get("ask")  or 0)

        estimated_fill = ((short_bid + short_ask) / 2) - ((long_bid + long_ask) / 2)
        net_credit = max(estimated_fill - ADV_REAL_FILL_SLIPPAGE, 0)
        if net_credit < ADV_MIN_CREDIT:
            continue

        short_ba = short_ask - short_bid
        if short_ba > ADV_TOP10_MAX_BA:
            continue

        max_loss = SPREAD_WIDTH - net_credit
        if max_loss <= 0:
            continue

        iv_history = [0.20, 0.25, 0.30, 0.35, 0.40, 0.45]
        ivr = adv_calculate_iv_rank(iv, iv_history)
        if ivr < ADV_MIN_IVR:
            continue

        return_on_risk   = net_credit / max_loss
        theta_efficiency = (abs(theta) / (max_loss * 100)) if theta is not None else 0
        pot_val          = pot if pot is not None else 0

        score = (
            return_on_risk * 40
            + min(short_oi / 1000, 5) * 4
            + min(short_vol / 500, 3) * 3
            + max(0, (0.25 - pot_val)) * 80
            + min(theta_efficiency * 1000, 10)
            + ivr * 0.3
        )

        qty = adv_kelly_size(ADV_TARGET_WIN_RATE, net_credit, max_loss)

        signals.append({
            "symbol":            symbol,
            "expiration":        expiration,
            "dte":               dte,
            "market_regime":     regime,
            "short_strike":      strike,
            "long_strike":       strike - SPREAD_WIDTH,
            "current_price":     round(price, 2),
            "expected_move":     round(exp_move, 2),
            "net_credit":        round(net_credit, 2),
            "max_loss":          round(max_loss, 2),
            "return_on_risk":    round(return_on_risk, 4),
            "delta":             round(delta, 4) if delta is not None else None,
            "gamma":             round(gamma, 4) if gamma is not None else None,
            "theta":             round(theta, 4) if theta is not None else None,
            "probability_touch": round(pot * 100, 1) if pot is not None else None,
            "iv":                round(iv * 100, 1),
            "iv_rank":           round(ivr, 1),
            "open_interest":     short_oi,
            "volume":            short_vol,
            "short_ba":          round(short_ba, 3),
            "rsi":               round(trend_data["rsi"], 1) if trend_data.get("rsi") is not None else None,
            "qty":               qty,
            "total_credit":      round(net_credit * qty * 100, 2),
            "total_risk":        round(max_loss   * qty * 100, 2),
            "theta_efficiency":  round(theta_efficiency, 6),
            "score":             round(score, 2),
        })

    signals.sort(key=lambda x: x["score"], reverse=True)
    return signals


def run_advanced_scan():
    """Run the advanced scan and return (advanced_signals, vix, regime)."""
    vix = get_quote("VIX")
    if vix is None:
        print("  [ADV] Unable to retrieve VIX — skipping advanced scan.")
        return [], None, "unknown"

    spy_price = get_quote("SPY")
    spy_trend = adv_trend_analysis("SPY", spy_price) if spy_price else None
    spy_bullish = spy_trend["bullish"] if spy_trend else False
    regime = adv_detect_regime(vix, spy_bullish)
    print(f"  [ADV] VIX={vix:.2f}  Regime={regime}")

    if regime == "crisis":
        print("  [ADV] Crisis regime — no advanced signals generated.")
        return [], round(vix, 2), regime

    adv_signals = []
    for symbol in SYMBOLS:
        price = get_quote(symbol)
        if price is None:
            continue
        trend_data = adv_trend_analysis(symbol, price)
        if trend_data is None or not trend_data["bullish"]:
            print(f"  [ADV] {symbol}: trend rejected")
            continue
        expirations = get_expirations(symbol)
        valid_exps  = [e for e in expirations
                       if MIN_DAYS_TO_EXPIRY <= days_to_expiry(e) <= MAX_DAYS_TO_EXPIRY]
        for exp in valid_exps:
            puts = get_puts(symbol, exp)
            if puts:
                adv_signals.extend(adv_find_spreads(symbol, price, puts, exp, regime, trend_data))

                

    adv_signals.sort(key=lambda x: x["score"], reverse=True)
    print(f"  [ADV] {len(adv_signals)} advanced signal(s) found.")
    return adv_signals, round(vix, 2), regime


# ─────────────────────────────────────────────
# FULL SCAN
# ─────────────────────────────────────────────

def run_scan():
    # Define Pacific Time (UTC-7 or UTC-8 depending on DST)
    # For a robust solution without external libs, you can offset UTC:
    # Note: 2026-04 is PDT (UTC-7)
    pt_timezone = timezone(timedelta(hours=-7)) 
    now_pt = datetime.datetime.now(pt_timezone)
    
    timestamp_str = now_pt.strftime("%Y-%m-%d %H:%M:%S %Z")
    
    print(f"\n[{now_pt.strftime('%H:%M:%S')}] Starting scan (PT)... shadow")
    ticker_data = {}
    all_signals = []
    new_signals = []

    for symbol in SYMBOLS:
        print(f"  Scanning {symbol}...", end=" ", flush=True)
        price = get_quote(symbol)
        if price is None:
            ticker_data[symbol] = {"price": None, "status": "error"}
            print("ERROR")
            continue

        expirations = get_expirations(symbol)
        valid_exps = [e for e in expirations
                      if MIN_DAYS_TO_EXPIRY <= days_to_expiry(e) <= MAX_DAYS_TO_EXPIRY]

        sym_signals = []
        for exp in valid_exps:
            puts = get_puts(symbol, exp)
            if puts:
                sym_signals.extend(find_spreads(symbol, price, puts, exp))
                new_signals.extend(find_new_spreads(symbol, price, puts, exp))

        sym_signals.sort(key=lambda x: x["score"], reverse=True)
        all_signals.extend(sym_signals)

        top10_count = sum(1 for s in sym_signals if s["top10_eligible"])
        ticker_data[symbol] = {
            "price":        round(price, 2),
            "signal_count": len(sym_signals),
            "top10_count":  top10_count,
            "status":       "ok"
        }
        print(f"${price:.2f}  →  {len(sym_signals)} signal(s)  ({top10_count} top10-eligible)")

    all_signals.sort(key=lambda x: x["score"], reverse=True)
    new_signals.sort(key=lambda x: x["score"], reverse=True)

    top10 = sorted(
        [s for s in all_signals if s["top10_eligible"]],
        key=lambda x: x["top10_score"],
        reverse=True
    )[:10]

    output = {
        "new_signals": new_signals,
        "last_updated":   timestamp_str,
        "next_scan_secs": SCAN_INTERVAL_SECS,
        "tickers":        ticker_data,
        "signals":        all_signals,
        "top10":          top10,
        "config": {
            "spread_width":          SPREAD_WIDTH,
            "min_otm_pct":           int(MIN_DISCOUNT_PCT * 100),
            "quantity":              QUANTITY,
            "min_oi":                MIN_OPEN_INTEREST,
            "min_volume":            MIN_VOLUME,
            "min_credit":            MIN_NET_CREDIT,
            "min_iv_pct":            int(MIN_IV * 100),
            "dte_range":             [MIN_DAYS_TO_EXPIRY, MAX_DAYS_TO_EXPIRY],
            "top10_min_oi":          TOP10_MIN_OI,
            "top10_min_vol":         TOP10_MIN_VOLUME,
            "top10_min_credit_pct":  TOP10_MIN_CREDIT_PCT,
            "top10_max_dte":         TOP10_MAX_DTE,
            "top10_max_delta":       TOP10_MAX_DELTA,
            "top10_max_ba":          TOP10_MAX_BA_SPREAD,
        }
    }

    with open(OUTPUT_FILE, "w") as f:
        json.dump(output, f, indent=2)

    print(f"  Done. {len(all_signals)} total, {len(top10)} in Top 10. Written to signalskelly.json")

    # ── Advanced scan ──────────────────────────
    print("\n  Running advanced scan…")
    adv_signals, adv_vix, adv_regime = run_advanced_scan()

    output["advanced_signals"] = adv_signals
    output["advanced_vix"]     = adv_vix
    output["advanced_regime"]  = adv_regime

    with open(OUTPUT_FILE, "w") as f:
        json.dump(output, f, indent=2)

    print(f"  Advanced: {len(adv_signals)} signal(s). signalskelly.json updated.")


# ─────────────────────────────────────────────
# LOOP
# ─────────────────────────────────────────────

if __name__ == "__main__":
    # Just run it once and exit
    try:
        run_scan()
    except Exception as e:
        print(f"Error: {e}")
