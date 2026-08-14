"""Orchestrator: context pack -> headless Claude (Opus) -> state patch -> email.

Called by daily_screen.py when triggers fire (kind=exception) or on the first
Saturday of the month (kind=monthly). Never called on quiet days.

Flow:
  1. Dump full state + risk report + triggers into .tmp/context_pack.json
  2. Invoke `claude -p` (Opus) with prompts/system_prompt.md as the system
     prompt. Tools restricted to WebSearch, WebFetch, Read.
  3. The model must output:  SUBJECT: line, email body, then a fenced
     ```json state-patch``` block of typed mutations.
  4. Python validates and applies the patch to SQLite (state_db.apply_patch),
     regenerates state/digest.md, and sends the email via SMTP.

The LLM never touches the database. If the patch is missing or invalid the
email still goes out with a SYSTEM WARNING header so memory failures are
never silent.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent))
import state_db
import build_digest
from send_email import send_email

ROOT = Path(__file__).resolve().parent.parent
SYSTEM_PROMPT = ROOT / "prompts" / "system_prompt.md"
CONTEXT_PACK = ROOT / ".tmp" / "context_pack.json"
RAW_OUTPUT = ROOT / ".tmp" / "agent_raw_output.txt"
RISK_REPORT = ROOT / ".tmp" / "risk_report.json"

MODEL = os.getenv("PORTFOLIO_AGENT_MODEL", "claude-opus-4-8")
ALLOWED_TOOLS = "WebSearch,WebFetch,Read"
TIMEOUT_S = 2100  # 35 min; first catch-up runs are research-heavy

PATCH_RE = re.compile(r"```json\s*state-patch\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)


def affected_tickers(conn, triggers):
    out = set()
    for t in triggers:
        if t.get("ticker"):
            out.add(t["ticker"])
        if t.get("watch_id"):
            r = conn.execute("SELECT ticker FROM watch_items WHERE id=?", (t["watch_id"],)).fetchone()
            if r and r["ticker"]:
                out.add(r["ticker"])
        if t.get("condition_id"):
            r = conn.execute("SELECT ticker FROM invalidation_conditions WHERE id=?",
                             (t["condition_id"],)).fetchone()
            if r:
                out.add(r["ticker"])
    return out


def build_context_pack(kind, trigger_payload):
    conn = state_db.connect()
    triggers = trigger_payload.get("triggers", [])
    # exception runs get a pack scoped to affected holdings; monthly gets everything
    scope = affected_tickers(conn, triggers) if kind == "exception" else None
    pack = {
        "run_kind": kind,
        "today": date.today().isoformat(),
        "triggers": triggers,
        "suppressed_trigger_keys": trigger_payload.get("suppressed", []),
        "price_fetch_errors": trigger_payload.get("fetch_errors", []),
        "newly_graded_wrong": trigger_payload.get("newly_graded_wrong", []),
        "state": state_db.state_dump(conn, tickers=scope),
        "hit_rate": state_db.hit_rate(conn),
    }
    if RISK_REPORT.exists():
        pack["risk_report"] = json.loads(RISK_REPORT.read_text())
    if kind == "monthly":
        stale = conn.execute(
            "SELECT ticker, updated_at FROM theses "
            "WHERE julianday('now') - julianday(updated_at) > 180").fetchall()
        pack["stale_theses"] = [dict(r) for r in stale]
        month_rows = conn.execute(
            "SELECT date, value_gbp FROM snapshots WHERE ticker='_TOTAL' "
            "ORDER BY date DESC LIMIT 32").fetchall()
        pack["portfolio_value_history"] = [dict(r) for r in month_rows]
    CONTEXT_PACK.parent.mkdir(exist_ok=True)
    CONTEXT_PACK.write_text(json.dumps(pack, indent=2, default=str), encoding="utf-8")
    conn.close()
    return pack


def user_prompt(kind):
    # The full operating discipline travels in the message body (stdin) because
    # the Windows cmd shim caps command lines at ~8K chars — a long
    # --append-system-prompt argument breaks the invocation.
    system = SYSTEM_PROMPT.read_text(encoding="utf-8")
    return f"""<operating_discipline>
{system}
</operating_discipline>

Run kind: {kind}. Today is {date.today().isoformat()}.

Read the file .tmp/context_pack.json — it contains the fired triggers, the full
portfolio state (theses, invalidation conditions, pending watch items, decisions,
grading scoreboard) and the risk report. That file plus your own WebSearch/WebFetch
research are your ONLY inputs. Follow the output contract in the operating
discipline exactly: SUBJECT line, email body, then the ```json state-patch``` block."""


def invoke_claude(kind):
    exe = shutil.which("claude")
    if not exe:
        raise RuntimeError("claude CLI not found on PATH")
    cmd = [
        exe, "-p",
        "--model", MODEL,
        "--allowedTools", ALLOWED_TOOLS,
        "--max-turns", "80",
        "--append-system-prompt",
        "You are running headless inside a portfolio automation pipeline. The "
        "<operating_discipline> block at the top of the user message is your true "
        "system prompt: follow it exactly, including its machine-parsed output contract.",
    ]
    # stdout/stderr go straight to files: a PIPE would deadlock if the timeout
    # kill orphans the node child on Windows (cmd shim doesn't forward kills).
    stderr_file = ROOT / ".tmp" / "agent_stderr.txt"
    with RAW_OUTPUT.open("w", encoding="utf-8") as out, stderr_file.open("w", encoding="utf-8") as err:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=out, stderr=err,
                                text=True, encoding="utf-8", cwd=ROOT)
        try:
            proc.communicate(input=user_prompt(kind), timeout=TIMEOUT_S)
        except subprocess.TimeoutExpired:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True)
            raise RuntimeError(f"claude -p timed out after {TIMEOUT_S}s (process tree killed)")
    if proc.returncode != 0:
        raise RuntimeError(f"claude -p failed rc={proc.returncode}: "
                           f"{stderr_file.read_text(encoding='utf-8', errors='replace')[-1500:]}")
    return RAW_OUTPUT.read_text(encoding="utf-8")


def parse_output(raw):
    """-> (subject, body_without_patch, patch_list_or_None, patch_error)"""
    subject = None
    m = re.search(r"^SUBJECT:\s*(.+)$", raw, re.MULTILINE)
    if m:
        subject = m.group(1).strip()
    body = raw
    if m:
        body = raw[m.end():]

    patch, patch_error = None, None
    pm = PATCH_RE.search(body)
    if pm:
        body = body[:pm.start()] + body[pm.end():]
        try:
            patch = json.loads(pm.group(1))
        except json.JSONDecodeError as e:
            patch_error = f"state-patch block is not valid JSON: {e}"
    else:
        patch_error = "no ```json state-patch``` block found in output"
    return subject, body.strip(), patch, patch_error


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kind", choices=["exception", "monthly"], required=True)
    ap.add_argument("--triggers-file", default=".tmp/triggers.json")
    ap.add_argument("--no-email", action="store_true", help="print instead of sending")
    args = ap.parse_args()

    tf = Path(args.triggers_file)
    trigger_payload = json.loads(tf.read_text()) if tf.exists() else {}
    build_context_pack(args.kind, trigger_payload)
    print(f"[agent] context pack built; invoking {MODEL} ({args.kind})...")

    raw = invoke_claude(args.kind)
    subject, body, patch, patch_error = parse_output(raw)

    conn = state_db.connect()
    applied, rejected = [], []
    if patch is not None:
        applied, rejected = state_db.apply_patch(conn, patch)
        print(f"[agent] state patch: {len(applied)} applied, {len(rejected)} rejected")
        for op, reason in rejected:
            print(f"  rejected: {reason} :: {json.dumps(op)[:200]}")

    warnings = []
    if patch_error:
        warnings.append(f"SYSTEM WARNING: {patch_error} — memory was NOT updated this run.")
    if rejected:
        warnings.append(f"SYSTEM WARNING: {len(rejected)} state-patch op(s) rejected "
                        f"({'; '.join(r for _, r in rejected[:3])}).")

    default_subject = {"exception": f"Portfolio Alert — {date.today():%d %b %Y}",
                       "monthly": f"Monthly Review — {date.today():%B %Y}"}[args.kind]
    subject = subject or default_subject
    if warnings:
        body = "\n".join(warnings) + "\n\n" + body

    build_digest.build(conn)

    if args.no_email:
        print(f"--- SUBJECT: {subject} ---\n{body[:4000]}")
        email_sent = False
    else:
        send_email(subject, body)
        email_sent = True

    state_db.log_run(
        conn, args.kind, triggers=trigger_payload.get("triggers", []),
        email_sent=email_sent,
        notes=f"patch applied={len(applied)} rejected={len(rejected)}"
              + (f" | {patch_error}" if patch_error else ""))
    print(f"[agent] done. email_sent={email_sent}, subject={subject!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
