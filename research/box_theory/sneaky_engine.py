"""sneaky_engine.py — event-driven backtest for the "Sneaky Pivot" strategy.

Differences vs the generic box engine:
  * FOUR levels (range high/low + swing high/low) via levels.py.
  * Entry = John Wick "sneaky" pivot AT a level, then enter on the break of that
    pivot candle's high (buy) / low (sell) — "one candle crosses another".
  * Timing: 'opening' restricts the pivot to the first `open_window_bars` bars
    AT/AFTER a session open (the opening-range / sneaky / entry 3-candle
    sequence); 'allday' allows it any time. The open is set by `open_anchor_hour`
    (UTC): None = broker-day rollover (first bar of the server day), 7 = London,
    13 = New York. FX is 24h, so the "daily open" the strategy assumes is a real
    session open, not the broker rollover. One trade per day.
  * Target = opposite side of the range (range-bound), or midpoint, or fixed RR.
  * Stop just beyond the pivot wick (the "big buyer/seller"). Same R accounting,
    costs, no-lookahead, SL-first ties, compounding equity as engine.py.

params:
  swing_def      : 'literal' | 'fractal'   (chosen when building levels)
  levels_used    : 'range' | 'swing' | 'both'
  entry_model    : 'opening' | 'allday'
  open_window_bars: int (opening only; default 6)
  entry_valid_bars: int  break must occur within this many bars of the pivot
  wick           : 'A'|'B'|'C'|'D'
  buffer_pct     : band around each level, fraction of range height H
  mid_band_pct   : no-trade zone around range midpoint
  target         : ('opposite',None) | ('midpoint',None) | ('rr',R)
  sl_buffer_pct  : stop beyond pivot wick, fraction of H
  risk_pct       : 0.01 / 0.005
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import config
from .wick import is_john_wick


def run_sneaky(df: pd.DataFrame, levels: dict, symbol: str, params: dict,
               initial_equity: float = config.DEFAULT_EQUITY) -> dict:
    spec = config.SYMBOL_SPEC[symbol]
    cost = spec["cost_price"]

    levels_used = params.get("levels_used", "both")
    entry_model = params.get("entry_model", "opening")
    open_window = params.get("open_window_bars", 6)
    open_anchor_hour = params.get("open_anchor_hour")  # None|7(London)|13(NY), UTC
    entry_valid = params.get("entry_valid_bars", 4)
    wick = params["wick"]
    buf = params["buffer_pct"]
    midband = params.get("mid_band_pct", config.MID_BAND_PCT)
    target_kind, target_val = params["target"]
    sl_buf = params.get("sl_buffer_pct", config.SL_BUFFER_PCT)
    risk_pct = params["risk_pct"]

    o = df["open"].to_numpy(); h = df["high"].to_numpy()
    l = df["low"].to_numpy(); c = df["close"].to_numpy()
    tday = df["trading_day"].tolist()
    sess = df["session"].tolist()
    ttime = df["time_utc"].tolist()
    n = len(df)

    # Precompute which bars may FORM a new pivot (pending resolution is not gated).
    if entry_model == "allday":
        open_ok = [True] * n
    else:
        open_ok = [False] * n
        i = 0
        while i < n:
            d = tday[i]
            j = i
            while j < n and tday[j] == d:
                j += 1
            if open_anchor_hour is None:
                a = i                                   # broker-day rollover
            else:
                # The server trading day starts near 21:00-22:00 UTC and wraps past
                # midnight, so anchor hours (07 London / 13 NY) fall AFTER the wrap.
                # First bar with anchor_hour <= hour < day-start-hour is that open.
                h0 = ttime[i].hour
                a = next((k for k in range(i, j)
                          if open_anchor_hour <= ttime[k].hour < h0), i)
            for k in range(a, min(a + open_window, j)):
                open_ok[k] = True
            i = j

    equity = initial_equity
    trades = []
    pos = None
    pending = None
    day = None
    bar_in_day = -1
    traded_today = False

    def buy_sell_levels(lv):
        buys, sells = [], []
        if levels_used in ("range", "both"):
            buys.append(lv.rl); sells.append(lv.rh)
        if levels_used in ("swing", "both"):
            if lv.sl is not None:
                buys.append(lv.sl)
            if lv.sh is not None:
                sells.append(lv.sh)
        return buys, sells

    def make_target(side, entry, lv):
        d = 1.0 if side == "buy" else -1.0
        if target_kind == "opposite":
            return lv.rh if side == "buy" else lv.rl
        if target_kind == "midpoint":
            return lv.mid
        if target_kind == "rr":
            return None  # computed from r_unit at open
        raise ValueError("bad target %s" % target_kind)

    def open_pos(side, entry_price, rej_low, rej_high, lv, idx):
        d = 1.0 if side == "buy" else -1.0
        sl = (rej_low - sl_buf * lv.height) if side == "buy" else (rej_high + sl_buf * lv.height)
        r_unit = abs(entry_price - sl)
        if r_unit <= 0:
            return None
        if target_kind == "rr":
            tp = entry_price + d * target_val * r_unit
        else:
            tp = make_target(side, entry_price, lv)
            if (tp - entry_price) * d <= 0:   # target must be beyond entry in trade direction
                return None
        return {"side": side, "dir": d, "entry": entry_price, "sl": sl, "tp": tp,
                "r_unit": r_unit, "idx": idx, "etime": ttime[idx], "lv": lv,
                "day": tday[idx], "sess": sess[idx]}

    def close_pos(p, exit_price, idx, reason):
        nonlocal equity
        gross = (exit_price - p["entry"]) * p["dir"]
        net = gross - cost
        r_net = net / p["r_unit"]
        ret = r_net * risk_pct
        eb = equity
        pnl = ret * eb
        equity = eb + pnl
        trades.append({
            "symbol": symbol, "side": p["side"], "entry_time": p["etime"],
            "exit_time": ttime[idx], "entry": p["entry"], "exit": exit_price,
            "sl": p["sl"], "tp": p["tp"], "r_unit": p["r_unit"], "r_net": r_net,
            "ret_frac": ret, "pnl_usd": pnl, "equity_before": eb, "equity_after": equity,
            "duration_min": (ttime[idx] - p["etime"]).total_seconds() / 60.0,
            "exit_reason": reason, "entry_session": p["sess"], "trading_day": p["day"],
            "entry_idx": p["idx"], "exit_idx": idx,
            "level_type": p.get("ltype"), "level_price": p.get("lvl_price"),
            "bar_in_day": p.get("bar_in_day"),
            "box_height": p["lv"].height, "mid": p["lv"].mid,
            "rh": p["lv"].rh, "rl": p["lv"].rl, "sh": p["lv"].sh, "sl_lvl": p["lv"].sl,
        })

    for i in range(n):
        d_i = tday[i]
        if d_i != day:                     # new trading day
            if pos is not None:            # EOD flat at new-day open
                close_pos(pos, o[i], i, "time_exit_eod")
                pos = None
            day = d_i
            bar_in_day = 0
            traded_today = False
            pending = None
        else:
            bar_in_day += 1

        lv = levels.get(d_i)
        if lv is None:
            continue
        H = lv.height

        # manage open position (bar after entry)
        if pos is not None and i > pos["idx"]:
            side = pos["side"]
            hit_sl = (l[i] <= pos["sl"]) if side == "buy" else (h[i] >= pos["sl"])
            hit_tp = (h[i] >= pos["tp"]) if side == "buy" else (l[i] <= pos["tp"])
            if hit_sl:
                close_pos(pos, pos["sl"], i, "stop_loss"); pos = None
            elif hit_tp:
                close_pos(pos, pos["tp"], i, "take_profit"); pos = None

        # resolve pending pivot -> break entry
        if pos is None and pending is not None:
            side = pending["side"]
            trig = pending["trigger"]
            broke = (h[i] >= trig) if side == "buy" else (l[i] <= trig)
            if broke:
                p = open_pos(side, trig, pending["rej_low"], pending["rej_high"], lv, i)
                if p is not None:
                    p["ltype"] = pending["ltype"]; p["lvl_price"] = pending["lvl_price"]
                    p["bar_in_day"] = pending["bar_in_day"]
                    pos = p; traded_today = True
                pending = None
            else:
                pending["bars"] -= 1
                if pending["bars"] <= 0:
                    pending = None

        # look for a new pivot
        if pos is None and pending is None and not traded_today:
            if open_ok[i]:
                buys, sells = buy_sell_levels(lv)
                side = None
                lvl_price = None
                # nearest buy level reached?
                for L in buys:
                    if l[i] <= L + buf * H and l[i] >= L - 4 * buf * H:
                        side = "buy"; lvl_price = L; break
                if side is None:
                    for L in sells:
                        if h[i] >= L - buf * H and h[i] <= L + 4 * buf * H:
                            side = "sell"; lvl_price = L; break
                if side is not None:
                    # not in the noise zone around the range midpoint
                    if abs(c[i] - lv.mid) >= midband * H:
                        if is_john_wick(o[i], h[i], l[i], c[i], side, wick, pdl=lv.rl, pdh=lv.rh):
                            ltype = "range" if lvl_price in (lv.rl, lv.rh) else "swing"
                            pending = {"side": side,
                                       "trigger": h[i] if side == "buy" else l[i],
                                       "rej_low": l[i], "rej_high": h[i], "bars": entry_valid,
                                       "lvl_price": lvl_price, "ltype": ltype,
                                       "bar_in_day": bar_in_day}

    if pos is not None:
        close_pos(pos, c[n - 1], n - 1, "end_of_data")

    return {"trades": pd.DataFrame(trades), "final_equity": equity, "initial_equity": initial_equity}
