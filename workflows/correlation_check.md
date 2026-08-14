# Correlation Check — On-Demand

## Objective
Check whether multiple holdings are moving as a single cluster. Sector weights alone
understate concentration: three names all riding the same news flow are effectively one
position, and a 25% "diversified" allocation can behave like a 25% single bet.

## When to run
- Before adding a new position
- Whenever a holding has a violent unexplained move and you suspect contagion from a related name
- Periodically as an input to the monthly review

## How to run

```
python tools/portfolio_analytics.py --correlation
```

Optional flags:
- `--window-days N` — lookback window (default 90)
- `--threshold X` — pair correlation threshold (default 0.7)

Output: a plain-English summary of pairs above the threshold, with the correlation coefficient.

## How to read the output
- **Correlation 1.0** = perfect lockstep
- **Correlation 0.9+** = the same position in different ticker symbols
- **Correlation 0.7-0.9** = strongly clustered; treat their combined weight as the relevant concentration
- **Correlation < 0.7** = meaningfully independent

## What to do with clustered holdings
For each cluster the tool flags:
1. Compute the combined weight (`compute_sector_weights` output) — does the cluster exceed
   the 25% cluster cap enforced by `risk_check.py` (R3)?
2. Ask: is there a single thesis driving all of them? If yes, the diversification is illusory.
3. Decide: keep all but treat as one position for sizing purposes, OR redirect future
   contributions away from the cluster to bring the combined weight back under the cap.

## How clusters usually form
Clusters tend to appear where holdings share a single demand driver — for example several
names all levered to the same capital-spending cycle, or two ETFs tracking the same
underlying index (which will correlate above 0.99). The point of running the tool is not to
assume the clusters but to confirm which holdings are *actually* behaving as one today, then
feed that combined weight into the R3 cluster-cap check.
