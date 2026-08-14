# portfolio-analytics

This is a personal portfolio analysis tool I built to run with [Claude Code](https://docs.claude.com/en/docs/claude-code). It grabs live prices and FX, runs some portfolio maths (metrics, concentration checks), and spits out structured daily, weekly, or monthly reviews based on my specific investment process.
The design goal is that an email should only ever arrive when it changes what I
should do or believe, and that every view the system expresses is written down with
the exact condition that would prove it wrong
> **Note:** This is the public version. I've uploaded  a dummy portfolio and placeholder  holdings and API keys.

## How it works

I deliberately kept the data fetching, the math, and the LLM analysis completely separated:



* **`fetch_data.py`**: Grabs live prices, FX, news, and earnings dates via `yfinance`. London tickers (`.L`) quote in pence, so this normalizes them to GBP. If a ticker fails, it fails *loudly*. There are no silent fallbacks to stale data.
* **`fetch_fundamentals.py`**: Handles on-demand deep dives (income statements, balance sheets, cash flow, ratios) using `yfinance` and Alpha Vantage. I added a local cache so you don't blow through Alpha Vantage's free-tier limit of 25 requests/day.
* **`compute_metrics.py`**: Handles all the portfolio math. Value, P&L, weights, sector breakdown, concentration flags, ETF allocation, and basic sanity checks. This is where the hard rules are enforced.
* **`run_daily.py`**: The main orchestrator script. It fetches, computes, and dumps everything into `data/today.json`. All the Claude prompts read from this snapshot. It also notes data lineage (for instance, explicitly stating that GBP cost basis uses *current* FX, so FX and price moves are mixed together).
* **`prompts/`**: The actual analysis instructions for Claude (`daily`, `weekly`, `monthly`, `bear`, `fundamentals`). These define the voice, structure, and hard rules. You run these directly inside Claude Code.

Reports get saved and dated in `analyses/`, `weekly/`, and `monthly/`. I actually use a separate Google Docs layer alongside this (for theses, catalyst logs, and decisions) so the system remembers its own past predictions, but that integration isn't included in this public repo.

## The Decision Framework

* **Every claim carries its falsifier.** A thesis is stored with machine-checkable
  invalidation and confirmation conditions (`metric`, `comparator`, `threshold`,
  `deadline`). Price-level conditions written as `price:<TICKER>` are checked
  automatically every day; the rest are checked at event or monthly runs. A view
  with no falsifier is treated as no view.
* **Both-sides framing**: The prompt rules forbid pure buy/sell instructions. If it flags something, it has to state *why you should look at it*, *why you shouldn't act*, and *what would change its mind*. 
* **15% single-name cap**: If a single position hits 15% of the portfolio, it throws a flag (25% for paired theses). It just reports the concentration; it doesn't force a rebalance. (holdings with >0.7 90-day correlation are treated as one position)
* **50% ETF floor**: ETFs are expected to make up at least half the portfolio. If the allocation drops below that, it suggests topping up with the next deposit rather than forcing a trade.
* **Thesis-breaking signal > price noise**: A massive drop on zero news is usually just noise. A small drop on news that breaks the core thesis is the actual headline. The prompts are tuned to look for fundamental decay before a big drawdown happens.
* **Three strict sell rules**: It evaluates every position against three questions: Have fundamentals deteriorated? Is it vastly overvalued? Are there better spots for the capital?
* **No false precision**: It doesn't guess single-number price targets. It flags weird data instead of smoothing it out, and it has to cite sources for specific events.

Speculative names (which you can configure in `compute_metrics.py`) get wider day-move thresholds. They are noisy by nature, so I don't want routine volatility drowning out actual signal.


