"""engine.py — event-driven backtest for the Box Theory model.

One symbol / one timeframe. Bar-by-bar (no lookahead), at most one open
position. Signals are decided at a candle CLOSE and managed only from the NEXT
bar onward. Intrabar SL/TP ties resolve to SL first (pessimistic). Costs
(spread + commission) are applied in price terms per round turn.

P&L: sized so a full stop-out = -1R = -risk_pct of equity, hence
     ret_frac = r_net * risk_pct, compounded.

params (dict):
  wick            : 'A'|'B'|'C'|'D'
  entry_method    : 1 (close) | 2 (break of rej candle) | 3 (next-candle confirm)
  exit            : (kind, value) — ('rr',R) | ('midpoint',None) | ('opposite',None)
                    | ('atr',mult) | ('trail',atr_mult)
  buffer_pct      : band near extreme, fraction of box height H
  mid_band_pct    : +/- no-trade zone around midpoint, fraction of H
  risk_pct        : e.g. 0.01
  sl_buffer_pct   : SL beyond rej wick, fraction of H (default cfg)
  session_filter  : None | set of session names
  one_trade_per_box : bool (default True)
  hold_to_eod     : bool (default True) — flatten on day change
  atr_period      : int (default 14)
  entry_valid_bars: int (default 3) — how long a method-2 stop order stays live
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import config
from .wick import is_john_wick


def _atr(df: pd.DataFrame, period: int) -> np.ndarray:
    h = df["high"].to_numpy()
    l = df["low"].to_numpy()
    c = df["close"].to_numpy()
    prev_c = np.concatenate([[c[0]], c[:-1]])
    tr = np.maximum(h - l, np.maximum(np.abs(h - prev_c), np.abs(l - prev_c)))
    atr = pd.Series(tr).rolling(period, min_periods=1).mean().to_numpy()
    return atr


def run_backtest(df: pd.DataFrame, boxes: dict, symbol: str, params: dict,
                 initial_equity: float = config.DEFAULT_EQUITY) -> dict:
    spec = config.SYMBOL_SPEC[symbol]
    cost = spec["cost_price"]
    pip = spec["pip_size"]

    wick = params["wick"]
    entry_method = params["entry_method"]
    exit_kind, exit_val = params["exit"]
    buf = params["buffer_pct"]
    midband = params.get("mid_band_pct", config.MID_BAND_PCT)
    risk_pct = params["risk_pct"]
    sl_buf = params.get("sl_buffer_pct", config.SL_BUFFER_PCT)
    session_filter = params.get("session_filter")
    one_per_box = params.get("one_trade_per_box", True)
    # Control mode: replace the wick+zone signal with a seeded random entry.
    random_entry = params.get("random_entry", False)
    random_prob = params.get("random_prob", 0.02)
    _rng = np.random.default_rng(params.get("random_seed", 0)) if random_entry else None
    hold_eod = params.get("hold_to_eod", True)
    atr_period = params.get("atr_period", 14)
    entry_valid = params.get("entry_valid_bars", 3)

    o = df["open"].to_numpy()
    h = df["high"].to_numpy()
    l = df["low"].to_numpy()
    c = df["close"].to_numpy()
    tday = df["trading_day"].tolist()
    sess = df["session"].tolist()
    ttime = df["time_utc"].tolist()
    atr = _atr(df, atr_period)
    n = len(df)

    equity = initial_equity
    trades = []
    pos = None          # open position dict
    pending = None      # pending entry dict (method 2/3)
    traded_days = set()  # trading days that already produced a trade (one_per_box)

    def target_price(side, entry, box):
        d = 1.0 if side == "buy" else -1.0
        if exit_kind == "rr":
            return None  # rr handled via r_unit after sl known
        if exit_kind == "midpoint":
            return box.mid
        if exit_kind == "opposite":
            return box.pdh if side == "buy" else box.pdl
        if exit_kind == "atr":
            return entry + d * exit_val * atr[entry_idx_holder[0]]
        if exit_kind == "trail":
            return None
        raise ValueError("unknown exit kind %s" % exit_kind)

    entry_idx_holder = [0]  # let target_price read the entry bar index for ATR

    def open_position(side, entry_price, rej_low, rej_high, box, entry_idx):
        d = 1.0 if side == "buy" else -1.0
        if side == "buy":
            sl = rej_low - sl_buf * box.height
        else:
            sl = rej_high + sl_buf * box.height
        r_unit = abs(entry_price - sl)
        if r_unit <= 0:
            return None
        entry_idx_holder[0] = entry_idx
        # take-profit
        if exit_kind == "rr":
            tp = entry_price + d * exit_val * r_unit
        elif exit_kind == "trail":
            tp = None
        else:
            tp = target_price(side, entry_price, box)
            # target must be on the correct side of entry, else invalid setup
            if tp is not None and (tp - entry_price) * d <= 0:
                return None
        return {
            "side": side, "dir": d, "entry": entry_price, "sl": sl, "tp": tp,
            "r_unit": r_unit, "entry_idx": entry_idx, "entry_time": ttime[entry_idx],
            "box": box, "day": tday[entry_idx], "entry_session": sess[entry_idx],
        }

    def close_position(p, exit_price, exit_idx, reason):
        nonlocal equity
        gross = (exit_price - p["entry"]) * p["dir"]
        net = gross - cost
        r_net = net / p["r_unit"]
        ret_frac = r_net * risk_pct
        equity_before = equity
        pnl = ret_frac * equity_before
        equity = equity_before + pnl
        dur = (ttime[exit_idx] - p["entry_time"]).total_seconds() / 60.0
        trades.append({
            "symbol": symbol, "side": p["side"], "direction": p["side"],
            "entry_time": p["entry_time"], "exit_time": ttime[exit_idx],
            "entry": p["entry"], "exit": exit_price, "sl": p["sl"], "tp": p["tp"],
            "r_unit": p["r_unit"], "r_net": r_net, "ret_frac": ret_frac,
            "pnl_usd": pnl, "equity_before": equity_before, "equity_after": equity,
            "duration_min": dur, "exit_reason": reason,
            "entry_session": p["entry_session"], "trading_day": p["day"],
            "pdh": p["box"].pdh, "pdl": p["box"].pdl, "mid": p["box"].mid,
            "box_height": p["box"].height,
        })

    for i in range(n):
        day = tday[i]
        box = boxes.get(day)

        # (A) End-of-day flatten (time exit) — position from a previous day.
        if pos is not None and hold_eod and day != pos["day"]:
            close_position(pos, o[i], i, "time_exit_eod")
            pos = None
        # cancel stale pending across day boundary
        if pending is not None and day != pending["day"]:
            pending = None

        # (B) Manage open position on this (future) bar.
        if pos is not None and i > pos["entry_idx"]:
            d = pos["dir"]
            # trailing update (before checking stop)
            if exit_kind == "trail":
                if pos["side"] == "buy":
                    new_sl = h[i] - exit_val * atr[i]
                    if new_sl > pos["sl"]:
                        pos["sl"] = new_sl
                else:
                    new_sl = l[i] + exit_val * atr[i]
                    if new_sl < pos["sl"]:
                        pos["sl"] = new_sl
            hit_sl = (l[i] <= pos["sl"]) if pos["side"] == "buy" else (h[i] >= pos["sl"])
            hit_tp = False
            if pos["tp"] is not None:
                hit_tp = (h[i] >= pos["tp"]) if pos["side"] == "buy" else (l[i] <= pos["tp"])
            if hit_sl:  # pessimistic: SL first on ties
                close_position(pos, pos["sl"], i, "stop_loss")
                pos = None
            elif hit_tp:
                close_position(pos, pos["tp"], i, "take_profit")
                pos = None

        # (C) Resolve pending entry (method 2 / 3) on this bar.
        if pos is None and pending is not None:
            side = pending["side"]
            if pending["method"] == 2:
                trig = pending["trigger"]
                filled = (h[i] >= trig) if side == "buy" else (l[i] <= trig)
                if filled:
                    p = open_position(side, trig, pending["rej_low"], pending["rej_high"],
                                      pending["box"], i)
                    pos = p
                    pending = None
                    if one_per_box:
                        traded_days.add(day)
                else:
                    pending["bars"] -= 1
                    if pending["bars"] <= 0:
                        pending = None
            elif pending["method"] == 3:
                # the "next candle": confirm if it closes in trade direction
                confirms = (c[i] > o[i]) if side == "buy" else (c[i] < o[i])
                if confirms:
                    p = open_position(side, c[i], pending["rej_low"], pending["rej_high"],
                                      pending["box"], i)
                    pos = p
                    if one_per_box and p is not None:
                        traded_days.add(day)
                pending = None

        # (D) Signal evaluation at this candle's close (flat, no pending).
        if pos is None and pending is None and box is not None:
            if not (one_per_box and day in traded_days):
                if session_filter is None or sess[i] in session_filter:
                    if random_entry:
                        # control baseline: random side at random bars, same SL/TP/cost machinery
                        if _rng.random() < random_prob:
                            side = "buy" if _rng.random() < 0.5 else "sell"
                            p = open_position(side, c[i], l[i], h[i], box, i)
                            if p is not None:
                                pos = p
                                if one_per_box:
                                    traded_days.add(day)
                        continue
                    H = box.height
                    lower_top = box.pdl + buf * H
                    upper_bot = box.pdh - buf * H
                    in_lower = l[i] <= lower_top
                    in_upper = h[i] >= upper_bot
                    side = None
                    if in_lower and not in_upper:
                        side = "buy"
                    elif in_upper and not in_lower:
                        side = "sell"
                    # else ambiguous/none -> skip
                    if side is not None:
                        # no-trade zone: reject if close sits in midpoint band
                        if abs(c[i] - box.mid) >= midband * H:
                            if is_john_wick(o[i], h[i], l[i], c[i], side, wick,
                                            pdl=box.pdl, pdh=box.pdh):
                                if entry_method == 1:
                                    p = open_position(side, c[i], l[i], h[i], box, i)
                                    if p is not None:
                                        pos = p
                                        if one_per_box:
                                            traded_days.add(day)
                                elif entry_method == 2:
                                    pending = {
                                        "method": 2, "side": side,
                                        "trigger": h[i] if side == "buy" else l[i],
                                        "rej_low": l[i], "rej_high": h[i],
                                        "box": box, "day": day, "bars": entry_valid,
                                    }
                                elif entry_method == 3:
                                    pending = {
                                        "method": 3, "side": side,
                                        "rej_low": l[i], "rej_high": h[i],
                                        "box": box, "day": day,
                                    }

    # close any residual position at the last bar
    if pos is not None:
        close_position(pos, c[n - 1], n - 1, "end_of_data")

    trades_df = pd.DataFrame(trades)
    return {"trades": trades_df, "final_equity": equity, "initial_equity": initial_equity}
