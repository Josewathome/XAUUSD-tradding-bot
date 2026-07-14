"""boxes.py — build the daily "Box" from the PREVIOUS day's D1 range.

For trading day D, the active box is defined by the immediately preceding
trading day's D1 High/Low:
    PDH = prev day High
    PDL = prev day Low
    mid = (PDH + PDL) / 2
    height H = PDH - PDL
Weekends are handled naturally: the previous *row* in the D1 series is the
previous trading day, so Friday's range is Monday's box.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class Box:
    pdh: float
    pdl: float
    mid: float
    height: float
    prev_day: dt.date  # the day whose range built this box


def build_boxes(d1: pd.DataFrame) -> dict:
    """Return {trading_day(date) -> Box} using the previous D1 bar."""
    boxes = {}
    days = d1["trading_day"].tolist()
    highs = d1["high"].tolist()
    lows = d1["low"].tolist()
    for i in range(1, len(d1)):
        pdh = highs[i - 1]
        pdl = lows[i - 1]
        if pdh <= pdl:
            continue
        boxes[days[i]] = Box(
            pdh=pdh, pdl=pdl, mid=(pdh + pdl) / 2.0,
            height=pdh - pdl, prev_day=days[i - 1],
        )
    return boxes
