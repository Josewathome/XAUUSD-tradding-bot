"""robustness.py — Phase 3 robustness tests on the strongest configs.

Tests (the point is to see whether in-sample "winners" survive):
  1. Out-of-sample 70/30 split (time-ordered): in-sample vs held-out.
  2. Walk-forward SELECTION: on each rolling train window pick the best config
     from a grid, then trade it on the next unseen window. Aggregates the
     out-of-sample expectancy an honest optimizer would actually have earned.
  3. Monte Carlo bootstrap: resample a config's trade sequence to get the
     distribution of net return and max drawdown, and P(profit).
  4. Buffer sensitivity: pulled from the sweep (does a small param change wreck it?).

    cd research && box_theory/.venv/bin/python -m box_theory.robustness
"""
from __future__ import annotations

import os
import json
import itertools

import numpy as np
import pandas as pd

from . import config
from .load import load_ohlc, load_d1
from .boxes import build_boxes
from .engine import run_backtest
from .metrics import compute_metrics

RES = os.path.join(os.path.dirname(__file__), "results")
REPORT = os.path.join(os.path.dirname(__file__), "report")
MAX_PERIOD = {"M5": 6, "M15": 24, "M30": 24}


def _params_from_row(r):
    return {
        "wick": r["wick"], "entry_method": int(r["entry_method"]),
        "exit": (r["exit_kind"], None if pd.isna(r["exit_val"]) else float(r["exit_val"])),
        "buffer_pct": float(r["buffer_pct"]), "mid_band_pct": config.MID_BAND_PCT,
        "risk_pct": 0.01, "one_trade_per_box": True, "hold_to_eod": True,
    }


def _short(r):
    ev = "" if pd.isna(r["exit_val"]) else r["exit_val"]
    return "%s %s wick%s e%s %s%s buf%.2f" % (
        r["symbol"], r["timeframe"], r["wick"], int(r["entry_method"]),
        r["exit_kind"], ev, r["buffer_pct"])


def _rmetrics(trades):
    if len(trades) == 0:
        return {"n": 0, "expR": float("nan"), "pf": float("nan"), "net": float("nan")}
    m = compute_metrics(trades, config.DEFAULT_EQUITY)
    return {"n": m["total_trades"], "expR": m["expectancy_r"],
            "pf": m["profit_factor"], "net": m["net_return_pct"]}


def oos_split(sym, tf, params, frac=0.7):
    months = MAX_PERIOD[tf]
    df = load_ohlc(sym, tf, months=months)
    boxes = build_boxes(load_d1(sym))
    cut = int(len(df) * frac)
    tr_in = run_backtest(df.iloc[:cut].reset_index(drop=True), boxes, sym, params)["trades"]
    tr_out = run_backtest(df.iloc[cut:].reset_index(drop=True), boxes, sym, params)["trades"]
    return _rmetrics(tr_in), _rmetrics(tr_out), months


def _run_cfg(df_slice, boxes, sym, params):
    return run_backtest(df_slice, boxes, sym, params)["trades"]


# reduced grid for walk-forward selection (keeps it fast, still realistic)
WF_GRID = list(itertools.product(
    ["A", "B", "C", "D"], [1, 2, 3],
    [("rr", 2.0), ("opposite", None), ("midpoint", None), ("atr", 1.5)],
    [0.05, 0.10]))


def walk_forward(sym, tf, folds=5, min_trades=15):
    """Anchored WF: pick best config on cumulative past, test on next fold."""
    months = MAX_PERIOD[tf]
    df = load_ohlc(sym, tf, months=months)
    boxes = build_boxes(load_d1(sym))
    n = len(df)
    edges = [int(n * k / folds) for k in range(folds + 1)]
    test_expR = []
    picks = []
    for i in range(1, folds):
        train = df.iloc[:edges[i]].reset_index(drop=True)
        test = df.iloc[edges[i]:edges[i + 1]].reset_index(drop=True)
        best = None
        for wick, entry, exit_m, buf in WF_GRID:
            p = {"wick": wick, "entry_method": entry, "exit": exit_m, "buffer_pct": buf,
                 "mid_band_pct": config.MID_BAND_PCT, "risk_pct": 0.01,
                 "one_trade_per_box": True, "hold_to_eod": True}
            tr = _run_cfg(train, boxes, sym, p)
            if len(tr) >= min_trades:
                e = float(tr["r_net"].mean())
                if best is None or e > best[0]:
                    best = (e, p)
        if best is None:
            continue
        tr_test = _run_cfg(test, boxes, sym, best[1])
        if len(tr_test) > 0:
            test_expR.append(float(tr_test["r_net"].mean()))
            picks.append((best[1]["wick"], best[1]["entry_method"],
                          best[1]["exit"][0], best[1]["buffer_pct"], len(tr_test)))
    return {"mean_oos_expR": float(np.mean(test_expR)) if test_expR else float("nan"),
            "fold_expR": [round(x, 3) for x in test_expR], "picks": picks}


def monte_carlo(sym, tf, params, sims=5000, seed=7):
    months = MAX_PERIOD[tf]
    df = load_ohlc(sym, tf, months=months)
    boxes = build_boxes(load_d1(sym))
    tr = run_backtest(df, boxes, sym, params)["trades"]
    r = tr["r_net"].to_numpy()
    if len(r) < 10:
        return {"n": len(r)}
    rng = np.random.default_rng(seed)
    risk = 0.01
    nets = np.empty(sims)
    maxdds = np.empty(sims)
    N = len(r)
    for s in range(sims):
        samp = rng.choice(r, size=N, replace=True)
        eq = config.DEFAULT_EQUITY
        peak = eq
        mdd = 0.0
        for rr in samp:
            eq *= (1 + rr * risk)
            peak = max(peak, eq)
            mdd = max(mdd, (peak - eq) / peak)
        nets[s] = 100.0 * (eq / config.DEFAULT_EQUITY - 1)
        maxdds[s] = 100.0 * mdd
    return {
        "n": N,
        "p_profit": round(100.0 * float((nets > 0).mean()), 1),
        "net_median": round(float(np.median(nets)), 1),
        "net_p5": round(float(np.percentile(nets, 5)), 1),
        "net_p95": round(float(np.percentile(nets, 95)), 1),
        "maxdd_median": round(float(np.median(maxdds)), 1),
        "maxdd_p95": round(float(np.percentile(maxdds, 95)), 1),
    }


def main():
    os.makedirs(REPORT, exist_ok=True)
    m = pd.read_csv(os.path.join(RES, "master_results.csv"))
    m1 = m[(m.risk_pct == 0.01) & (m.total_trades >= 30)].copy()

    # strongest configs: best per pair + best overall
    idx = m1.groupby("symbol")["expectancy_r"].idxmax()
    top = m1.loc[idx].sort_values("expectancy_r", ascending=False)
    overall = m1.sort_values("expectancy_r", ascending=False).iloc[0]

    out = {"oos": [], "walk_forward": [], "monte_carlo": [], "sensitivity": []}

    print("=" * 90)
    print("1) OUT-OF-SAMPLE 70/30 (best config per pair, re-run on longest data for its TF)")
    print("=" * 90)
    print("%-38s %18s %18s" % ("config", "IN-SAMPLE", "OUT-OF-SAMPLE"))
    for _, r in top.iterrows():
        p = _params_from_row(r)
        ins, oos, months = oos_split(r["symbol"], r["timeframe"], p)
        print("%-38s  expR=%6.3f PF=%4.2f n=%3d | expR=%6.3f PF=%4.2f n=%3d  [%dmo]" % (
            _short(r), ins["expR"], ins["pf"], ins["n"],
            oos["expR"], oos["pf"], oos["n"], months))
        out["oos"].append({"config": _short(r), "months": months,
                           "in_sample": ins, "out_sample": oos})

    print("\n" + "=" * 90)
    print("2) WALK-FORWARD SELECTION (pick best on past, trade next unseen fold; M15, 24mo)")
    print("=" * 90)
    for sym in config.SYMBOLS:
        wf = walk_forward(sym, "M15")
        print("  %-7s mean OOS expR=%7.3f   folds=%s" % (sym, wf["mean_oos_expR"], wf["fold_expR"]))
        out["walk_forward"].append({"symbol": sym, **wf})
    all_folds = [e for w in out["walk_forward"] for e in w["fold_expR"]]
    print("  -> POOLED walk-forward OOS expectancy = %.4f R (n folds=%d)" % (
        np.mean(all_folds) if all_folds else float("nan"), len(all_folds)))
    out["pooled_wf_expR"] = float(np.mean(all_folds)) if all_folds else float("nan")

    print("\n" + "=" * 90)
    print("3) MONTE CARLO bootstrap (best-per-pair config, 5000 sims, 1%% risk)")
    print("=" * 90)
    for _, r in top.iterrows():
        p = _params_from_row(r)
        mc = monte_carlo(r["symbol"], r["timeframe"], p)
        print("  %-38s P(profit)=%5.1f%%  net med=%6.1f%% [p5=%7.1f, p95=%6.1f]  maxDD med=%.1f%% p95=%.1f%%" % (
            _short(r), mc.get("p_profit", float("nan")), mc.get("net_median", float("nan")),
            mc.get("net_p5", float("nan")), mc.get("net_p95", float("nan")),
            mc.get("maxdd_median", float("nan")), mc.get("maxdd_p95", float("nan"))))
        out["monte_carlo"].append({"config": _short(r), **mc})

    print("\n" + "=" * 90)
    print("4) BUFFER SENSITIVITY (top overall config family; expectancy vs buffer)")
    print("=" * 90)
    fam = m1[(m1.symbol == overall.symbol) & (m1.timeframe == overall.timeframe) &
             (m1.months == overall.months) & (m1.wick == overall.wick) &
             (m1.entry_method == overall.entry_method) &
             (m1.exit_kind == overall.exit_kind)]
    fam = fam.sort_values("buffer_pct")
    print("  family: %s %s %smo wick%s e%s %s" % (
        overall.symbol, overall.timeframe, overall.months, overall.wick,
        int(overall.entry_method), overall.exit_kind))
    for _, r in fam.iterrows():
        print("    buffer %.2f -> expR=%6.3f  PF=%4.2f  net=%7.1f%%  trades=%d" % (
            r.buffer_pct, r.expectancy_r, r.profit_factor, r.net_return_pct, r.total_trades))
        out["sensitivity"].append({"buffer_pct": r.buffer_pct, "expR": r.expectancy_r,
                                    "pf": r.profit_factor, "net": r.net_return_pct,
                                    "trades": int(r.total_trades)})

    with open(os.path.join(RES, "robustness.json"), "w") as f:
        json.dump(out, f, indent=2, default=float)
    print("\nSaved results/robustness.json")


if __name__ == "__main__":
    main()
