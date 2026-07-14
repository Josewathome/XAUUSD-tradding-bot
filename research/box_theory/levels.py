"""levels.py — the four Sneaky Pivot levels per trading day.

Range high/low  = previous day's D1 high/low (same as the box).
Swing high/low  = the next structural level BEYOND the range, two definitions:
  * literal : the nearest PRIOR daily bar whose high is above the range high
              (scan left) / low below the range low. Matches the video's
              "go back until you find the next higher/lower price".
  * fractal : nearest prior N-bar swing pivot above the range high / below the
              range low (a high with N lower highs each side, mirror for lows).

No look-ahead: a level active on trading day D uses only D1 bars strictly before
D (fractal pivots must be fully confirmed by day D-1).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Levels:
    rh: float           # range high (prev day high)
    rl: float           # range low  (prev day low)
    mid: float
    height: float
    sh: float | None    # swing high (above rh), may be None
    sl: float | None    # swing low  (below rl), may be None


def _fractal_pivots(highs, lows, n=2):
    """Return (is_swing_high, is_swing_low) boolean arrays; pivot confirmed at i+n."""
    m = len(highs)
    sh = np.zeros(m, dtype=bool)
    sl = np.zeros(m, dtype=bool)
    for i in range(n, m - n):
        wl, wr = highs[i - n:i], highs[i + 1:i + 1 + n]
        if highs[i] > wl.max() and highs[i] > wr.max():
            sh[i] = True
        wl2, wr2 = lows[i - n:i], lows[i + 1:i + 1 + n]
        if lows[i] < wl2.min() and lows[i] < wr2.min():
            sl[i] = True
    return sh, sl


def build_levels(d1: pd.DataFrame, swing_def: str = "literal", fractal_n: int = 2) -> dict:
    days = d1["trading_day"].tolist()
    highs = d1["high"].to_numpy(dtype=float)
    lows = d1["low"].to_numpy(dtype=float)
    out = {}

    if swing_def == "fractal":
        is_sh, is_sl = _fractal_pivots(highs, lows, fractal_n)

    for i in range(1, len(d1)):
        rh, rl = highs[i - 1], lows[i - 1]     # range = previous day
        if rh <= rl:
            continue
        sh = sl = None
        if swing_def == "literal":
            # nearest prior bar (before the range day i-1) with a higher high / lower low
            for j in range(i - 2, -1, -1):
                if sh is None and highs[j] > rh:
                    sh = highs[j]
                if sl is None and lows[j] < rl:
                    sl = lows[j]
                if sh is not None and sl is not None:
                    break
        elif swing_def == "fractal":
            # nearest confirmed pivot (confirmed by i-1, i.e. pivot index + n <= i-1) beyond range
            for j in range(i - 2, -1, -1):
                if j + fractal_n > i - 1:
                    continue  # not yet confirmed by the trading day
                if sh is None and is_sh[j] and highs[j] > rh:
                    sh = highs[j]
                if sl is None and is_sl[j] and lows[j] < rl:
                    sl = lows[j]
                if sh is not None and sl is not None:
                    break
        else:
            raise ValueError("unknown swing_def %r" % swing_def)

        out[days[i]] = Levels(rh=rh, rl=rl, mid=(rh + rl) / 2.0, height=rh - rl, sh=sh, sl=sl)
    return out
