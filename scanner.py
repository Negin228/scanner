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
#SYMBOLS = ["NVDA", "AMZN", "MSFT", "META", "GOOG", "NFLX", "TSLA", "SPY", "AMD", "QCOM", "AAPL"]
SYMBOLS =['TQQQ', 'SQQQ', 'UPRO', 'SPXU', 'UDOW', 'SDOW', 'SOXL','SOXS', 'MMM', 'AOS', 'ABT', 'ABBV', 'ACN', 'ADBE', 'AMD', 'AES', 'AFL', 'A', 'APD', 'ABNB', 'AKAM', 'ALB', 'ARE', 'ALGN', 'ALLE', 'LNT', 'ALL', 'GOOGL', 'GOOG', 'MO', 'AMZN', 'AMCR', 'AEE', 'AEP', 'AXP', 'AIG', 'AMT', 'AWK', 'AMP', 'AME', 'AMGN', 'APH', 'ADI', 'AON', 'APA', 'APO', 'AAPL', 'AMAT', 'APTV', 'ACGL', 'ADM', 'ANET', 'AJG', 'AIZ', 'T', 'ATO', 'ADSK', 'ADP', 'AZO', 'AVB', 'AVY', 'AXON', 'BKR', 'BALL', 'BAC', 'BAX', 'BDX', 'BRK-B', 'BBY', 'TECH', 'BIIB', 'BLK', 'BX', 'XYZ', 'BK', 'BA', 'BKNG', 'BSX', 'BMY', 'AVGO', 'BR', 'BRO', 'BF-B', 'BLDR', 'BG', 'BXP', 'CHRW', 'CDNS', 'CZR','CPT', 'CPB', 'COF', 'CAH', 'KMX', 'CCL', 'CARR', 'CAT', 'CBOE', 'CBRE', 'CDW', 'COR', 'CNC', 'CNP', 'CF', 'CRL', 'SCHW', 'CHTR', 'CVX', 'CMG', 'CB', 'CHD', 'CI', 'CINF', 'CTAS', 'CSCO', 'C', 'CFG', 'CLX', 'CME', 'CMS', 'KO', 'CTSH', 'COIN', 'CL', 'CMCSA','CAG', 'COP', 'ED', 'STZ', 'CEG', 'COO', 'CPRT', 'GLW', 'CPAY', 'CTVA', 'CSGP', 'COST', 'CTRA', 'CRWD', 'CCI', 'CSX', 'CMI', 'CVS', 'DHR', 'DRI', 'DDOG', 'DVA', 'DAY', 'DECK', 'DE', 'DELL', 'DAL', 'DVN', 'DXCM', 'FANG', 'DLR', 'DG', 'DLTR', 'D', 'DPZ', 'DASH', 'DOV', 'DOW', 'DHI', 'DTE', 'DUK', 'DD', 'EMN', 'ETN', 'EBAY', 'ECL', 'EIX', 'EW', 'EA', 'ELV', 'EMR', 'ENPH', 'ETR', 'EOG', 'EPAM', 'EQT', 'EFX', 'EQIX', 'EQR', 'ERIE', 'ESS', 'EL', 'EG', 'EVRG', 'ES', 'EXC', 'EXE', 'EXPE', 'EXPD', 'EXR', 'XOM', 'FFIV', 'FDS', 'FICO', 'FAST', 'FRT', 'FDX', 'FIS', 'FITB', 'FSLR', 'FE', 'FI', 'FISV', 'F', 'FTNT', 'FTV', 'FOXA', 'FOX', 'BEN', 'FCX', 'GRMN', 'IT', 'GE', 'GEHC', 'GEV', 'GEN', 'GNRC', 'GD', 'GIS', 'GM', 'GPC', 'GILD', 'GPN', 'GL', 'GDDY', 'GS', 'HAL', 'HIG', 'HAS', 'HCA', 'DOC', 'HSIC', 'HSY', 'HPE', 'HLT', 'HOLX', 'HD', 'HON', 'HRL', 'HST', 'HWM', 'HPQ', 'HUBB', 'HUM', 'HBAN', 'HII', 'IBM', 'IEX', 'IDXX', 'ITW', 'INCY', 'IR', 'PODD', 'INTC', 'ICE', 'IFF', 'IP', 'IPG', 'INTU', 'ISRG', 'IVZ', 'INVH', 'IQV', 'IRM', 'JBHT', 'JBL', 'JKHY', 'J', 'JNJ', 'JCI', 'JPM', 'K', 'KVUE', 'KDP', 'KEY', 'KEYS', 'KMB', 'KIM', 'KMI', 'KKR', 'KLAC', 'KHC', 'KR', 'LHX', 'LH', 'LRCX', 'LW', 'LVS', 'LDOS', 'LEN', 'LII', 'LLY', 'LIN', 'LYV', 'LKQ', 'LMT', 'L', 'LOW', 'LULU', 'LYB', 'MTB', 'MPC', 'MKTX', 'MAR', 'MMC', 'MLM', 'MAS', 'MA', 'MTCH', 'MKC', 'MCD', 'MCK', 'MDT', 'MRK', 'META', 'MET', 'MTD', 'MGM', 'MCHP', 'MU', 'MSFT', 'MAA', 'MRNA', 'MHK', 'MOH', 'TAP', 'MDLZ', 'MPWR', 'MNST', 'MCO', 'MS', 'MOS', 'MSI', 'MSCI', 'NDAQ', 'NTAP', 'NFLX', 'NEM', 'NWSA', 'NWS', 'NEE', 'NKE', 'NI', 'NDSN', 'NSC', 'NTRS', 'NOC', 'NCLH', 'NRG', 'NUE', 'NVDA', 'NVR', 'NXPI', 'ORLY', 'OXY', 'ODFL', 'OMC', 'ON', 'OKE', 'ORCL', 'OTIS', 'PCAR', 'PKG', 'PLTR', 'PANW', 'PSKY', 'PH', 'PAYX', 'PAYC', 'PYPL', 'PNR', 'PEP', 'PFE', 'PCG', 'PM', 'PSX', 'PNW', 'PNC', 'POOL', 'PPG', 'PPL', 'PFG', 'PG', 'PGR', 'PLD', 'PRU', 'PEG', 'PTC', 'PSA', 'PHM', 'PWR', 'QCOM', 'DGX', 'RL', 'RJF', 'RTX', 'O', 'REG', 'REGN', 'RF', 'RSG', 'RMD', 'RVTY', 'ROK', 'ROL', 'ROP', 'ROST', 'RCL', 'SPGI', 'CRM', 'SBAC', 'SLB', 'STX', 'SRE', 'NOW', 'SHW', 'SPG', 'SWKS', 'SJM', 'SW', 'SNA', 'SOLV', 'SO', 'LUV', 'SWK', 'SBUX', 'STT', 'STLD', 'STE', 'SYK', 'SMCI', 'SYF', 'SNPS', 'SYY', 'TMUS', 'TROW', 'TTWO', 'TPR', 'TRGP', 'TGT', 'TEL', 'TDY', 'TER', 'TSLA', 'TXN', 'TPL', 'TXT', 'TMO', 'TJX', 'TKO', 'TTD', 'TSCO', 'TT', 'TDG', 'TRV', 'TRMB', 'TFC', 'TYL', 'TSN', 'USB', 'UBER', 'UDR', 'ULTA', 'UNP', 'UAL', 'UPS', 'URI', 'UNH', 'UHS', 'VLO', 'VTR', 'VLTO', 'VRSN', 'VRSK', 'VZ', 'VRTX', 'VTRS', 'VICI', 'V', 'VST', 'VMC', 'WRB', 'GWW', 'WAB', 'WMT', 'DIS', 'WBD', 'WM', 'WAT', 'WEC', 'WFC', 'WELL', 'WST', 'WDC', 'WY', 'WSM', 'WMB', 'WTW', 'WDAY', 'WYNN', 'XEL', 'XYL', 'YUM', 'ZBRA', 'ZBH', 'ZTS', 'IBKR', 'ZS','TRI','Hood', 'PDD', 'TEAM', 'APP','SHOP', 'MRVL', 'CCEP', 'ASML', 'ARM', 'GFS', 'AZN', 'MELI', 'MSTR']



BENCHMARK = "SPY"

# Risk Management Parameters ($100k Account)
ACCOUNT_SIZE       = 100000
MAX_RISK_PER_TRADE = 0.02  # 2% ($2,000)
SPREAD_WIDTH       = 5.0
MIN_DTE            = 7
MAX_DTE            = 45
MIN_PROB_PROFIT    = 85.0  # Statistical floor
SLIPPAGE_ADJUST    = 0.02  # 2% haircut on mid-price

# Liquidity Guards (Crucial for real fills)
MIN_BID_PRICE      = 0.10  # Ignore anything paying less than $10 per contract
MAX_BID_ASK_RATIO  = 2.5   # Ignore if Ask is more than 2.5x the Bid (Illiquid)
MIN_OI             = 200

OUTPUT_FILE = "signals.json"

HEADERS = {
    "Authorization": f"Bearer {TRADIER_API_KEY}",
    "Accept": "application/json"
}

# ─────────────────────────────────────────────
# RISK & ANALYTICS ENGINE
# ─────────────────────────────────────────────

def calculate_institutional_metrics(short_delta, net_credit, max_loss, price, spy_price, ticker_beta=1.2):
    p_loss = abs(short_delta)
    p_win = 1.0 - p_loss
    ev = (p_win * net_credit) - (p_loss * max_loss)
    pop = p_win * 100

    dollar_risk_cap = ACCOUNT_SIZE * MAX_RISK_PER_TRADE
    risk_per_spread = max_loss * 100
    recommended_qty = math.floor(dollar_risk_cap / risk_per_spread) if risk_per_spread > 0 else 0

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
    
    spy_data = fetch_data("markets/quotes", {"symbols": BENCHMARK, "greeks": "true"})
    if not spy_data or "quotes" not in spy_data:
        print("Fatal: Could not fetch SPY data.")
        return
        
    spy_price = float(spy_data["quotes"]["quote"]["last"])
    spy_hist = get_historical_closes(BENCHMARK)
    
    all_signals = []
    
    for symbol in SYMBOLS:
        print(f"  > Scanning {symbol}...", end="\r")
        
        quote_data = fetch_data("markets/quotes", {"symbols": symbol, "greeks": "true"})
        if not quote_data or not quote_data.get("quotes"): continue
        quote = quote_data["quotes"]["quote"]
        price = float(quote["last"])
        hist = get_historical_closes(symbol)
        correlation = get_correlation(hist, spy_hist)

        exp_data = fetch_data("markets/options/expirations", {"symbol": symbol})
        if not exp_data: continue
        dates = exp_data.get("expirations", {}).get("date", [])
        if isinstance(dates, str): dates = [dates]
        
        valid_dates = [d for d in dates if MIN_DTE <= (datetime.datetime.strptime(d, "%Y-%m-%d").date() - datetime.date.today()).days <= MAX_DTE]

        for exp in valid_dates:
            chain = fetch_data("markets/options/chains", {"symbol": symbol, "expiration": exp, "greeks": "true"})
            options = chain.get("options", {}).get("option", []) if chain else []
            if isinstance(options, dict): options = [options]

            puts = [o for o in options if o["option_type"] == "put"]
            by_strike = {float(o["strike"]): o for o in puts}

            for strike, short_opt in by_strike.items():
                long_opt = by_strike.get(strike - SPREAD_WIDTH)
                if not long_opt: continue

                # --- INSTITUTIONAL LIQUIDITY GUARD ---
                s_bid = float(short_opt.get("bid", 0))
                s_ask = float(short_opt.get("ask", 0))
                
                # Check 1: Must have a real buyer (No $0.00 bids)
                if s_bid < MIN_BID_PRICE: continue
                
                # Check 2: Bid/Ask spread must be tight enough to execute
                if s_bid > 0 and (s_ask / s_bid) > MAX_BID_ASK_RATIO: continue
                
                # Check 3: Open Interest check
                if int(short_opt.get("open_interest", 0)) < MIN_OI: continue

                # Mid-price calculation with Slippage Haircut
                l_bid, l_ask = float(long_opt.get("bid", 0)), float(long_opt.get("ask", 0))
                mid_credit = ((s_bid + s_ask)/2) - ((l_bid + l_ask)/2)
                net_credit = mid_credit * (1 - SLIPPAGE_ADJUST)
                max_loss = SPREAD_WIDTH - net_credit
                
                if net_credit <= 0.05: continue

                # Risk Analytics
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

    all_signals.sort(key=lambda x: x["edge_ratio"], reverse=True)

    report = {
        "scan_time": datetime.datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "account_basis": ACCOUNT_SIZE,
        "benchmark_spy": spy_price,
        "top_signals": all_signals[:20]
    }

    with open(OUTPUT_FILE, "w") as f:
        json.dump(report, f, indent=4)

    print(f"\n[SUCCESS] Scan complete. {len(all_signals)} valid signals written to {OUTPUT_FILE}")

if __name__ == "__main__":
    run_workstation_scan()
