"""Deterministic risk rule engine.

Rules (market value, GBP):
  R1  single-name cap:        any non-ETF holding > 15%           -> BREACH
  R2  ETF floor:              ETF share < 50% (trip-wire 48%)     -> WARNING below 50, BREACH below 48
  R3  correlation cluster cap: any >0.7-correlated cluster > 25%  -> BREACH (WARNING > 22%)
  R4  speculative sleeve cap: pre-profit names as a group > 20%   -> BREACH (WARNING > 18%)
  R5  contribution drift:     share of weekly DCA going to ETFs
                              below current ETF weight -> contributions mechanically
                              erode the floor -> WARNING (BREACH if ETF share also < 52%)
  R6  dust positions:         non-ETF positions < 1% listed for consolidation (INFO, monthly)

Reads .tmp/portfolio_data.json (run fetch_portfolio_data.py first) and
config/portfolio.csv. Correlation clusters come from portfolio_analytics
and are cached for 7 days in .tmp/correlation_cache.json.

Output: .tmp/risk_report.json + stdout summary. Exit code 0 always
(daily_screen decides what to do with findings).
"""

import csv
import json
import sys
from datetime import datetime, UTC
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from portfolio_analytics import get_correlation_clusters

PORTFOLIO_DATA = Path(".tmp/portfolio_data.json")
PORTFOLIO_CSV = Path("config/portfolio.csv")
CORR_CACHE = Path(".tmp/correlation_cache.json")
REPORT = Path(".tmp/risk_report.json")

SINGLE_NAME_CAP = 15.0
ETF_FLOOR = 50.0
ETF_TRIPWIRE = 48.0
CLUSTER_CAP = 25.0
CLUSTER_WARN = 22.0
SLEEVE_CAP = 20.0
SLEEVE_WARN = 18.0
DUST_THRESHOLD = 1.0
CORR_MAX_AGE_DAYS = 7

# Asset classification for the sample portfolio — edit to match your own holdings.
ETF_TICKERS = {"VOO", "VWRP"}
# Pre-profit / speculative names: high multiple, no sustained GAAP profitability.
SPECULATIVE = {"PLTR", "RIVN"}


def load_weights():
    data = json.loads(PORTFOLIO_DATA.read_text())
    holdings = [h for h in data["holdings"] if "error" not in h]
    total = sum(h["price_gbp"] * h["shares"] for h in holdings)
    weights = {h["ticker"]: h["price_gbp"] * h["shares"] / total * 100 for h in holdings}
    return weights, total, data


def load_contributions():
    contrib = {}
    with PORTFOLIO_CSV.open() as f:
        for row in csv.DictReader(f):
            contrib[row["ticker"]] = float(row.get("Weekly Contribution", 0) or 0)
    return contrib


def get_clusters_cached(max_age_days=CORR_MAX_AGE_DAYS):
    if CORR_CACHE.exists():
        cache = json.loads(CORR_CACHE.read_text())
        age = (datetime.now(UTC) - datetime.fromisoformat(cache["fetched_at"])).days
        if age < max_age_days:
            return cache["pairs"]
    result = get_correlation_clusters()
    CORR_CACHE.write_text(json.dumps(
        {"fetched_at": datetime.now(UTC).isoformat(), "pairs": result["pairs"]}, indent=2))
    return result["pairs"]


def connected_components(pairs, yf_to_ticker):
    adj = {}
    for p in pairs:
        a = yf_to_ticker.get(p["ticker_a"], p["ticker_a"])
        b = yf_to_ticker.get(p["ticker_b"], p["ticker_b"])
        adj.setdefault(a, set()).add(b)
        adj.setdefault(b, set()).add(a)
    seen, comps = set(), []
    for node in adj:
        if node in seen:
            continue
        comp, stack = set(), [node]
        while stack:
            n = stack.pop()
            if n in comp:
                continue
            comp.add(n)
            stack.extend(adj[n] - comp)
        seen |= comp
        comps.append(sorted(comp))
    return comps


def run_checks(skip_correlation=False):
    weights, total_gbp, data = load_weights()
    contrib = load_contributions()
    findings = []

    def add(rule, severity, message, **metrics):
        findings.append({"rule": rule, "severity": severity, "message": message, **metrics})

    # R1 single-name cap
    for t, w in sorted(weights.items(), key=lambda x: -x[1]):
        if t not in ETF_TICKERS and w > SINGLE_NAME_CAP:
            add("R1_single_name_cap", "BREACH",
                f"{t} is {w:.1f}% of the portfolio — above the 15% single-name cap.",
                ticker=t, weight_pct=round(w, 2))

    # R2 ETF floor
    etf_weight = sum(w for t, w in weights.items() if t in ETF_TICKERS)
    if etf_weight < ETF_TRIPWIRE:
        add("R2_etf_floor", "BREACH",
            f"ETF share is {etf_weight:.1f}% — below the 48% trip-wire (floor 50%). "
            f"Contribution routing must redirect new money to ETFs.",
            etf_weight_pct=round(etf_weight, 2))
    elif etf_weight < ETF_FLOOR:
        add("R2_etf_floor", "WARNING",
            f"ETF share is {etf_weight:.1f}% — below the 50% floor but above the 48% trip-wire.",
            etf_weight_pct=round(etf_weight, 2))

    # R3 correlation cluster cap
    if not skip_correlation:
        try:
            yf_to_ticker = {}
            with PORTFOLIO_CSV.open() as f:
                for row in csv.DictReader(f):
                    yf_to_ticker[row["yfinance_ticker"]] = row["ticker"]
            pairs = get_clusters_cached()
            for comp in connected_components(pairs, yf_to_ticker):
                non_etf = [t for t in comp if t not in ETF_TICKERS]
                if len(non_etf) < 2:
                    continue
                cw = sum(weights.get(t, 0) for t in non_etf)
                if cw > CLUSTER_CAP:
                    add("R3_cluster_cap", "BREACH",
                        f"Correlated cluster {non_etf} moves as one position and is "
                        f"{cw:.1f}% of the portfolio — above the 25% cluster cap.",
                        cluster=non_etf, weight_pct=round(cw, 2))
                elif cw > CLUSTER_WARN:
                    add("R3_cluster_cap", "WARNING",
                        f"Correlated cluster {non_etf} is {cw:.1f}% — approaching the 25% cluster cap.",
                        cluster=non_etf, weight_pct=round(cw, 2))
        except Exception as e:
            add("R3_cluster_cap", "INFO", f"Correlation check unavailable: {e}")

    # R4 speculative sleeve
    sleeve = sum(w for t, w in weights.items() if t in SPECULATIVE)
    if sleeve > SLEEVE_CAP:
        add("R4_speculative_sleeve", "BREACH",
            f"Pre-profit sleeve ({', '.join(sorted(SPECULATIVE & set(weights)))}) is "
            f"{sleeve:.1f}% — above the 20% cap.",
            sleeve_weight_pct=round(sleeve, 2))
    elif sleeve > SLEEVE_WARN:
        add("R4_speculative_sleeve", "WARNING",
            f"Pre-profit sleeve is {sleeve:.1f}% — approaching the 20% cap.",
            sleeve_weight_pct=round(sleeve, 2))

    # R5 contribution drift
    total_contrib = sum(contrib.values())
    if total_contrib > 0:
        etf_contrib_share = sum(v for t, v in contrib.items() if t in ETF_TICKERS) / total_contrib * 100
        spec_contrib_share = sum(v for t, v in contrib.items() if t in SPECULATIVE) / total_contrib * 100
        if etf_contrib_share < etf_weight:
            severity = "BREACH" if etf_weight < 52.0 else "WARNING"
            add("R5_contribution_drift", severity,
                f"Only {etf_contrib_share:.0f}% of weekly contributions go to ETFs while the ETF "
                f"share is {etf_weight:.1f}% — every week of DCA mechanically pushes the portfolio "
                f"further from the 50% floor ({spec_contrib_share:.0f}% of new money goes to the "
                f"pre-profit sleeve).",
                etf_contrib_share_pct=round(etf_contrib_share, 1),
                speculative_contrib_share_pct=round(spec_contrib_share, 1),
                etf_weight_pct=round(etf_weight, 2))

    # R6 dust positions (INFO — surfaced monthly)
    dust = sorted([(t, w) for t, w in weights.items()
                   if t not in ETF_TICKERS and w < DUST_THRESHOLD], key=lambda x: x[1])
    if dust:
        add("R6_dust_positions", "INFO",
            f"{len(dust)} positions are under 1% each ({', '.join(t for t, _ in dust)}) — "
            f"too small to move the portfolio, but each costs monitoring attention. "
            f"Candidates for consolidation.",
            positions={t: round(w, 2) for t, w in dust})

    report = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "total_value_gbp": round(total_gbp, 2),
        "etf_weight_pct": round(etf_weight, 2),
        "speculative_sleeve_pct": round(sleeve, 2),
        "weights": {t: round(w, 2) for t, w in sorted(weights.items(), key=lambda x: -x[1])},
        "findings": findings,
    }
    REPORT.parent.mkdir(exist_ok=True)
    REPORT.write_text(json.dumps(report, indent=2))
    return report


if __name__ == "__main__":
    skip = "--skip-correlation" in sys.argv
    report = run_checks(skip_correlation=skip)
    print(f"Portfolio £{report['total_value_gbp']:,.0f} | ETF {report['etf_weight_pct']}% | "
          f"speculative sleeve {report['speculative_sleeve_pct']}%")
    for f in report["findings"]:
        print(f"  [{f['severity']}] {f['rule']}: {f['message']}")
    if not report["findings"]:
        print("  All rules pass.")
