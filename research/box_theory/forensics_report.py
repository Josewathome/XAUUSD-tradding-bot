"""forensics_report.py — charts + FORENSICS_REPORT.md from saved forensics output.

    cd research && box_theory/.venv/bin/python -m box_theory.forensics_report
"""
from __future__ import annotations

import os
import json

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

RES = os.path.join(os.path.dirname(__file__), "results")
REPORT = os.path.join(os.path.dirname(__file__), "report")


def _fig(name):
    plt.tight_layout(); plt.savefig(os.path.join(REPORT, name), dpi=110); plt.close()
    return name


def chart_loss_causes(o):
    lc = o["loss_causes"]
    items = sorted(lc.items(), key=lambda x: x[1]["n"])
    labels = [k.replace("_", " ") for k, _ in items]
    vals = [v["pct"] for _, v in items]
    plt.figure(figsize=(9, 4.6))
    plt.barh(labels, vals, color="#B23A30")
    for i, v in enumerate(vals):
        plt.text(v + 0.3, i, "%.1f%%" % v, va="center", fontsize=8)
    plt.xlabel("% of losing trades")
    plt.title("Root cause of losses (primary, priority-assigned)")
    return _fig("fig_forensic_loss_causes.png")


def chart_effects(o):
    wl = o["win_vs_loss"]
    items = [(k, v["cohen_d"]) for k, v in wl.items() if v["cohen_d"] is not None]
    items = sorted(items, key=lambda x: x[1])
    labels = [k for k, _ in items]; vals = [v for _, v in items]
    colors = ["#B23A30" if v < 0 else "#2F7D52" for v in vals]
    plt.figure(figsize=(8.5, 4.8))
    plt.barh(labels, vals, color=colors)
    plt.axvline(0, color="black", lw=0.8)
    plt.xlabel("Cohen's d  (positive = higher in WINNERS)")
    plt.title("What separates winners from losers (effect size)")
    return _fig("fig_forensic_effects.png")


def chart_filters(o):
    base = o["base"]
    f = o["filters"]
    names = list(f.keys())
    exp = [f[n]["exp"] for n in names]
    plt.figure(figsize=(10, 4.8))
    y = np.arange(len(names))
    colors = ["#2F7D52" if e > base["exp"] else "#B23A30" for e in exp]
    plt.barh(y, exp, color=colors)
    plt.axvline(base["exp"], color="black", ls="--", lw=1, label="baseline %.3f" % base["exp"])
    plt.yticks(y, [n.replace("skip ", "").replace(" ", "\n", 0) for n in names], fontsize=8)
    plt.xlabel("expectancy (R) after filter")
    plt.title("Filter feasibility — only 'skip wide-range days' improves the edge")
    plt.legend(fontsize=8)
    return _fig("fig_forensic_filters.png")


def chart_bar_in_day(T):
    T = T.dropna(subset=["bar_in_day"]).copy()
    T["q"] = pd.qcut(T["bar_in_day"], 6, labels=False, duplicates="drop")
    g = T.groupby("q").agg(wr=("win", "mean"), expR=("r_net", "mean"),
                           mid=("bar_in_day", "median"))
    fig, ax1 = plt.subplots(figsize=(8.5, 4.4))
    ax1.bar(g.index, g["wr"] * 100, color="#4C78A8", alpha=0.7)
    ax1.set_ylabel("win rate %", color="#4C78A8")
    ax1.set_xlabel("entry timing sextile (0 = earliest bar of day → 5 = latest)")
    ax2 = ax1.twinx()
    ax2.plot(g.index, g["expR"], color="#E45756", marker="o")
    ax2.axhline(0, color="grey", lw=0.6)
    ax2.set_ylabel("expectancy (R)", color="#E45756")
    plt.title("Later-in-day entries win more often — but expectancy stays ~flat")
    return _fig("fig_forensic_timing.png")


def chart_category(o):
    bc = o["by_category"]
    fig, (a, b) = plt.subplots(1, 2, figsize=(11, 4.2))
    ses = bc["session"]
    a.bar(list(ses.keys()), [v["expR"] for v in ses.values()],
          color=["#B23A30" if v["expR"] < 0 else "#2F7D52" for v in ses.values()])
    a.axhline(0, color="black", lw=0.8); a.set_title("Expectancy by session (R)")
    sym = bc["symbol"]
    b.bar(list(sym.keys()), [v["expR"] for v in sym.values()],
          color=["#B23A30" if v["expR"] < 0 else "#2F7D52" for v in sym.values()])
    b.axhline(0, color="black", lw=0.8); b.set_title("Expectancy by instrument (R)")
    b.tick_params(axis="x", rotation=30)
    return _fig("fig_forensic_category.png")


def main():
    with open(os.path.join(RES, "forensics_summary.json")) as f:
        o = json.load(f)
    T = pd.read_csv(os.path.join(RES, "forensics_trades.csv"))

    figs = {
        "causes": chart_loss_causes(o),
        "effects": chart_effects(o),
        "filters": chart_filters(o),
        "timing": chart_bar_in_day(T),
        "category": chart_category(o),
    }
    base = o["base"]
    lc = o["loss_causes"]; lcond = o["loss_conditions"]; filt = o["filters"]
    bc = o["by_category"]

    def top_causes(n=4):
        return sorted(lc.items(), key=lambda x: -x[1]["n"])[:n]

    md = []
    A = md.append
    A("# Sneaky Pivot — Forensic Trade Analysis\n")
    A("A trade-level root-cause investigation of **why** the strategy behaves as it does, "
      "on a fixed canonical config pooled across all six instruments for statistical power: "
      "**M15, 24 months, literal swing levels, range+swing, all-day entry, wick B, 10%% buffer, "
      "opposite-side target, one trade/day** — **%d trades** (%d wins / %d losses).\n" % (
        o["n_trades"], o["wins"], o["losses"]))
    A("> All-day timing is used here (not the session-open sequence) purely for sample size; "
      "the timing question was already settled in Part II (no anchor added edge). Metrics below "
      "are in-sample and descriptive — the walk-forward verdict (≈0 R) still governs tradeability.\n")

    A("\n## 1. Strategy Ambiguities — resolved\n")
    A("Every subjective rule in the transcript, the interpretations considered, the most faithful "
      "reading, the most testable one, and what was implemented:\n")
    A("| Rule | Interpretations | Most faithful | Most testable / used |")
    A("|---|---|---|---|")
    A("| **Range high/low** | (a) prev *calendar* day H/L; (b) prev *trading* (D1 broker) day H/L | (b) — the video points at \"yesterday's\" candles | Prev D1 bar H/L (weekend→Friday) |")
    A("| **Swing high/low** | (a) nearest prior daily bar beyond the range (\"scan left\"); (b) N-bar fractal swing pivot; (c) a fixed lookback high/low | (a) — matches \"go back until the next higher/lower price\" | **Both (a) literal and (b) fractal tested** — results similar |")
    A("| **\"Scan left…\"** | first bar with a higher high vs first *pivot* higher high; how far back | Nearest prior extreme, unbounded | Literal = nearest prior daily extreme beyond range |")
    A("| **Sneaky candle** | any rejection candle / a John Wick (hammer/shooting-star) at the level | John Wick at the level | John Wick, 4 defs (2×/3× body, 60% range, close-inside) |")
    A("| **Entry trigger** | (a) break of pivot *high/low*; (b) break/close past pivot *body*; (c) at pivot close | (a)/(b) — \"one candle crosses another\" | Break of pivot high (buy)/low (sell) |")
    A("| **Stop-loss** | tip of the pivot wick; or below the \"big buyer\" swing | Tip of the wick + small buffer | Pivot wick ± 2% of box height |")
    A("| **Target** | opposite side of range; opposite swing; midpoint; fixed RR | Opposite side (\"trade it back\") | **opposite / midpoint / RR all tested**; opposite is the thesis |")
    A("| **Session start** | broker rollover; London; NY (US cash open in the video) | NY for the video's US stocks; ambiguous for FX | **broker / London / NY all tested** (fixed a UTC-wrap bug) |")
    A("| **Invalidation** | price breaks the wick tip; or closes beyond it; time-out | Break of the wick tip | SL at wick tip; flat at end of day; pivot expires in N bars |")
    A("Net: the ambiguous rules were not guessed — each was parameterised and swept, so conclusions "
      "do not hinge on one reading. Two mattered materially: **which levels** (swing vs range) and "
      "**target** (opposite vs RR); the rest moved results little.\n")

    A("\n## 2. Why trades reverse (root cause of losses)\n")
    A("![causes](%s)\n" % figs["causes"])
    tc = top_causes(4)
    A("Primary cause of the %d losing trades (priority-assigned so each loss counts once):\n" % o["losses"])
    A("| Cause | Share of losses | Avg R |")
    A("|---|---:|---:|")
    for k, v in sorted(lc.items(), key=lambda x: -x[1]["n"]):
        A("| %s | %.1f%% | %+.2f |" % (k.replace("_", " "), v["pct"], v["avg_r"]))
    A("\n**The dominant mechanism is a fade that never gets its reversal.** Counting conditions "
      "independently across *all* losses: the day ultimately closed **against** the fade (≥0.5R) in "
      "**%.0f%%** of losses, the faded level was **broken** and price kept going in **%.0f%%**, and the "
      "trade **never moved in our favour** (MFE < 0.5R) in **%.0f%%**. Only **%.0f%%** sat in a hard "
      "trend (ADX≥25 against), and volatility spikes (%.0f%%) and gaps (%.0f%%) are minor. In plain "
      "terms: most losses are simply **the level failing to hold** — the strategy is fading real "
      "directional movement and being run over, not being stopped by news or gaps.\n" % (
        lcond["day_closed_against(>=0.5R)"], lcond["level_broken"], lcond["never_went_our_way(mfe<0.5R)"],
        lcond["trend_against(adx>=25)"], lcond["vol_expansion(>=1.8)"], lcond["gap(>0.25H)"]))

    A("\n## 3. Why winners win\n")
    A("![effects](%s)\n" % figs["effects"])
    A("![timing](%s)\n" % figs["timing"])
    A("Winners are not a different animal — they share the losers' setups. The separators are weak "
      "and mostly *mechanical*: winners enter **later in the day** (Cohen's d ≈ 0.41 — the strongest "
      "single separator) and in **higher-volatility** regimes (atr percentile d ≈ 0.31, atr-expansion "
      "d ≈ 0.22). Crucially, later entries win *more often* but expectancy stays flat — they simply have "
      "less room, so smaller targets fill more reliably. The real profit engine is the **opposite-side "
      "target catching an occasional large reversal swing**: a ~20% win rate paying multi-R winners.\n")
    A("![category](%s)\n" % figs["category"])
    A("By segment: **Asian/off-hours** carry the highest expectancy (big quiet-hour reversions), "
      "**London is negative** (trending session — fades die), **New York** has the highest *win rate* "
      "(37%) but ~0 expectancy. **Swing** levels win more often than **range** levels (29.7% vs 19.9%). "
      "By instrument, **AUDUSD/XAUUSD/USDJPY** are positive; **USDCAD is the black hole** (−0.30 R).\n")

    A("\n## 4. Filter feasibility\n")
    A("![filters](%s)\n" % figs["filters"])
    A("For each candidate filter: trades removed (losses/wins), and the net effect. Baseline: "
      "%d trades, WR %.1f%%, PF %.2f, expectancy %+.3f R.\n" % (
        base["n"], base["wr"], base["pf"], base["exp"]))
    A("| Filter | Removed (L/W) | WR | PF | Expectancy | Net% |")
    A("|---|---|---:|---:|---:|---:|")
    for name, v in filt.items():
        A("| %s | %d (%d/%d) | %.1f | %.2f | %+.3f | %+.1f |" % (
            name, v["removed"], v["losses_removed"], v["wins_removed"], v["wr"], v["pf"], v["exp"], v["net"]))
    A("\n**The decisive finding of this whole study:** the intuitive filters *destroy* the strategy. "
      "Skipping volatility spikes takes expectancy to ~0 and net to **−61%**; skipping the Asian session "
      "to **−49%**; skipping trend-against days to **−10%**. Why? **The winners live in the same "
      "conditions as the losers** — the volatility that breaks levels is the very volatility that powers "
      "the big reversals. You cannot filter the failure mode without amputating the profit source.\n")
    A("**One filter is different:** skipping days whose *previous range was abnormally wide relative to "
      "ATR* (`box_height_atr` top quartile) lifts expectancy %+.3f→%+.3f R and PF to %.2f while lowering "
      "drawdown — it removes %d losses at the cost of only %d wins. Wide prior ranges signal an "
      "expansion/trend regime where fading is worst; this is the one clean, pre-entry-detectable edge "
      "improver found.\n" % (
        base["exp"], filt["skip wide box (top quartile)"]["exp"], filt["skip wide box (top quartile)"]["pf"],
        filt["skip wide box (top quartile)"]["losses_removed"], filt["skip wide box (top quartile)"]["wins_removed"]))

    A("\n## 5. Hidden-edge investigation\n")
    A("A logistic model and gradient-boosted trees were fit to predict a win from 13 pre-entry "
      "features. The honest result: **no strong hidden variable exists.** The largest correlation with "
      "the R outcome is `level_broken` at −0.12 (and that is near-tautological — a broken level *is* the "
      "loss). Gradient-boosting importance is spread almost evenly across features (top = `bar_in_day` "
      "0.16, then dist-to-target, stop-size, atr-expansion, atr-percentile, adx, all ~0.10) — the diffuse, "
      "low-signal fingerprint of noise, not a discoverable edge. The predictable structure that does "
      "exist is mechanical (timing/volatility scale the target-hit geometry), not informational.\n")

    A("\n## 6. Final synthesis\n")
    A("1. **Raw performance:** near break-even in-sample (PF %.2f, expectancy %+.3f R, WR %.1f%%), "
      "≈0 R in walk-forward. High variance: the pooled equity's drawdown is severe (an artifact of a "
      "20%% win rate compounded sequentially; per-pair/parallel trading and the wide-range filter cut it materially).\n" % (
        base["pf"], base["exp"], base["wr"]))
    A("2. **Root cause of losses:** fading a level that then breaks and continues — %.0f%% of losses see "
      "the day close against the fade, %.0f%% see the level broken. It is counter-trend risk, not news/gaps.\n" % (
        lcond["day_closed_against(>=0.5R)"], lcond["level_broken"]))
    A("3. **Root cause of profits:** an occasional large mean-reversion swing back across the box, "
      "concentrated in quiet (Asian/off) hours, higher-volatility regimes, swing levels, and AUD/XAU/JPY, "
      "captured by the far opposite-side target.\n")
    A("4. **Preventable?** Mostly **no**. The failure modes (level breaks, trend continuation, whipsaw) "
      "are not separable from the profit source by trend/volatility/session filters — those remove the "
      "winners too. Only the **wide-prior-range** regime filter cleanly helps.\n")
    A("5. **Highest-impact filter:** skip abnormally wide prior-range days (expectancy +%.0f%%, lower DD). "
      "Secondary: skip gap days. Everything else is net-negative.\n" % (
        100 * (filt["skip wide box (top quartile)"]["exp"] / base["exp"] - 1) if base["exp"] else 0))
    A("6. **Would filtering help or destroy it?** The good filter *improves* it in-sample without killing "
      "the source; the intuitive filters *destroy* it. But none of this survives the earlier walk-forward "
      "(≈0 R), so filtering polishes a signal too weak to bank.\n")
    A("7. **Underlying mechanism:** a weak **mean-reversion / liquidity-sweep** at prior-day levels — "
      "price overshoots a level (sweep), then reverts toward the box centre/opposite side. The reversion "
      "is real but shallow (~62–66% reach the midpoint) and is overwhelmed on expansion/trend days.\n")
    A("8. **What the strategy fundamentally is:** **not** an opening-range reversal (timing/anchor added "
      "no edge) and **not** a market-maker/session-repricing play. It is a **counter-trend mean-reversion "
      "fade of liquidity sweeps at the previous day's range/swing extremes** — a real but weak effect whose "
      "profit and loss share the same volatility, which is precisely why it is so hard to filter into "
      "something bankable.\n")
    A("\n**Bottom line:** the forensics locate a genuine micro-effect (shallow mean reversion after "
      "sweeps of prior-day extremes) but confirm it is too weak, too volatility-entangled, and too "
      "regime-dependent to trade as specified. The one honest lever — avoid wide-range/expansion days — "
      "improves it in-sample and deserves an out-of-sample retest before any capital risk.\n")

    with open(os.path.join(REPORT, "FORENSICS_REPORT.md"), "w") as f:
        f.write("\n".join(md))
    print("Wrote report/FORENSICS_REPORT.md and %d charts:" % len(figs), list(figs.values()))


if __name__ == "__main__":
    main()
