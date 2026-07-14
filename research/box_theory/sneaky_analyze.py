"""sneaky_analyze.py — distribution-first summary of the Sneaky Pivot sweep."""
from __future__ import annotations

import os
import numpy as np
import pandas as pd

RES = os.path.join(os.path.dirname(__file__), "results")
MIN_TRADES = 30


def line(c="="):
    print(c * 80)


def main():
    m = pd.read_csv(os.path.join(RES, "sneaky_master.csv"))
    m1 = m[m.risk_pct == 0.01].copy()
    valid = m1[m1.total_trades >= MIN_TRADES].copy()

    line()
    print("EDGE DISTRIBUTION (Sneaky Pivot) — valid configs (>=%d trades): %d of %d" % (
        MIN_TRADES, len(valid), len(m1)))
    line()
    for col in ["expectancy_r", "net_return_pct", "profit_factor"]:
        s = valid[col].replace([np.inf, -np.inf], np.nan).dropna()
        thr = 1.0 if col == "profit_factor" else 0.0
        print("  %-16s median=%.4f mean=%.4f  frac>%.0f: %.1f%%" % (
            col, s.median(), s.mean(), thr, 100 * (s > thr).mean()))

    line()
    print("AVERAGE expectancy_r BY DIMENSION")
    line()
    for dim in ["timing", "levels_used", "swing_def", "target_kind",
                "timeframe", "months", "wick", "buffer_pct"]:
        g = valid.groupby(dim)["expectancy_r"].agg(["mean", "median", "count"]).round(4)
        print("\n[%s]\n%s" % (dim, g.to_string()))

    line()
    print("TIMING x LEVELS (mean expectancy_r) — the core Sneaky Pivot question")
    line()
    piv = valid.pivot_table(index="timing", columns="levels_used",
                            values="expectancy_r", aggfunc="mean").round(3)
    print(piv.to_string())

    line()
    print("PER-PAIR best config (>=%d trades)" % MIN_TRADES)
    line()
    cols = ["timeframe", "months", "swing_def", "timing", "levels_used", "wick",
            "buffer_pct", "target_kind", "total_trades", "win_rate", "profit_factor",
            "expectancy_r", "net_return_pct", "max_drawdown_pct"]
    for sym in valid.symbol.unique():
        sub = valid[valid.symbol == sym]
        b = sub.sort_values("expectancy_r", ascending=False).iloc[0]
        print("\n%s (median expR=%.3f, frac PF>1=%.0f%%)" % (
            sym, sub.expectancy_r.median(), 100 * (sub.profit_factor > 1).mean()))
        print("  " + " ".join("%s=%s" % (c, b[c]) for c in cols))

    line()
    print("TOP 15 OVERALL by expectancy_r (>=%d trades)" % MIN_TRADES)
    line()
    top = valid.sort_values("expectancy_r", ascending=False).head(15)
    show = ["symbol"] + cols
    print(top[show].to_string(index=False))

    line()
    print("SESSION (trade-weighted mean expectancy_r across valid configs)")
    line()
    def wavg(e, nn):
        d = valid[[e, nn]].dropna()
        w = d[nn].to_numpy()
        return float(np.average(d[e], weights=w)) if w.sum() else float("nan")
    for s in ["ASIAN", "LONDON", "NEWYORK"]:
        print("  %-8s expR=%.4f (n=%d)" % (s, wavg("expR_%s" % s, "n_%s" % s), int(valid["n_%s" % s].sum())))


if __name__ == "__main__":
    main()
