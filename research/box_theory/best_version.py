"""best_version.py — build the best config the research supports, then TEST it
as a unit (in-sample + 70/30 out-of-sample). Grounds the final recommendation in
an actual number rather than hand-assembly.

Choices (all evidence-driven, see forensics):
  * instruments: XAUUSD/AUDUSD/USDJPY/EURUSD (positive expR); drop USDCAD/GBPUSD
  * TF: M30 (least-bad in sweep; M5 killed by cost)
  * levels: swing preferred but keep range too (both); target = opposite side
  * session filter: DROP London (expR -0.22)
  * volatility filter: DROP wide prior-range days (box_height/ATR top quartile) -- the
    only filter that improved edge; also drop large-gap days
  * min stop: drop r_unit/ATR < 0.5 (cost drag on tiny stops)
  * risk: 0.5% (halves the variance/DD vs 1%)

    cd research && box_theory/.venv/bin/python -m box_theory.best_version
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import config
from .load import load_ohlc, load_d1
from .levels import build_levels
from .engine import _atr
from .sneaky_engine import run_sneaky

PAIRS = ["XAUUSD", "AUDUSD", "USDJPY", "EURUSD"]
TF = "M30"
MONTHS = 24
RISK = 0.005
CFG = {"swing_def": "literal", "levels_used": "both", "entry_model": "allday",
       "open_anchor_hour": None, "wick": "B", "buffer_pct": 0.10,
       "mid_band_pct": config.MID_BAND_PCT, "target": ("opposite", None), "risk_pct": 1.0}


def labeled_trades(sym, df, d1):
    lv = build_levels(d1, CFG["swing_def"])
    tr = run_sneaky(df, lv, sym, CFG)["trades"]
    if len(tr) == 0:
        return tr
    o = df["open"].to_numpy(); atr = _atr(df, 14)
    tdays = df["trading_day"].tolist()
    day_first = {}
    for i, d in enumerate(tdays):
        day_first.setdefault(d, i)
    d1days = d1["trading_day"].tolist(); d1close = d1["close"].to_numpy(); d1atr = _atr(d1, 14)
    prevclose, prioratr = {}, {}
    for k, d in enumerate(d1days):
        if k >= 1:
            prevclose[d] = d1close[k - 1]; prioratr[d] = d1atr[k - 1]
    rows = []
    for _, t in tr.iterrows():
        d = t["trading_day"]; ei = int(t["entry_idx"]); H = t["box_height"]
        da = prioratr.get(d, np.nan); pc = prevclose.get(d, np.nan)
        rows.append({
            "box_height_atr": H / da if da and da > 0 else np.nan,
            "gap_H": (o[day_first[d]] - pc) / H if (H > 0 and not np.isnan(pc)) else np.nan,
            "r_unit_atr": t["r_unit"] / atr[ei] if atr[ei] > 0 else np.nan,
            "session": t["entry_session"], "r_net": t["r_net"], "entry_time": t["entry_time"],
        })
    return pd.DataFrame(rows)


def apply_filters(T, wide_q):
    keep = (T.session != "LONDON") & (T.box_height_atr <= wide_q) & \
           (T.gap_H.abs() <= 0.25) & (T.r_unit_atr >= 0.5)
    return T[keep.fillna(False)]


def metrics(T):
    r = T.sort_values("entry_time")["r_net"].to_numpy()
    if len(r) == 0:
        return {"n": 0}
    eq = config.DEFAULT_EQUITY; peak = eq; mdd = 0
    for x in r:
        eq *= (1 + x * RISK); peak = max(peak, eq); mdd = max(mdd, (peak - eq) / peak)
    gp = r[r > 0].sum(); gl = -r[r < 0].sum()
    return {"n": len(r), "wr": 100 * (r > 0).mean(), "pf": gp / gl if gl > 0 else float("inf"),
            "exp": r.mean(), "dd": 100 * mdd, "net": 100 * (eq / config.DEFAULT_EQUITY - 1)}


def run(split=None):
    """split=None -> full; 'is'/'oos' -> first/last 30% by bar index per pair."""
    raw, filt = [], []
    for sym in PAIRS:
        df = load_ohlc(sym, TF, months=MONTHS); d1 = load_d1(sym)
        if split:
            cut = int(len(df) * 0.7)
            df = (df.iloc[:cut] if split == "is" else df.iloc[cut:]).reset_index(drop=True)
        T = labeled_trades(sym, df, d1)
        if len(T) == 0:
            continue
        raw.append(T)
    R = pd.concat(raw, ignore_index=True)
    wide_q = R["box_height_atr"].quantile(0.75)
    F = apply_filters(R, wide_q)
    return metrics(R), metrics(F), wide_q


def main():
    print("BEST VERSION — %s %s %dmo risk=%.1f%% pairs=%s" % (
        CFG["swing_def"], TF, MONTHS, RISK * 100, PAIRS))
    for label, split in [("FULL 24mo", None), ("in-sample (first 70%)", "is"),
                         ("out-of-sample (last 30%)", "oos")]:
        raw, filt, q = run(split)
        print("\n[%s]  (wide-range cutoff box/ATR=%.2f)" % (label, q))
        print("  unfiltered : n=%4d WR=%.1f%% PF=%.2f exp=%+.3fR DD=%.0f%% net=%+.0f%%" % (
            raw["n"], raw["wr"], raw["pf"], raw["exp"], raw["dd"], raw["net"]))
        print("  FILTERED   : n=%4d WR=%.1f%% PF=%.2f exp=%+.3fR DD=%.0f%% net=%+.0f%%" % (
            filt["n"], filt["wr"], filt["pf"], filt["exp"], filt["dd"], filt["net"]))


if __name__ == "__main__":
    main()
