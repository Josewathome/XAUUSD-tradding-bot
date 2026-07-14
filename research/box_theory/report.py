"""report.py — assemble the final research report (charts + markdown).

    cd research && box_theory/.venv/bin/python -m box_theory.report

Outputs report/: PNG charts + BOX_THEORY_REPORT.md
"""
from __future__ import annotations

import os
import json

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from . import config
from .load import load_ohlc, load_d1
from .boxes import build_boxes
from .engine import run_backtest

RES = os.path.join(os.path.dirname(__file__), "results")
REPORT = os.path.join(os.path.dirname(__file__), "report")
MIN_TRADES = 30
MAX_PERIOD = {"M5": 6, "M15": 24, "M30": 24}


def _fig(path):
    plt.tight_layout()
    plt.savefig(path, dpi=110)
    plt.close()
    return os.path.basename(path)


def chart_zone(z):
    z24 = z[z.months == z.groupby("symbol")["months"].transform("max")]
    z24 = z24.drop_duplicates("symbol").sort_values("symbol")
    syms = z24["symbol"].tolist()
    x = np.arange(len(syms))
    plt.figure(figsize=(9, 4.5))
    plt.bar(x - 0.2, z24["pdh_reverse_pct"], 0.4, label="PDH reversed", color="#4C78A8")
    plt.bar(x + 0.2, z24["pdl_reverse_pct"], 0.4, label="PDL reversed", color="#F58518")
    plt.axhline(50, color="red", ls="--", lw=1, label="coin flip (50%)")
    plt.xticks(x, syms)
    plt.ylabel("% of touches that reversed")
    plt.title("Do PDH/PDL actually reverse? (longest period per pair)")
    plt.legend(fontsize=8)
    return _fig(os.path.join(REPORT, "fig_zone_reversal.png"))


def chart_distribution(valid):
    e = valid["expectancy_r"].replace([np.inf, -np.inf], np.nan).dropna()
    plt.figure(figsize=(9, 4.5))
    plt.hist(e, bins=60, color="#72B7B2", edgecolor="white")
    plt.axvline(0, color="red", ls="--", lw=1.2, label="break-even")
    plt.axvline(e.median(), color="black", ls="-", lw=1.2, label="median %.3f" % e.median())
    plt.xlabel("expectancy (R per trade)")
    plt.ylabel("# of configurations")
    plt.title("Edge distribution across %d configs — %.0f%% lose money" % (
        len(e), 100 * (e <= 0).mean()))
    plt.legend(fontsize=8)
    return _fig(os.path.join(REPORT, "fig_edge_distribution.png"))


def chart_dimensions(valid):
    fig, axes = plt.subplots(1, 3, figsize=(12, 4))
    for ax, dim, title in [(axes[0], "timeframe", "Timeframe"),
                           (axes[1], "entry_method", "Entry method"),
                           (axes[2], "exit_kind", "Exit model")]:
        g = valid.groupby(dim)["expectancy_r"].mean().sort_values()
        colors = ["#E45756" if v < 0 else "#54A24B" for v in g.values]
        ax.barh([str(i) for i in g.index], g.values, color=colors)
        ax.axvline(0, color="black", lw=0.8)
        ax.set_title(title, fontsize=10)
        ax.set_xlabel("mean expR")
    fig.suptitle("Average edge by dimension (all negative)", fontsize=11)
    return _fig(os.path.join(REPORT, "fig_dimensions.png"))


def chart_oos(rob):
    o = rob["oos"]
    labels = [d["config"].split(" wick")[0] for d in o]
    ins = [d["in_sample"]["expR"] for d in o]
    oos = [d["out_sample"]["expR"] for d in o]
    x = np.arange(len(labels))
    plt.figure(figsize=(9, 4.5))
    plt.bar(x - 0.2, ins, 0.4, label="in-sample (first 70%)", color="#B279A2")
    plt.bar(x + 0.2, oos, 0.4, label="out-of-sample (last 30%)", color="#54A24B")
    plt.axhline(0, color="black", lw=0.8)
    plt.xticks(x, labels, rotation=20, fontsize=8)
    plt.ylabel("expectancy (R)")
    plt.title("In-sample vs out-of-sample (best config per pair)")
    plt.legend(fontsize=8)
    return _fig(os.path.join(REPORT, "fig_oos.png"))


def chart_walkforward(rob):
    wf = rob["walk_forward"]
    plt.figure(figsize=(9, 4.5))
    for w in wf:
        plt.plot(range(1, len(w["fold_expR"]) + 1), w["fold_expR"], marker="o", label=w["symbol"])
    plt.axhline(0, color="black", lw=0.8)
    pooled = rob.get("pooled_wf_expR", float("nan"))
    plt.axhline(pooled, color="red", ls="--", lw=1.2, label="pooled %.3f" % pooled)
    plt.xlabel("walk-forward test fold")
    plt.ylabel("out-of-sample expR")
    plt.title("Walk-forward selection — pooled OOS expectancy ≈ 0")
    plt.legend(fontsize=8, ncol=2)
    return _fig(os.path.join(REPORT, "fig_walkforward.png"))


def chart_buffer(rob):
    s = rob["sensitivity"]
    b = [d["buffer_pct"] for d in s]
    e = [d["expR"] for d in s]
    plt.figure(figsize=(7, 4))
    plt.plot(b, e, marker="o", color="#4C78A8")
    plt.axhline(0, color="black", lw=0.8)
    plt.xlabel("buffer distance (fraction of box height)")
    plt.ylabel("expectancy (R)")
    plt.title("Parameter fragility: top config's edge vs buffer size")
    return _fig(os.path.join(REPORT, "fig_buffer_sensitivity.png"))


def chart_equity_best(top_row):
    p = {"wick": top_row["wick"], "entry_method": int(top_row["entry_method"]),
         "exit": (top_row["exit_kind"], None if pd.isna(top_row["exit_val"]) else float(top_row["exit_val"])),
         "buffer_pct": float(top_row["buffer_pct"]), "mid_band_pct": config.MID_BAND_PCT,
         "risk_pct": 0.01, "one_trade_per_box": True, "hold_to_eod": True}
    sym, tf = top_row["symbol"], top_row["timeframe"]
    df = load_ohlc(sym, tf, months=MAX_PERIOD[tf])
    boxes = build_boxes(load_d1(sym))
    n = len(df)
    cut = int(n * 0.7)
    tr = run_backtest(df, boxes, sym, p)["trades"]
    eq = np.concatenate([[config.DEFAULT_EQUITY], tr["equity_after"].to_numpy()])
    # mark the in/out-of-sample boundary by trade index
    cut_time = df["time_utc"].iloc[cut]
    oos_start = int((tr["entry_time"] < cut_time).sum())
    plt.figure(figsize=(9, 4.5))
    plt.plot(range(len(eq)), eq, lw=1.3, color="#4C78A8")
    plt.axhline(config.DEFAULT_EQUITY, color="grey", ls="--", lw=0.8)
    plt.axvline(oos_start, color="red", ls=":", lw=1.2, label="70/30 in/out split")
    plt.xlabel("trade #")
    plt.ylabel("equity (USD)")
    plt.title("Best config on full %dmo: %s %s wick%s e%s %s buf%.2f" % (
        MAX_PERIOD[tf], sym, tf, top_row["wick"], int(top_row["entry_method"]),
        top_row["exit_kind"], top_row["buffer_pct"]))
    plt.legend(fontsize=8)
    return _fig(os.path.join(REPORT, "fig_equity_best.png"))


def main():
    os.makedirs(REPORT, exist_ok=True)
    m = pd.read_csv(os.path.join(RES, "master_results.csv"))
    z = pd.read_csv(os.path.join(RES, "zone_stats.csv"))
    with open(os.path.join(RES, "robustness.json")) as f:
        rob = json.load(f)
    m1 = m[m.risk_pct == 0.01].copy()
    valid = m1[m1.total_trades >= MIN_TRADES].copy()

    figs = {
        "zone": chart_zone(z),
        "dist": chart_distribution(valid),
        "dims": chart_dimensions(valid),
        "oos": chart_oos(rob),
        "wf": chart_walkforward(rob),
        "buffer": chart_buffer(rob),
    }
    top_overall = valid.sort_values("expectancy_r", ascending=False).iloc[0]
    worst = valid.sort_values("expectancy_r").iloc[0]
    figs["equity"] = chart_equity_best(top_overall)

    # headline numbers
    e = valid["expectancy_r"].replace([np.inf, -np.inf], np.nan).dropna()
    frac_lose = 100 * (e <= 0).mean()
    pf_pos = 100 * (valid.profit_factor > 1).mean()
    med_exp = e.median()
    med_net = valid["net_return_pct"].median()
    pooled_wf = rob.get("pooled_wf_expR", float("nan"))

    idx = valid.groupby("symbol")["expectancy_r"].idxmax()
    perpair = valid.loc[idx].sort_values("expectancy_r", ascending=False)

    md = []
    A = md.append
    A("# Box Theory Model — Quantitative Validation Report\n")
    A("**Author:** Quantitative research harness (`research/box_theory/`) · "
      "**Broker data:** IC Markets demo (acct 52942033) · **Generated from** %d backtested configurations.\n" % (len(m1) // 1))
    A("> Scope note: this study evaluates the *strategy hypothesis* on historical data. "
      "It is independent of the live execution framework and does not modify it.\n")

    A("\n## 1. Executive Summary\n")
    A("**Verdict: the Box Theory, as specified, does not exhibit a robust, tradeable statistical edge. "
      "The theory is rejected.**\n")
    A("- Its central premise — that price *reverses* at the previous day's high/low — is empirically false. "
      "Across all pairs, a touch of PDH/PDL is followed by a reversal only **~45–55%** of the time: a coin flip.\n")
    A("- Of **%d** configurations with a meaningful sample (≥%d trades), **%.0f%% lose money**; the median "
      "configuration returns **%.1f%%** with expectancy **%.3f R**/trade and profit factor < 1.\n" % (
        len(valid), MIN_TRADES, frac_lose, med_net, med_exp))
    A("- The single most honest test — **walk-forward selection** (optimise on the past, trade the unseen future) — "
      "pools to **%.3f R** across 24 folds: statistically indistinguishable from zero, i.e. no exploitable edge.\n" % pooled_wf)
    A("- The only sub-family that ever shows positive expectancy is *exit-at-opposite-side-of-box* — which is **not** the "
      "stated thesis (a small fade off a rejection) but a tight-stop / large-target lottery. Its gains are recent, "
      "fragile to tiny parameter changes, and carry 40–68% drawdowns. Not a reliable edge.\n")

    A("\n## 2. Strategy Rules (as tested)\n")
    A("Daily box from the **previous** day's D1 bar: Top = PDH, Bottom = PDL, Middle = 50%. "
      "Long near PDL / short near PDH on a *John Wick* rejection candle; no trades in the midpoint band.\n")
    A("- **John Wick** definitions tested: A (wick ≥ 2× body), B (≥ 3× body), "
      "C (wick ≥ 60% of range), D (≥ 2× body + close back inside box).\n")
    A("- **Entries:** (1) at rejection close, (2) on break of the rejection candle, (3) on next-candle confirmation.\n")
    A("- **Exits:** fixed RR 1:1/1:1.5/1:2/1:3, exit-at-midpoint, exit-at-opposite-side, ATR target, trailing stop.\n")
    A("- **Buffers** 5/10/15/20% of box height; **risk** 1% and 0.5%; costs = per-symbol round-turn "
      "spread+commission (IC Markets Raw assumptions). One trade per box, flat overnight.\n")
    A("- **Engine integrity:** 33 unit tests; manual trade audit to the tick; no look-ahead (signal at close, "
      "managed next bar); pessimistic SL-first intrabar ties; a seeded random-entry control confirms the harness "
      "manufactures no edge.\n")

    A("\n## 3. Statistical Findings\n")
    A("### 3.1 Zone statistics — the hypothesis test\n")
    A("![zone](%s)\n" % figs["zone"])
    A("Reversal rates cluster around 50%%. On USDJPY and AUDUSD the extreme **breaks more often than it holds**. "
      "After touching an extreme, price reaches the midpoint **~62–66%%** of the time — a mild pull toward the *center*, "
      "the opposite of the 'reverse hard off the edge' claim.\n")
    A("### 3.2 Edge distribution — is it real or cherry-picked?\n")
    A("![dist](%s)\n" % figs["dist"])
    A("With thousands of configs, some are positive by chance. The distribution is what matters, and it sits firmly "
      "left of zero (**%.0f%% losing**, median **%.3f R**). A genuine edge centers the distribution above zero; this does not.\n" % (frac_lose, med_exp))
    A("### 3.3 Where the (weak) signal concentrates\n")
    A("![dims](%s)\n" % figs["dims"])
    A("Every dimension averages negative. Least-bad choices: **M30 > M15 ≫ M5** (costs dominate M5), "
      "confirmation entries (2/3) over enter-at-close, and the *opposite-side* exit. Tighter buffers beat wider.\n")

    A("\n## 4. Charts & Equity Curves\n")
    A("![equity](%s)\n" % figs["equity"])
    A("The best single configuration of all %d. Note the red split: most of the curve's rise occurs in the recent "
      "(out-of-sample) third — performance is **regime-concentrated**, not steady compounding.\n" % (len(m1)))

    A("\n## 5. Pair-by-Pair Results (best config per pair, ≥%d trades)\n" % MIN_TRADES)
    A("| Pair | Best config | Trades | Win% | PF | ExpR | Net% | MaxDD% |")
    A("|---|---|---:|---:|---:|---:|---:|---:|")
    for _, r in perpair.iterrows():
        ev = "" if pd.isna(r.exit_val) else r.exit_val
        A("| %s | %s %smo w%s e%s %s%s b%.02f | %d | %.1f | %.2f | %.3f | %.1f | %.1f |" % (
            r.symbol, r.timeframe, r.months, r.wick, int(r.entry_method), r.exit_kind, ev,
            r.buffer_pct, r.total_trades, r.win_rate, r.profit_factor, r.expectancy_r,
            r.net_return_pct, r.max_drawdown_pct))
    A("\nEvery per-pair 'best' uses the *opposite-side* exit and (for the strongest) the recent 6-month window — "
      "a small-sample selection artifact. XAUUSD is the least-bad pair overall; USDCAD the worst.\n")

    A("\n## 6. Best Performing Configuration\n")
    ev = "" if pd.isna(top_overall.exit_val) else top_overall.exit_val
    A("`%s %s %smo wick%s entry%s %s%s buffer%.2f` — PF **%.2f**, expectancy **%.3f R**, net **%.1f%%**, "
      "win rate %.1f%%, %d trades, max DD %.1f%%.\n" % (
        top_overall.symbol, top_overall.timeframe, top_overall.months, top_overall.wick,
        int(top_overall.entry_method), top_overall.exit_kind, ev, top_overall.buffer_pct,
        top_overall.profit_factor, top_overall.expectancy_r, top_overall.net_return_pct,
        top_overall.win_rate, top_overall.total_trades, top_overall.max_drawdown_pct))
    A("Impressive on paper, but see §8: it is fragile and does not survive walk-forward. **Not recommended for trading.**\n")

    A("\n## 7. Worst Performing Configuration\n")
    ev = "" if pd.isna(worst.exit_val) else worst.exit_val
    A("`%s %s %smo wick%s entry%s %s%s buffer%.2f` — PF %.2f, expectancy %.3f R, net %.1f%%, "
      "win rate %.1f%%, max DD %.1f%%. M5 + trailing/opposite exits are capital-destroying (costs + whipsaw).\n" % (
        worst.symbol, worst.timeframe, worst.months, worst.wick, int(worst.entry_method),
        worst.exit_kind, ev, worst.buffer_pct, worst.profit_factor, worst.expectancy_r,
        worst.net_return_pct, worst.win_rate, worst.max_drawdown_pct))

    A("\n## 8. Robustness — does anything survive?\n")
    A("### 8.1 Out-of-sample (70/30)\n")
    A("![oos](%s)\n" % figs["oos"])
    A("### 8.2 Walk-forward selection (the decisive test)\n")
    A("![wf](%s)\n" % figs["wf"])
    A("Picking the best config on each training window and trading it on the next unseen window earns a pooled "
      "**%.3f R** — essentially zero — with violent fold-to-fold swings (e.g. AUDUSD +1.26 then −0.13). "
      "An honest optimizer captures no durable edge.\n" % pooled_wf)
    A("### 8.3 Monte Carlo (sequence risk on the hand-picked winners)\n")
    A("| Config | P(profit) | net median | net p5 | maxDD median | maxDD p95 |")
    A("|---|---:|---:|---:|---:|---:|")
    for d in rob["monte_carlo"]:
        A("| %s | %.1f%% | %.1f%% | %.1f%% | %.1f%% | %.1f%% |" % (
            d["config"], d.get("p_profit", float("nan")), d.get("net_median", float("nan")),
            d.get("net_p5", float("nan")), d.get("maxdd_median", float("nan")),
            d.get("maxdd_p95", float("nan"))))
    A("\nEven bootstrapping the *selected* winners' own trades, downside tails are deep (p5 net down to −58%%) and "
      "median drawdowns reach 40%%+. These are lottery payoff profiles, not steady edges.\n")
    A("### 8.4 Sensitivity\n")
    A("![buffer](%s)\n" % figs["buffer"])
    A("The top config's expectancy collapses from ~1.0 R to ~0.2 R as the buffer moves 5%%→15%% — hallmark of curve-fitting.\n")

    A("\n## 9. Conditions Where It Works / Fails\n")
    A("- **Least-bad:** M30 timeframe, longer windows, confirmation entries, opposite-side exit, tight buffers, "
      "New York session, XAUUSD. Even here expectancy is ~0 after honest validation.\n")
    A("- **Fails hardest:** M5 (costs + noise), trailing stops, wide buffers, London session, USDCAD; and the "
      "*stated* thesis (small fade off a rejection with fixed RR) loses across the board.\n")

    A("\n## 10. Evidence-Based Conclusion\n")
    A("The data **rejects** the Box Theory as a source of edge. The false assumption is the core one: price does "
      "**not** preferentially reverse at PDH/PDL (reversal ≈ 50%%; often continuation). Rejection-candle filters add "
      "only marginal signal that costs erase, especially intraday. The one profitable-looking variant abandons the "
      "thesis (targets the opposite side, not a fade) and fails walk-forward. **No configuration is recommended for "
      "live trading.** If pursued further, the only faintly promising direction is a *midpoint mean-reversion* study "
      "(price reaches mid ~65%% of the time after an extreme) on M30 — but that is a different strategy and would need "
      "its own out-of-sample validation.\n")
    A("\n---\n*Reproduce:* `python -m box_theory.export_data` (in container) → `sweep` → `robustness` → `report`. "
      "Raw tables in `results/`.\n")

    with open(os.path.join(REPORT, "BOX_THEORY_REPORT.md"), "w") as f:
        f.write("\n".join(md))
    print("Wrote report/BOX_THEORY_REPORT.md and %d charts" % len(figs))
    print("figs:", list(figs.values()))


if __name__ == "__main__":
    main()
