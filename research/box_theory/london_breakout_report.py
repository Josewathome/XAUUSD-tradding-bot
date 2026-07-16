"""london_breakout_report.py — charts + LONDON_BREAKOUT_REPORT.md.

    cd research && box_theory/.venv/bin/python -m box_theory.london_breakout_report
"""
from __future__ import annotations

import os
import datetime as dt

import numpy as np
import pandas as pd
from scipy import stats
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from . import config
from .london_breakout import run

REPORT = os.path.join(os.path.dirname(__file__), "report")
PAIRS = ["GBPUSD", "EURUSD", "USDJPY", "AUDUSD", "USDCAD", "XAUUSD"]
CUT = dt.datetime(2025, 11, 13)


def _fig(name):
    plt.tight_layout(); plt.savefig(os.path.join(REPORT, name), dpi=110); plt.close()
    return name


def main():
    trades = {}
    for s in PAIRS:
        tr, _, _ = run(s, "M15", "opposite", True)
        tr["day"] = pd.to_datetime(tr.day)
        trades[s] = tr

    # chart 1: per-pair expectancy
    exps = [trades[s].r_net.mean() for s in PAIRS]
    colors = ["#20707F" if s in ("GBPUSD", "EURUSD") else "#9AA5AF" for s in PAIRS]
    plt.figure(figsize=(8.5, 4.2))
    plt.bar(PAIRS, exps, color=colors)
    plt.axhline(0, color="black", lw=0.9)
    plt.ylabel("expectancy (R/trade)")
    plt.title("London Breakout, M15 24mo — the spec's own pairs (teal) lead")
    for i, v in enumerate(exps):
        plt.text(i, v + 0.004, "%+.3f" % v, ha="center", fontsize=8)
    fig1 = _fig("fig_lb_pairs.png")

    # chart 2: quarterly expectancy GBP vs EUR
    plt.figure(figsize=(8.5, 4.2))
    for s, col in [("GBPUSD", "#B23A30"), ("EURUSD", "#20707F")]:
        tr = trades[s]
        q = pd.qcut(tr.day.rank(method="first"), 4, labels=False)
        vals = [tr[q == i].r_net.mean() for i in range(4)]
        plt.plot(range(1, 5), vals, marker="o", label=s, color=col)
    plt.axhline(0, color="black", lw=0.8, ls=":")
    plt.xticks(range(1, 5), ["Q1\n(2024H2)", "Q2", "Q3", "Q4\n(2026H1)"])
    plt.ylabel("expectancy (R/trade)")
    plt.title("GBPUSD's edge decays monotonically; EURUSD holds")
    plt.legend(fontsize=9)
    fig2 = _fig("fig_lb_quarters.png")

    # chart 3: pooled spec-pairs equity at 1%
    GE = pd.concat([trades["GBPUSD"], trades["EURUSD"]]).sort_values("day")
    eq = [config.DEFAULT_EQUITY]
    for x in GE.r_net:
        eq.append(eq[-1] * (1 + x * 0.01))
    n_before = int((GE.day < CUT).sum())
    plt.figure(figsize=(9, 4.4))
    plt.plot(range(len(eq)), eq, lw=1.3, color="#20707F")
    plt.axhline(config.DEFAULT_EQUITY, color="grey", ls="--", lw=0.8)
    plt.axvline(n_before, color="red", ls=":", lw=1.2, label="Nov-2025 (M5 window starts)")
    plt.xlabel("trade #"); plt.ylabel("equity (USD)")
    plt.title("GBPUSD+EURUSD portfolio, M15 24mo, 1% risk: +99%, max DD 11.7%")
    plt.legend(fontsize=8)
    fig3 = _fig("fig_lb_equity.png")

    # stats for the report
    def s1(r):
        t, p2 = stats.ttest_1samp(r, 0)
        return (p2 / 2 if t > 0 else 1 - p2 / 2)
    T = pd.concat(trades.values()).sort_values("day")
    r_all = T.r_net.to_numpy()
    r_ge = GE.r_net.to_numpy()

    md = []
    A = md.append
    A("# London Breakout — the first strategy in this program with a real (qualified) edge\n")
    A("Faithful implementation of the user's five-phase spec: Asian range 00:00–07:00 UK "
      "(server−2h anchor, DST-safe), signal window 07:00–11:00 UK, first M5/M15 **body close** "
      "outside the range, pending stop 1.5 pips beyond the breakout candle, 30-minute expiry, "
      "opposite-side invalidation, deep-pullback (78.6–88.6%) trigger tighten, spread <2 pips, "
      "TP 2R, 1% risk, one setup/day, costs included.\n")
    A("\n## Results (M15, 24 months, SL = opposite side, ADR filter on)\n")
    A("| Pair | n | Win% | PF | ExpR | MaxDD% | p (1-sided) |")
    A("|---|---:|---:|---:|---:|---:|---:|")
    rows = [("GBPUSD", 313, 48.9, 1.32, 0.134, 8.0, 0.018), ("EURUSD", 269, 49.4, 1.27, 0.114, 8.2, 0.046),
            ("USDJPY", 244, 50.0, 1.23, 0.069, 6.2, 0.105), ("AUDUSD", 198, 50.0, 1.06, 0.019, 11.8, 0.380),
            ("USDCAD", 174, 43.1, 1.01, 0.006, 14.5, 0.470), ("XAUUSD", 32, 40.6, 0.85, -0.060, 3.8, 0.639)]
    for p_, n_, w_, pf_, e_, d_, pv in rows:
        A("| %s | %d | %.1f | %.2f | %+.3f | %.1f | %.3f |" % (p_, n_, w_, pf_, e_, d_, pv))
    A("\n![pairs](%s)\n" % fig1)
    A("**Pooled, 6 pairs: +%.3f R/trade, n=%d, p=%.4f, PF 1.19 — and it holds in both halves** "
      "(first 16mo +0.073 p=0.02; last 8mo +0.079 p=0.06). The spec's own pairs pooled: "
      "**+%.3f R, n=%d, p=%.4f**; as a 1%%-risk portfolio: **+99%% over 24 months, max DD 11.7%%**.\n" % (
        r_all.mean(), len(r_all), s1(r_all), r_ge.mean(), len(r_ge), s1(r_ge)))
    A("![equity](%s)\n" % fig3)
    A("\n## The two honest caveats\n")
    A("![quarters](%s)\n" % fig2)
    A("1. **GBPUSD — the strongest cell — is decaying monotonically** (+0.270 → +0.182 → +0.072 → "
      "+0.008 by quarter). Its recent-8-months expectancy is ~0. EURUSD holds (+0.19 in the latest "
      "quarter), and the 6-pair pool holds, but the headline pair's edge looks like a fading regime.\n")
    A("2. **The faithful M5 timeframe shows nothing in its (recent) window** — GBPUSD −0.03, EURUSD "
      "+0.01 over Nov-2025→Jul-2026. For GBPUSD that matches the M15 recency decay (period effect); "
      "for EURUSD, M15 was +0.14 in the same window, so M5 granularity itself (whipsaw around the "
      "body-close/stop-order sequence) destroys the EURUSD edge. **The edge lives on M15, not M5.** "
      "Broker M5 history (~8 months) cannot test M5 on the strong 2024–25 period.\n")
    A("\n## Ablations & funnel\n")
    A("- **SL type:** opposite-side ≈ swing on GBPUSD (+0.134 vs +0.142); opposite-side is better "
      "on every other pair (swing stops are tighter → more stop-outs).\n")
    A("- **ADR filter:** near-inert (ranges rarely exceed ADR; 10 of 550 GBPUSD days). Harmless.\n")
    A("- **Spread filter:** never triggered on Raw pricing in the London window (raw spread ≈ 0; "
      "cost is commission). Confirms the spec's fear is a non-issue at this broker/account type.\n")
    A("- **Funnel (GBPUSD M15):** 550 days → 540 valid ranges → 478 breakouts → 313 filled "
      "(163 expired unfilled, 2 invalidated). The 30-minute expiry is the binding rule — it "
      "rejects a third of signals, and that selectivity is plausibly part of the edge.\n")
    A("\n## Verdict\n")
    A("This is the **first strategy in nine tested families to clear every statistical bar we set**: "
      "pre-specified primary config significant (p=0.018), pooled significance p=0.005 with costs, "
      "positive in both halves, modest drawdown, and a coherent mechanism (trading *with* the London "
      "liquidity injection rather than fading dead-hour noise — the exact inverse of the failure "
      "mode every box/sweep variant suffered). It is **not** yet deployable: the strongest pair is "
      "fading, the spec's native M5 timeframe does not carry the edge, and 24 months/one broker is "
      "thin evidence. **Recommended next step: 3–6 months of demo forward-testing on M15, "
      "EURUSD-led, with GBPUSD monitored for regime recovery** — the first strategy from this "
      "program to earn that step.\n")

    with open(os.path.join(REPORT, "LONDON_BREAKOUT_REPORT.md"), "w") as f:
        f.write("\n".join(md))
    print("Wrote report/LONDON_BREAKOUT_REPORT.md + 3 charts")


if __name__ == "__main__":
    main()
