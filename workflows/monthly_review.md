# Monthly Review — first Saturday of the month (replaces Monthly Strategy)

## Objective
The single calendar anchor. Everything else is exception-based; this run guarantees
the feedback loops close monthly: grading post-mortems, thesis freshness, risk report,
contribution routing. Always produces an email.

## How it runs
`daily_screen.py` detects the first Saturday (or `--monthly` forces it) and invokes
`tools/run_agent.py --kind monthly`. Same machinery as exception runs — context pack →
headless Opus → email + state patch → SQLite → digest → SMTP.

The monthly context pack additionally contains:
- `stale_theses` (updated_at > 180 days ago)
- `portfolio_value_history` (last ~32 daily totals)
- the full grading scoreboard (`hit_rate`)

## Required sections (enforced by the system prompt)
1. **Month in portfolio** — value change, drivers, vs what the risk report says.
2. **Watch updates** — anything due or resolved this month.
3. **Thesis status review** — every STRESSED/INVALIDATED thesis and every stale one:
   re-underwrite (new conditions in the patch) or escalate.
4. **Scoreboard** — hit rate is computed by Python; the model writes honest post-mortems
   for calls graded `wrong` (op `grade_note`), never adjusts grades.
5. **Routing** — is the weekly DCA still consistent with the rules? Specific
   reallocation if not (the R5 contribution-drift finding feeds this).
6. **Dust report** — sub-1% positions flagged by R6, consolidation candidates.
7. **New ideas** — max 2, only if genuinely earned, each born with ≥2 invalidation
   conditions in the patch. Zero is normal.

## Manual invocation
```
python tools/daily_screen.py --monthly              # full run incl. email
python tools/run_agent.py --kind monthly --no-email # test without sending
```
