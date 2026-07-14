"""config.py — symbol specs, sessions, cost model, and parameter grids.

P&L approach (important): every trade is sized so that a full stop-out loses
exactly `risk_pct` of current equity. Therefore net $ P&L = R_net * risk_amount,
independent of contract size. We only need, per symbol:
  * pip_size        — for expressing distances/costs in pips (reporting)
  * cost_price      — effective ROUND-TURN transaction cost in PRICE units
                      (raw spread + IC Markets Raw commission), subtracted from
                      every trade's gross price move. Documented assumptions;
                      cost sensitivity can be tested separately.
Digits/pip_size follow src/domain/value_objects.py PipCalculator.
"""
from __future__ import annotations

SYMBOLS = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "XAUUSD"]

# digits per src/config.py:147; pip_size per PipCalculator (digits 2/3 -> 0.01 else 1e-4)
SYMBOL_SPEC = {
    "EURUSD": {"digits": 5, "pip_size": 0.0001, "cost_price": 0.00008},  # ~0.8 pip r/t
    "GBPUSD": {"digits": 5, "pip_size": 0.0001, "cost_price": 0.00011},  # ~1.1 pip r/t
    "USDJPY": {"digits": 3, "pip_size": 0.01,   "cost_price": 0.010},    # ~1.0 pip r/t
    "AUDUSD": {"digits": 5, "pip_size": 0.0001, "cost_price": 0.00010},  # ~1.0 pip r/t
    "USDCAD": {"digits": 5, "pip_size": 0.0001, "cost_price": 0.00013},  # ~1.3 pip r/t
    "XAUUSD": {"digits": 2, "pip_size": 0.01,   "cost_price": 0.30},     # ~30c r/t (spread+comm)
}

# Sessions in TRUE UTC hours (non-overlapping buckets for clean attribution).
# Asian(Tokyo) 00-08, London 08-13, NewYork 13-21, else Off. Applied to the
# entry bar's UTC hour. Note: server->UTC offset is DST-sensitive (see loader);
# session boundaries carry ~1h uncertainty at DST switches.
SESSIONS = {
    "ASIAN": (0, 8),
    "LONDON": (8, 13),
    "NEWYORK": (13, 21),
}

def session_of(utc_hour: int) -> str:
    for name, (a, b) in SESSIONS.items():
        if a <= utc_hour < b:
            return name
    return "OFF"

# Default starting equity (per .env ACCOUNT_BALANCE).
DEFAULT_EQUITY = 400.0

# ── Parameter grids for the Phase-2 sweep ─────────────────────────────
WICK_VARIATIONS = ["A", "B", "C", "D"]          # see wick.py
ENTRY_METHODS = [1, 2, 3]                        # see engine.py
EXIT_MODELS = [
    ("rr", 1.0), ("rr", 1.5), ("rr", 2.0), ("rr", 3.0),
    ("midpoint", None), ("opposite", None),
    ("atr", 1.5), ("trail", 2.0),
]
BUFFER_PCTS = [0.05, 0.10, 0.15, 0.20]           # band width near extreme, fraction of box H
MID_BAND_PCT = 0.10                               # +/- no-trade zone around midpoint (fraction of H)
RISK_PCTS = [0.01, 0.005]
TIMEFRAMES = ["M5", "M15", "M30"]

# Periods (months back from last bar). M5 only supports ~6 months.
PERIODS_MONTHS = [6, 12, 24]

# Stop buffer beyond the rejection wick, as a fraction of the box height.
SL_BUFFER_PCT = 0.02
