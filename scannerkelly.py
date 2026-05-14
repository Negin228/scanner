# scannerkelly.py
"""
Professional Put Credit Spread Scanner
- Tradier API
- Advanced risk management
- Portfolio exposure control
- Regime detection
- Expected value ranking
- JSON dashboard output
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

BASE_URL = "https://sandbox.tradier.com/v1"
TRADIER_API_KEY = os.getenv("TRADIER_API_KEY", "").strip()

print("=" * 60)
print("TRADIER PROFESSIONAL SCANNER")
print("=" * 60)
print("BASE URL:", BASE_URL)
print("API KEY EXISTS:", bool(TRADIER_API_KEY))
print("=" * 60)

SYMBOLS = [
    "NVDA", "AMZN", "MSFT", "META", "GOOG",
    "NFLX", "PLTR", "TSLA", "SPY", "TQQQ",
    "SQQQ", "AMD", "ORCL"
]

TECH_SYMBOLS = {
    "NVDA", "AMD", "MSFT", "META", "GOOG",
    "NFLX", "PLTR", "TSLA", "AMZN", "ORCL"
}

LEVERAGED_ETFS = {
    "TQQQ", "SQQQ"
}

SPREAD_WIDTH = 5
MIN_DISCOUNT_PCT = 0.20
MIN_OPEN_INTEREST = 100
MIN_VOLUME = 50
MIN_IV = 0.20
MIN_DAYS_TO_EXPIRY = 7
MAX_DAYS_TO_EXPIRY = 45
SCAN_INTERVAL_SECS = 300

# ─────────────────────────────────────────────
# PROFESSIONAL RISK MANAGEMENT
# ─────────────────────────────────────────────

ADV_ACCOUNT_SIZE = 100000
MAX_RISK_PER_TRADE_PCT = 0.0075
MAX_PORTFOLIO_RISK_PCT = 0.06
MIN_RETURN_ON_RISK = 0.20

ADV_MIN_CREDIT = 0.15
ADV_REAL_FILL_SLIPPAGE = 0.02
ADV_EXPECTED_MOVE_MULT = 1.0
ADV_MAX_PROBABILITY_TOUCH = 0.25

ADV_MAX_DELTA_NORMAL = 0.10
ADV_MAX_DELTA_HIGH_VOL = 0.07
ADV_MAX_DELTA_CRISIS = 0.05
ADV_MAX_DELTA = ADV_MAX_DELTA_NORMAL

ADV_TOP10_MAX_BA = 0.05

MAX_TECH_SPREADS = 2
MAX_LEVERAGED_ETF_SPREADS = 1

ADV_TREND_FAST_MA = 20
ADV_TREND_SLOW_MA = 50
ADV_MIN_RSI = 40
ADV_MAX_RSI = 75

ADV_VIX_LOW = 15
ADV_VIX_HIGH = 30
ADV_VIX_CRISIS = 40

OUTPUT_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "signalskelly.json"
)

HEADERS = {
    "Authorization": f"Bearer {TRADIER_API_KEY}",
    "Accept": "application/json"
}

# ─────────────────────────────────────────────
# API HELPERS
# ─────────────────────────────────────────────

def get_quote(symbol):
    url = f"{BASE_URL}/markets/quotes"
    params = {
        "symbols": symbol,
        "greeks": "false"
    }

    try:
        r = requests.get(
            url,
            headers=HEADERS,
            params=params,
            timeout=10
        )

        print(f"[QUOTE] {symbol} -> {r.status_code}")

        r.raise_for_status()

        data = r.json()
        quote = data.get("quotes", {}).get("quote", {})

        last = quote.get("last") or quote.get("bid")

        return float(last) if last else None

    except Exception as e:
        print(f"[ERROR] Quote {symbol}: {e}")
        return None


def get_expirations(symbol):
    url = f"{BASE_URL}/markets/options/expirations"

    params = {
        "symbol": symbol,
        "includeAllRoots": "true",
        "strikes": "false"
    }

    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=10)
        r.raise_for_status()

        dates = r.json().get("expirations", {}).get("date", [])

        return [dates] if isinstance(dates, str) else (dates or [])

    except Exception as e:
        print(f"[ERROR] Expirations {symbol}: {e}")
        return []


def get_puts(symbol, expiration):
    url = f"{BASE_URL}/markets/options/chains"

    params = {
        "symbol": symbol,
        "expiration": expiration,
        "greeks": "true"
    }

    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=10)
        r.raise_for_status()

        options = r.json().get("options", {}).get("option", [])

        if isinstance(options, dict):
            options = [options]

        return [o for o in options if o.get("option_type") == "put"]

    except Exception as e:
        print(f"[ERROR] Chain {symbol}: {e}")
        return []


def days_to_expiry(exp_str):
    exp = datetime.datetime.strptime(exp_str, "%Y-%m-%d").date()
    return (exp - datetime.date.today()).days


def extract_greeks(option):
    greeks = option.get("greeks")

    if not greeks or not isinstance(greeks, dict):
        return None, None, None, None

    delta = greeks.get("delta")
    gamma = greeks.get("gamma")
    theta = greeks.get("theta")

    iv = (
        greeks.get("mid_iv")
        or greeks.get("ask_iv")
        or greeks.get("bid_iv")
    )

    return (
        float(delta) if delta is not None else None,
        float(gamma) if gamma is not None else None,
        float(theta) if theta is not None else None,
        float(iv) if iv is not None else None,
    )

# ─────────────────────────────────────────────
# TREND / REGIME ANALYSIS
# ─────────────────────────────────────────────

def adv_moving_average(values, period):
    if len(values) < period:
        return None

    return sum(values[-period:]) / period


def adv_calculate_rsi(closes, period=14):
    if len(closes) < period + 1:
        return None

    gains = []
    losses = []

    for i in range(1, period + 1):
        diff = closes[-i] - closes[-i - 1]

        if diff >= 0:
            gains.append(diff)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(diff))

    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    if avg_loss == 0:
        return 100

    rs = avg_gain / avg_loss

    return 100 - (100 / (1 + rs))


def adv_get_history(symbol, days=120):
    url = f"{BASE_URL}/markets/history"

    end = datetime.date.today()
    start = end - timedelta(days=days)

    try:
        r = requests.get(
            url,
            headers=HEADERS,
            params={
                "symbol": symbol,
                "interval": "daily",
                "start": start.strftime("%Y-%m-%d"),
                "end": end.strftime("%Y-%m-%d")
            },
            timeout=15
        )

        r.raise_for_status()

        history = r.json().get("history", {}).get("day", [])

        return [float(d["close"]) for d in history if d.get("close")]

    except Exception as e:
        print(f"[ERROR] History {symbol}: {e}")
        return []


def adv_trend_analysis(symbol, price):
    closes = adv_get_history(symbol)

    if len(closes) < ADV_TREND_SLOW_MA:
        return None

    ma_fast = adv_moving_average(closes, ADV_TREND_FAST_MA)
    ma_slow = adv_moving_average(closes, ADV_TREND_SLOW_MA)

    rsi = adv_calculate_rsi(closes)

    bullish = (
        price > ma_fast
        and ma_fast > ma_slow
        and ADV_MIN_RSI <= rsi <= ADV_MAX_RSI
    )

    return {
        "bullish": bullish,
        "ma_fast": ma_fast,
        "ma_slow": ma_slow,
        "rsi": rsi
    }


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

# ─────────────────────────────────────────────
# RISK HELPERS
# ─────────────────────────────────────────────

def adv_expected_move(price, iv, dte):
    return price * iv * math.sqrt(dte / 365)


def adv_probability_of_touch(delta):
    if delta is None:
        return None

    return min(abs(delta) * 2, 1.0)


def adv_position_size(max_loss, current_portfolio_risk=0):
    if max_loss <= 0:
        return 0

    risk_per_contract = max_loss * 100

    max_trade_risk = (
        ADV_ACCOUNT_SIZE * MAX_RISK_PER_TRADE_PCT
    )

    remaining_portfolio_risk = (
        ADV_ACCOUNT_SIZE * MAX_PORTFOLIO_RISK_PCT
        - current_portfolio_risk
    )

    allowed_risk = min(
        max_trade_risk,
        remaining_portfolio_risk
    )

    if allowed_risk <= 0:
        return 0

    contracts = int(allowed_risk / risk_per_contract)

    return max(1, contracts)


def earnings_before_expiration(symbol, expiration):
    return False

# ─────────────────────────────────────────────
# ADVANCED SPREAD FINDER
# ─────────────────────────────────────────────

def adv_find_spreads(
    symbol,
    price,
    puts,
    expiration,
    regime,
    trend_data,
    current_portfolio_risk
):

    signals = []

    by_strike = {
        float(p["strike"]): p
        for p in puts
        if p.get("strike") is not None
    }

    dte = days_to_expiry(expiration)

    for strike, short_put in by_strike.items():

        if strike > price * (1 - MIN_DISCOUNT_PCT):
            continue

        long_put = by_strike.get(strike - SPREAD_WIDTH)

        if long_put is None:
            continue

        short_oi = int(short_put.get("open_interest") or 0)
        short_vol = int(short_put.get("volume") or 0)

        if short_oi < MIN_OPEN_INTEREST:
            continue

        if short_vol < MIN_VOLUME:
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
        long_bid = float(long_put.get("bid") or 0)
        long_ask = float(long_put.get("ask") or 0)

        estimated_fill = (
            ((short_bid + short_ask) / 2)
            - ((long_bid + long_ask) / 2)
        )

        net_credit = max(
            estimated_fill - ADV_REAL_FILL_SLIPPAGE,
            0
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

        qty = adv_position_size(
            max_loss,
            current_portfolio_risk
        )

        if qty <= 0:
            continue

        theta_efficiency = (
            abs(theta) / (max_loss * 100)
            if theta is not None else 0
        )

        pot_val = pot if pot is not None else 0

        estimated_win_rate = max(0.50, 1 - pot_val)

        expected_value = (
            estimated_win_rate * net_credit
            - (1 - estimated_win_rate) * max_loss
        )

        score = (
            expected_value * 100
            + return_on_risk * 50
            + min(short_oi / 1000, 5) * 4
            + min(short_vol / 500, 3) * 3
            + max(0, (0.25 - pot_val)) * 100
            + min(theta_efficiency * 1000, 10)
        )

        signals.append({
            "symbol": symbol,
            "expiration": expiration,
            "dte": dte,
            "market_regime": regime,
            "short_strike": strike,
            "long_strike": strike - SPREAD_WIDTH,
            "current_price": round(price, 2),
            "expected_move": round(exp_move, 2),
            "net_credit": round(net_credit, 2),
            "max_loss": round(max_loss, 2),
            "return_on_risk": round(return_on_risk, 4),
            "delta": round(delta, 4) if delta is not None else None,
            "gamma": round(gamma, 4) if gamma is not None else None,
            "theta": round(theta, 4) if theta is not None else None,
            "probability_touch": round((pot or 0) * 100, 1),
            "iv": round(iv * 100, 1),
            "open_interest": short_oi,
            "volume": short_vol,
            "short_ba": round(short_ba, 3),
            "rsi": round(trend_data["rsi"], 1),
            "qty": qty,
            "total_credit": round(net_credit * qty * 100, 2),
            "total_risk": round(max_loss * qty * 100, 2),
            "expected_value": round(expected_value, 4),
            "estimated_win_rate": round(estimated_win_rate * 100, 1),
            "take_profit_50": round(net_credit * 0.5, 2),
            "take_profit_75": round(net_credit * 0.25, 2),
            "stop_loss_price": round(net_credit * 2.0, 2),
            "score": round(score, 2)
        })

    signals.sort(key=lambda x: x["score"], reverse=True)

    return signals

# ─────────────────────────────────────────────
# MAIN ADVANCED SCAN
# ─────────────────────────────────────────────

def run_advanced_scan():

    global ADV_MAX_DELTA

    vix = get_quote("VIX")

    if vix is None:
        print("[ERROR] Unable to retrieve VIX")
        return [], None, "unknown"

    spy_price = get_quote("SPY")

    spy_trend = (
        adv_trend_analysis("SPY", spy_price)
        if spy_price else None
    )

    spy_bullish = spy_trend["bullish"] if spy_trend else False

    regime = adv_detect_regime(vix, spy_bullish)

    print(f"[REGIME] {regime} | VIX={vix}")

    if regime == "high_volatility":
        ADV_MAX_DELTA = ADV_MAX_DELTA_HIGH_VOL
    elif regime == "crisis":
        ADV_MAX_DELTA = ADV_MAX_DELTA_CRISIS
    else:
        ADV_MAX_DELTA = ADV_MAX_DELTA_NORMAL

    if regime == "crisis":
        return [], round(vix, 2), regime

    current_portfolio_risk = 0
    tech_spread_count = 0
    leveraged_etf_count = 0

    adv_signals = []

    for symbol in SYMBOLS:

        if (
            symbol in TECH_SYMBOLS
            and tech_spread_count >= MAX_TECH_SPREADS
        ):
            print(f"[SKIP] {symbol} tech exposure limit")
            continue

        if (
            symbol in LEVERAGED_ETFS
            and leveraged_etf_count >= MAX_LEVERAGED_ETF_SPREADS
        ):
            print(f"[SKIP] {symbol} leveraged ETF limit")
            continue

        print(f"[SCAN] {symbol}")

        price = get_quote(symbol)

        if price is None:
            continue

        trend_data = adv_trend_analysis(symbol, price)

        if trend_data is None:
            continue

        if not trend_data["bullish"]:
            print(f"[REJECT] {symbol} bearish trend")
            continue

        expirations = get_expirations(symbol)

        valid_exps = [
            e for e in expirations
            if MIN_DAYS_TO_EXPIRY <= days_to_expiry(e) <= MAX_DAYS_TO_EXPIRY
        ]

        for exp in valid_exps:

            if earnings_before_expiration(symbol, exp):
                continue

            puts = get_puts(symbol, exp)

            if not puts:
                continue

            spreads = adv_find_spreads(
                symbol,
                price,
                puts,
                exp,
                regime,
                trend_data,
                current_portfolio_risk
            )

            for s in spreads:
                adv_signals.append(s)

                current_portfolio_risk += s["total_risk"]

                if s["symbol"] in TECH_SYMBOLS:
                    tech_spread_count += 1

                if s["symbol"] in LEVERAGED_ETFS:
                    leveraged_etf_count += 1

    adv_signals.sort(
        key=lambda x: x["score"],
        reverse=True
    )

    return adv_signals, round(vix, 2), regime

# ─────────────────────────────────────────────
# MAIN RUNNER
# ─────────────────────────────────────────────

def run_scan():

    pt_timezone = timezone(timedelta(hours=-7))
    now_pt = datetime.datetime.now(pt_timezone)

    timestamp_str = now_pt.strftime("%Y-%m-%d %H:%M:%S %Z")

    print(f"\n[{timestamp_str}] Starting professional scan")

    adv_signals, adv_vix, adv_regime = run_advanced_scan()

    output = {
        "last_updated": timestamp_str,
        "advanced_vix": adv_vix,
        "advanced_regime": adv_regime,
        "advanced_signals": adv_signals,
        "config": {
            "spread_width": SPREAD_WIDTH,
            "max_delta": ADV_MAX_DELTA,
            "max_risk_per_trade_pct": MAX_RISK_PER_TRADE_PCT,
            "max_portfolio_risk_pct": MAX_PORTFOLIO_RISK_PCT,
            "min_return_on_risk": MIN_RETURN_ON_RISK,
            "max_bid_ask_spread": ADV_TOP10_MAX_BA
        }
    }

    with open(OUTPUT_FILE, "w") as f:
        json.dump(output, f, indent=2)

    print("=" * 60)
    print(f"ADVANCED SIGNALS: {len(adv_signals)}")
    print(f"OUTPUT FILE: {OUTPUT_FILE}")
    print("=" * 60)

# ─────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────

if __name__ == "__main__":

    try:
        run_scan()

    except Exception as e:
        print(f"FATAL ERROR: {e}")
