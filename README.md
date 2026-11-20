# portfolio-analytics

This is a personal portfolio analysis tool I built to run with [Claude Code](https://docs.claude.com/en/docs/claude-code). It grabs live prices and FX, runs some portfolio maths (metrics, concentration checks), and spits out structured daily, weekly, or monthly reviews based on my specific investment process.
The design goal is that an email should only ever arrive when it changes what I
should do or believe, and that every view the system expresses is written down with
the exact condition that would prove it wrong
> **Note:** This is the public version. I've uploaded a dummy portfolio, laceholder  holdings and API keys.

## How it works

* **`fetch_data.py`**: Grabs live prices, FX, news, and earnings dates via `yfinance`. London tickers (`.L`) quote in pence, so this normalizes them to GBP. If a ticker fails, it fails *loudly*. There are no silent fallbacks to stale data.
* **`fetch_fundamentals.py`**: Handles on-demand deep dives (income statements, balance sheets, cash flow, ratios) using `yfinance` and Alpha Vantage. 
* **`compute_metrics.py`**: Handles all the portfolio math. Value, P&L, weights, sector breakdown, concentration flags, ETF allocation, and basic sanity checks. This is where the hard rules are enforced.
* **`run_daily.py`**: The main orchestrator script. It fetches, computes, and dumps everything into `data/today.json`. All the Claude prompts read from this snapshot. It also notes data lineage (for instance, explicitly stating that GBP cost basis uses *current* FX, so FX and price moves are mixed together).
* **`prompts/`**: The actual analysis instructions for Claude (`daily`, `weekly`, `monthly`, `bear`, `fundamentals`). These define the voice, structure, and hard rules. You run these directly inside Claude Code.

Reports get saved and dated in `analyses/`, `weekly/`, and `monthly/`. I actually use a separate Google Docs layer alongside this (for theses, catalyst logs, and decisions) so the system remembers its own past predictions, but that integration isn't included in this public repo.

## How it decides what to flag

Every thesis has to say what would prove it wrong. I store invalidation and confirmation conditions as a metric, a comparator, a threshold and a deadline. Price conditions (written as `price:<TICKER>`) are checked every day, and everything else at the event or monthly runs. 

The rules it works to:

- It never just says buy or sell. If it flags something, it has to give the case for looking at it, the case for doing nothing, and what would change its mind.
- Any single holding above 15% of the portfolio gets flagged (25% for paired theses). Holdings with a 90-day correlation above 0.7 count as one position. It reports the concentration but doesn't force a rebalance.
- ETFs should make up at least half the portfolio. If they drop below that, it suggests putting the next deposit into them rather than selling anything.
- News that breaks the thesis matters more than a big price move. A large drop with no news is usually noise. A small drop on news that undermines the reason I own something is the one to look at, so the prompts look for the fundamentals getting worse before the price does.
- Every position gets checked against three sell questions: have the fundamentals got worse, is it badly overvalued, and is there a better use for the money?
- It doesn't give single price targets, it flags odd data instead of smoothing it over, and it has to cite a source for any specific event.

Speculative names (set in `compute_metrics.py`) get wider thresholds for daily moves. 


