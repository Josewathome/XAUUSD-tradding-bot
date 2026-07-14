"""wick.py — objective "John Wick" rejection-candle definitions.

A rejection FOR A LONG (near PDL) shows a long LOWER wick (price probed lower
and was rejected). A rejection FOR A SHORT (near PDH) shows a long UPPER wick.

Variations (test all):
  A: relevant wick >= 2x body
  B: relevant wick >= 3x body
  C: relevant wick >= 60% of total candle range
  D: strong rejection (>=2x body) AND close back inside the box in the trade
     direction (bullish close for a buy, bearish close for a sell, close within
     the box bounds).

Degenerate guards: total_range must be > 0; body floored at a tiny epsilon so a
doji with a genuine one-sided wick counts as rejection under A/B.
"""
from __future__ import annotations

_EPS = 1e-12


def _parts(o, h, l, c):
    body = abs(c - o)
    rng = h - l
    upper = h - max(o, c)
    lower = min(o, c) - l
    return body, rng, upper, lower


def is_john_wick(o, h, l, c, side, variation, pdl=None, pdh=None) -> bool:
    body, rng, upper, lower = _parts(o, h, l, c)
    if rng <= 0:
        return False
    rel = lower if side == "buy" else upper  # rejection wick for this direction
    ratio = rel / max(body, _EPS)

    if variation == "A":
        return ratio >= 2.0
    if variation == "B":
        return ratio >= 3.0
    if variation == "C":
        return rel >= 0.60 * rng
    if variation == "D":
        if ratio < 2.0:
            return False
        if side == "buy":
            closes_right = c >= o                       # bullish rejection
            inside = (pdl is None or c > pdl) and (pdh is None or c < pdh)
        else:
            closes_right = c <= o                       # bearish rejection
            inside = (pdh is None or c < pdh) and (pdl is None or c > pdl)
        return closes_right and inside
    raise ValueError("unknown wick variation %r" % variation)
