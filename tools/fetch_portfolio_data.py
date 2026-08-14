import csv
import json
import yfinance as yf
from datetime import datetime, UTC
from pathlib import Path

PORTFOLIO_CSV = Path("config/portfolio.csv")
OUTPUT_JSON = Path(".tmp/portfolio_data.json")
LSE_CURRENCIES = {"GBp", "GBX"}  # yfinance returns GBp (pence) for LSE


def get_usd_gbp_rate():
    ticker = yf.Ticker("GBPUSD=X")
    rate = ticker.fast_info.last_price
    return float(rate) if rate else 1.27  # fallback


def get_eur_gbp_rate():
    ticker = yf.Ticker("EURGBP=X")
    rate = ticker.fast_info.last_price
    return float(rate) if rate else 0.85  # fallback


def _price_pair(ticker_obj):
    """(last_price, prev_close) via fast_info, falling back to history()
    for symbols where fast_info is broken (e.g. some Xetra ETF lines)."""
    try:
        price = ticker_obj.fast_info.last_price
        prev = ticker_obj.fast_info.previous_close
        if price:
            return float(price), float(prev or price)
    except Exception:
        pass
    hist = ticker_obj.history(period="5d")["Close"].dropna()
    if hist.empty:
        raise ValueError("no price data (fast_info and history both empty)")
    price = float(hist.iloc[-1])
    prev = float(hist.iloc[-2]) if len(hist) > 1 else price
    return price, prev


def to_gbp(price, currency, usd_gbp_rate, eur_gbp_rate):
    if currency in LSE_CURRENCIES:
        return price / 100
    if currency == "GBP":
        return price
    if currency == "EUR":
        return price * eur_gbp_rate
    return price / usd_gbp_rate  # USD and default


def build_row(ticker, yf_ticker, ticker_obj, usd_gbp_rate, eur_gbp_rate):
    try:
        info = ticker_obj.info
    except Exception:
        info = {}
    try:
        currency = getattr(ticker_obj.fast_info, "currency", None) or info.get("currency") or "USD"
    except Exception:
        currency = info.get("currency") or "USD"
    price_raw, prev_close = _price_pair(ticker_obj)

    price_gbp = to_gbp(price_raw, currency, usd_gbp_rate, eur_gbp_rate)
    price_usd = price_gbp * usd_gbp_rate

    day_change_pct = ((price_raw - prev_close) / prev_close * 100) if prev_close else 0.0

    return {
        "ticker": ticker,
        "yf_ticker": yf_ticker,
        "price_usd": round(price_usd, 4),
        "price_gbp": round(price_gbp, 4),
        "day_change_pct": round(day_change_pct, 2),
        "pe_ratio": info.get("trailingPE"),
        "market_cap_usd": info.get("marketCap"),
        "week_high": info.get("fiftyTwoWeekHigh"),
        "week_low": info.get("fiftyTwoWeekLow"),
        "currency": currency,
        "fetched_at": datetime.now(UTC).isoformat(),
    }


def fetch_all(output_path=OUTPUT_JSON):
    output_path = Path(output_path)
    output_path.parent.mkdir(exist_ok=True)

    usd_gbp_rate = get_usd_gbp_rate()
    eur_gbp_rate = get_eur_gbp_rate()
    rows = []

    with open(PORTFOLIO_CSV) as f:
        reader = csv.DictReader(f)
        for record in reader:
            ticker = record["ticker"]
            yf_ticker = record["yfinance_ticker"]
            try:
                obj = yf.Ticker(yf_ticker)
                row = build_row(ticker, yf_ticker, obj, usd_gbp_rate, eur_gbp_rate)
                row["shares"] = float(record["shares"])
                row["weekly_contribution"] = float(record.get("Weekly Contribution", 0))
                rows.append(row)
                print(f"  {ticker}: £{row['price_gbp']:.2f} ({row['day_change_pct']:+.1f}%)")
            except Exception as e:
                print(f"  ERROR {ticker}: {e}")
                rows.append({"ticker": ticker, "error": str(e)})

    result = {"fetched_at": datetime.now(UTC).isoformat(), "usd_gbp_rate": usd_gbp_rate, "holdings": rows}
    output_path.write_text(json.dumps(result, indent=2))
    print(f"\nSaved to {output_path}")
    return result


if __name__ == "__main__":
    fetch_all()
