"""Deterministic retro-grading of decisions — no LLM involved.

For every ungraded decision whose horizon has elapsed:
  - backfill price_at_call / benchmark_at_call from price history if missing
  - compute the holding's return over the horizon vs the benchmark (VWRP.L)
  - grade on relative return in the called direction:
        bullish:  rel > +3%  correct | within ±3% partial | < -3% wrong
        bearish:  rel < -3%  correct | within ±3% partial | > +3% wrong
        neutral:  ungradable (alerts and both-sides recaps are not calls)

The LLM only ever writes post-mortem notes for 'wrong' grades during the
monthly review. It never assigns grades.
"""

import csv
import sys
from datetime import datetime, timedelta, UTC
from pathlib import Path

import pandas as pd
import yfinance as yf

sys.path.insert(0, str(Path(__file__).parent))
import state_db

PORTFOLIO_CSV = Path("config/portfolio.csv")
BENCHMARK = "VWRP.L"
REL_THRESHOLD = 3.0  # percentage points vs benchmark


def yf_ticker_map():
    m = {}
    with PORTFOLIO_CSV.open() as f:
        for row in csv.DictReader(f):
            m[row["ticker"]] = row["yfinance_ticker"]
    return m


def _price_on_or_after(series, date):
    """First close on/after date (call date may be a weekend)."""
    s = series.loc[series.index >= pd.Timestamp(date)].dropna()
    return float(s.iloc[0]) if len(s) else None


def grade_all(conn, today=None):
    today = today or datetime.now(UTC).date()
    rows = conn.execute(
        "SELECT * FROM decisions WHERE grade IS NULL ORDER BY made_at").fetchall()
    if not rows:
        return []

    graded = []

    # Neutral entries are never price-gradable — settle them immediately.
    for r in rows:
        if r["direction"] == "neutral":
            conn.execute(
                "UPDATE decisions SET grade='ungradable', graded_at=?, grade_notes=? WHERE id=?",
                (state_db.now_iso(),
                 "Not a directional call (alert / both-sides recap) — excluded from hit rate.",
                 r["id"]))
            graded.append({"id": r["id"], "ticker": r["ticker"], "grade": "ungradable"})

    due = [r for r in rows if r["direction"] != "neutral"
           and datetime.fromisoformat(r["made_at"][:10]).date()
           + timedelta(days=r["horizon_days"]) <= today]
    if not due:
        conn.commit()
        return graded

    tmap = yf_ticker_map()
    tickers = sorted({tmap.get(r["ticker"], r["ticker"]) for r in due} | {BENCHMARK})
    earliest = min(r["made_at"][:10] for r in due)
    prices = yf.download(tickers, start=earliest, auto_adjust=True, progress=False)["Close"]
    if isinstance(prices, pd.Series):
        prices = prices.to_frame(name=tickers[0])
    prices.index = prices.index.tz_localize(None)

    for r in due:
        yft = tmap.get(r["ticker"], r["ticker"])
        if yft not in prices.columns or BENCHMARK not in prices.columns:
            note = f"No price history for {yft} — cannot grade."
            conn.execute("UPDATE decisions SET grade='ungradable', graded_at=?, grade_notes=? WHERE id=?",
                         (state_db.now_iso(), note, r["id"]))
            graded.append({"id": r["id"], "ticker": r["ticker"], "grade": "ungradable"})
            continue

        call_date = r["made_at"][:10]
        p0 = r["price_at_call"] or _price_on_or_after(prices[yft], call_date)
        b0 = r["benchmark_at_call"] or _price_on_or_after(prices[BENCHMARK], call_date)
        p1 = float(prices[yft].dropna().iloc[-1])
        b1 = float(prices[BENCHMARK].dropna().iloc[-1])
        if not p0 or not b0:
            conn.execute("UPDATE decisions SET grade='ungradable', graded_at=?, grade_notes=? WHERE id=?",
                         (state_db.now_iso(), "Missing at-call price — cannot grade.", r["id"]))
            graded.append({"id": r["id"], "ticker": r["ticker"], "grade": "ungradable"})
            continue

        ret = (p1 / p0 - 1) * 100
        bench_ret = (b1 / b0 - 1) * 100
        rel = ret - bench_ret

        if r["direction"] == "bullish":
            grade = "correct" if rel > REL_THRESHOLD else ("wrong" if rel < -REL_THRESHOLD else "partial")
        else:  # bearish
            grade = "correct" if rel < -REL_THRESHOLD else ("wrong" if rel > REL_THRESHOLD else "partial")

        notes = (f"{r['direction']} call graded over {r['horizon_days']}d: {r['ticker']} "
                 f"{ret:+.1f}% vs {BENCHMARK} {bench_ret:+.1f}% -> relative {rel:+.1f}pp.")
        conn.execute(
            "UPDATE decisions SET grade=?, graded_at=?, grade_notes=?, "
            "price_at_call=?, benchmark_at_call=? WHERE id=?",
            (grade, state_db.now_iso(), notes, round(p0, 4), round(b0, 4), r["id"]))
        graded.append({"id": r["id"], "ticker": r["ticker"], "grade": grade, "relative_pp": round(rel, 1)})

    conn.commit()
    return graded


if __name__ == "__main__":
    conn = state_db.connect()
    results = grade_all(conn)
    if not results:
        print("Nothing due for grading.")
    for g in results:
        print(f"  #{g['id']} {g['ticker']}: {g['grade']}"
              + (f" ({g['relative_pp']:+.1f}pp vs benchmark)" if "relative_pp" in g else ""))
    hr = state_db.hit_rate(conn)
    if hr:
        print("Hit rate by call type:", hr)
