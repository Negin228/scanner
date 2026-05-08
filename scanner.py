import requests
import datetime
import json
import time
import os
import math
from datetime import timedelta, timezone

# ─────────────────────────────────────────────
# INSTITUTIONAL CONFIGURATION
# ─────────────────────────────────────────────
BASE_URL = "https://api.tradier.com/v1"
TRADIER_API_KEY = os.getenv("TRADIER_API_KEY")

# High-Liquidity Institutional Watchlist
SYMBOLS = ["NVDA", "AMZN", "MSFT", "META", "GOOG", "NFLX", "TSLA", "SPY", "AMD", "QCOM", "AAPL"]
BENCHMARK = "SPY"

# Risk Management Parameters ($100k Account)
ACCOUNT_SIZE       = 100000
MAX_RISK_PER_TRADE = 0.02  # 2% ($2,000)
SPREAD_WIDTH       = 5.0
MIN_DTE            = 7
MAX_DTE            = 45
MIN_PROB_PROFIT    = 85.0  # Statistical floor
SLIPPAGE_ADJUST    = 0.02  # 2% haircut on mid-price

# Liquidity Floors
MIN_OI             = 500
MIN_VOL            = 100

HEADERS = {
    "Authorization": f"Bearer {TRADIER_API_KEY}",
    "Accept": "application/json"
}

# ─────────────────────────────────────────────
# RISK & ANALYTICS ENGINE
# ─────────────────────────────────────────────

def calculate_institutional_metrics(short_delta, net_credit, max_loss, price, spy_price, ticker_beta=1.2):
    """
    Computes EV, PoP, and Beta-Weighted Delta for the workstation.
    """
    # 1. Expected Value & Probability
    p_loss = abs(short_delta)
    p_win = 1.0 - p_loss
    ev = (p_win * net_credit) - (p_loss * max_loss)
    pop = p_win * 100

    # 2. Position Sizing (Risking 2% of $100k)
    dollar_risk_cap = ACCOUNT_SIZE * MAX_RISK_PER_TRADE
    risk_per_spread = max_loss * 100
    recommended_qty = math.floor(dollar_risk_cap / risk_per_spread) if risk_per_spread > 0 else 0

    # 3. Beta-Weighted Delta (Normalized to SPY)
    # Tells us: "This position is equivalent to being long X shares of SPY"
    pos_delta = short_delta * recommended_qty * 100
    weighted_delta = pos_delta * (price / spy_price) * ticker_beta

    return {
        "ev": round(ev, 2),
        "pop": round(pop, 1),
        "qty": recommended_qty,
        "spy_weighted_delta": round(weighted_delta, 2),
        "edge_ratio": round(ev / max_loss, 4) if max_loss > 0 else 0
    }

def get_correlation(hist1, hist2):
    """Pearson Correlation Coefficient for Cluster Risk Analysis."""
    if len(hist1) < 10 or len(hist2) < 10: return 0.0
    n = min(len(hist1), len(hist2))
    h1, h2 = hist1[-n:], hist2[-n:]
    mu1, mu2 = sum(h1)/n, sum(h2)/n
    num = sum((h1[i]-mu1)*(h2[i]-mu2) for i in range(n))
    den = math.sqrt(sum((x-mu1)**2 for x in h1) * sum((y-mu2)**2 for y in h2))
    return round(num/den, 3) if den != 0 else 0

# ─────────────────────────────────────────────
# API DATA WRAPPERS
# ─────────────────────────────────────────────

def fetch_data(endpoint, params=None):
    try:
        r = requests.get(f"{BASE_URL}/{endpoint}", headers=HEADERS, params=params, timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"API Error on {endpoint}: {e}")
        return None

def get_historical_closes(symbol):
    data = fetch_data("markets/history", {
        "symbol": symbol, "interval": "daily", 
        "start": (datetime.date.today() - timedelta(days=40)).strftime("%Y-%m-%d")
    })
    history = data.get("history", {}).get("day", []) if data else []
    return [float(d["close"]) for d in history if "close" in d]

# ─────────────────────────────────────────────
# MAIN SCANNER EXECUTION
# ─────────────────────────────────────────────

def run_workstation_scan():
    print(f"\n[SYSTEM] Initializing $100k Institutional Scan...")
    
    # 1. Establish Benchmark
    spy_data = fetch_data("markets/quotes", {"symbols": BENCHMARK, "greeks": "true"})
    spy_price = float(spy_data["quotes"]["quote"]["last"])
    spy_hist = get_historical_closes(BENCHMARK)
    
    all_signals = []
    
    for symbol in SYMBOLS:
        print(f"  > Analyzing {symbol}...", end="\r")
        
        # Fetch Underlying & History
        quote_data = fetch_data("markets/quotes", {"symbols": symbol, "greeks": "true"})
        if not quote_data: continue
        quote = quote_data["quotes"]["quote"]
        price = float(quote["last"])
        hist = get_historical_closes(symbol)
        correlation = get_correlation(hist, spy_hist)

        # Fetch Expirations
        exp_data = fetch_data("markets/options/expirations", {"symbol": symbol})
        if not exp_data: continue
        dates = exp_data.get("expirations", {}).get("date", [])
        
        valid_dates = [d for d in dates if MIN_DTE <= (datetime.datetime.strptime(d, "%Y-%m-%d").date() - datetime.date.today()).days <= MAX_DTE]

        for exp in valid_dates:
            chain = fetch_data("markets/options/chains", {"symbol": symbol, "expiration": exp, "greeks": "true"})
            options = chain.get("options", {}).get("option", []) if chain else []
            if isinstance(options, dict): options = [options]

            # Filter for Puts
            puts = [o for o in options if o["option_type"] == "put"]
            by_strike = {float(o["strike"]): o for o in puts}

            for strike, short_opt in by_strike.items():
                long_opt = by_strike.get(strike - SPREAD_WIDTH)
                if not long_opt: continue

                # Basic Institutional Filters
                short_oi = int(short_opt.get("open_interest", 0))
                if short_oi < MIN_OI: continue

                # Calculate Mid-Price and apply Slippage Haircut
                mid_credit = ((float(short_opt["bid"]) + float(short_opt["ask"]))/2) - \
                             ((float(long_opt["bid"]) + float(long_opt["ask"]))/2)
                
                net_credit = mid_credit * (1 - SLIPPAGE_ADJUST)
                max_loss = SPREAD_WIDTH - net_credit
                
                if net_credit <= 0.10: continue

                # Greeks & Risk Analytics
                delta = float(short_opt.get("greeks", {}).get("delta", 0))
                metrics = calculate_institutional_metrics(delta, net_credit, max_loss, price, spy_price)

                if metrics["pop"] < MIN_PROB_PROFIT or metrics["ev"] <= 0:
                    continue

                all_signals.append({
                    "symbol": symbol,
                    "expiration": exp,
                    "spread": f"{strike}/{strike-SPREAD_WIDTH}P",
                    "price": price,
                    "net_credit": round(net_credit, 2),
                    "max_loss": round(max_loss, 2),
                    "ev": metrics["ev"],
                    "pop_pct": metrics["pop"],
                    "rec_qty": metrics["qty"],
                    "spy_delta_eq": metrics["spy_weighted_delta"],
                    "correlation_spy": correlation,
                    "edge_ratio": metrics["edge_ratio"],
                    "total_risk": round(metrics["qty"] * max_loss * 100, 2)
                })

    # Sort by Edge Ratio (Most mathematical bang for your buck)
    all_signals.sort(key=lambda x: x["edge_ratio"], reverse=True)

    # Final Output Generation
    report = {
        "scan_time": datetime.datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "account_basis": ACCOUNT_SIZE,
        "benchmark_spy": spy_price,
        "top_signals": all_signals[:15]
    }

    with open("signals.json", "w") as f:
        json.dump(report, f, indent=4)

    print(f"\n[SUCCESS] Scan complete. Found {len(all_signals)} high-EV institutional signals.")
    print(f"Results written to signals.json")

if __name__ == "__main__":
    run_workstation_scan()
