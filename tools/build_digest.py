"""Regenerate state/digest.md — the human-readable snapshot of the SQLite state.

Rebuilt after every run so the user can browse current theses, invalidation
conditions, watch items, and the grading scoreboard without touching the DB.
Read-only convenience; the DB remains the source of truth.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import state_db

DIGEST = Path("state/digest.md")
RISK_REPORT = Path(".tmp/risk_report.json")

STATUS_ICON = {"INTACT": "🟢", "STRESSED": "🟡", "INVALIDATED": "🔴"}
GRADE_ICON = {"correct": "✅", "partial": "⚠️", "wrong": "❌", "ungradable": "➖", None: "⏳"}


def build(conn):
    lines = [f"# Portfolio State Digest", f"_Generated {state_db.now_iso()} — regenerated after every run; do not edit._", ""]

    if RISK_REPORT.exists():
        r = json.loads(RISK_REPORT.read_text())
        lines += [
            "## Portfolio & rules",
            f"- Total value: £{r['total_value_gbp']:,.0f} (as of {r['generated_at']})",
            f"- ETF share: **{r['etf_weight_pct']}%** (floor 50%, trip-wire 48%)",
            f"- Speculative sleeve: **{r['speculative_sleeve_pct']}%** (cap 20%)",
        ]
        for f in r["findings"]:
            lines.append(f"- [{f['severity']}] {f['rule']}: {f['message']}")
        lines.append("")

    lines.append("## Theses")
    for t in conn.execute("SELECT * FROM theses ORDER BY status DESC, ticker"):
        icon = STATUS_ICON.get(t["status"], "")
        lines += [f"### {icon} {t['ticker']} — {t['company_name']} ({t['status']})",
                  f"- Sector: {t['sector']} | updated {t['updated_at']}"]
        if t["key_kpi"]:
            lines.append(f"- Key KPI: {t['key_kpi']}")
        lines += [f"- **Bull:** {t['bull_case']}", f"- **Bear:** {t['bear_case']}"]
        conds = conn.execute(
            "SELECT * FROM invalidation_conditions WHERE ticker=? ORDER BY side DESC, id", (t["ticker"],)).fetchall()
        if conds:
            lines.append("- Conditions:")
            for c in conds:
                flag = {"open": "⏳", "breached": "🔴", "confirmed": "✅", "expired": "⌛"}.get(c["status"], "")
                dl = f", deadline {c['deadline']}" if c["deadline"] else ""
                lines.append(f"  - {flag} [{c['id']}] {c['side'].upper()}: {c['metric']} {c['comparator']} "
                             f"{c['threshold']}{dl} ({c['status']}"
                             + (f" — {c['evidence']}" if c["evidence"] else "") + ")")
        lines.append("")

    lines.append("## Watch items — pending")
    pending = conn.execute("SELECT * FROM watch_items WHERE status='pending' ORDER BY added_at").fetchall()
    if not pending:
        lines.append("(none)")
    for w in pending:
        tv = f" | trigger date/value: {w['trigger_value']}" if w["trigger_value"] else ""
        lines += [f"- **[{w['id']}] {w['description']}** ({w['trigger_type']}{tv}, added {w['added_at']})",
                  f"  - {w['reasoning']}"]
    lines.append("")

    lines.append("## Watch items — resolved (last 15)")
    for w in conn.execute("SELECT * FROM watch_items WHERE status!='pending' ORDER BY resolved_at DESC LIMIT 15"):
        lines.append(f"- [{w['id']}] {w['description']} — **{w['status']}** {w['resolved_at'] or ''}: {w['outcome'] or ''}")
    lines.append("")

    lines.append("## Decision scoreboard")
    hr = state_db.hit_rate(conn)
    gradable = {"correct": 0, "partial": 0, "wrong": 0}
    for ct, grades in hr.items():
        for g, n in grades.items():
            if g in gradable:
                gradable[g] += n
    total = sum(gradable.values())
    if total:
        lines.append(f"- Graded directional calls: {total} — "
                     f"✅ {gradable['correct']} / ⚠️ {gradable['partial']} / ❌ {gradable['wrong']} "
                     f"(hit rate {gradable['correct'] / total * 100:.0f}%)")
    else:
        lines.append("- No directional calls graded yet.")
    ungraded = conn.execute("SELECT COUNT(*) FROM decisions WHERE grade IS NULL").fetchone()[0]
    lines.append(f"- Awaiting horizon: {ungraded}")
    lines.append("")

    lines.append("## Recent decisions (last 15)")
    for d in conn.execute("SELECT * FROM decisions ORDER BY made_at DESC LIMIT 15"):
        icon = GRADE_ICON.get(d["grade"], "⏳")
        lines.append(f"- {icon} {d['made_at'][:10]} **{d['ticker']}** {d['call_type']}/{d['direction']}: {d['summary']}")
        if d["grade_notes"] and d["grade"] not in (None, "ungradable"):
            lines.append(f"  - {d['grade_notes']}")
    lines.append("")

    upcoming = conn.execute("SELECT * FROM catalysts WHERE consumed=0 ORDER BY event_date").fetchall()
    lines.append("## Upcoming catalysts")
    if not upcoming:
        lines.append("(none logged)")
    for c in upcoming:
        lines.append(f"- {c['event_date']} **{c['ticker'] or 'MACRO'}**: {c['event']}"
                     + (f" — {c['why_it_matters']}" if c["why_it_matters"] else ""))
    lines.append("")

    DIGEST.parent.mkdir(exist_ok=True)
    DIGEST.write_text("\n".join(lines), encoding="utf-8")
    return DIGEST


if __name__ == "__main__":
    conn = state_db.connect()
    path = build(conn)
    print(f"Digest written to {path}")
