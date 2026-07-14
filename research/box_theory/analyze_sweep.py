"""analyze_sweep.py — summarize the Phase-2 sweep honestly (distribution first).

    cd research && box_theory/.venv/bin/python -m box_theory.analyze_sweep
"""
from __future__ import annotations

import os
import numpy as np
import pandas as pd

RES = os.path.join(os.path.dirname(__file__), "results")
MIN_TRADES = 30


def line(c="-"):
    print(c * 78)


def main():
    m = pd.read_csv(os.path.join(RES, "master_results.csv"))
    z = pd.read_csv(os.path.join(RES, "zone_stats.csv"))
    # de-dupe risk: R-based stats identical across risk; use 1% rows for expectancy/PF
    m1 = m[m.risk_pct == 0.01].copy()
    valid = m1[m1.total_trades >= MIN_TRADES].copy()

    line("=")
    print("ZONE STATISTICS — does price actually reverse at PDH/PDL? (all pairs, 24mo where avail)")
    line("=")
    zz = z.sort_values(["symbol", "timeframe", "months"])
    print(zz.to_string(index=False))
    print("\nInterpretation: pdh_reverse_pct / pdl_reverse_pct near 50 = coin-flip (no edge).")
    print("touch_* = %% of days the extreme is reached; reach_mid_after_extreme = mean-reversion tendency.")

    line("=")
    print("EDGE DISTRIBUTION — are configs profitable in general, or only by cherry-pick?")
    line("=")
    print("valid configs (>=%d trades): %d of %d" % (MIN_TRADES, len(valid), len(m1)))
    for col, unit in [("expectancy_r", "R"), ("net_return_pct", "%"), ("profit_factor", "")]:
        s = valid[col].replace([np.inf, -np.inf], np.nan).dropna()
        print("  %-16s median=%.4f mean=%.4f  frac>0: %.1f%%" % (
            col, s.median(), s.mean(),
            100.0 * (s > (1.0 if col == "profit_factor" else 0.0)).mean()))
    pf_pos = (valid["profit_factor"] > 1.0).mean() * 100

    line("=")
    print("AVERAGE EDGE BY DIMENSION (expectancy_r mean over valid configs)")
    line("=")
    for dim in ["timeframe", "months", "wick", "entry_method", "exit_kind", "buffer_pct"]:
        g = valid.groupby(dim)["expectancy_r"].agg(["mean", "median", "count"]).round(4)
        print("\n[%s]" % dim)
        print(g.to_string())

    line("=")
    print("PER-PAIR: best config by expectancy_r (>=%d trades)  + pair-wide median" % MIN_TRADES)
    line("=")
    for sym in valid["symbol"].unique():
        sub = valid[valid.symbol == sym]
        best = sub.sort_values("expectancy_r", ascending=False).iloc[0]
        print("\n%s  (median expR across valid=%.4f, frac PF>1=%.0f%%)" % (
            sym, sub["expectancy_r"].median(), 100 * (sub.profit_factor > 1).mean()))
        print("  BEST: %s %smo wick%s entry%s %s%s buf%.2f | trades=%d WR=%.1f%% PF=%.2f expR=%.3f net=%.1f%% DD=%.1f%%" % (
            best.timeframe, best.months, best.wick, best.entry_method, best.exit_kind,
            "" if pd.isna(best.exit_val) else best.exit_val, best.buffer_pct,
            best.total_trades, best.win_rate, best.profit_factor, best.expectancy_r,
            best.net_return_pct, best.max_drawdown_pct))

    line("=")
    print("TOP 12 CONFIGS OVERALL by expectancy_r (>=%d trades)" % MIN_TRADES)
    line("=")
    cols = ["symbol", "timeframe", "months", "wick", "entry_method", "exit_kind", "exit_val",
            "buffer_pct", "total_trades", "win_rate", "profit_factor", "expectancy_r",
            "net_return_pct", "max_drawdown_pct", "sharpe_annual"]
    top = valid.sort_values("expectancy_r", ascending=False).head(12)
    print(top[cols].to_string(index=False))

    line("=")
    print("WORST 6 CONFIGS by expectancy_r (>=%d trades)" % MIN_TRADES)
    line("=")
    print(valid.sort_values("expectancy_r").head(6)[cols].to_string(index=False))

    line("=")
    print("SESSION & REGIME (avg expectancy_r across valid configs, trade-weighted)")
    line("=")
    def wavg(expcol, ncol):
        d = valid[[expcol, ncol]].dropna()
        w = d[ncol].to_numpy()
        return float(np.average(d[expcol], weights=w)) if w.sum() else float("nan")
    for label, e, n in [
        ("trend", "expR_trend", "n_trend"), ("range", "expR_range", "n_range"),
        ("high_vol", "expR_highvol", "n_highvol"), ("low_vol", "expR_lowvol", "n_lowvol"),
        ("ASIAN", "expR_ASIAN", "n_ASIAN"), ("LONDON", "expR_LONDON", "n_LONDON"),
        ("NEWYORK", "expR_NEWYORK", "n_NEWYORK")]:
        print("  %-9s expR=%.4f  (total trades=%d)" % (label, wavg(e, n), int(valid[n].sum())))


if __name__ == "__main__":
    main()
