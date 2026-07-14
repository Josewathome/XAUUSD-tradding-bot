"""sneaky_robustness.py — OOS, walk-forward selection, and Monte Carlo for the
Sneaky Pivot's strongest configs. Mirrors robustness.py.

    cd research && box_theory/.venv/bin/python -m box_theory.sneaky_robustness
"""
from __future__ import annotations

import os
import json
import itertools

import numpy as np
import pandas as pd

from . import config
from .load import load_ohlc, load_d1
from .levels import build_levels
from .sneaky_engine import run_sneaky
from .metrics import compute_metrics

RES = os.path.join(os.path.dirname(__file__), "results")
MIN_TRADES = 30
MAX_PERIOD = {"M5": 6, "M15": 24, "M30": 24}


def _params(r):
    return {"swing_def": r["swing_def"], "levels_used": r["levels_used"],
            "entry_model": "allday" if r["timing"] == "allday" else "opening",
            "open_anchor_hour": {"allday": None, "open_broker": None,
                                 "open_london": 7, "open_ny": 13}[r["timing"]],
            "open_window_bars": 6, "wick": r["wick"], "buffer_pct": float(r["buffer_pct"]),
            "mid_band_pct": config.MID_BAND_PCT,
            "target": (r["target_kind"], None if pd.isna(r["target_val"]) else float(r["target_val"])),
            "risk_pct": 0.01}


def _short(r):
    return "%s %s %smo %s/%s/%s w%s b%.2f %s" % (
        r["symbol"], r["timeframe"], r["months"], r["swing_def"], r["timing"],
        r["levels_used"], r["wick"], r["buffer_pct"], r["target_kind"])


def _rm(tr):
    if len(tr) == 0:
        return {"n": 0, "expR": float("nan"), "pf": float("nan"), "net": float("nan")}
    m = compute_metrics(tr, config.DEFAULT_EQUITY)
    return {"n": m["total_trades"], "expR": m["expectancy_r"], "pf": m["profit_factor"],
            "net": m["net_return_pct"]}


def oos_split(r, frac=0.7):
    sym, tf = r["symbol"], r["timeframe"]
    months = MAX_PERIOD[tf]
    df = load_ohlc(sym, tf, months=months)
    lv = build_levels(load_d1(sym), r["swing_def"])
    p = _params(r)
    cut = int(len(df) * frac)
    ins = run_sneaky(df.iloc[:cut].reset_index(drop=True), lv, sym, p)["trades"]
    oos = run_sneaky(df.iloc[cut:].reset_index(drop=True), lv, sym, p)["trades"]
    return _rm(ins), _rm(oos), months


WF_GRID = list(itertools.product(
    ["literal"], [("allday", None), ("opening", None), ("opening", 13)],
    ["range", "swing", "both"], ["A", "B", "C", "D"], [0.05, 0.10], [("opposite", None)]))


def walk_forward(sym, tf="M15", folds=5, min_trades=12):
    months = MAX_PERIOD[tf]
    df = load_ohlc(sym, tf, months=months)
    lv = build_levels(load_d1(sym), "literal")
    n = len(df)
    edges = [int(n * k / folds) for k in range(folds + 1)]
    test = []
    for i in range(1, folds):
        tr_win = df.iloc[:edges[i]].reset_index(drop=True)
        te_win = df.iloc[edges[i]:edges[i + 1]].reset_index(drop=True)
        best = None
        for sd, (model, anc), lu, wick, buf, tgt in WF_GRID:
            p = {"swing_def": sd, "levels_used": lu, "entry_model": model,
                 "open_anchor_hour": anc, "open_window_bars": 6, "wick": wick,
                 "buffer_pct": buf, "mid_band_pct": config.MID_BAND_PCT,
                 "target": tgt, "risk_pct": 0.01}
            tt = run_sneaky(tr_win, lv, sym, p)["trades"]
            if len(tt) >= min_trades:
                e = float(tt["r_net"].mean())
                if best is None or e > best[0]:
                    best = (e, p)
        if best is None:
            continue
        tte = run_sneaky(te_win, lv, sym, best[1])["trades"]
        if len(tte):
            test.append(float(tte["r_net"].mean()))
    return {"mean_oos_expR": float(np.mean(test)) if test else float("nan"),
            "folds": [round(x, 3) for x in test]}


def monte_carlo(r, sims=5000, seed=11):
    sym, tf = r["symbol"], r["timeframe"]
    df = load_ohlc(sym, tf, months=MAX_PERIOD[tf])
    lv = build_levels(load_d1(sym), r["swing_def"])
    tr = run_sneaky(df, lv, sym, _params(r))["trades"]
    rr = tr["r_net"].to_numpy()
    if len(rr) < 10:
        return {"n": len(rr)}
    rng = np.random.default_rng(seed)
    risk = 0.01
    nets, dds = np.empty(sims), np.empty(sims)
    for s in range(sims):
        samp = rng.choice(rr, size=len(rr), replace=True)
        eq = config.DEFAULT_EQUITY; peak = eq; mdd = 0.0
        for x in samp:
            eq *= (1 + x * risk); peak = max(peak, eq); mdd = max(mdd, (peak - eq) / peak)
        nets[s] = 100 * (eq / config.DEFAULT_EQUITY - 1); dds[s] = 100 * mdd
    return {"n": len(rr), "p_profit": round(100 * float((nets > 0).mean()), 1),
            "net_median": round(float(np.median(nets)), 1),
            "net_p5": round(float(np.percentile(nets, 5)), 1),
            "maxdd_median": round(float(np.median(dds)), 1),
            "maxdd_p95": round(float(np.percentile(dds, 95)), 1)}


def main():
    m = pd.read_csv(os.path.join(RES, "sneaky_master.csv"))
    m1 = m[(m.risk_pct == 0.01) & (m.total_trades >= MIN_TRADES)].copy()
    top = m1.loc[m1.groupby("symbol")["expectancy_r"].idxmax()].sort_values("expectancy_r", ascending=False)
    out = {"oos": [], "walk_forward": [], "monte_carlo": []}

    print("=" * 92)
    print("SNEAKY PIVOT — 1) OUT-OF-SAMPLE 70/30 (best per pair, longest data for TF)")
    print("=" * 92)
    for _, r in top.iterrows():
        ins, oos, months = oos_split(r)
        print("%-52s IS expR=%6.3f PF=%4.2f n=%3d | OOS expR=%6.3f PF=%4.2f n=%3d [%dmo]" % (
            _short(r), ins["expR"], ins["pf"], ins["n"], oos["expR"], oos["pf"], oos["n"], months))
        out["oos"].append({"config": _short(r), "in_sample": ins, "out_sample": oos})

    print("\n" + "=" * 92)
    print("2) WALK-FORWARD SELECTION (Sneaky grid, M15 24mo, pick best on past -> next fold)")
    print("=" * 92)
    for sym in config.SYMBOLS:
        wf = walk_forward(sym)
        print("  %-7s mean OOS expR=%7.3f  folds=%s" % (sym, wf["mean_oos_expR"], wf["folds"]))
        out["walk_forward"].append({"symbol": sym, **wf})
    allf = [e for w in out["walk_forward"] for e in w["folds"]]
    out["pooled_wf_expR"] = float(np.mean(allf)) if allf else float("nan")
    print("  -> POOLED walk-forward OOS expectancy = %.4f R (n=%d folds)" % (out["pooled_wf_expR"], len(allf)))

    print("\n" + "=" * 92)
    print("3) MONTE CARLO (best per pair, 5000 sims)")
    print("=" * 92)
    for _, r in top.iterrows():
        mc = monte_carlo(r)
        print("  %-52s P(profit)=%5.1f%% net med=%6.1f%% p5=%7.1f%% maxDD med=%.1f%% p95=%.1f%%" % (
            _short(r), mc.get("p_profit", float("nan")), mc.get("net_median", float("nan")),
            mc.get("net_p5", float("nan")), mc.get("maxdd_median", float("nan")), mc.get("maxdd_p95", float("nan"))))
        out["monte_carlo"].append({"config": _short(r), **mc})

    with open(os.path.join(RES, "sneaky_robustness.json"), "w") as f:
        json.dump(out, f, indent=2, default=float)
    print("\nSaved results/sneaky_robustness.json")


if __name__ == "__main__":
    main()
