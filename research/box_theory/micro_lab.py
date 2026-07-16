"""micro_lab.py — microstructure event dataset for PDH/PDL sweeps.

Event: the FIRST candle of a trading day whose high pierces PDH (side=sell-sweep)
or whose low pierces PDL (side=buy-sweep) — one event per (day, side). NO wick
precondition: rejection anatomy is a FEATURE here, not a filter.

Label (unbiased, no hindsight): symmetric barrier race from the event candle's
close — REVERSAL (A) if price travels 1*ATR back toward the box first,
CONTINUATION (B) if it travels 1*ATR beyond first, within the same day.
Same-bar double-hit -> U (excluded). Identical methodology to sweep_lab.

Features are computed ONLY from information available at the event candle's
close: event-candle anatomy, prior 3/5/10-candle windows, candle sequences,
tick-volume behaviour, approach/order-flow proxies, session/regime context.

    cd research && box_theory/.venv/bin/python -m box_theory.micro_lab
Writes results/micro_events_M15.csv (24mo) and micro_events_M5.csv (6mo).
"""
from __future__ import annotations

import os

import numpy as np
import pandas as pd

from . import config
from .load import load_ohlc, load_d1
from .boxes import build_boxes
from .engine import _atr
from .conditions import daily_regime

RES = os.path.join(os.path.dirname(__file__), "results")
CONT_MULT = 1.0
PCTL_WIN = 200          # lookback for percentile features


def _linslope(y):
    n = len(y)
    if n < 2:
        return 0.0
    x = np.arange(n, dtype=float)
    xm, ym = x.mean(), y.mean()
    den = ((x - xm) ** 2).sum()
    return float(((x - xm) * (y - ym)).sum() / den) if den else 0.0


def events_for(sym: str, tf: str, months: int) -> pd.DataFrame:
    df = load_ohlc(sym, tf, months=months)
    d1 = load_d1(sym)
    boxes = build_boxes(d1)
    o = df["open"].to_numpy(); h = df["high"].to_numpy()
    l = df["low"].to_numpy(); c = df["close"].to_numpy()
    v = df["volume"].to_numpy(dtype=float)
    atr = _atr(df, 14)
    tdays = df["trading_day"].tolist(); ttime = df["time_utc"].tolist()
    sess = df["session"].tolist()
    n = len(df)

    day_first, day_last = {}, {}
    for i, d in enumerate(tdays):
        day_first.setdefault(d, i); day_last[d] = i

    reg = daily_regime(d1)
    d1days = d1["trading_day"].tolist()
    d1close = d1["close"].to_numpy(); d1atr = _atr(d1, 14)
    prior_adx, prioratr, trend5 = {}, {}, {}
    for k, d in enumerate(d1days):
        if k >= 1:
            prior_adx[d] = reg[d1days[k - 1]]["adx"]; prioratr[d] = d1atr[k - 1]
        if k >= 6:
            trend5[d] = d1close[k - 1] - d1close[k - 6]

    rows = []
    done = set()
    for i in range(n):
        d = tdays[i]
        box = boxes.get(d)
        if box is None or atr[i] <= 0 or i < PCTL_WIN or i >= day_last[d]:
            continue
        # sweep detection: first pierce per (day, side)
        for side, level in (("sell", box.pdh), ("buy", box.pdl)):
            pierced = (h[i] > level) if side == "sell" else (l[i] < level)
            if not pierced or (d, side) in done:
                continue
            done.add((d, side))

            # ---- label: symmetric +/-1 ATR race from close ----
            ref = c[i]; band = CONT_MULT * atr[i]
            if side == "sell":      # revert = down (back into box), continue = up
                rev_bar, con_bar = ref - band, ref + band
            else:                   # buy-side sweep of PDL: revert = up, continue = down
                rev_bar, con_bar = ref + band, ref - band
            label = "U"
            for k in range(i + 1, day_last[d] + 1):
                hit_r = (l[k] <= rev_bar) if side == "sell" else (h[k] >= rev_bar)
                hit_c = (h[k] >= con_bar) if side == "sell" else (l[k] <= con_bar)
                if hit_r and hit_c:
                    label = "U"; break
                if hit_r:
                    label = "A"; break
                if hit_c:
                    label = "B"; break

            # ---- event-candle anatomy ----
            body = abs(c[i] - o[i]); rng = h[i] - l[i]
            upw = h[i] - max(o[i], c[i]); low = min(o[i], c[i]) - l[i]
            rejw = upw if side == "sell" else low          # wick on the sweep side
            close_pos = (c[i] - l[i]) / rng if rng > 0 else 0.5
            open_pos = (o[i] - l[i]) / rng if rng > 0 else 0.5
            ranges_hist = h[i - PCTL_WIN:i] - l[i - PCTL_WIN:i]
            depth = (h[i] - level) if side == "sell" else (level - l[i])
            back_inside = int(c[i] < level) if side == "sell" else int(c[i] > level)

            # ---- prior windows ----
            def win_feats(N):
                sl = slice(i - N, i)
                dirs = np.sign(c[sl] - o[sl])
                net = (c[i - 1] - c[i - N]) / atr[i]
                # signed toward the level: + = moving toward the swept level
                toward = net if side == "sell" else -net
                return {
                    "n_bull_%d" % N: int((dirs > 0).sum()),
                    "net_toward_%d" % N: float(toward),
                    "range_slope_%d" % N: _linslope((h[sl] - l[sl]) / atr[i]),
                }
            f3, f5, f10 = win_feats(3), win_feats(5), win_feats(10)
            # momentum acceleration: move of last 3 vs the 3 before them (toward level)
            m_recent = (c[i - 1] - c[i - 4]) * (1 if side == "sell" else -1) / atr[i]
            m_before = (c[i - 4] - c[i - 7]) * (1 if side == "sell" else -1) / atr[i]
            accel = m_recent - m_before
            # consecutive candles in sweep direction immediately before event
            consec = 0
            want = 1 if side == "sell" else -1
            for k in range(i - 1, i - 11, -1):
                if np.sign(c[k] - o[k]) == want:
                    consec += 1
                else:
                    break
            seq3 = "".join("U" if c[k] >= o[k] else "D" for k in range(i - 3, i))

            # ---- volume ----
            vol_hist = v[i - PCTL_WIN:i]
            vol_pct = float((vol_hist < v[i]).mean())
            vol_ratio3 = float(v[i] / max(v[i - 3:i].mean(), 1e-9))
            vol_slope5 = _linslope(v[i - 5:i] / max(vol_hist.mean(), 1e-9))
            vol_climax = int(v[i] >= v[i - 10:i + 1].max())

            # ---- approach / order-flow proxies ----
            j0 = day_first[d]
            bars_to_level = i - j0
            dist_traveled = abs(c[i] - o[j0]) / atr[i]
            velocity3 = abs(c[i - 1] - c[i - 4]) / 3.0 / atr[i]
            atr_exp = float(atr[i] / atr[i - 20]) if atr[i - 20] > 0 else np.nan
            atr_pctile = float((atr[i - PCTL_WIN:i] < atr[i]).mean())

            da = prioratr.get(d, np.nan)
            t5 = trend5.get(d, 0.0)
            with_trend = int((side == "sell" and t5 > 0) or (side == "buy" and t5 < 0))

            rows.append({
                "symbol": sym, "tf": tf, "entry_time": ttime[i], "side": side,
                "label": label, "session": sess[i], "utc_hour": ttime[i].hour,
                "dow": ttime[i].weekday(),
                # event candle
                "body_atr": body / atr[i], "range_atr": rng / atr[i],
                "upwick_atr": upw / atr[i], "lowick_atr": low / atr[i],
                "rejwick_atr": rejw / atr[i],
                "wick_body": min(rejw / max(body, 1e-9), 20.0),
                "close_pos": close_pos, "open_pos": open_pos,
                "range_pctile": float((ranges_hist < rng).mean()),
                "sweep_depth_atr": depth / atr[i], "close_back_inside": back_inside,
                # windows
                **f3, **f5, **f10,
                "momentum_accel": float(accel), "consec_dir": consec, "seq3": seq3,
                # volume
                "vol_pctile": vol_pct, "vol_ratio3": vol_ratio3,
                "vol_slope5": vol_slope5, "vol_climax": vol_climax,
                # approach
                "bars_to_level": bars_to_level, "dist_traveled_atr": dist_traveled,
                "velocity3_atr": velocity3, "atr_expansion": atr_exp,
                "atr_pctile": atr_pctile,
                # context
                "adx_d1": prior_adx.get(d, np.nan),
                "box_height_atr": box.height / da if da and da > 0 else np.nan,
                "with_d1_trend": with_trend,
                "side_is_pdh": int(side == "sell"),
            })
    return pd.DataFrame(rows)


def main():
    for tf, months in [("M15", 24), ("M5", 6)]:
        parts = [events_for(s, tf, months) for s in config.SYMBOLS]
        T = pd.concat([p for p in parts if len(p)], ignore_index=True)
        path = os.path.join(RES, "micro_events_%s.csv" % tf)
        T.to_csv(path, index=False)
        ab = T[T.label != "U"]
        print("%s (%dmo): events=%d  A=%d  B=%d  U=%d  baseB=%.1f%%  -> %s" % (
            tf, months, len(T), (T.label == "A").sum(), (T.label == "B").sum(),
            (T.label == "U").sum(), 100 * (ab.label == "B").mean(), os.path.basename(path)))


if __name__ == "__main__":
    main()
