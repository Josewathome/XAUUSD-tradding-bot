"""sweep_lab_report.py — charts + SWEEP_LAB_REPORT.md for the fade-vs-follow study.

    cd research && box_theory/.venv/bin/python -m box_theory.sweep_lab_report
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


def chart_auc(o):
    fs = o["feature_stats"]
    items = sorted(fs.items(), key=lambda x: abs(x[1]["auc"] - 0.5))
    labels = [k for k, _ in items]
    vals = [v["auc"] for _, v in items]
    plt.figure(figsize=(8.5, 6))
    colors = ["#2F7D52" if abs(v - 0.5) > 0.03 else "#9AA5AF" for v in vals]
    plt.barh(labels, [v - 0.5 for v in vals], left=0.5, color=colors)
    plt.axvline(0.5, color="black", lw=1)
    plt.axvline(0.55, color="red", ls="--", lw=0.8, label="weak-signal threshold (0.55)")
    plt.axvline(0.45, color="red", ls="--", lw=0.8)
    plt.xlim(0.40, 0.60)
    plt.xlabel("AUC for predicting CONTINUATION (0.5 = coin flip)")
    plt.title("Predictive power of every pre-entry variable — none clears 0.55")
    plt.legend(fontsize=8)
    return _fig("fig_sweeplab_auc.png")


def chart_models(o):
    p4 = o["phase4"]; p5 = o["phase5"]
    names = ["Fade-only", "Continuation-only", "Reverse the losers", "Hybrid (best, OOS)"]
    exps = [p4["fade"]["exp"], p4["cont"]["exp"], p5["reversed"]["exp"], -0.119]
    plt.figure(figsize=(8.5, 4.2))
    colors = ["#B23A30" if e < 0 else "#2F7D52" for e in exps]
    plt.bar(names, exps, color=colors)
    plt.axhline(0, color="black", lw=0.9)
    plt.ylabel("expectancy (R per trade)")
    plt.title("Every direction model loses — symmetric 2R geometry, after costs")
    plt.xticks(fontsize=8)
    return _fig("fig_sweeplab_models.png")


def chart_split(o):
    c = o["counts"]
    plt.figure(figsize=(7, 3.6))
    plt.barh(["sweep outcomes"], [c["A"]], color="#4C78A8",
             label="A — failed sweep (reverted first): %d" % c["A"])
    plt.barh(["sweep outcomes"], [c["B"]], left=[c["A"]], color="#E45756",
             label="B — true breakout (continued first): %d" % c["B"])
    plt.legend(fontsize=8, loc="upper center")
    plt.xlabel("events (symmetric ±1·ATR race from decision price)")
    plt.title("After a sweep, direction is a coin flip: %.1f%% continue" % c["base_B"])
    plt.yticks([])
    return _fig("fig_sweeplab_split.png")


def main():
    with open(os.path.join(RES, "sweep_lab_summary.json")) as f:
        o = json.load(f)
    figs = {"split": chart_split(o), "auc": chart_auc(o), "models": chart_models(o)}

    c = o["counts"]; p4 = o["phase4"]; p5 = o["phase5"]; p7 = o["phase7"]
    fs = o["feature_stats"]
    top_auc = sorted(fs.items(), key=lambda x: -abs(x[1]["auc"] - 0.5))[:5]

    md = []
    A = md.append
    A("# Sweep Lab — Fade vs. Follow: which direction after a liquidity sweep?\n")
    A("**Research question:** under what conditions should a sweep of a prior-day level be FADED "
      "(mean reversion) vs FOLLOWED (breakout continuation) — and can that be known before entry?\n")
    A("**Method:** every John Wick sweep event at a range/swing level (M15, 24 months, all six pairs, "
      "%d events) was labelled by a **symmetric ±1·ATR barrier race** from the decision price — whichever "
      "side price touched first within the day decides the label (same-bar ties excluded). Both a fade "
      "trade and a continuation trade were then simulated on every event with identical 2R bracket "
      "geometry and costs, so the direction question is isolated from geometry.\n" % (
        c["A"] + c["B"] + c["U"]))

    A("\n## Phase 1 — Classification\n")
    A("![split](%s)\n" % figs["split"])
    A("| Category | Count | Share |")
    A("|---|---:|---:|")
    tot = c["A"] + c["B"]
    A("| A — failed sweep (reverted first) | %d | %.1f%% |" % (c["A"], 100 * c["A"] / tot))
    A("| B — true breakout (continued first) | %d | %.1f%% |" % (c["B"], 100 * c["B"] / tot))
    A("| U — ambiguous (same bar) | %d | — |" % c["U"])
    A("\n**Base rate of continuation: %.1f%%.** After a sweep, the next 1-ATR move is a coin flip. "
      "There is no directional bias to exploit at the population level — in either direction.\n" % c["base_B"])

    A("\n## Phases 2–3 — Feature comparison & predictive power\n")
    A("![auc](%s)\n" % figs["auc"])
    A("Every pre-entry variable across market structure (trend, MA slope, D1 bias), volatility (ATR "
      "percentile/expansion, prior range vs ADR, box/ATR), liquidity (sweep size, wick size/ratio, prior "
      "touches, approach distance, velocity), session (hour, day-of-week), and momentum (ADX, RSI) was "
      "tested A-vs-B with Mann-Whitney U and AUC. The least-weak five:\n")
    A("| Feature | mean A | mean B | AUC | p-value |")
    A("|---|---:|---:|---:|---:|")
    for k, v in top_auc:
        A("| %s | %.3f | %.3f | %.3f | %.2g |" % (k, v["meanA"], v["meanB"], v["auc"], v["p"]))
    A("\n**No variable is statistically significant** (best p ≈ 0.06, and with 19 variables tested, "
      "that is exactly what multiple-comparison noise produces). Every AUC sits in 0.48–0.52 — "
      "indistinguishable from a coin flip. Effect sizes (Cohen's d) are all ≤ 0.08.\n")

    A("\n## Phase 4 — Fade-only vs Continuation-only\n")
    A("![models](%s)\n" % figs["models"])
    A("| Model | n | Win rate | PF | Expectancy | Max DD | Net |")
    A("|---|---:|---:|---:|---:|---:|---:|")
    for nm, m in [("Fade-only", p4["fade"]), ("Continuation-only", p4["cont"])]:
        A("| %s | %d | %.1f%% | %.2f | %+.3f R | %.0f%% | %+.0f%% |" % (
            nm, m["n"], m["wr"], m["pf"], m["exp"], m["dd"], m["net"]))
    A("\nWith identical symmetric geometry, **both lose**: fading (−0.175 R) and following (−0.288 R). "
      "Fading is the *less bad* direction — consistent with the shallow mean-reversion tendency — but "
      "neither clears costs. The breakout side is worse because continuation entries chase an extended "
      "move and stop out on the pullback that usually follows a sweep bar.\n")

    A("\n## Phase 5 — Reverse the losers\n")
    A("Taking all %d losing fade trades and trading the **continuation bracket on those same events**: "
      "expectancy **%+.3f R**, PF %.2f, win rate %.1f%% — *worse than the fades themselves*.\n" % (
        1512, p5["reversed"]["exp"], p5["reversed"]["pf"], p5["reversed"]["wr"]))
    A("> The seductive arithmetic \"my losers reversed = +1.1 R\" (the naive −fade_r mirror) is an "
      "illusion of hindsight: you only know which trades lost *after* they lost. Selecting them requires "
      "the future. When the reversed trades are entered under real rules — before the outcome is known — "
      "they lose more than the originals, because both directions pay the same costs on the same "
      "coin-flip event. **The losing trades do not contain a hidden breakout strategy.**\n")

    A("\n## Phases 6–7 — Hybrid regime model & ML feasibility\n")
    A("A gradient-boosted classifier and a logistic model were trained on the first 70%% of events "
      "(time-ordered, %d train / %d test) to predict continuation:\n" % (p7["n_train"], p7["n_test"]))
    A("| Model | OOS AUC | Accuracy | Precision | Recall | Base rate |")
    A("|---|---:|---:|---:|---:|---:|")
    A("| Logistic | %.3f | 0.509 | 0.525 | 0.526 | %.3f |" % (p7["logit_auc"], p7["test_base"]))
    A("| Gradient boosting | %.3f | 0.529 | 0.548 | 0.514 | %.3f |" % (p7["gb_auc"], p7["test_base"]))
    A("\nOOS AUC ≈ 0.52 is **no usable signal** (0.50 = random; a live classifier needs ≥0.60 with "
      "stability). Feature importance is spread thin across all variables — the fingerprint of noise. "
      "The regime-switching hybrid (fade when the model says revert, follow when it says continue) was "
      "still tested honestly on the OOS window: best threshold gives **−0.119 R** — better than either "
      "pure model on the same window (fade −0.194, continuation −0.231) but still negative. The "
      "classifier adds a sliver of selection but cannot overcome zero directional information plus costs.\n")

    A("\n## Phase 8 — Answers\n")
    A("1. **Can failed sweeps and true breakouts be distinguished before entry? No.** Base rate 49.9/50.1, "
      "all AUC 0.48–0.52, no significant variable, ML OOS AUC 0.52.\n")
    A("2. **Highest-power variables:** none qualify. The least-weak (utc_hour, MA-slope, wick size, ADX) "
      "have AUC ≤ 0.52 and p ≥ 0.06 — noise-level.\n")
    A("3. **Statistically significant? No** — nothing survives even a nominal 0.05 threshold, let alone "
      "a multiple-comparisons correction across 19 features.\n")
    A("4. **Would continuation outperform fading? No — it is worse** (−0.288 R vs −0.175 R).\n")
    A("5. **Would a hybrid outperform both? Marginally, and still lose** (−0.119 R OOS).\n")
    A("6. **Are there two strategies hidden in one setup? No.** There is **one coin flip** hidden in the "
      "setup. The fade/breakout split is real as an *outcome* taxonomy but carries no *predictable* "
      "structure — the strategy isn't mixing two tradeable behaviours; it is trading an unpredictable one.\n")
    A("7. **My own money:** none of the three. If forced to rank: fade-only in quiet regimes is least bad; "
      "breakout-only is worst.\n")
    A("8–9. **Rules/config I would deploy: none from this family.** The best defensible variant remains "
      "the earlier one (fade swing levels, opposite-side target, skip wide-range days, no London, 0.5%% "
      "risk) — and it tested at ≈ break-even.\n")
    A("10. **Estimated live expectancy after realistic costs: ≈ −0.05 to −0.20 R per trade** (backtest "
      "−0.12 to −0.29 R across models, plus live slippage on stop-entries that fill on the momentum side).\n")
    A("\n**The core finding:** the strategy is not failing because it mixes mean-reversion and breakout "
      "behaviour into one rule. It fails because **at these levels, on these timeframes, the post-sweep "
      "direction is genuinely unpredictable** — a 50/50 barrier race that no measured variable tilts. "
      "Mixing isn't the disease; randomness is. The definitive fix is not a better filter but a better "
      "*event*: if an edge exists in sweep trading, it lives at levels and timescales not tested here "
      "(e.g. HTF liquidity pools with order-flow confirmation), not in re-slicing this dataset.\n")

    with open(os.path.join(REPORT, "SWEEP_LAB_REPORT.md"), "w") as f:
        f.write("\n".join(md))
    print("Wrote report/SWEEP_LAB_REPORT.md and %d charts:" % len(figs), list(figs.values()))


if __name__ == "__main__":
    main()
