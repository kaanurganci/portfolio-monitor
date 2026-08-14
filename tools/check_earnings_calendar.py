import csv
import json
import yfinance as yf
from datetime import date, datetime, timedelta
from pathlib import Path

PORTFOLIO_CSV = Path("config/portfolio.csv")
CATALYST_JSON = Path("config/catalyst_calendar.json")
ETF_TICKERS = {"VOO", "VWRP"}


def get_upcoming_earnings(tickers, days_ahead=30):
    results = []
    cutoff = date.today() + timedelta(days=days_ahead)
    for ticker in tickers:
        if ticker in ETF_TICKERS:
            continue
        try:
            t = yf.Ticker(ticker)
            cal = t.calendar
            if cal is None or cal.empty or "Earnings Date" not in cal.columns:
                continue
            earnings_date = cal["Earnings Date"].iloc[0].date()
            days_until = (earnings_date - date.today()).days
            if 0 <= days_until <= days_ahead:
                results.append({
                    "ticker": ticker,
                    "earnings_date": str(earnings_date),
                    "days_until": days_until,
                })
        except Exception:
            continue
    return sorted(results, key=lambda x: x["days_until"])


def load_portfolio_tickers():
    tickers = []
    with open(PORTFOLIO_CSV) as f:
        for row in csv.DictReader(f):
            tickers.append(row["ticker"])
    return tickers


def update_catalyst_calendar(earnings_list):
    data = json.loads(CATALYST_JSON.read_text())
    existing_tickers = {e["ticker"] for e in data["events"] if e.get("type") == "earnings"}
    for item in earnings_list:
        if item["ticker"] not in existing_tickers:
            data["events"].append({
                "type": "earnings",
                "ticker": item["ticker"],
                "date": item["earnings_date"],
                "days_until": item["days_until"],
            })
    data["_meta"]["last_updated"] = datetime.utcnow().isoformat()
    CATALYST_JSON.write_text(json.dumps(data, indent=2))


if __name__ == "__main__":
    tickers = load_portfolio_tickers()
    upcoming = get_upcoming_earnings(tickers, days_ahead=30)
    if upcoming:
        print("Upcoming earnings:")
        for e in upcoming:
            print(f"  {e['ticker']}: {e['earnings_date']} ({e['days_until']} days)")
        update_catalyst_calendar(upcoming)
    else:
        print("No earnings in next 30 days.")
