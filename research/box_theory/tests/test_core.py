"""test_core.py — unit tests for box/wick/metrics/engine.

Run (from the research/ directory, with the module venv):
    cd research && box_theory/.venv/bin/python -m box_theory.tests.test_core
"""
from __future__ import annotations

import datetime as dt
import math

import numpy as np
import pandas as pd

from .. import config
from ..boxes import build_boxes
from ..wick import is_john_wick
from ..metrics import compute_metrics
from ..engine import run_backtest

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print("  PASS", name)
    else:
        FAIL += 1
        print("  FAIL", name, "--", detail)


# ── boxes ─────────────────────────────────────────────────────────────
def test_boxes():
    print("boxes:")
    d1 = pd.DataFrame({
        "trading_day": [dt.date(2025, 1, 1), dt.date(2025, 1, 2), dt.date(2025, 1, 3)],
        "high": [110.0, 112.0, 111.0],
        "low": [100.0, 101.0, 102.0],
    })
    boxes = build_boxes(d1)
    b2 = boxes[dt.date(2025, 1, 2)]
    check("box uses prev-day range", b2.pdh == 110.0 and b2.pdl == 100.0, str(b2))
    check("box midpoint", b2.mid == 105.0)
    check("box height", b2.height == 10.0)
    b3 = boxes[dt.date(2025, 1, 3)]
    check("box day3 uses day2 range", b3.pdh == 112.0 and b3.pdl == 101.0, str(b3))
    check("no box for first day", dt.date(2025, 1, 1) not in boxes)


# ── wick ──────────────────────────────────────────────────────────────
def test_wick():
    print("wick:")
    # bullish pin bar: long lower wick. o=101.5 c=101.8 h=101.9 l=100.5
    o, h, l, c = 101.5, 101.9, 100.5, 101.8
    # body=0.3 lower_wick=1.0 ratio=3.33
    check("A buy (>=2x)", is_john_wick(o, h, l, c, "buy", "A"))
    check("B buy (>=3x)", is_john_wick(o, h, l, c, "buy", "B"))
    # C: lower_wick 1.0 / range 1.4 = 0.71 >= 0.6
    check("C buy (>=60% range)", is_john_wick(o, h, l, c, "buy", "C"))
    # D: bullish close inside box
    check("D buy inside box", is_john_wick(o, h, l, c, "buy", "D", pdl=100.0, pdh=110.0))
    # not a sell rejection (upper wick tiny)
    check("A sell false on bull pin", not is_john_wick(o, h, l, c, "sell", "A"))
    # bearish pin: long upper wick. o=108.5 c=108.2 h=109.5 l=108.1
    o2, h2, l2, c2 = 108.5, 109.5, 108.1, 108.2
    check("A sell (upper wick)", is_john_wick(o2, h2, l2, c2, "sell", "A"))
    check("D sell inside box", is_john_wick(o2, h2, l2, c2, "sell", "D", pdl=100.0, pdh=110.0))
    # zero-range candle rejected
    check("zero range rejected", not is_john_wick(100, 100, 100, 100, "buy", "A"))


# ── metrics ───────────────────────────────────────────────────────────
def test_metrics():
    print("metrics:")
    # two trades: +2R and -1R at 1% risk on 400 equity
    eq0 = 400.0
    t1_pnl = 2.0 * 0.01 * 400.0            # +8 -> 408
    eq1 = eq0 + t1_pnl
    t2_pnl = -1.0 * 0.01 * eq1             # -4.08 -> 403.92
    eq2 = eq1 + t2_pnl
    trades = pd.DataFrame([
        {"entry_time": dt.datetime(2025, 1, 1, 10), "exit_time": dt.datetime(2025, 1, 1, 12),
         "direction": "buy", "r_net": 2.0, "ret_frac": 0.02, "pnl_usd": t1_pnl,
         "equity_before": eq0, "equity_after": eq1, "duration_min": 120.0, "exit_reason": "tp"},
        {"entry_time": dt.datetime(2025, 1, 2, 10), "exit_time": dt.datetime(2025, 1, 2, 11),
         "direction": "sell", "r_net": -1.0, "ret_frac": -0.01, "pnl_usd": t2_pnl,
         "equity_before": eq1, "equity_after": eq2, "duration_min": 60.0, "exit_reason": "sl"},
    ])
    m = compute_metrics(trades, eq0)
    check("total trades", m["total_trades"] == 2)
    check("win rate 50%", abs(m["win_rate"] - 50.0) < 1e-9)
    check("profit factor", abs(m["profit_factor"] - (t1_pnl / abs(t2_pnl))) < 1e-9, str(m["profit_factor"]))
    check("avg R = 0.5", abs(m["avg_r"] - 0.5) < 1e-9)
    check("final equity", abs(m["final_equity"] - eq2) < 1e-9, str(m["final_equity"]))
    check("net return %", abs(m["net_return_pct"] - 100.0 * (eq2 / eq0 - 1)) < 1e-9)
    # max drawdown: peak 408 -> 403.92 => 1.0% of 408
    check("max drawdown", abs(m["max_drawdown_pct"] - 100.0 * (eq1 - eq2) / eq1) < 1e-9, str(m["max_drawdown_pct"]))


# ── engine round trips ────────────────────────────────────────────────
def _mk_df(bars):
    """bars: list of (o,h,l,c). Same trading day, 15-min spacing."""
    t0 = dt.datetime(2025, 1, 2, 10, 0)
    rows = []
    for k, (o, h, l, c) in enumerate(bars):
        tt = t0 + dt.timedelta(minutes=15 * k)
        rows.append({"open": o, "high": h, "low": l, "close": c,
                     "trading_day": dt.date(2025, 1, 2), "session": "LONDON",
                     "time_utc": tt})
    return pd.DataFrame(rows)


def test_engine_win():
    print("engine buy -> 2R win:")
    # box pdl=100 pdh=110 mid=105 H=10; buffer 10% -> lower_top=101
    box_d1 = pd.DataFrame({
        "trading_day": [dt.date(2025, 1, 1), dt.date(2025, 1, 2)],
        "high": [110.0, 110.0], "low": [100.0, 100.0]})
    boxes = build_boxes(box_d1)
    # bar0: bullish pin at PDL (rej). bar1: rallies to TP. bar2: filler.
    df = _mk_df([
        (101.5, 101.9, 100.5, 101.8),   # rejection: low 100.5<=101, close 101.8 (|.-105|=3.2>1)
        (101.8, 105.2, 101.7, 105.0),   # rises; TP=104.8 hit, low 101.7 > SL 100.3
        (105.0, 105.1, 104.9, 105.0),
    ])
    params = {"wick": "B", "entry_method": 1, "exit": ("rr", 2.0),
              "buffer_pct": 0.10, "mid_band_pct": 0.10, "risk_pct": 0.01,
              "one_trade_per_box": True, "hold_to_eod": True}
    res = run_backtest(df, boxes, "EURUSD", params, initial_equity=400.0)
    tr = res["trades"]
    check("one trade taken", len(tr) == 1, str(len(tr)))
    if len(tr):
        row = tr.iloc[0]
        check("side buy", row["side"] == "buy")
        check("entry at close 101.8", abs(row["entry"] - 101.8) < 1e-9)
        check("SL 100.3", abs(row["sl"] - 100.3) < 1e-9, str(row["sl"]))
        check("exit at TP 104.8", abs(row["exit"] - 104.8) < 1e-9, str(row["exit"]))
        check("r_net ~2 (cost negligible)", 1.98 <= row["r_net"] <= 2.0, str(row["r_net"]))
        check("reason take_profit", row["exit_reason"] == "take_profit")
        check("equity ~408", abs(res["final_equity"] - (400 + row["r_net"] * 0.01 * 400)) < 1e-6)


def test_engine_stop():
    print("engine buy -> stop out:")
    box_d1 = pd.DataFrame({
        "trading_day": [dt.date(2025, 1, 1), dt.date(2025, 1, 2)],
        "high": [110.0, 110.0], "low": [100.0, 100.0]})
    boxes = build_boxes(box_d1)
    df = _mk_df([
        (101.5, 101.9, 100.5, 101.8),   # same rejection, entry 101.8, SL 100.3
        (101.8, 101.9, 100.2, 100.25),  # drops through SL 100.3
        (100.25, 100.3, 100.1, 100.2),
    ])
    params = {"wick": "B", "entry_method": 1, "exit": ("rr", 2.0),
              "buffer_pct": 0.10, "mid_band_pct": 0.10, "risk_pct": 0.01,
              "one_trade_per_box": True, "hold_to_eod": True}
    res = run_backtest(df, boxes, "EURUSD", params, initial_equity=400.0)
    tr = res["trades"]
    check("one trade taken", len(tr) == 1, str(len(tr)))
    if len(tr):
        row = tr.iloc[0]
        check("exit at SL 100.3", abs(row["exit"] - 100.3) < 1e-9, str(row["exit"]))
        check("r_net ~ -1 (slightly worse w/ cost)", -1.001 <= row["r_net"] <= -1.0 + 1e-9 or -1.01 <= row["r_net"] <= -1.0, str(row["r_net"]))
        check("reason stop_loss", row["exit_reason"] == "stop_loss")
        check("equity < 400", res["final_equity"] < 400.0)


def main():
    test_boxes()
    test_wick()
    test_metrics()
    test_engine_win()
    test_engine_stop()
    print("\n%d passed, %d failed" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
