"""sweep.py — Phase 2 full parameter matrix.

Runs pairs x timeframes x periods x wick x entry x exit x buffer, and derives
both risk levels analytically (R-multiples are risk-independent). Each run is
bucketed by market regime (trend/range, high/low vol) and session, all in R.
Also emits per (pair,tf,period) descriptive zone statistics.

    cd research && box_theory/.venv/bin/python -m box_theory.sweep
    # optional: limit for a quick smoke test
    cd research && box_theory/.venv/bin/python -m box_theory.sweep --smoke

Outputs (results/):
  master_results.csv      one row per (config, risk)
  zone_stats.csv          one row per (pair, tf, period)
  trades/<best configs>   full trade logs for the best config per pair/tf
"""
from __future__ import annotations

import os
import sys
import time
import itertools
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

from . import config
from .load import load_ohlc, load_d1
from .boxes import build_boxes
from .engine import run_backtest
from .metrics import compute_metrics
from .conditions import daily_regime, zone_stats

RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")
TRADES_DIR = os.path.join(RESULTS_DIR, "trades")

# M5 only has ~6 months of data.
TF_PERIODS = {"M5": [6], "M15": [6, 12, 24], "M30": [6, 12, 24]}


def _tasks(smoke=False):
    syms = ["EURUSD"] if smoke else config.SYMBOLS
    tfs = ["M15"] if smoke else config.TIMEFRAMES
    out = []
    for sym in syms:
        for tf in tfs:
            for months in TF_PERIODS[tf]:
                out.append((sym, tf, months))
    return out


def _metrics_at_risk(trades: pd.DataFrame, risk_pct: float, initial: float) -> dict:
    """Recompound equity from r_net at a given risk level, then compute metrics."""
    if len(trades) == 0:
        return compute_metrics(trades, initial)
    r = trades["r_net"].to_numpy()
    eq = initial
    ret_frac = np.empty(len(r))
    pnl = np.empty(len(r))
    eq_before = np.empty(len(r))
    eq_after = np.empty(len(r))
    for i, rr in enumerate(r):
        rf = rr * risk_pct
        eq_before[i] = eq
        p = rf * eq
        eq = eq + p
        ret_frac[i] = rf
        pnl[i] = p
        eq_after[i] = eq
    t = trades.copy()
    t["ret_frac"] = ret_frac
    t["pnl_usd"] = pnl
    t["equity_before"] = eq_before
    t["equity_after"] = eq_after
    return compute_metrics(t, initial)


def _bucket_expectancy(trades: pd.DataFrame) -> dict:
    """Per-regime and per-session expectancy (R) + counts. Risk-independent."""
    out = {}
    def bucket(mask, name):
        sub = trades[mask]
        out["expR_%s" % name] = round(float(sub["r_net"].mean()), 4) if len(sub) else None
        out["n_%s" % name] = int(len(sub))
    if len(trades) == 0:
        for name in ["trend", "range", "highvol", "lowvol", "ASIAN", "LONDON", "NEWYORK", "OFF"]:
            out["expR_%s" % name] = None
            out["n_%s" % name] = 0
        return out
    bucket(trades["is_trend"] == True, "trend")
    bucket(trades["is_trend"] == False, "range")
    bucket(trades["is_high_vol"] == True, "highvol")
    bucket(trades["is_high_vol"] == False, "lowvol")
    for s in ["ASIAN", "LONDON", "NEWYORK", "OFF"]:
        bucket(trades["entry_session"] == s, s)
    return out


def run_task(task):
    sym, tf, months = task
    df = load_ohlc(sym, tf, months=months)
    d1 = load_d1(sym)
    boxes = build_boxes(d1)
    regimes = daily_regime(d1)
    is_trend_map = {d: v["is_trend"] for d, v in regimes.items()}
    is_hv_map = {d: v["is_high_vol"] for d, v in regimes.items()}

    zs = zone_stats(df, boxes)
    zs_row = {"symbol": sym, "timeframe": tf, "months": months, **zs}

    rows = []
    best = {"key": None, "expR": -1e9, "trades": None, "n": 0}
    for wick, entry, buf, exit_model in itertools.product(
            config.WICK_VARIATIONS, config.ENTRY_METHODS,
            config.BUFFER_PCTS, config.EXIT_MODELS):
        params = {
            "wick": wick, "entry_method": entry, "exit": exit_model,
            "buffer_pct": buf, "mid_band_pct": config.MID_BAND_PCT,
            "risk_pct": 0.01, "one_trade_per_box": True, "hold_to_eod": True,
        }
        res = run_backtest(df, boxes, sym, params, initial_equity=config.DEFAULT_EQUITY)
        tr = res["trades"]
        if len(tr):
            tr = tr.copy()
            tr["is_trend"] = tr["trading_day"].map(is_trend_map)
            tr["is_high_vol"] = tr["trading_day"].map(is_hv_map)
        buckets = _bucket_expectancy(tr)
        for risk in config.RISK_PCTS:
            m = _metrics_at_risk(tr, risk, config.DEFAULT_EQUITY)
            row = {
                "symbol": sym, "timeframe": tf, "months": months,
                "wick": wick, "entry_method": entry,
                "exit_kind": exit_model[0], "exit_val": exit_model[1],
                "buffer_pct": buf, "risk_pct": risk,
                **m, **buckets,
            }
            rows.append(row)
        # track best signal config by expectancy_R with a min-trades filter
        expR = float(tr["r_net"].mean()) if len(tr) else -1e9
        if len(tr) >= 30 and expR > best["expR"]:
            best = {"key": "%s_%s_%smo_w%s_e%s_%s%s_b%s" % (
                sym, tf, months, wick, entry, exit_model[0],
                exit_model[1] if exit_model[1] is not None else "", buf),
                "expR": expR, "trades": tr, "n": len(tr)}
    return rows, zs_row, best


def main():
    smoke = "--smoke" in sys.argv
    os.makedirs(RESULTS_DIR, exist_ok=True)
    os.makedirs(TRADES_DIR, exist_ok=True)
    tasks = _tasks(smoke)
    print("sweep: %d tasks (%s)  cores=%d" % (
        len(tasks), "SMOKE" if smoke else "FULL", os.cpu_count()))
    t0 = time.time()
    all_rows = []
    zone_rows = []
    bests = []
    workers = min(12, len(tasks))
    with ProcessPoolExecutor(max_workers=workers) as ex:
        for i, (rows, zs_row, best) in enumerate(ex.map(run_task, tasks), 1):
            all_rows.extend(rows)
            zone_rows.append(zs_row)
            if best["trades"] is not None:
                bests.append(best)
            print("  [%d/%d] %s  (+%d rows)  %.1fs" % (
                i, len(tasks), "%s %s %smo" % tasks[i - 1], len(rows), time.time() - t0))

    master = pd.DataFrame(all_rows)
    master.to_csv(os.path.join(RESULTS_DIR, "master_results.csv"), index=False)
    pd.DataFrame(zone_rows).to_csv(os.path.join(RESULTS_DIR, "zone_stats.csv"), index=False)
    for b in bests:
        b["trades"].to_csv(os.path.join(TRADES_DIR, "%s.csv" % b["key"]), index=False)

    print("\nDONE %.1fs  configs=%d rows=%d" % (time.time() - t0, len(master) // 2, len(master)))
    print("Saved results/master_results.csv, results/zone_stats.csv, results/trades/*.csv")


if __name__ == "__main__":
    main()
