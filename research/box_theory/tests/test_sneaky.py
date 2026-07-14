"""test_sneaky.py — unit tests for levels.py and sneaky_engine.py.

    cd research && box_theory/.venv/bin/python -m box_theory.tests.test_sneaky
"""
from __future__ import annotations

import datetime as dt

import pandas as pd

from ..levels import build_levels, Levels
from ..sneaky_engine import run_sneaky

PASS = 0
FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1; print("  PASS", name)
    else:
        FAIL += 1; print("  FAIL", name, "--", detail)


def test_levels_literal():
    print("levels (literal):")
    d1 = pd.DataFrame({
        "trading_day": [dt.date(2025, 1, d) for d in range(1, 5)],
        "high": [120.0, 108.0, 110.0, 111.0],
        "low":  [90.0,  98.0,  100.0, 104.0],
    })
    lv = build_levels(d1, "literal")[dt.date(2025, 1, 4)]
    check("range from prev day", lv.rh == 110.0 and lv.rl == 100.0, str(lv))
    check("swing high = nearest prior higher high", lv.sh == 120.0, str(lv.sh))
    check("swing low = nearest prior lower low", lv.sl == 98.0, str(lv.sl))


def test_levels_fractal():
    print("levels (fractal):")
    d1 = pd.DataFrame({
        "trading_day": [dt.date(2025, 1, d) for d in range(1, 9)],
        "high": [90.0, 95.0, 130.0, 96.0, 94.0, 105.0, 108.0, 111.0],
        "low":  [88.0, 85.0, 120.0, 90.0, 70.0, 100.0, 101.0, 104.0],
    })
    lv = build_levels(d1, "fractal", fractal_n=2)[dt.date(2025, 1, 8)]
    check("range from prev day", lv.rh == 108.0 and lv.rl == 101.0, str(lv))
    check("swing high = confirmed pivot high 130", lv.sh == 130.0, str(lv.sh))
    check("swing low = confirmed pivot low 70", lv.sl == 70.0, str(lv.sl))


def _mk_df(bars, day=dt.date(2025, 1, 2)):
    t0 = dt.datetime(2025, 1, 2, 8, 0)
    rows = []
    for k, (o, h, l, c) in enumerate(bars):
        rows.append({"open": o, "high": h, "low": l, "close": c,
                     "trading_day": day, "session": "LONDON",
                     "time_utc": t0 + dt.timedelta(minutes=15 * k)})
    return pd.DataFrame(rows)


def test_sneaky_roundtrip():
    print("sneaky engine buy -> opposite-side target:")
    day = dt.date(2025, 1, 2)
    levels = {day: Levels(rh=110.0, rl=100.0, mid=105.0, height=10.0, sh=115.0, sl=95.0)}
    # bar0: bullish John Wick pivot at range low. bar1: breaks pivot high (entry).
    # bar2: rallies to opposite side (range high 110) => take profit.
    df = _mk_df([
        (101.0, 101.2, 100.3, 101.1),   # pivot: low 100.3<=101, lower wick 0.7 vs body 0.1
        (101.2, 101.6, 101.0, 101.4),   # breaks 101.2 -> entry at 101.2
        (101.4, 110.3, 101.3, 110.1),   # hits TP=110 (opposite side)
        (110.1, 110.2, 109.9, 110.0),
    ])
    params = {"swing_def": "literal", "levels_used": "both", "entry_model": "allday",
              "wick": "B", "buffer_pct": 0.10, "mid_band_pct": 0.10,
              "target": ("opposite", None), "risk_pct": 0.01}
    res = run_sneaky(df, levels, "EURUSD", params)
    tr = res["trades"]
    check("one trade", len(tr) == 1, str(len(tr)))
    if len(tr):
        r = tr.iloc[0]
        check("side buy", r["side"] == "buy")
        check("entry at pivot-break 101.2", abs(r["entry"] - 101.2) < 1e-9, str(r["entry"]))
        check("SL below pivot wick (100.1)", abs(r["sl"] - 100.1) < 1e-9, str(r["sl"]))
        check("target = opposite side (range high 110)", abs(r["tp"] - 110.0) < 1e-9, str(r["tp"]))
        check("exit take_profit", r["exit_reason"] == "take_profit")
        check("r_net large positive (~8R)", r["r_net"] > 7.0, str(r["r_net"]))


def test_sneaky_no_break_no_trade():
    print("sneaky engine: pivot but no break -> no trade:")
    day = dt.date(2025, 1, 2)
    levels = {day: Levels(rh=110.0, rl=100.0, mid=105.0, height=10.0, sh=115.0, sl=95.0)}
    df = _mk_df([
        (101.0, 101.2, 100.3, 101.1),   # pivot
        (101.1, 101.15, 100.5, 100.7),  # never breaks 101.2
        (100.7, 100.9, 100.4, 100.6),
    ])
    params = {"swing_def": "literal", "levels_used": "range", "entry_model": "allday",
              "wick": "B", "buffer_pct": 0.10, "mid_band_pct": 0.10,
              "target": ("opposite", None), "risk_pct": 0.01, "entry_valid_bars": 2}
    res = run_sneaky(df, levels, "EURUSD", params)
    check("no trade when pivot high never broken", len(res["trades"]) == 0, str(len(res["trades"])))


def main():
    test_levels_literal()
    test_levels_fractal()
    test_sneaky_roundtrip()
    test_sneaky_no_break_no_trade()
    print("\n%d passed, %d failed" % (PASS, FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
