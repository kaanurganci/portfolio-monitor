# Exception Alert — event-driven

## Objective
One focused email, sent only when a deterministic trigger fires. Quiet days produce
no LLM call and no cost. The Python layer decides WHETHER anything happened;
the LLM only reasons about WHAT it means.

## How it runs
Windows Task Scheduler runs `python tools/daily_screen.py` daily at 13:00 local
(catches up at next boot if the machine was off). The screen:

1. Fetches prices (`fetch_portfolio_data.py`) and snapshots them into `state/portfolio.db`.
2. Auto-grades any decisions whose horizon elapsed (`grade_decisions.py` — deterministic,
   graded vs the all-world benchmark; the LLM never grades).
3. Evaluates triggers:
   - **T1** statistical anomaly: |move| > 2.5σ of the holding's own 90-day vol (1d or 5d-scaled)
   - **T2** portfolio drawdown > 5% from 30-day high
   - **T3** rule breach (from `risk_check.py`): 15% single-name cap, 48% ETF trip-wire,
     25% correlation-cluster cap, 20% speculative-sleeve cap, contribution drift
   - **T4** a holding reports earnings within 5 days
   - **T5** watch item hit its date or price trigger
   - **T6** machine-checkable invalidation condition (metric `price:<TICKER>`) breached
4. Nothing fired → heartbeat row in `runs` **and a deterministic daily "all-clear"
   email** (`send_heartbeat`, no LLM): confirms the screen ran, gives a value / ETF% /
   sleeve% snapshot, standing rule notes, and the next date-triggered watch items. This
   is an optional confirmation layered on top of the exception design; real events still
   email separately.
5. Something fired → `tools/run_agent.py --kind exception`:
   context pack → headless `claude -p` (system prompt `prompts/system_prompt.md`,
   tools WebSearch/WebFetch/Read) → email body + `json state-patch` → Python validates
   and applies the patch to SQLite → regenerates `state/digest.md` → sends via SMTP.

A trigger key already handled within 7 days is suppressed (no repeat emails for the
same event).

## What the email must do (enforced by the system prompt)
- Resolve every fired watch item against its original ✅/❌ criteria, restating the
  original reasoning.
- For anomalies: find the cause, judge it against the thesis at a 12-month horizon —
  "no action" in two sentences is a valid and common answer.
- For rule breaches: quantify and give the exact contribution-routing fix.
- Every new case carries machine-checkable invalidation conditions in the patch.
- No new investment ideas in exception runs.

## Debugging
- `python tools/daily_screen.py --dry-run` — evaluate triggers, never invoke the LLM
- `python tools/daily_screen.py --force-trigger T1:PLTR --dry-run` — synthetic trigger
- `python tools/run_agent.py --kind exception --no-email` — full LLM run, print instead of send
- Raw model output of the last run: `.tmp/agent_raw_output.txt`
- Context pack given to the model: `.tmp/context_pack.json`

## Learned constraints
- yfinance: a symbol can be delisted from Yahoo or move exchanges (e.g. an LSE line
  becoming a Xetra `.DE` line priced in EUR, converted via `EURGBP=X`). If a ticker
  starts erroring, check for a listing change first.
- Windows console is cp1252: tools reconfigure stdout to UTF-8; keep that in new tools.
- The Gmail app-password path can send SMTP but cannot write to Drive — that is why
  delivery is SMTP and persistent state is a local SQLite database, not a cloud doc.
