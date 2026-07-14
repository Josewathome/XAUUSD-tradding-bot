"""run_phase1.py — Phase-1 validation on EURUSD M15, 6 months, one config.

    cd research && box_theory/.venv/bin/python -m box_theory.run_phase1
"""
from __future__ import annotations

import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from . import config
from .load import load_ohlc, load_d1, load_meta
from .boxes import build_boxes
from .engine import run_backtest
from .metrics import compute_metrics

REPORT_DIR = os.path.join(os.path.dirname(__file__), "report")
RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")

SYMBOL = "EURUSD"
TF = "M15"
MONTHS = 6
PRIMARY = {
    "wick": "B", "entry_method": 1, "exit": ("rr", 2.0),
    "buffer_pct": 0.10, "mid_band_pct": 0.10, "risk_pct": 0.01,
    "one_trade_per_box": True, "hold_to_eod": True,
}


def equity_curve_png(trades, initial, path, title):
    eq = np.concatenate([[initial], trades["equity_after"].to_numpy()])
    x = list(range(len(eq)))
    plt.figure(figsize=(10, 5))
    plt.plot(x, eq, lw=1.3)
    plt.axhline(initial, color="grey", ls="--", lw=0.8)
    plt.title(title)
    plt.xlabel("trade #")
    plt.ylabel("equity (USD)")
    plt.tight_layout()
    plt.savefig(path, dpi=110)
    plt.close()


def main():
    os.makedirs(REPORT_DIR, exist_ok=True)
    os.makedirs(RESULTS_DIR, exist_ok=True)
    meta = load_meta()
    print("=" * 72)
    print("BOX THEORY — PHASE 1 VALIDATION")
    print("account=%s server=%s server_utc_offset=%sh" % (
        meta["account"], meta["server"], meta["detected_server_utc_offset_hours"]))
    print("symbol=%s tf=%s months=%s  config=%s" % (SYMBOL, TF, MONTHS, PRIMARY))
    print("=" * 72)

    df = load_ohlc(SYMBOL, TF, months=MONTHS)
    d1 = load_d1(SYMBOL)
    boxes = build_boxes(d1)
    days_in_df = df["trading_day"].nunique()
    days_with_box = sum(1 for d in df["trading_day"].unique() if d in boxes)
    print("bars=%d  first=%s  last=%s" % (len(df), df["time_server"].iloc[0], df["time_server"].iloc[-1]))
    print("trading days in window=%d  with box=%d" % (days_in_df, days_with_box))

    res = run_backtest(df, boxes, SYMBOL, PRIMARY, initial_equity=config.DEFAULT_EQUITY)
    tr = res["trades"]
    m = compute_metrics(tr, config.DEFAULT_EQUITY)

    print("\n--- PRIMARY CONFIG RESULTS ---")
    for k, v in m.items():
        if isinstance(v, float):
            print("  %-20s %s" % (k, "nan" if (v != v) else round(v, 4)))
        else:
            print("  %-20s %s" % (k, v))

    if len(tr):
        tr.to_csv(os.path.join(RESULTS_DIR, "phase1_eurusd_m15_trades.csv"), index=False)
        equity_curve_png(tr, config.DEFAULT_EQUITY,
                         os.path.join(REPORT_DIR, "phase1_eurusd_m15_equity.png"),
                         "EURUSD M15 6mo — Box Theory (wick B, entry1, RR 1:2, 1%%)")
        print("\n--- SAMPLE TRADES (for manual audit vs raw candles) ---")
        cols = ["trading_day", "side", "entry_time", "pdl", "pdh", "mid",
                "entry", "sl", "tp", "exit", "exit_time", "r_net", "exit_reason"]
        show = tr[cols].head(8).copy()
        for col in ["pdl", "pdh", "mid", "entry", "sl", "tp", "exit"]:
            show[col] = show[col].round(5)
        show["r_net"] = show["r_net"].round(3)
        with pd.option_context("display.width", 200, "display.max_columns", 20):
            print(show.to_string(index=False))

    # ── random-entry control: same machinery, random signals ──
    print("\n--- RANDOM-ENTRY CONTROL (fair-harness sanity check) ---")
    ctrl_exp = []
    ctrl_net = []
    for seed in range(8):
        cp = dict(PRIMARY)
        cp.update({"random_entry": True, "random_prob": 0.03, "random_seed": seed})
        r2 = run_backtest(df, boxes, SYMBOL, cp, initial_equity=config.DEFAULT_EQUITY)
        cm = compute_metrics(r2["trades"], config.DEFAULT_EQUITY)
        ctrl_exp.append(cm["expectancy_r"])
        ctrl_net.append(cm["net_return_pct"])
    print("  random expectancy_R  mean=%.4f  (per seed: %s)" % (
        np.nanmean(ctrl_exp), [round(x, 3) for x in ctrl_exp]))
    print("  random net_return%%  mean=%.2f" % np.nanmean(ctrl_net))
    print("  strategy expectancy_R=%.4f  net_return%%=%.2f" % (m["expectancy_r"], m["net_return_pct"]))
    print("\nSaved: report/phase1_eurusd_m15_equity.png, results/phase1_eurusd_m15_trades.csv")


if __name__ == "__main__":
    main()
