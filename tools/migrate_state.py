"""One-time migration: thesis_store.json + Google Docs transcription -> SQLite.

Sources:
  - config/thesis_store.json      (theses, written 2026-05-13)
  - config/migration_seed.json    (watch items + decisions transcribed from the
                                   Portfolio Catalysts / Decisions Log Google Docs,
                                   plus per-ticker key KPIs from the Theses doc)

Run once:  python tools/migrate_state.py
Re-run:    python tools/migrate_state.py --force   (wipes and re-imports)

After this, the three Google Docs are retired. SQLite is the source of truth.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import state_db

THESIS_STORE = Path("config/thesis_store.json")
SEED = Path("config/migration_seed.json")
CONDITIONS_SEED = Path("config/conditions_seed.json")


def import_conditions(force=False):
    """Load the invalidation/confirmation condition backfill (idempotent)."""
    conn = state_db.connect()
    existing = conn.execute("SELECT COUNT(*) FROM invalidation_conditions").fetchone()[0]
    if existing and not force:
        print(f"invalidation_conditions already has {existing} rows. Use --force to wipe and re-import.")
        return 1
    if force:
        conn.execute("DELETE FROM invalidation_conditions")
    seed = json.loads(CONDITIONS_SEED.read_text(encoding="utf-8"))
    n = 0
    for c in seed["invalidation_conditions"]:
        conn.execute(
            "INSERT INTO invalidation_conditions (ticker, side, metric, comparator, threshold, window_desc, deadline) "
            "VALUES (?,?,?,?,?,?,?)",
            (c["ticker"], c["side"], c["metric"], c["comparator"], str(c["threshold"]),
             c.get("window_desc"), c.get("deadline")))
        n += 1
    conn.commit()
    print(f"invalidation_conditions: {n} imported")
    return 0


def migrate(force=False):
    conn = state_db.connect()

    populated = conn.execute("SELECT COUNT(*) FROM theses").fetchone()[0]
    if populated and not force:
        print(f"theses already has {populated} rows. Use --force to wipe and re-import.")
        return 1
    if force:
        for t in ("invalidation_conditions", "watch_items", "decisions", "catalysts", "theses"):
            conn.execute(f"DELETE FROM {t}")

    thesis_store = json.loads(THESIS_STORE.read_text(encoding="utf-8"))
    seed = json.loads(SEED.read_text(encoding="utf-8"))
    key_kpis = seed.get("key_kpis", {})

    n = 0
    for ticker, t in thesis_store["holdings"].items():
        conn.execute(
            "INSERT INTO theses (ticker, company_name, sector, bull_case, bear_case, key_kpi, status, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (ticker, t.get("company_name"), t.get("sector"), t.get("bull_case"),
             t.get("bear_case"), key_kpis.get(ticker), "INTACT", t.get("date_written")))
        n += 1
    print(f"theses: {n} imported")

    n = 0
    for w in seed.get("watch_items", []):
        conn.execute(
            "INSERT INTO watch_items (ticker, description, reasoning, trigger_type, trigger_value, "
            "trigger_query, status, added_at, resolved_at, outcome) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (w.get("ticker"), w["description"], w["reasoning"], w["trigger_type"],
             w.get("trigger_value"), w.get("trigger_query"), w.get("status", "pending"),
             w["added_at"], w.get("resolved_at"), w.get("outcome")))
        n += 1
    print(f"watch_items: {n} imported")

    n = 0
    for d in seed.get("decisions", []):
        conn.execute(
            "INSERT INTO decisions (made_at, ticker, call_type, direction, summary, reasoning, "
            "price_at_call, horizon_days) VALUES (?,?,?,?,?,?,?,?)",
            (d["made_at"], d["ticker"], d["call_type"], d.get("direction", "neutral"),
             d["summary"], d.get("reasoning"), d.get("price_at_call"), d.get("horizon_days", 60)))
        n += 1
    print(f"decisions: {n} imported")

    conn.commit()
    state_db.log_run(conn, "screen", notes="migration from Google Docs + thesis_store.json")
    print("Migration complete. SQLite is now the source of truth; the Google Docs are retired.")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="wipe tables and re-import")
    ap.add_argument("--conditions", action="store_true",
                    help="import only the invalidation-condition backfill")
    args = ap.parse_args()
    if args.conditions:
        sys.exit(import_conditions(force=args.force))
    sys.exit(migrate(force=args.force))
