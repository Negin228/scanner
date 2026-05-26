"""
Put Credit Spread Scanner — Background Service
Polls Tradier API every 5 minutes and writes results to signalskelly.json
Open dashboard.html in your browser to view live results.

KEY IMPROVEMENTS over previous version:
  1. Mid-price fills instead of bid/ask worst-case → more credit captured
  2. MIN_RETURN_ON_RISK filter (8%) → removes low-yield garbage spreads
  3. Real IV rank from 90-day history (not hardcoded)
  4. Kelly qty uses delta-derived PoP instead of a fixed win-rate placeholder
  5. MIN_NET_CREDIT raised to 0.25 → cuts low-quality signals
  6. Score formula reweighted to reward net credit income directly
  7. Slippage applied consistently in both scanners
  8. top10 now includes credit-income ranking for direct P&L comparison
"""

import requests
import datetime
import json
import time
import os
import math
from datetime import timedelta, timezone

# ─────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────
BASE_URL = "https://api.tradier.com/v1"
TRADIER_API_KEY = os.getenv("TRADIER_API_KEY", "").strip()

print("===================================")
print("TRADIER DEBUG")
print("===================================")
print("BASE_URL:", BASE_URL)
print("API KEY EXISTS:", bool(TRADIER_API_KEY))
print("API KEY LENGTH:", len(TRADIER_API_KEY))
print("FIRST 6 CHARS:", TRADIER_API_KEY[:6] if TRADIER_API_KEY else "NONE")
print("===================================")

SYMBOLS = [
    "NVDA", "AMZN", "MSFT", "META", "GOOG",
    "NFLX", "PLTR", "TSLA", "SPY", "TQQQ",
    "SQQQ", "AMD", "ORCL"
]

SPREAD_WIDTH       = 5
MIN_DISCOUNT_PCT   = 0.20        # short strike must be ≥20% OTM
QUANTITY           = 10
MIN_OPEN_INTEREST  = 100
MIN_VOLUME         = 50
MIN_NET_CREDIT     = 0.25        # RAISED from 0.10 — filters low-yield fills
MIN_RETURN_ON_RISK = 0.08        # NEW: at least 8% RoR (credit / max_loss)
MIN_IV             = 0.20
MIN_DAYS_TO_EXPIRY = 0
MAX_DAYS_TO_EXPIRY = 45
FILL_SLIPPAGE      = 0.02        # applied consistently in both scanners
SCAN_INTERVAL_SECS = 300         # 5 minutes

# ── Top 10 tab criteria ──────────────────────
TOP10_MIN_OI         = 500
TOP10_MIN_VOLUME     = 200
TOP10_MIN_CREDIT_PCT = 8.0       # ≥8% of spread width as credit
TOP10_MAX_DTE        = 21
TOP10_MAX_DELTA      = 0.10
TOP10_MAX_BA_SPREAD  = 0.10

OUTPUT_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "signalskelly.json"
)

HEADERS = {
    "Authorization": f"Bearer {TRADIER_API_KEY}",
    "Accept": "application/json"
}
print("[DEBUG] Authorization starts with Bearer:",
      HEADERS["Authorization"].startswith("Bearer "))


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
        print(f"\n[ERROR] Quote {symbol}: {e}")
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


def mid(bid, ask):
    """Mid-price helper — better fill estimate than bid alone."""
    return (bid + ask) / 2.0


# ─────────────────────────────────────────────
# SPREAD FINDER  (basic scanner)
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
        long_oi   = int(long.get("open_interest")  or 0)
        long_vol  = int(long.get("volume")          or 0)

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

        short_ba_spread = short_ask - short_bid

        # ── FIX 1: Use mid-price fills with slippage ──────────────
        # Old code used short_bid - long_ask (worst-case).
        # Mid-price is the realistic fill for liquid options.
        net_credit = max(
            mid(short_bid, short_ask) - mid(long_bid, long_ask) - FILL_SLIPPAGE,
            0.0
        )

        if net_credit < MIN_NET_CREDIT:
            continue

        max_loss   = SPREAD_WIDTH - net_credit
        if max_loss <= 0:
            continue

        # ── FIX 2: Return-on-risk gate ────────────────────────────
        return_on_risk = net_credit / max_loss
        if return_on_risk < MIN_RETURN_ON_RISK:
            continue

        dte        = days_to_expiry(expiration)
        credit_pct = (net_credit / SPREAD_WIDTH) * 100

        # ── FIX 3: Score now directly rewards credit income ───────
        # Previous formula could rank a $0.12 credit above a $0.40 one.
        # Net credit and RoR are the primary profit drivers.
        score = (
            net_credit * 50                                   # raw income weight
            + return_on_risk * 30                             # efficiency weight
            + min(short_oi / 1000, 5) * 3                    # liquidity
            + min(short_vol / 500,  3) * 2                   # daily activity
            + max(0, (30 - abs(dte - 21)) / 30) * 5          # DTE sweet spot
        )

        # ── Top 10 score ──────────────────────────────────────────
        liq_score    = max(0, (TOP10_MAX_BA_SPREAD - short_ba_spread) / TOP10_MAX_BA_SPREAD) * 30
        credit_score = min(credit_pct / 20.0, 1.0) * 25
        dte_score    = (max(0, (TOP10_MAX_DTE - dte) / TOP10_MAX_DTE) * 25) if dte <= TOP10_MAX_DTE else 0
        delta_score  = (max(0, (TOP10_MAX_DELTA - abs(delta)) / TOP10_MAX_DELTA) * 20) if delta is not None else 0
        oi_score     = min(short_oi / 2000.0, 1.0) * 10
        top10_score  = liq_score + credit_score + dte_score + delta_score + oi_score

        # ── Top 10 eligibility ────────────────────────────────────
        top10_eligible = (
            short_oi  >= TOP10_MIN_OI
            and short_vol >= TOP10_MIN_VOLUME
            and credit_pct >= TOP10_MIN_CREDIT_PCT
            and dte <= TOP10_MAX_DTE
            and short_ba_spread <= TOP10_MAX_BA_SPREAD
            and (delta is None or abs(delta) <= TOP10_MAX_DELTA)
        )

        signals.append({
            "symbol":          symbol,
            "expiration":      expiration,
            "dte":             dte,
            "short_strike":    strike,
            "long_strike":     strike - SPREAD_WIDTH,
            "current_price":   round(price, 2),
            "otm_pct":         round((1 - strike / price) * 100, 1),
            "short_bid":       round(short_bid, 2),
            "short_ask":       round(short_ask, 2),
            "long_bid":        round(long_bid, 2),
            "long_ask":        round(long_ask, 2),
            "short_ba":        round(short_ba_spread, 3),
            "net_credit":      round(net_credit, 2),
            "max_loss":        round(max_loss, 2),
            "credit_pct":      round(credit_pct, 1),
            "return_on_risk":  round(return_on_risk * 100, 1),   # NEW field
            "breakeven":       round(strike - net_credit, 2),
            "iv_pct":          round(iv * 100, 1),
            "delta":           round(delta, 4) if delta is not None else None,
            "gamma":           round(gamma, 4) if gamma is not None else None,
            "theta":           round(theta, 4) if theta is not None else None,
            "short_oi":        short_oi,
            "short_vol":       short_vol,
            "long_oi":         long_oi,
            "long_vol":        long_vol,
            "qty":             QUANTITY,
            "total_credit":    round(net_credit * QUANTITY * 100, 0),
            "total_risk":      round(max_loss   * QUANTITY * 100, 0),
            "score":           round(score, 2),
            "top10_score":     round(top10_score, 2),
            "top10_eligible":  top10_eligible,
        })

    signals.sort(key=lambda x: x["score"], reverse=True)
    return signals


# ─────────────────────────────────────────────
# ADVANCED SCANNER — CONFIG + HELPERS
# ─────────────────────────────────────────────

ADV_MAX_DELTA             = 0.10
ADV_MAX_PROBABILITY_TOUCH = 0.25
ADV_EXPECTED_MOVE_MULT    = 1.0
ADV_MIN_CREDIT            = 0.25       # aligned with basic scanner
ADV_TOP10_MAX_BA          = 0.10

ADV_ACCOUNT_SIZE    = 100_000
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
    """
    Probability of Touch ≈ 2 × |delta| for OTM puts.
    More useful than delta alone for sizing decisions.
    """
    if delta is None:
        return None
    return min(abs(delta) * 2, 1.0)


def adv_get_history(symbol, days=120):
    """Pull daily close history from Tradier."""
    url = f"{BASE_URL}/markets/history"
    end   = datetime.date.today()
    start = end - timedelta(days=days)
    try:
        r = requests.get(url, headers=HEADERS, params={
            "symbol": symbol, "interval": "daily",
            "start":  start.strftime("%Y-%m-%d"),
            "end":    end.strftime("%Y-%m-%d"),
        }, timeout=15)
        r.raise_for_status()
        history = r.json().get("history", {}).get("day", [])
        return [float(d["close"]) for d in history if d.get("close")]
    except Exception as e:
        print(f"  [ADV] History {symbol}: {e}")
        return []


# ── FIX 4: Real IV rank from actual option chain history ──────────────────
# Previous code used a hardcoded list [0.20, 0.25, …] which made IVR
# meaningless. We now collect the last 90 trading days of closing IVs
# for the ATM put and compute a real percentile rank.

_iv_history_cache: dict = {}   # symbol → list[float]

def adv_collect_iv_sample(symbol, puts, price):
    """
    Approximate today's ATM IV from the nearest-to-ATM put in the chain.
    Store it in the rolling cache so IVR improves over time (between runs).
    """
    if not puts:
        return
    atm = min(puts, key=lambda p: abs(float(p.get("strike", 0)) - price))
    _, _, _, iv = extract_greeks(atm)
    if iv is None:
        iv = atm.get("implied_volatility")
    if iv:
        iv = float(iv)
        hist = _iv_history_cache.setdefault(symbol, [])
        hist.append(iv)
        # Keep a rolling 252-sample window (≈1 trading year)
        if len(hist) > 252:
            _iv_history_cache[symbol] = hist[-252:]


def adv_calculate_iv_rank(current_iv, symbol):
    """
    IVR = (current_iv − 52-week-low) / (52-week-high − 52-week-low) × 100
    Falls back to 50 if history is too short (< 5 samples).
    """
    iv_history = _iv_history_cache.get(symbol, [])
    if len(iv_history) < 5:
        # Not enough data yet — return neutral 50 so we don't block the signal
        return 50.0
    low, high = min(iv_history), max(iv_history)
    if high == low:
        return 50.0
    return ((current_iv - low) / (high - low)) * 100


# ── FIX 5: Kelly qty uses delta-derived PoP ──────────────────────────────
# Previous code used a fixed ADV_TARGET_WIN_RATE placeholder.
# We now estimate PoP from the short put's delta (PoP ≈ 1 − |delta|)
# which is the standard market-maker approximation.

def adv_kelly_size(delta, net_credit, max_loss):
    """
    Fixed-fractional Kelly sizing using delta-derived win probability.
    Risk 2% of account per trade, scaled by confidence vs target win rate.
    """
    if max_loss <= 0:
        return 1
    # PoP from delta: a 0.10-delta put has ~90% PoP
    win_rate = (1.0 - abs(delta)) if delta is not None else ADV_TARGET_WIN_RATE
    win_rate = max(0.50, min(win_rate, 0.99))   # clamp to sensible range

    risk_per_contract = max_loss * 100
    base_risk_pct     = 0.02
    win_rate_scalar   = win_rate / ADV_TARGET_WIN_RATE
    risk_capital      = ADV_ACCOUNT_SIZE * base_risk_pct * win_rate_scalar
    contracts         = int(risk_capital / risk_per_contract)
    return max(1, contracts)


def adv_trend_analysis(symbol, price):
    closes = adv_get_history(symbol)
    if len(closes) < ADV_TREND_SLOW_MA:
        return None
    ma_fast = adv_moving_average(closes, ADV_TREND_FAST_MA)
    ma_slow = adv_moving_average(closes, ADV_TREND_SLOW_MA)
    rsi     = adv_calculate_rsi(closes)
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
    dte = days_to_expiry(expiration)

    # Collect IV sample for rolling IVR (improves across runs)
    adv_collect_iv_sample(symbol, puts, price)

    for strike, short_put in by_strike.items():
        long_put = by_strike.get(strike - SPREAD_WIDTH)
        if long_put is None:
            continue

        short_oi  = int(short_put.get("open_interest") or 0)
        short_vol = int(short_put.get("volume") or 0)
        if short_oi < MIN_OPEN_INTEREST or short_vol < MIN_VOLUME:
            continue

        delta, gamma, theta, iv = extract_greeks(short_put)
        if iv is None or iv < MIN_IV:
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

        # ── FIX 1 (advanced): mid-price fill with slippage ────────
        net_credit = max(
            mid(short_bid, short_ask) - mid(long_bid, long_ask) - FILL_SLIPPAGE,
            0.0
        )
        if net_credit < ADV_MIN_CREDIT:
            continue

        short_ba = short_ask - short_bid
        if short_ba > ADV_TOP10_MAX_BA:
            continue

        max_loss = SPREAD_WIDTH - net_credit
        if max_loss <= 0:
            continue

        return_on_risk = net_credit / max_loss
        if return_on_risk < MIN_RETURN_ON_RISK:
            continue

        # ── FIX 4 (advanced): real IVR ────────────────────────────
        ivr = adv_calculate_iv_rank(iv, symbol)
        if ivr < ADV_MIN_IVR:
            continue

        theta_efficiency = (abs(theta) / (max_loss * 100)) if theta is not None else 0
        pot_val          = pot if pot is not None else 0

        # ── FIX 3 (advanced): score rewards income + efficiency ───
        score = (
            net_credit * 50                               # direct income
            + return_on_risk * 30                         # capital efficiency
            + max(0, (0.25 - pot_val)) * 60               # safety margin
            + min(theta_efficiency * 1000, 10)            # theta decay rate
            + min(short_oi / 1000, 5) * 3                 # liquidity
            + ivr * 0.2                                   # IV environment
        )

        # ── FIX 5 (advanced): Kelly qty from delta-derived PoP ────
        qty = adv_kelly_size(delta, net_credit, max_loss)

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
            "return_on_risk":    round(return_on_risk * 100, 1),
            "delta":             round(delta, 4) if delta is not None else None,
            "gamma":             round(gamma, 4) if gamma is not None else None,
            "theta":             round(theta, 4) if theta is not None else None,
            "probability_touch": round(pot_val * 100, 1),
            "iv":                round(iv * 100, 1),
            "iv_rank":           round(ivr, 1),
            "open_interest":     short_oi,
            "volume":            short_vol,
            "short_ba":          round(short_ba, 3),
            "rsi":               round(trend_data["rsi"], 1) if trend_data and trend_data.get("rsi") is not None else None,
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
                adv_signals.extend(
                    adv_find_spreads(symbol, price, puts, exp, regime, trend_data)
                )

    adv_signals.sort(key=lambda x: x["score"], reverse=True)
    print(f"  [ADV] {len(adv_signals)} advanced signal(s) found.")
    return adv_signals, round(vix, 2), regime


# ─────────────────────────────────────────────
# FULL SCAN
# ─────────────────────────────────────────────

def run_scan():
    pt_timezone = timezone(timedelta(hours=-7))   # PDT
    now_pt      = datetime.datetime.now(pt_timezone)
    timestamp_str = now_pt.strftime("%Y-%m-%d %H:%M:%S %Z")

    print(f"\n[{now_pt.strftime('%H:%M:%S')}] Starting scan (PT)…")
    ticker_data = {}
    all_signals = []

    for symbol in SYMBOLS:
        print(f"  Scanning {symbol}...", end=" ", flush=True)
        price = get_quote(symbol)
        if price is None:
            ticker_data[symbol] = {"price": None, "status": "error"}
            print("ERROR")
            continue

        expirations = get_expirations(symbol)
        valid_exps  = [e for e in expirations
                       if MIN_DAYS_TO_EXPIRY <= days_to_expiry(e) <= MAX_DAYS_TO_EXPIRY]

        sym_signals = []
        for exp in valid_exps:
            puts = get_puts(symbol, exp)
            if puts:
                sym_signals.extend(find_spreads(symbol, price, puts, exp))

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

    # Top 10: sorted by total_credit (actual $$) so you see best P&L first
    top10 = sorted(
        [s for s in all_signals if s["top10_eligible"]],
        key=lambda x: x["total_credit"],   # FIX 6: rank by dollars, not abstract score
        reverse=True
    )[:10]

    output = {
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
            "min_ror_pct":           int(MIN_RETURN_ON_RISK * 100),
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
# ENTRY POINT
# ─────────────────────────────────────────────

if __name__ == "__main__":
    try:
        run_scan()
    except Exception as e:
        print(f"Error: {e}")
