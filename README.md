# portfolio-monitor

A rules-based monitoring system for a long-term equity portfolio. It fetches live
prices and FX daily, runs a fixed set of concentration and risk checks, and stays
silent unless something crosses a threshold. When something does, it assembles the
relevant state and a research brief, hands them to a language model for reasoning,
and emails one focused note. It keeps its own memory between runs — theses,
pre-registered watch items, and a scored record of its past calls — in a local
SQLite database.

I built it to manage my own stocks-and-shares ISA without the two failure modes I
kept hitting by hand: reacting to noise, and losing track of why I held something.
The design goal is that an email should only ever arrive when it changes what I
should do or believe, and that every view the system expresses is written down with
the exact condition that would prove it wrong, so it can be graded later rather than
quietly forgotten.

> **The data in this repository is sample data.** `config/portfolio.csv` and the
> seed files under `config/` use well-known tickers with deliberately illustrative
> quantities so the code runs after cloning. They are not a real portfolio. All API
> keys and credentials are read from environment variables.

## The investment process it encodes

The interesting part is not the plumbing but the discipline the plumbing enforces.

- **Pre-registered watch items.** Before an event, the reason for watching it is
  written down together with an explicit `✅` (confirms the thesis) and `❌`
  (breaks it) condition and, where possible, a date or price the question becomes
  answerable. When the trigger fires, the system resolves the item against the
  criteria it set *in advance*, so hindsight cannot rewrite the test.

- **Every claim carries its falsifier.** A thesis is stored with machine-checkable
  invalidation and confirmation conditions (`metric`, `comparator`, `threshold`,
  `deadline`). Price-level conditions written as `price:<TICKER>` are checked
  automatically every day; the rest are checked at event or monthly runs. A view
  with no falsifier is treated as no view.

- **Retrospective grading.** Directional calls are scored deterministically once
  their horizon elapses, on return *relative to a broad-market benchmark* — correct
  / partial / wrong. The language model never assigns or softens a grade; it only
  writes a one-paragraph post-mortem for calls graded wrong. The running hit rate
  sits on the digest.

- **Concentration rules as hard constraints.** 15% single-name cap; 50% ETF floor
  (48% trip-wire); 25% cap on any correlated cluster (holdings with >0.7 90-day
  correlation are treated as one position); 20% cap on the pre-profit/speculative
  sleeve; a contribution-drift check that flags when the weekly top-up is
  mechanically eroding the ETF floor. These are in
  [`tools/risk_check.py`](tools/risk_check.py) as parameters, not prose.

- **Both sides, never a buy/sell command.** The system states the bull and bear
  case and the conditions that would settle them, and leaves the decision to the
  investor. The one exception is a thesis that reaches INVALIDATED on its own terms:
  there it says plainly that its own rules now call for exit, and presents three
  options (exit / re-underwrite with new conditions / logged override).

- **Rebalancing is contribution routing only.** The only lever the system will
  recommend is redirecting the weekly contribution; it does not recommend selling
  to rebalance, because spreads and FX fees punish churn inside an ISA. Selling is
  reserved for invalidated theses.

## Architecture

Three layers, so that probabilistic reasoning and deterministic execution stay
separate:

- **Tools** (`tools/`) — deterministic Python. Data fetch, the risk-rule engine,
  grading, the state database, digest generation.
- **Workflows** (`workflows/`) — plain-language standard operating procedures that
  describe what each run must do.
- **Reasoning prompt** (`prompts/system_prompt.md`) — the operating discipline for
  the language-model step: what to research, the output contract, and the
  prohibitions (no invented numbers, no unfalsifiable claims, silence is a valid
  output).

**Data sources.** Prices and FX from Yahoo Finance via `yfinance` (LSE lines are
quoted in pence and converted to GBP; USD and EUR lines are converted via
`GBPUSD=X` / `EURGBP=X`). News sentiment from Alpha Vantage. US market-calendar
checks via `pandas-market-calendars`.

**Schedule.** A single daily job (`tools/daily_screen.py`, run by Windows Task
Scheduler) is the entry point:

1. Fetch prices and snapshot them into SQLite.
2. Auto-grade any decisions whose horizon has elapsed.
3. Evaluate six triggers — statistical anomaly (>2.5σ of a holding's own 90-day
   volatility), portfolio drawdown >5% from its 30-day high, a risk-rule breach, an
   earnings date within five days, a watch item reaching its trigger, or a
   machine-checkable invalidation condition breached.
4. If nothing fired, log a heartbeat and (optionally) send a short all-clear — no
   language-model call, no cost.
5. If something fired, or on the first Saturday of the month, invoke the reasoning
   step for one focused email. A trigger already handled within seven days is
   suppressed so the same event does not email twice.

**Memory across cycles.** State lives in `state/portfolio.db` (SQLite), which is the
single source of truth: theses, invalidation conditions, watch items, decisions and
their grades, price snapshots, and a run log. An earlier version kept this in three
Google Docs — a theses document, a catalysts log, and a decisions record — but the
send path (a Gmail app password over SMTP) cannot write back to Drive, so the memory
was migrated into a local database and the docs retired. The language model never
writes to the database directly: it emits a typed, validated `json state-patch`,
which the Python layer checks op-by-op and applies atomically, so a malformed edit
is rejected rather than partially applied. `state/digest.md` is a human-readable
mirror regenerated after every run.

### File inventory

| Path | What it is |
|------|------------|
| `tools/daily_screen.py` | Daily entry point: fetch, grade, evaluate triggers, decide whether to escalate |
| `tools/fetch_portfolio_data.py` | Live prices and FX → GBP, with multi-currency handling |
| `tools/risk_check.py` | Deterministic rule engine (the concentration rules R1–R6) |
| `tools/portfolio_analytics.py` | Sector weights and pairwise correlation clusters |
| `tools/grade_decisions.py` | Deterministic retro-grading of past calls vs the benchmark |
| `tools/state_db.py` | SQLite schema, state dump, and the validated state-patch contract |
| `tools/run_agent.py` | Builds the context pack, runs the headless reasoning step, applies the patch, sends the email |
| `tools/build_digest.py` | Regenerates `state/digest.md` from the database |
| `tools/migrate_state.py` | Loads the seed files in `config/` into SQLite |
| `tools/fetch_news_sentiment.py` | Alpha Vantage news sentiment, cached |
| `tools/check_earnings_calendar.py`, `check_market_holiday.py` | Earnings dates and NYSE calendar |
| `prompts/system_prompt.md` | Operating discipline for the reasoning step |
| `workflows/` | Standard operating procedures (exception alert, monthly review, correlation check) |
| `config/` | Sample portfolio and seed theses / conditions / watch items |

## Running it

Requires Python 3.11+.

```bash
pip install -r requirements.txt
cp .env.example .env          # then fill in your keys
python tools/migrate_state.py            # load sample theses, watch items, decisions
python tools/migrate_state.py --conditions   # load the invalidation conditions
python tools/daily_screen.py --dry-run   # fetch + evaluate triggers, no email, no LLM
```

Useful commands:

```bash
python tools/risk_check.py                       # run the concentration rules now
python tools/portfolio_analytics.py --correlation  # correlation clusters
python tools/daily_screen.py --force-trigger T1:PLTR --dry-run  # inject a synthetic trigger
python tools/run_agent.py --kind monthly --no-email  # full reasoning run, printed not sent
pytest                                            # unit tests (mocked; no network)
```

### Environment variables

Set these in `.env` (see `.env.example`); nothing is hard-coded.

| Variable | Purpose |
|----------|---------|
| `ALPHA_VANTAGE_KEY` | Alpha Vantage API key for news sentiment |
| `GMAIL_USER` | Gmail address used to send alerts |
| `GMAIL_APP_PASSWORD` | 16-character Google App Password (not the account password) |
| `EMAIL_TO` | Recipient (optional; defaults to `GMAIL_USER`) |
| `PORTFOLIO_AGENT_MODEL` | Model id for the reasoning step (optional) |

The reasoning step shells out to the `claude` command-line tool running headless
(`claude -p`) with web search, web fetch and file read enabled and everything else
withheld. The deterministic screen runs with no language model at all, so the daily
job costs nothing on quiet days.

## Limitations and things I would change

- **Single data vendor.** Prices, FX and earnings dates all come from Yahoo Finance
  via `yfinance`, which is unofficial and occasionally returns nothing for a valid
  symbol, or moves a listing between exchanges (an LSE line becoming a `.DE` line in
  EUR). The code degrades gracefully — a failed fetch is reported, not fatal — but
  there is no second source to cross-check against.
- **No brokerage integration.** Positions are read from a CSV; there is no
  reconciliation against an actual account, and cost basis and transaction history
  are not modelled. It monitors, it does not trade.
- **Correlation is backward-looking.** The 90-day window that defines a "cluster"
  lags regime changes; correlations that matter in a sell-off can be invisible in
  calm markets.
- **The grader is mechanical by design.** Relative return over a fixed horizon is a
  crude proxy for whether a call was *right*; a correct thesis can be graded wrong
  by a horizon that ends mid-drawdown. That is a deliberate trade for objectivity —
  the model cannot mark its own homework — but it is a real limitation.
- **Windows-specific scheduling.** The runner assumes Windows Task Scheduler and a
  local machine that is on at the scheduled time (it catches up at next boot). A
  cloud cron or a systemd timer would be a straightforward but unimplemented swap.
- **The reasoning step is not reproducible.** A language model's output varies run
  to run. The typed state-patch contract and the deterministic grader are there to
  contain that — the model can reason and draft, but it cannot silently corrupt the
  record or grade itself.
