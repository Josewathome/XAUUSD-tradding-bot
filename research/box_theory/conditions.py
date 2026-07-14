"""conditions.py — market-regime tagging and descriptive zone statistics.

Regime (per trading day, from D1):
  * ADX(14): is_trend = ADX >= 25, else ranging.
  * volatility: prev-day range (= box height) vs its trailing 20-day median;
    is_high_vol = above median.

Zone statistics test the CORE hypothesis directly, independent of any trade:
  For each day with a box, using intraday bars of that day:
    * touched_pdh / touched_pdl (intraday high>=PDH / low<=PDL)
    * pdh_reversed: touched PDH but the day CLOSED back below PDH (fade worked)
      pdh_broke:    touched PDH and closed above PDH (continuation)
    * same for PDL (reversed = closed back above PDL)
    * reached_mid_after_extreme: touched an extreme AND later traded through mid
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def _adx(d1: pd.DataFrame, period: int = 14) -> np.ndarray:
    h = d1["high"].to_numpy(dtype=float)
    l = d1["low"].to_numpy(dtype=float)
    c = d1["close"].to_numpy(dtype=float)
    n = len(d1)
    if n < 2:
        return np.full(n, np.nan)
    up = h[1:] - h[:-1]
    dn = l[:-1] - l[1:]
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    prev_c = c[:-1]
    tr = np.maximum(h[1:] - l[1:], np.maximum(np.abs(h[1:] - prev_c), np.abs(l[1:] - prev_c)))

    def wilder(x):
        out = np.full(len(x), np.nan)
        if len(x) < period:
            return out
        s = x[:period].sum()
        out[period - 1] = s
        for i in range(period, len(x)):
            s = s - s / period + x[i]
            out[i] = s
        return out

    tr_s = wilder(tr)
    pdm_s = wilder(plus_dm)
    mdm_s = wilder(minus_dm)
    with np.errstate(divide="ignore", invalid="ignore"):
        pdi = 100.0 * pdm_s / tr_s
        mdi = 100.0 * mdm_s / tr_s
        dx = 100.0 * np.abs(pdi - mdi) / (pdi + mdi)
    adx_shifted = pd.Series(dx).rolling(period, min_periods=period).mean().to_numpy()
    adx = np.concatenate([[np.nan], adx_shifted])  # realign to d1 index (dropped first row)
    return adx


def daily_regime(d1: pd.DataFrame) -> dict:
    """Return {trading_day -> {adx, is_trend, is_high_vol}}."""
    adx = _adx(d1)
    day_range = (d1["high"] - d1["low"]).to_numpy()
    med = pd.Series(day_range).rolling(20, min_periods=5).median().to_numpy()
    out = {}
    days = d1["trading_day"].tolist()
    for i in range(len(d1)):
        a = adx[i]
        out[days[i]] = {
            "adx": None if (a != a) else float(a),
            "is_trend": bool(a >= 25) if (a == a) else None,
            "is_high_vol": bool(day_range[i] > med[i]) if (med[i] == med[i]) else None,
        }
    return out


def zone_stats(df: pd.DataFrame, boxes: dict) -> dict:
    """Descriptive test of the reversal hypothesis, per day."""
    n_days = 0
    t_pdh = t_pdl = 0
    pdh_rev = pdh_brk = 0
    pdl_rev = pdl_brk = 0
    mid_after_extreme = 0
    extreme_days = 0

    for day, g in df.groupby("trading_day", sort=True):
        box = boxes.get(day)
        if box is None or len(g) == 0:
            continue
        n_days += 1
        hi = g["high"].max()
        lo = g["low"].min()
        close = g["close"].iloc[-1]
        touched_h = hi >= box.pdh
        touched_l = lo <= box.pdl
        if touched_h:
            t_pdh += 1
            if close < box.pdh:
                pdh_rev += 1
            else:
                pdh_brk += 1
        if touched_l:
            t_pdl += 1
            if close > box.pdl:
                pdl_rev += 1
            else:
                pdl_brk += 1
        if touched_h or touched_l:
            extreme_days += 1
            # did price trade through the midpoint at any point that day?
            if (g["low"].min() <= box.mid <= g["high"].max()):
                mid_after_extreme += 1

    def pct(a, b):
        return round(100.0 * a / b, 2) if b else None

    return {
        "days": n_days,
        "touch_pdh_pct": pct(t_pdh, n_days),
        "touch_pdl_pct": pct(t_pdl, n_days),
        "pdh_reverse_pct": pct(pdh_rev, t_pdh),   # of days that touched PDH, % that faded back
        "pdh_break_pct": pct(pdh_brk, t_pdh),
        "pdl_reverse_pct": pct(pdl_rev, t_pdl),
        "pdl_break_pct": pct(pdl_brk, t_pdl),
        "reach_mid_after_extreme_pct": pct(mid_after_extreme, extreme_days),
    }
