"""Daily exception screen — deterministic, no LLM. The system's daily entry point.

Runs every day via Task Scheduler. Steps:
  1. fetch prices -> snapshot into SQLite
  2. auto-grade any decisions whose horizon elapsed
  3. evaluate triggers T1-T6 (T7 staleness feeds the monthly only)
  4. no triggers -> heartbeat row, exit silently (no email, no LLM)
     triggers   -> invoke run_agent.py (headless Opus) for ONE focused email
  5. first Saturday of the month -> monthly review runs regardless of triggers

Triggers:
  T1 stat anomaly   |1d move| > 2.5 sigma of the holding's own 90d daily vol,
                    or |5d move| > 2.5 sigma * sqrt(5)
  T2 drawdown       portfolio value > 5% below its 30-day high
  T3 rule breach    any BREACH finding from risk_check (warnings feed monthly)
  T4 earnings       a holding reports within 5 days (batched)
  T5 watch item     date-type trigger date reached; price-type "TICKER >= x" crossed
  T6 invalidation   machine-checkable price condition ("price" metric) breached

A trigger key fired in an exception run within the last SUPPRESS_DAYS days is
suppressed so the same event doesn't email daily.

Debug flags:
  --force-trigger T1:PLTR   inject a synthetic trigger
  --dry-run                 evaluate and print, but never invoke the LLM
  --monthly                 force the monthly run now
"""

import argparse
import calendar
import json
import math
import re
import subprocess
import sys
from datetime import date, datetime, UTC
from pathlib import Path

import pandas as pd
import yfinance as yf

# Windows consoles default to cp1252; state text contains unicode arrows/sigils
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent))
import state_db
from fetch_portfolio_data import fetch_all
from risk_check import run_checks, ETF_TICKERS
from grade_decisions import grade_all, yf_ticker_map
from check_earnings_calendar import get_upcoming_earnings
from send_email import send_email
import build_digest

SIGMA_MULT = 2.5
DRAWDOWN_PCT = 5.0
EARNINGS_WINDOW_DAYS = 5
SUPPRESS_DAYS = 7
TRIGGERS_FILE = Path(".tmp/triggers.json")

PRICE_COND = re.compile(r"^\s*([A-Z0-9.\-=]+)\s*(<=|>=|<|>)\s*([\d.]+)\s*$")


def snapshot(conn, data):
    today = date.today().isoformat()
    holdings = [h for h in data["holdings"] if "error" not in h]
    total = sum(h["price_gbp"] * h["shares"] for h in holdings)
    for h in holdings:
        value = h["price_gbp"] * h["shares"]
        conn.execute(
            "INSERT OR REPLACE INTO snapshots (date, ticker, price_gbp, value_gbp, weight_pct) VALUES (?,?,?,?,?)",
            (today, h["ticker"], round(h["price_gbp"], 4), round(value, 2),
             round(value / total * 100, 2)))
    conn.execute(
        "INSERT OR REPLACE INTO snapshots (date, ticker, price_gbp, value_gbp, weight_pct) VALUES (?,?,?,?,?)",
        (today, "_TOTAL", None, round(total, 2), 100.0))
    conn.commit()
    return total


def price_history(days=95):
    tmap = yf_ticker_map()
    tickers = sorted(set(tmap.values()))
    prices = yf.download(tickers, period=f"{days}d", auto_adjust=True, progress=False)["Close"]
    if isinstance(prices, pd.Series):
        prices = prices.to_frame()
    rev = {v: k for k, v in tmap.items()}
    prices.columns = [rev.get(c, c) for c in prices.columns]
    return prices


def t1_stat_anomalies(prices):
    out = []
    returns = prices.pct_change()
    for t in prices.columns:
        r = returns[t].dropna()
        if len(r) < 40:
            continue
        sigma = float(r.iloc[:-1].std())
        if not sigma or math.isnan(sigma):
            continue
        d1 = float(r.iloc[-1])
        closes = prices[t].dropna()
        d5 = float(closes.iloc[-1] / closes.iloc[-6] - 1) if len(closes) > 6 else 0.0
        if abs(d1) > SIGMA_MULT * sigma:
            out.append({"key": f"T1:{t}:1d", "type": "T1_stat_anomaly", "ticker": t,
                        "detail": f"{t} moved {d1 * 100:+.1f}% in 1 day = {abs(d1) / sigma:.1f} sigma "
                                  f"vs its own 90d daily vol ({sigma * 100:.1f}%)."})
        elif abs(d5) > SIGMA_MULT * sigma * math.sqrt(5):
            out.append({"key": f"T1:{t}:5d", "type": "T1_stat_anomaly", "ticker": t,
                        "detail": f"{t} moved {d5 * 100:+.1f}% in 5 days = "
                                  f"{abs(d5) / (sigma * math.sqrt(5)):.1f} sigma (5d-scaled)."})
    return out


def t2_drawdown(conn, prices, current_total):
    # Prefer real snapshots once we have 30 days of them; bootstrap from
    # price history x current shares before that.
    rows = conn.execute(
        "SELECT date, value_gbp FROM snapshots WHERE ticker='_TOTAL' ORDER BY date DESC LIMIT 30").fetchall()
    if len(rows) >= 20:
        high = max(r["value_gbp"] for r in rows)
    else:
        import csv
        shares = {}
        with open("config/portfolio.csv") as f:
            for row in csv.DictReader(f):
                shares[row["ticker"]] = float(row["shares"])
        recent = prices.tail(30)
        totals = (recent * pd.Series(shares)).sum(axis=1, skipna=True)
        high = float(totals.max()) if len(totals) else current_total
        # bootstrap mixes GBp/USD/EUR units; scale by aligning last row to today's GBP total
        if len(totals) and totals.iloc[-1] > 0:
            high = high / totals.iloc[-1] * current_total
    dd = (high - current_total) / high * 100 if high else 0.0
    if dd > DRAWDOWN_PCT:
        return [{"key": f"T2:{date.today().isocalendar().week}", "type": "T2_drawdown",
                 "detail": f"Portfolio is {dd:.1f}% below its 30-day high "
                           f"(£{current_total:,.0f} vs £{high:,.0f})."}]
    return []


def t3_rule_breaches(report):
    return [{"key": f"T3:{f['rule']}", "type": "T3_rule_breach", "detail": f["message"], "finding": f}
            for f in report["findings"] if f["severity"] == "BREACH"]


def t4_earnings(conn):
    tmap = yf_ticker_map()
    non_etf = [v for k, v in tmap.items() if k not in ETF_TICKERS]
    upcoming = get_upcoming_earnings(non_etf, days_ahead=EARNINGS_WINDOW_DAYS)
    out = []
    rev = {v: k for k, v in tmap.items()}
    for e in upcoming:
        t = rev.get(e["ticker"], e["ticker"])
        out.append({"key": f"T4:{t}:{e['earnings_date']}", "type": "T4_earnings", "ticker": t,
                    "detail": f"{t} reports earnings on {e['earnings_date']} "
                              f"({e['days_until']} days away)."})
    return out


def t5_watch_items(conn, latest_prices):
    out = []
    today = date.today().isoformat()
    for w in conn.execute("SELECT * FROM watch_items WHERE status='pending'"):
        if w["trigger_type"] == "date" and w["trigger_value"] and w["trigger_value"] <= today:
            out.append({"key": f"T5:{w['id']}", "type": "T5_watch_item", "watch_id": w["id"],
                        "detail": f"Watch item [{w['id']}] reached its trigger date "
                                  f"{w['trigger_value']}: {w['description']}"})
        elif w["trigger_type"] == "price" and w["trigger_value"]:
            m = PRICE_COND.match(w["trigger_value"])
            if not m:
                continue
            t, op, thresh = m.group(1), m.group(2), float(m.group(3))
            px = latest_prices.get(t)
            if px is None:
                continue
            hit = {"<": px < thresh, "<=": px <= thresh, ">": px > thresh, ">=": px >= thresh}[op]
            if hit:
                out.append({"key": f"T5:{w['id']}", "type": "T5_watch_item", "watch_id": w["id"],
                            "detail": f"Watch item [{w['id']}] price trigger hit: {t} at {px:.2f} "
                                      f"{op} {thresh}: {w['description']}"})
    return out


def t6_invalidation_conditions(conn, latest_prices):
    """Machine-checkable price conditions use metric 'price:<TICKER>'."""
    out = []
    for c in conn.execute("SELECT * FROM invalidation_conditions WHERE status='open'"):
        if not c["metric"].startswith("price:"):
            continue
        t = c["metric"].split(":", 1)[1]
        px = latest_prices.get(t)
        try:
            thresh = float(c["threshold"])
        except (TypeError, ValueError):
            continue
        if px is None:
            continue
        op = c["comparator"]
        hit = {"<": px < thresh, "<=": px <= thresh, ">": px > thresh, ">=": px >= thresh}.get(op)
        if hit:
            out.append({"key": f"T6:{c['id']}", "type": "T6_invalidation", "condition_id": c["id"],
                        "ticker": c["ticker"],
                        "detail": f"{c['side']} condition [{c['id']}] on {c['ticker']} hit: "
                                  f"{c['metric']} {op} {thresh} (price {px:.2f})."})
    return out


def recent_trigger_keys(conn, days=SUPPRESS_DAYS):
    keys = set()
    for r in conn.execute(
            "SELECT triggers_json FROM runs WHERE kind IN ('exception','monthly') "
            "AND ts >= datetime('now', ?) AND triggers_json IS NOT NULL",
            (f"-{days} days",)):
        try:
            for t in json.loads(r["triggers_json"]):
                keys.add(t.get("key"))
        except (json.JSONDecodeError, AttributeError):
            pass
    return keys


def is_first_saturday(d=None):
    d = d or date.today()
    return d.weekday() == 5 and d.day <= 7


def send_heartbeat(conn, risk_report, suppressed, fetch_errors):
    """Deterministic 'all-clear' email for quiet days — no LLM.

    Confirms the screen ran and nothing crossed a threshold, plus a small
    health snapshot and the next things being watched. Returns True if sent.
    """
    today = date.today()
    total = risk_report["total_value_gbp"]
    etf = risk_report["etf_weight_pct"]
    sleeve = risk_report["speculative_sleeve_pct"]

    lines = [
        f"Daily all-clear — {today:%A, %d %b %Y}.",
        "",
        "The screen ran and nothing crossed a trigger: no >2.5-sigma moves, no rule",
        "breach, no watch item due, no earnings inside 5 days. Your positions need no",
        "action today. (This is the routine daily confirmation the system ran — real",
        "events arrive as separate, clearly-titled emails.)",
        "",
        "SNAPSHOT",
        f"  Portfolio value: GBP {total:,.0f}",
        f"  ETF share: {etf:.1f}% (floor 50%)",
        f"  Speculative sleeve: {sleeve:.1f}% (cap 20%)",
    ]

    standing = [f for f in risk_report["findings"] if f["severity"] in ("WARNING", "INFO")]
    if standing:
        lines += ["", "STANDING NOTES (not new — persist until addressed)"]
        for f in standing:
            lines.append(f"  - {f['message']}")

    # next date-triggered watch items
    upcoming = conn.execute(
        "SELECT description, trigger_value FROM watch_items "
        "WHERE status='pending' AND trigger_type='date' AND trigger_value >= ? "
        "ORDER BY trigger_value LIMIT 5", (today.isoformat(),)).fetchall()
    pending_total = conn.execute(
        "SELECT COUNT(*) FROM watch_items WHERE status='pending'").fetchone()[0]
    lines += ["", f"WATCHING ({pending_total} open items)"]
    if upcoming:
        for w in upcoming:
            lines.append(f"  - {w['trigger_value']}: {w['description']}")
    else:
        lines.append("  - No date-triggered items scheduled; the rest fire on price or event.")

    cats = conn.execute(
        "SELECT event_date, ticker, event FROM catalysts WHERE consumed=0 "
        "AND event_date >= ? ORDER BY event_date LIMIT 5", (today.isoformat(),)).fetchall()
    if cats:
        lines += ["", "UPCOMING CATALYSTS"]
        for c in cats:
            lines.append(f"  - {c['event_date']} {c['ticker'] or 'MACRO'}: {c['event']}")

    if suppressed:
        lines += ["", f"({len(suppressed)} trigger(s) already handled in the last "
                      f"{SUPPRESS_DAYS} days are held quiet to avoid repeats.)"]
    if fetch_errors:
        lines += ["", f"WARNING: price fetch failed for {', '.join(fetch_errors)} — "
                      f"check for a ticker/listing change."]

    send_email(f"Portfolio all-clear — no action ({today:%d %b %Y})", "\n".join(lines))
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force-trigger", action="append", default=[],
                    help="inject synthetic trigger, e.g. T1:PLTR")
    ap.add_argument("--dry-run", action="store_true", help="never invoke the LLM")
    ap.add_argument("--monthly", action="store_true", help="force monthly run")
    ap.add_argument("--skip-fetch", action="store_true", help="reuse existing .tmp data (debug)")
    args = ap.parse_args()

    conn = state_db.connect()

    print("[screen] fetching prices...")
    if args.skip_fetch:
        data = json.loads(Path(".tmp/portfolio_data.json").read_text())
    else:
        data = fetch_all()
    total = snapshot(conn, data)
    fetch_errors = [h["ticker"] for h in data["holdings"] if "error" in h]

    print("[screen] grading due decisions...")
    graded = grade_all(conn)
    newly_wrong = [g for g in graded if g.get("grade") == "wrong"]

    print("[screen] evaluating triggers...")
    prices = price_history()
    latest = {t: float(prices[t].dropna().iloc[-1]) for t in prices.columns
              if len(prices[t].dropna())}
    risk_report = run_checks()

    triggers = []
    triggers += t1_stat_anomalies(prices)
    triggers += t2_drawdown(conn, prices, total)
    triggers += t3_rule_breaches(risk_report)
    triggers += t4_earnings(conn)
    triggers += t5_watch_items(conn, latest)
    triggers += t6_invalidation_conditions(conn, latest)

    for ft in args.force_trigger:
        triggers.append({"key": f"FORCED:{ft}", "type": ft.split(":")[0] + "_forced",
                         "detail": f"Synthetic trigger injected for testing: {ft}"})

    seen = recent_trigger_keys(conn)
    suppressed = [t for t in triggers if t["key"] in seen]
    active = [t for t in triggers if t["key"] not in seen]

    monthly_due = args.monthly or is_first_saturday()

    for t in active:
        print(f"  ACTIVE    {t['key']}: {t['detail']}")
    for t in suppressed:
        print(f"  suppressed {t['key']} (already handled in last {SUPPRESS_DAYS}d)")

    if not active and not monthly_due:
        build_digest.build(conn)
        sent = False
        if not args.dry_run:
            try:
                sent = send_heartbeat(conn, risk_report, suppressed, fetch_errors)
                print("[screen] no exceptions — sent daily all-clear email.")
            except Exception as e:
                print(f"[screen] no exceptions — heartbeat email FAILED: {e}")
        else:
            print("[screen] no exceptions — DRY RUN, heartbeat not sent.")
        state_db.log_run(conn, "screen", triggers=[], email_sent=sent,
                         notes=f"quiet day (heartbeat sent={sent}); {len(suppressed)} suppressed; "
                               f"fetch errors: {fetch_errors or 'none'}")
        return 0

    kind = "monthly" if monthly_due else "exception"
    payload = {
        "kind": kind,
        "triggers": active,
        "suppressed": [t["key"] for t in suppressed],
        "fetch_errors": fetch_errors,
        "newly_graded_wrong": newly_wrong,
    }
    TRIGGERS_FILE.parent.mkdir(exist_ok=True)
    TRIGGERS_FILE.write_text(json.dumps(payload, indent=2))

    if args.dry_run:
        print(f"[screen] DRY RUN — would invoke run_agent --kind {kind} "
              f"with {len(active)} trigger(s). Payload at {TRIGGERS_FILE}")
        state_db.log_run(conn, "screen", triggers=active, notes="dry run — LLM not invoked")
        build_digest.build(conn)
        return 0

    print(f"[screen] invoking agent ({kind}, {len(active)} triggers)...")
    agent_log = Path(".tmp/agent_run.log")
    with agent_log.open("w", encoding="utf-8") as log:
        proc = subprocess.Popen(
            [sys.executable, "tools/run_agent.py", "--kind", kind,
             "--triggers-file", str(TRIGGERS_FILE)],
            stdout=log, stderr=subprocess.STDOUT, text=True, encoding="utf-8")
        try:
            proc.wait(timeout=2700)
        except subprocess.TimeoutExpired:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
            proc.returncode = -1
    result_text = agent_log.read_text(encoding="utf-8", errors="replace")
    print(result_text[-3000:])
    if proc.returncode != 0:
        # log WITHOUT trigger keys so a failed run never suppresses re-firing
        state_db.log_run(conn, kind, triggers=None, email_sent=False,
                         notes=f"run_agent FAILED rc={proc.returncode}; "
                               f"unhandled triggers: {[t['key'] for t in active]}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
