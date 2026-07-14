"""load.py — read exported CSV OHLC into tidy DataFrames.

Adds:
  * time_server (datetime, broker wall-clock),
  * time_utc    (datetime, true UTC = server - offset hours),
  * trading_day (server calendar date — aligns intraday bars with D1 boxes),
  * utc_hour, session.

The server->UTC offset comes from data/_meta.json (detected at export time).
It is DST-sensitive; we apply it uniformly and document ~1h uncertainty at DST
switches. This affects ONLY session tagging — daily boxes are offset-independent.
"""
from __future__ import annotations

import json
import os
import datetime as dt

import pandas as pd

from . import config

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")


def load_meta() -> dict:
    with open(os.path.join(DATA_DIR, "_meta.json")) as f:
        return json.load(f)


def _offset_hours() -> int:
    meta = load_meta()
    off = meta.get("detected_server_utc_offset_hours")
    return int(off) if off is not None else 0


def load_ohlc(symbol: str, timeframe: str, months: int | None = None) -> pd.DataFrame:
    """Load one symbol/timeframe. If months given, keep only the last N months."""
    path = os.path.join(DATA_DIR, "%s_%s.csv" % (symbol, timeframe))
    df = pd.read_csv(path)
    df["time_server"] = pd.to_datetime(df["time_server"])
    off = _offset_hours()
    df["time_utc"] = df["time_server"] - pd.Timedelta(hours=off)
    df["trading_day"] = df["time_server"].dt.date
    df["utc_hour"] = df["time_utc"].dt.hour
    df["session"] = df["utc_hour"].map(config.session_of)
    df = df.sort_values("epoch").reset_index(drop=True)
    if months is not None:
        last = df["time_server"].iloc[-1]
        cutoff = last - pd.DateOffset(months=months)
        df = df[df["time_server"] >= cutoff].reset_index(drop=True)
    return df


def load_d1(symbol: str) -> pd.DataFrame:
    """Daily bars (full history) for box construction — never period-trimmed."""
    return load_ohlc(symbol, "D1", months=None)
