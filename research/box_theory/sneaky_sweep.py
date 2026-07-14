"""sneaky_sweep.py — Phase 4 full matrix for the Sneaky Pivot strategy.

Dimensions: pairs x TF x period x swing_def x timing(anchor) x levels_used
x wick x buffer x target, with both risk levels derived analytically. Levels
are built once per (symbol, swing_def) and reused. Per-config session & regime
buckets in R. Reuses sweep.py helpers and conditions/metrics.

    cd research && box_theory/.venv/bin/python -m box_theory.sneaky_sweep [--smoke]
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
from .levels import build_levels
from .sneaky_engine import run_sneaky
from .conditions import daily_regime
from .sweep import _metrics_at_risk, _bucket_expectancy, TF_PERIODS

RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")
TRADES_DIR = os.path.join(RESULTS_DIR, "sneaky_trades")

SWING_DEFS = ["literal", "fractal"]
TIMINGS = [("allday", None, "allday"), ("opening", None, "open_broker"),
           ("opening", 7, "open_london"), ("opening", 13, "open_ny")]
LEVELS_USED = ["range", "swing", "both"]
TARGETS = [("opposite", None), ("midpoint", None), ("rr", 2.0)]


def _tasks(smoke=False):
    syms = ["EURUSD"] if smoke else config.SYMBOLS
    tfs = ["M15"] if smoke else config.TIMEFRAMES
    return [(s, tf, m) for s in syms for tf in tfs for m in TF_PERIODS[tf]]


def run_task(task):
    sym, tf, months = task
    df = load_ohlc(sym, tf, months=months)
    d1 = load_d1(sym)
    levels_by_def = {sd: build_levels(d1, sd) for sd in SWING_DEFS}
    regimes = daily_regime(d1)
    is_trend = {d: v["is_trend"] for d, v in regimes.items()}
    is_hv = {d: v["is_high_vol"] for d, v in regimes.items()}

    rows = []
    best = {"key": None, "expR": -1e9, "trades": None}
    for sd, timing, lu, wick, buf, tgt in itertools.product(
            SWING_DEFS, TIMINGS, LEVELS_USED, config.WICK_VARIATIONS,
            config.BUFFER_PCTS, TARGETS):
        model, anchor, tlabel = timing
        params = {
            "swing_def": sd, "levels_used": lu, "entry_model": model,
            "open_anchor_hour": anchor, "open_window_bars": 6,
            "wick": wick, "buffer_pct": buf, "mid_band_pct": config.MID_BAND_PCT,
            "target": tgt, "risk_pct": 0.01,
        }
        tr = run_sneaky(df, levels_by_def[sd], sym, params, config.DEFAULT_EQUITY)["trades"]
        if len(tr):
            tr = tr.copy()
            tr["is_trend"] = tr["trading_day"].map(is_trend)
            tr["is_high_vol"] = tr["trading_day"].map(is_hv)
        buckets = _bucket_expectancy(tr)
        for risk in config.RISK_PCTS:
            m = _metrics_at_risk(tr, risk, config.DEFAULT_EQUITY)
            rows.append({
                "symbol": sym, "timeframe": tf, "months": months,
                "swing_def": sd, "timing": tlabel, "levels_used": lu,
                "wick": wick, "buffer_pct": buf,
                "target_kind": tgt[0], "target_val": tgt[1], "risk_pct": risk,
                **m, **buckets,
            })
        expR = float(tr["r_net"].mean()) if len(tr) else -1e9
        if len(tr) >= 30 and expR > best["expR"]:
            best = {"key": "%s_%s_%smo_%s_%s_%s_w%s_b%s_%s" % (
                sym, tf, months, sd, tlabel, lu, wick, buf, tgt[0]),
                "expR": expR, "trades": tr}
    return rows, best


def main():
    smoke = "--smoke" in sys.argv
    os.makedirs(RESULTS_DIR, exist_ok=True)
    os.makedirs(TRADES_DIR, exist_ok=True)
    tasks = _tasks(smoke)
    n_cfg = len(SWING_DEFS) * len(TIMINGS) * len(LEVELS_USED) * len(config.WICK_VARIATIONS) * len(config.BUFFER_PCTS) * len(TARGETS)
    print("sneaky sweep: %d tasks x %d configs (%s) cores=%d" % (
        len(tasks), n_cfg, "SMOKE" if smoke else "FULL", os.cpu_count()))
    t0 = time.time()
    all_rows, bests = [], []
    with ProcessPoolExecutor(max_workers=min(12, len(tasks))) as ex:
        for i, (rows, best) in enumerate(ex.map(run_task, tasks), 1):
            all_rows.extend(rows)
            if best["trades"] is not None:
                bests.append(best)
            print("  [%d/%d] %s %s %smo (+%d rows) %.1fs" % (
                i, len(tasks), *tasks[i - 1][:2], tasks[i - 1][2], len(rows), time.time() - t0))
    master = pd.DataFrame(all_rows)
    master.to_csv(os.path.join(RESULTS_DIR, "sneaky_master.csv"), index=False)
    for b in bests:
        b["trades"].to_csv(os.path.join(TRADES_DIR, "%s.csv" % b["key"]), index=False)
    print("\nDONE %.1fs  configs=%d rows=%d -> results/sneaky_master.csv" % (
        time.time() - t0, len(master) // 2, len(master)))


if __name__ == "__main__":
    main()
