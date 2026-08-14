import argparse
import json
from pathlib import Path
from collections import defaultdict

PORTFOLIO_DATA = Path(".tmp/portfolio_data.json")
PORTFOLIO_CSV = Path("config/portfolio.csv")
CONCENTRATION_THRESHOLD = 30.0
CORRELATION_THRESHOLD = 0.7
CORRELATION_WINDOW_DAYS = 90

# Sector labels for the sample portfolio. A holding whose ticker is absent here
# falls back to "Other". Swap this map to match your own config/portfolio.csv.
SECTOR_MAP = {
    "VOO": "ETF (S&P 500)", "VWRP": "ETF (All-World)",
    "AAPL": "Big Tech", "MSFT": "Big Tech", "GOOGL": "Big Tech", "AMZN": "Big Tech",
    "NVDA": "Semiconductors", "AMD": "Semiconductors",
    "PLTR": "AI / Data Analytics", "RIVN": "EV / Innovation",
}


def compute_sector_weights(holdings):
    sector_value = defaultdict(float)
    total_value = 0.0

    for h in holdings:
        val = h["price_gbp"] * h["shares"]
        sector = h.get("sector") or SECTOR_MAP.get(h["ticker"], "Other")
        sector_value[sector] += val
        total_value += val

    if total_value == 0:
        return []

    result = []
    for sector, value in sorted(sector_value.items(), key=lambda x: -x[1]):
        pct = value / total_value * 100
        result.append({
            "sector": sector,
            "value_gbp": round(value, 2),
            "weight_pct": round(pct, 2),
            "concentration_flag": pct > CONCENTRATION_THRESHOLD,
        })
    return result


def generate_plain_english_summary(sector_weights, benchmark_change_pct=None):
    lines = ["Your portfolio by sector this week:"]
    for s in sector_weights:
        flag = " ⚠️ CONCENTRATED" if s["concentration_flag"] else ""
        lines.append(f"  {s['sector']}: {s['weight_pct']:.1f}% of portfolio{flag}")
    if benchmark_change_pct is not None:
        lines.append(f"\nFor reference, the all-world benchmark (VWRP) moved {benchmark_change_pct:+.1f}% this week.")
    return "\n".join(lines)


def get_correlation_clusters(tickers=None, window_days=CORRELATION_WINDOW_DAYS, threshold=CORRELATION_THRESHOLD):
    import csv
    import yfinance as yf
    import pandas as pd

    if tickers is None:
        with PORTFOLIO_CSV.open() as f:
            tickers = [row["yfinance_ticker"] for row in csv.DictReader(f)]

    prices = yf.download(tickers, period=f"{window_days}d", auto_adjust=True, progress=False)["Close"]
    returns = prices.pct_change().dropna(how="all")
    corr = returns.corr()

    clusters = []
    seen = set()
    for i, a in enumerate(corr.columns):
        for b in corr.columns[i + 1:]:
            r = corr.loc[a, b]
            if pd.notna(r) and r >= threshold:
                clusters.append({"ticker_a": a, "ticker_b": b, "correlation": round(float(r), 3)})
                seen.add(a)
                seen.add(b)

    clusters.sort(key=lambda c: -c["correlation"])
    return {"window_days": window_days, "threshold": threshold, "pairs": clusters, "tickers_in_cluster": sorted(seen)}


def generate_correlation_summary(result):
    if not result["pairs"]:
        lines = [f"Pairwise correlation (last {result['window_days']} days, threshold {result['threshold']}):"]
        lines.append("  No holding pairs exceed the correlation threshold — true diversification looks healthy.")
        return "\n".join(lines)

    lines = [
        f"Pairwise correlation check (last {result['window_days']} days, threshold {result['threshold']}):",
        f"  Found {len(result['pairs'])} pair(s) that move together more than {int(result['threshold']*100)}% of the time.",
        "  In plain English: when one moves, the other tends to move the same direction — so they are not independent positions.",
        "",
    ]
    for p in result["pairs"]:
        lines.append(f"  {p['ticker_a']} ↔ {p['ticker_b']}: correlation {p['correlation']}")
    return "\n".join(lines)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Portfolio analytics: sector weights and correlation clusters.")
    parser.add_argument("--correlation", action="store_true", help="Run pairwise correlation analysis instead of sector weights.")
    parser.add_argument("--window-days", type=int, default=CORRELATION_WINDOW_DAYS, help=f"Correlation window (default {CORRELATION_WINDOW_DAYS}).")
    parser.add_argument("--threshold", type=float, default=CORRELATION_THRESHOLD, help=f"Correlation flag threshold (default {CORRELATION_THRESHOLD}).")
    args = parser.parse_args()

    if args.correlation:
        result = get_correlation_clusters(window_days=args.window_days, threshold=args.threshold)
        print(generate_correlation_summary(result))
    else:
        data = json.loads(PORTFOLIO_DATA.read_text())
        holdings = [h for h in data["holdings"] if "error" not in h]
        weights = compute_sector_weights(holdings)
        print(generate_plain_english_summary(weights))
