# portfolio-monitor

A tool I built to keep an eye on my own ISA, running with [Claude Code](https://docs.claude.com/en/docs/claude-code). It runs once a day, grabs live prices and FX, checks the portfolio against a set of limits I decided on in advance, and only bothers a language model if something has actually happened.

The whole point is that an email should only turn up if it changes what I should do. If it didn't need to exist, it's a bug.

> **Note:** this is the public version. The portfolio in `config/portfolio.csv` is made up, the holdings are placeholders, and the keys all come from environment variables.

## How it works

**Python, no model involved:**

* **`tools/fetch_portfolio_data.py`** — prices, FX and daily moves from `yfinance`. London tickers quote in pence, so those get converted to GBP. 
* **`tools/portfolio_analytics.py`** — sector weights, and which holdings have been moving together over the last 90 days.
* **`tools/risk_check.py`** — the six limits below.
* **`tools/grade_decisions.py`** — scores old calls once the horizon I set at the time has passed, against a global tracker (`VWRP.L`). Beat it by more than 3% and it's right, lose by more than 3% and it's wrong, anything in between is partial.
* **`tools/state_db.py`** — SQLite, and the only real record. The model never writes to it directly. It sends back changes in a fixed format and Python checks them first. Anything malformed gets thrown out rather than half-applied.
* **`tools/daily_screen.py`** — the bit that actually runs daily. If none of the six triggers fire, it logs that it ran and stops. 

**The model:**

* **`tools/run_agent.py`** — only runs when a trigger fires, or on the first Saturday of the month. Hands over the current state and whatever fired, and gets back one email plus a list of changes to the record.
* **`prompts/system_prompt.md`** — the rules it works to. Saying nothing is allowed. Every view has to come with the thing that would prove it wrong. It never tells me to buy or sell.
* **`workflows/`** — the three kinds of run: exception alert, correlation check, monthly review.

Everything else (news sentiment, earnings dates, market holidays, email, the readable summary of the database) is in `tools/`. Tests are in `tests/`, run `pytest` from the root.

## The limits

In `risk_check.py`. All on market value, in GBP.

| | |
|---|---|
| **R1** | No single company above **15%** |
| **R2** | ETFs at least **50%** of the portfolio |
| **R3** | No group of holdings that move together above **25%**. Anything with a 90-day correlation over 0.7 counts as one position |
| **R4** | Speculative names (companies not making money yet) no more than **20%** as a group |
| **R5** | If less of my weekly money is going into ETFs than my current ETF weight, then every week of buying is pushing me further below my own floor. That gets flagged |
| **R6** | Anything under **1%** is too small to matter but still takes up attention. Listed monthly so I can tidy up |

The only way it fixes a breach is by changing where the weekly money goes. It never tells me to sell to rebalance, because spreads and FX fees make churning an ISA expensive. Selling is only for when a thesis has actually broken.

## What wakes the model up

| | |
|---|---|
| **T1** | A one-day move far outside what's normal for that holding (2.5 standard deviations of its own 90-day volatility), or the same over five days |
| **T2** | The portfolio more than **5%** below its 30-day high |
| **T3** | Any of the limits above being broken |
| **T4** | A holding reporting earnings in the next **5 days** |
| **T5** | Something I said I'd watch hitting its date or its price |
| **T6** | A price level I wrote down as "this would prove me wrong" being hit |

If the same thing fired in the last 7 days it gets ignored, so one event can't email me every day for a week.

## How it decides what's worth flagging

Every holding has a thesis, and the thesis has to say what would prove it wrong, written down before I buy. Those get stored as a number and a date so they can actually be checked rather than argued about. A thesis goes from intact, to stressed, to broken as those conditions fail, and only a broken one raises the question of selling.

Other than that:

- It never just says buy or sell. If it flags something it has to give the case for looking at it, the case for doing nothing, and what would change its mind.
- News that breaks the thesis matters more than a big price move. A large drop with no news is usually noise. A small drop on news that undermines why I own the thing is the one to look at.
- No price targets. Weird data gets flagged rather than smoothed over. Anything specific has to come with a source.

## What's wrong with it

- The correlation check looks backwards over 90 days and gets cached for a week. Correlations go up in a sell-off, so it's slowest exactly when I need it to be quickest.
- R3 groups things in a chain. If A moves with B, and B moves with C, all three get counted as one position even if A and C have nothing to do with each other. That's deliberate but it will over-group when everything falls at once.
- Which holdings count as ETFs and which count as speculative is just a list I typed in.
- Cost basis in GBP uses today's FX rate, so currency moves and price moves are mixed together in the P&L.
- Calls get scored against a global tracker, which for a US company mixes in the dollar and the US market as well as the company.
