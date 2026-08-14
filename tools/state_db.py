"""Persistent state store for the portfolio system.

SQLite is the single source of truth (replaces the three Google Docs).
Two consumers:
  - deterministic tools (daily_screen, risk_check, grade_decisions) read/write directly
  - the LLM never touches the DB: it emits a JSON state-patch that
    apply_patch() validates and applies. Malformed ops are rejected and logged,
    never partially applied.
"""

import json
import sqlite3
from datetime import datetime, UTC
from pathlib import Path

DB_PATH = Path("state/portfolio.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS theses (
    ticker      TEXT PRIMARY KEY,
    company_name TEXT,
    sector      TEXT,
    bull_case   TEXT,
    bear_case   TEXT,
    key_kpi     TEXT,
    status      TEXT NOT NULL DEFAULT 'INTACT'
                CHECK (status IN ('INTACT','STRESSED','INVALIDATED')),
    updated_at  TEXT
);

CREATE TABLE IF NOT EXISTS invalidation_conditions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker      TEXT NOT NULL REFERENCES theses(ticker),
    side        TEXT NOT NULL CHECK (side IN ('invalidate','confirm')),
    metric      TEXT NOT NULL,
    comparator  TEXT NOT NULL,
    threshold   TEXT NOT NULL,
    window_desc TEXT,
    deadline    TEXT,
    status      TEXT NOT NULL DEFAULT 'open'
                CHECK (status IN ('open','breached','confirmed','expired')),
    evidence    TEXT,
    checked_at  TEXT
);

CREATE TABLE IF NOT EXISTS watch_items (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker       TEXT,
    description  TEXT NOT NULL,
    reasoning    TEXT NOT NULL,
    trigger_type TEXT NOT NULL CHECK (trigger_type IN ('date','price','event')),
    trigger_value TEXT,
    trigger_query TEXT,
    status       TEXT NOT NULL DEFAULT 'pending'
                 CHECK (status IN ('pending','fired','expired')),
    added_at     TEXT NOT NULL,
    resolved_at  TEXT,
    outcome      TEXT
);

CREATE TABLE IF NOT EXISTS decisions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    made_at      TEXT NOT NULL,
    ticker       TEXT NOT NULL,
    call_type    TEXT NOT NULL,
    direction    TEXT NOT NULL DEFAULT 'neutral'
                 CHECK (direction IN ('bullish','bearish','neutral')),
    summary      TEXT NOT NULL,
    reasoning    TEXT,
    price_at_call REAL,
    benchmark_at_call REAL,
    horizon_days INTEGER NOT NULL DEFAULT 60,
    grade        TEXT CHECK (grade IN (NULL,'correct','partial','wrong','ungradable')),
    graded_at    TEXT,
    grade_notes  TEXT
);

CREATE TABLE IF NOT EXISTS catalysts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    event_date   TEXT,
    ticker       TEXT,
    event        TEXT NOT NULL,
    why_it_matters TEXT,
    source       TEXT,
    consumed     INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS snapshots (
    date       TEXT NOT NULL,
    ticker     TEXT NOT NULL,
    price_gbp  REAL,
    value_gbp  REAL,
    weight_pct REAL,
    PRIMARY KEY (date, ticker)
);

CREATE TABLE IF NOT EXISTS runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT NOT NULL,
    kind          TEXT NOT NULL CHECK (kind IN ('screen','exception','monthly')),
    triggers_json TEXT,
    email_sent    INTEGER NOT NULL DEFAULT 0,
    notes         TEXT
);
"""


def now_iso():
    return datetime.now(UTC).isoformat(timespec="seconds")


def connect(db_path=DB_PATH):
    db_path = Path(db_path)
    db_path.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


# ---------------------------------------------------------------- state dump

def state_dump(conn, tickers=None):
    """State as a dict — the LLM's context pack is built from this.

    tickers=None -> full dump (monthly runs). A ticker set scopes theses and
    conditions to the affected holdings (exception runs) to keep the pack lean;
    watch items, decisions, and catalysts are always included in full.
    """
    def rows(sql, params=()):
        return [dict(r) for r in conn.execute(sql, params).fetchall()]

    if tickers:
        ph = ",".join("?" * len(tickers))
        theses = rows(f"SELECT * FROM theses WHERE ticker IN ({ph}) ORDER BY ticker", tuple(tickers))
        conds = rows(f"SELECT * FROM invalidation_conditions WHERE status IN ('open','breached') "
                     f"AND ticker IN ({ph}) ORDER BY ticker", tuple(tickers))
    else:
        theses = rows("SELECT * FROM theses ORDER BY ticker")
        conds = rows("SELECT * FROM invalidation_conditions WHERE status IN ('open','breached') ORDER BY ticker")

    return {
        "theses": theses,
        "invalidation_conditions": conds,
        "watch_items_pending": rows(
            "SELECT * FROM watch_items WHERE status = 'pending' ORDER BY added_at"),
        "decisions_ungraded": rows(
            "SELECT * FROM decisions WHERE grade IS NULL ORDER BY made_at"),
        "decisions_recent_graded": rows(
            "SELECT * FROM decisions WHERE grade IS NOT NULL ORDER BY graded_at DESC LIMIT 20"),
        "catalysts_upcoming": rows(
            "SELECT * FROM catalysts WHERE consumed = 0 ORDER BY event_date"),
    }


def hit_rate(conn):
    """Grading scoreboard per call_type."""
    out = {}
    for r in conn.execute(
        "SELECT call_type, grade, COUNT(*) AS n FROM decisions "
        "WHERE grade IS NOT NULL GROUP BY call_type, grade"
    ):
        out.setdefault(r["call_type"], {})[r["grade"]] = r["n"]
    return out


# ---------------------------------------------------------------- patch apply

PATCH_OPS = {
    "update_thesis": {"required": {"ticker"}, "optional": {"bull_case", "bear_case", "key_kpi", "status", "sector", "company_name"}},
    "set_condition_status": {"required": {"id", "status"}, "optional": {"evidence"}},
    "add_invalidation_condition": {"required": {"ticker", "side", "metric", "comparator", "threshold"}, "optional": {"window_desc", "deadline"}},
    "add_watch_item": {"required": {"description", "reasoning", "trigger_type"}, "optional": {"ticker", "trigger_value", "trigger_query"}},
    "resolve_watch_item": {"required": {"id", "status", "outcome"}, "optional": set()},
    "log_decision": {"required": {"ticker", "call_type", "direction", "summary"}, "optional": {"reasoning", "price_at_call", "benchmark_at_call", "horizon_days"}},
    "add_catalyst": {"required": {"event", "event_date"}, "optional": {"ticker", "why_it_matters", "source"}},
    "grade_note": {"required": {"id", "grade_notes"}, "optional": set()},
}

THESIS_STATUSES = {"INTACT", "STRESSED", "INVALIDATED"}
CONDITION_STATUSES = {"open", "breached", "confirmed", "expired"}
WATCH_STATUSES = {"fired", "expired"}


def _known_tickers(conn):
    return {r["ticker"] for r in conn.execute("SELECT ticker FROM theses")}


def apply_patch(conn, patch):
    """Apply a list of mutation ops emitted by the LLM.

    Returns (applied, rejected) where rejected is a list of
    (op_dict, reason) tuples. Each op is atomic; a bad op never blocks
    the good ones.
    """
    if not isinstance(patch, list):
        return [], [(patch, "patch root must be a JSON array of ops")]

    known = _known_tickers(conn)
    applied, rejected = [], []

    for op in patch:
        try:
            reason = _validate_op(op, known)
            if reason:
                rejected.append((op, reason))
                continue
            _apply_op(conn, op)
            applied.append(op)
        except Exception as e:
            rejected.append((op, f"apply error: {e}"))

    conn.commit()
    return applied, rejected


def _validate_op(op, known_tickers):
    if not isinstance(op, dict) or "op" not in op:
        return "op must be an object with an 'op' field"
    spec = PATCH_OPS.get(op["op"])
    if spec is None:
        return f"unknown op '{op['op']}'"
    fields = set(op) - {"op"}
    missing = spec["required"] - fields
    if missing:
        return f"missing required fields: {sorted(missing)}"
    unknown = fields - spec["required"] - spec["optional"]
    if unknown:
        return f"unknown fields: {sorted(unknown)}"
    t = op.get("ticker")
    if t and op["op"] in ("update_thesis", "add_invalidation_condition") and t not in known_tickers:
        return f"unknown ticker '{t}'"
    if op["op"] == "update_thesis" and op.get("status") and op["status"] not in THESIS_STATUSES:
        return f"invalid thesis status '{op['status']}'"
    if op["op"] == "set_condition_status" and op["status"] not in CONDITION_STATUSES:
        return f"invalid condition status '{op['status']}'"
    if op["op"] == "resolve_watch_item" and op["status"] not in WATCH_STATUSES:
        return f"invalid watch status '{op['status']}' (pending items are resolved to fired/expired)"
    if op["op"] == "add_watch_item" and op["trigger_type"] not in ("date", "price", "event"):
        return f"invalid trigger_type '{op['trigger_type']}'"
    if op["op"] == "log_decision" and op["direction"] not in ("bullish", "bearish", "neutral"):
        return f"invalid direction '{op['direction']}'"
    return None


def _apply_op(conn, op):
    kind = op["op"]
    ts = now_iso()

    if kind == "update_thesis":
        sets, vals = ["updated_at = ?"], [ts]
        for f in ("bull_case", "bear_case", "key_kpi", "status", "sector", "company_name"):
            if f in op:
                sets.append(f"{f} = ?")
                vals.append(op[f])
        vals.append(op["ticker"])
        conn.execute(f"UPDATE theses SET {', '.join(sets)} WHERE ticker = ?", vals)

    elif kind == "set_condition_status":
        conn.execute(
            "UPDATE invalidation_conditions SET status = ?, evidence = COALESCE(?, evidence), checked_at = ? WHERE id = ?",
            (op["status"], op.get("evidence"), ts, op["id"]))

    elif kind == "add_invalidation_condition":
        conn.execute(
            "INSERT INTO invalidation_conditions (ticker, side, metric, comparator, threshold, window_desc, deadline) "
            "VALUES (?,?,?,?,?,?,?)",
            (op["ticker"], op["side"], op["metric"], op["comparator"], str(op["threshold"]),
             op.get("window_desc"), op.get("deadline")))

    elif kind == "add_watch_item":
        conn.execute(
            "INSERT INTO watch_items (ticker, description, reasoning, trigger_type, trigger_value, trigger_query, added_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (op.get("ticker"), op["description"], op["reasoning"], op["trigger_type"],
             op.get("trigger_value"), op.get("trigger_query"), ts))

    elif kind == "resolve_watch_item":
        conn.execute(
            "UPDATE watch_items SET status = ?, outcome = ?, resolved_at = ? WHERE id = ? AND status = 'pending'",
            (op["status"], op["outcome"], ts, op["id"]))

    elif kind == "log_decision":
        conn.execute(
            "INSERT INTO decisions (made_at, ticker, call_type, direction, summary, reasoning, price_at_call, benchmark_at_call, horizon_days) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (ts, op["ticker"], op["call_type"], op["direction"], op["summary"], op.get("reasoning"),
             op.get("price_at_call"), op.get("benchmark_at_call"), op.get("horizon_days", 60)))

    elif kind == "add_catalyst":
        conn.execute(
            "INSERT INTO catalysts (event_date, ticker, event, why_it_matters, source) VALUES (?,?,?,?,?)",
            (op["event_date"], op.get("ticker"), op["event"], op.get("why_it_matters"), op.get("source")))

    elif kind == "grade_note":
        conn.execute("UPDATE decisions SET grade_notes = ? WHERE id = ?", (op["grade_notes"], op["id"]))


def log_run(conn, kind, triggers=None, email_sent=False, notes=None):
    conn.execute(
        "INSERT INTO runs (ts, kind, triggers_json, email_sent, notes) VALUES (?,?,?,?,?)",
        (now_iso(), kind, json.dumps(triggers) if triggers else None, int(email_sent), notes))
    conn.commit()


if __name__ == "__main__":
    conn = connect()
    print(f"DB ready at {DB_PATH}")
    for table in ("theses", "invalidation_conditions", "watch_items", "decisions", "catalysts", "snapshots", "runs"):
        n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"  {table}: {n} rows")
