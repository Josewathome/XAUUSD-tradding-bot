"""sweep_lab.py — Fade-vs-Follow forensic lab for liquidity sweeps.

Core question: at a prior-day-level sweep, should we FADE (reversion) or FOLLOW
(continuation), and can we tell which BEFORE entry?

For every sweep event (John Wick pivot at a range/swing level) we:
  1. compute ~20 PRE-ENTRY features (structure/vol/liquidity/session/momentum),
  2. assign a geometry-independent label via a barrier race — did price REVERT to
     the range mid (A: failed sweep) or CONTINUE past the swept extreme by
     cont_mult*ATR (B: true breakout) first, within the day,
  3. simulate BOTH trades with identical symmetric RR geometry (fixed R multiple):
     FADE  = trade back into range on the pivot break,
     FOLLOW= trade with the sweep on the opposite pivot break,
     stop just the other side of the pivot; same RR target both ways.

Everything is R-multiple, cost-adjusted, one attempt per (day, side), EOD flat.
Data is pooled across all six pairs, M15, 24 months.

    cd research && box_theory/.venv/bin/python -m box_theory.sweep_lab
"""
from __future__ import annotations

import os
import json

import numpy as np
import pandas as pd

from . import config
from .load import load_ohlc, load_d1
from .levels import build_levels
from .wick import is_john_wick
from .engine import _atr
from .conditions import daily_regime

RES = os.path.join(os.path.dirname(__file__), "results")
TF = "M15"
MONTHS = 24
WICK = "B"
BUFFER = 0.10
MIDBAND = config.MID_BAND_PCT
SLBUF = 0.02
ENTRY_VALID = 4
CONT_MULT = 1.0        # continuation barrier = swept extreme + CONT_MULT*ATR
RR = 2.0               # symmetric target for the head-to-head direction test
FEATURES = ["adx_d1", "ma_slope_atr", "rsi14", "atr_pct", "atr_expansion",
            "box_height_atr", "prevrange_adr", "sweep_size_atr", "wick_atr",
            "wick_body", "prior_touches", "approach_atr", "velocity_atr",
            "gap_H", "against_d1trend", "level_type_swing", "utc_hour", "dow",
            "dist_level_mid_H"]


def _rsi(c, period=14):
    d = np.diff(c, prepend=c[0])
    up = np.where(d > 0, d, 0.0); dn = np.where(d < 0, -d, 0.0)
    ru = pd.Series(up).ewm(alpha=1 / period, adjust=False).mean().to_numpy()
    rd = pd.Series(dn).ewm(alpha=1 / period, adjust=False).mean().to_numpy()
    rs = ru / np.where(rd == 0, np.nan, rd)
    return 100 - 100 / (1 + rs)


def _simulate(o, h, l, c, i0, day_end, direction, trigger, sl, tp, cost):
    """Stop-entry bracket. direction +1 long / -1 short. Returns (r_net, reason) or None."""
    r_unit = abs(trigger - sl)
    if r_unit <= 0:
        return None
    fill = None
    for j in range(i0 + 1, min(i0 + 1 + ENTRY_VALID, day_end + 1)):
        if (direction > 0 and h[j] >= trigger) or (direction < 0 and l[j] <= trigger):
            fill = j; break
    if fill is None:
        return None
    for k in range(fill, day_end + 1):
        hit_sl = (l[k] <= sl) if direction > 0 else (h[k] >= sl)
        hit_tp = (h[k] >= tp) if direction > 0 else (l[k] <= tp)
        if hit_sl:
            gross = (sl - trigger) * direction; return ((gross - cost) / r_unit, "sl")
        if hit_tp:
            gross = (tp - trigger) * direction; return ((gross - cost) / r_unit, "tp")
    gross = (c[day_end] - trigger) * direction
    return ((gross - cost) / r_unit, "eod")


def events_for_pair(sym):
    df = load_ohlc(sym, TF, months=MONTHS)
    d1 = load_d1(sym)
    lv = build_levels(d1, "literal")
    o = df["open"].to_numpy(); h = df["high"].to_numpy()
    l = df["low"].to_numpy(); c = df["close"].to_numpy()
    atr = _atr(df, 14); rsi = _rsi(c); ema = pd.Series(c).ewm(span=50, adjust=False).mean().to_numpy()
    tdays = df["trading_day"].tolist(); ttime = df["time_utc"].tolist()
    sess = df["session"].tolist()
    spec = config.SYMBOL_SPEC[sym]; cost = spec["cost_price"]

    # day groups
    day_first, day_last = {}, {}
    for i, d in enumerate(tdays):
        day_first.setdefault(d, i); day_last[d] = i
    # d1-derived, known at entry (prior day)
    reg = daily_regime(d1); d1days = d1["trading_day"].tolist()
    d1close = d1["close"].to_numpy(); d1atr = _atr(d1, 14)
    d1range = (d1["high"] - d1["low"]).to_numpy()
    adr = pd.Series(d1range).rolling(20, min_periods=5).mean().to_numpy()
    prior_adx, prevclose, prioratr, prioradr, trend5 = {}, {}, {}, {}, {}
    for k, d in enumerate(d1days):
        if k >= 1:
            prior_adx[d] = reg[d1days[k - 1]]["adx"]; prevclose[d] = d1close[k - 1]
            prioratr[d] = d1atr[k - 1]; prioradr[d] = adr[k - 1]
        if k >= 6:
            trend5[d] = d1close[k - 1] - d1close[k - 6]

    rows = []
    traded = set()   # (day, side)
    for i in range(len(df)):
        d = tdays[i]
        lvl = lv.get(d)
        if lvl is None or atr[i] <= 0:
            continue
        H = lvl.height
        # candidate levels
        buys = [lvl.rl] + ([lvl.sl] if lvl.sl is not None else [])
        sells = [lvl.rh] + ([lvl.sh] if lvl.sh is not None else [])
        side = None; L = None
        for x in buys:
            if l[i] <= x + BUFFER * H and l[i] >= x - 4 * BUFFER * H:
                side = "buy"; L = x; break
        if side is None:
            for x in sells:
                if h[i] >= x - BUFFER * H and h[i] <= x + 4 * BUFFER * H:
                    side = "sell"; L = x; break
        if side is None or (d, side) in traded:
            continue
        if abs(c[i] - lvl.mid) < MIDBAND * H:
            continue
        if not is_john_wick(o[i], h[i], l[i], c[i], side, WICK, pdl=lvl.rl, pdh=lvl.rh):
            continue
        traded.add((d, side))
        day_end = day_last[d]
        if i >= day_end:
            continue
        ltype = "range" if L in (lvl.rl, lvl.rh) else "swing"
        swept = l[i] if side == "buy" else h[i]           # sweep extreme
        # ---- label: SYMMETRIC +/-1 ATR race from the decision price c[i] ----
        # fade direction = back into range; continuation = with the sweep.
        ref = c[i]; band = CONT_MULT * atr[i]
        if side == "buy":                 # fade=up, continue=down
            fade_bar, cont_bar = ref + band, ref - band
        else:                             # sell: fade=down, continue=up
            fade_bar, cont_bar = ref - band, ref + band
        label = "U"
        for k in range(i + 1, day_end + 1):
            hit_fade = (h[k] >= fade_bar) if side == "buy" else (l[k] <= fade_bar)
            hit_cont = (l[k] <= cont_bar) if side == "buy" else (h[k] >= cont_bar)
            if hit_fade and hit_cont:
                label = "U"; break        # same bar -> intrabar order unknown
            if hit_fade:
                label = "A"; break         # failed sweep (reverted first)
            if hit_cont:
                label = "B"; break         # true breakout (continued first)
        # ---- both trades, symmetric RR ----
        fdir = 1.0 if side == "buy" else -1.0
        f_trig = h[i] if side == "buy" else l[i]
        f_sl = (l[i] - SLBUF * H) if side == "buy" else (h[i] + SLBUF * H)
        f_ru = abs(f_trig - f_sl)
        f_tp = f_trig + fdir * RR * f_ru
        fade = _simulate(o, h, l, c, i, day_end, fdir, f_trig, f_sl, f_tp, cost)
        cdir = -fdir
        c_trig = l[i] if side == "buy" else h[i]
        c_sl = (h[i] + SLBUF * H) if side == "buy" else (l[i] - SLBUF * H)
        c_ru = abs(c_trig - c_sl)
        c_tp = c_trig + cdir * RR * c_ru
        cont = _simulate(o, h, l, c, i, day_end, cdir, c_trig, c_sl, c_tp, cost)

        # ---- pre-entry features ----
        da = prioratr.get(d, np.nan); pc = prevclose.get(d, np.nan)
        padr = prioradr.get(d, np.nan)
        k0 = max(0, i - 50)
        touches = int(np.sum(np.abs((l[k0:i] if side == "buy" else h[k0:i]) - L) <= BUFFER * H))
        body = abs(c[i] - o[i]); wick = (min(o[i], c[i]) - l[i]) if side == "buy" else (h[i] - max(o[i], c[i]))
        rows.append({
            "symbol": sym, "entry_time": ttime[i], "side": side, "level_type": ltype,
            "label": label, "fade_r": None if fade is None else fade[0],
            "cont_r": None if cont is None else cont[0],
            "fade_filled": int(fade is not None), "cont_filled": int(cont is not None),
            # features
            "adx_d1": prior_adx.get(d, np.nan),
            "ma_slope_atr": (c[i] - ema[i]) / atr[i],
            "rsi14": rsi[i],
            "atr_pct": float((atr[k0:i] < atr[i]).mean()) if i > 20 else np.nan,
            "atr_expansion": float(atr[i] / atr[i - 20]) if i > 20 and atr[i - 20] > 0 else np.nan,
            "box_height_atr": H / da if da and da > 0 else np.nan,
            "prevrange_adr": H / padr if padr and padr > 0 else np.nan,
            "sweep_size_atr": abs(swept - L) / atr[i],
            "wick_atr": wick / atr[i],
            "wick_body": wick / max(body, 1e-9),
            "prior_touches": touches,
            "approach_atr": abs(swept - o[day_first[d]]) / atr[i],
            "velocity_atr": abs(c[i] - c[max(0, i - 3)]) / 3 / atr[i],
            "gap_H": (o[day_first[d]] - pc) / H if (H > 0 and not np.isnan(pc)) else np.nan,
            "against_d1trend": int((side == "buy" and trend5.get(d, 0) < 0) or
                                   (side == "sell" and trend5.get(d, 0) > 0)),
            "level_type_swing": int(ltype == "swing"),
            "utc_hour": ttime[i].hour, "dow": ttime[i].weekday(),
            "session": sess[i],
            "dist_level_mid_H": abs(L - lvl.mid) / H if H > 0 else np.nan,
        })
    return pd.DataFrame(rows)


def build():
    parts = [events_for_pair(s) for s in config.SYMBOLS]
    T = pd.concat([p for p in parts if len(p)], ignore_index=True)
    T.to_csv(os.path.join(RES, "sweep_events.csv"), index=False)
    return T


if __name__ == "__main__":
    T = build()
    ab = T[T.label != "U"]
    print("events=%d  A(failed/revert)=%d  B(breakout/continue)=%d  U=%d" % (
        len(T), (T.label == "A").sum(), (T.label == "B").sum(), (T.label == "U").sum()))
    print("base rate B (continuation) = %.1f%%" % (100 * (ab.label == "B").mean()))
    print("saved results/sweep_events.csv")
