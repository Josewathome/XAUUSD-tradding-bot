"""london_breakout.py — faithful backtest of the London Breakout strategy.

Spec (user's Phases 1-5), anchored to SERVER time. This broker's server runs
UK+2 under both DST regimes (EET vs UK: +2 summer and winter, modulo the 2-3
week US/EU DST mismatch), so UK 00:00-07:00 = server 02:00-09:00 and the
07:00-11:00 UK window = server 09:00-13:00. This sidesteps the fixed-UTC-offset
pitfall the spec itself warns about.

Rules implemented:
  P1  Asian range = absolute H/L of server 02:00-08:55 M5 bars; day skipped if
      range > 20-day average daily range (ADR filter, ablatable).
  P2  Window server 09:00-12:55. Signal = first candle BODY close outside the
      range (wick-only breaches ignored). Nothing taken after the window.
  P3  Pending stop order 1.5 pips beyond the breakout candle's extreme, valid
      30 minutes (6 M5 bars); cancelled if untriggered, or earlier if any bar
      CLOSES on the opposite side of the Asian range. All pendings die at 13:00.
  P4  Deep pullback (retrace 78.6-88.6% of the breakout leg before trigger):
      trigger tightened to the most recent candle's extreme +/- 1.5 pips.
      Retrace past the opposite side of the range = idea dead. Shallow/healthy
      pullbacks: no action.
  P5  SL = opposite side of Asian range ('opposite') or nearest structural
      swing (12-bar extreme, 'swing'); TP = fixed 2R; spread at signal must be
      < 2 pips; risk 1% (R accounting, compounding); one setup per day; flat at
      end of server day as backstop.

    cd research && box_theory/.venv/bin/python -m box_theory.london_breakout
"""
from __future__ import annotations

import os
import json

import numpy as np
import pandas as pd
from scipy import stats

from . import config

RES = os.path.join(os.path.dirname(__file__), "results")
DATA = os.path.join(os.path.dirname(__file__), "data")

ASIA_H = (2, 9)        # server hours [start, end)
WIN_H = (9, 13)
PIP_PAD = 1.5          # pips beyond breakout candle
ORDER_BARS_M5 = 6      # 30 minutes
MAX_SPREAD_PIPS = 2.0


def load(sym, tf):
    df = pd.read_csv(os.path.join(DATA, "%s_%s.csv" % (sym, tf)))
    df["time_server"] = pd.to_datetime(df["time_server"])
    if "spread" not in df.columns:
        df["spread"] = 0
    df["day"] = df.time_server.dt.date
    df["hour"] = df.time_server.dt.hour
    return df.sort_values("epoch").reset_index(drop=True)


def adr20(sym):
    d1 = pd.read_csv(os.path.join(DATA, "%s_D1.csv" % sym))
    d1["day"] = pd.to_datetime(d1["time_server"]).dt.date
    rng = (d1.high - d1.low).rolling(20, min_periods=5).mean().shift(1)  # known at day open
    return dict(zip(d1.day, rng))


def run(sym, tf="M5", sl_type="opposite", adr_filter=True, tp_r=2.0, risk=0.01):
    df = load(sym, tf)
    spec = config.SYMBOL_SPEC[sym]
    pip = spec["pip_size"]
    cost = spec["cost_price"]
    pad = PIP_PAD * pip
    order_bars = ORDER_BARS_M5 if tf == "M5" else 2
    adr = adr20(sym)
    point = pip / 10.0 if spec["digits"] in (3, 5) else pip  # spread pts -> price

    o = df.open.to_numpy(); h = df.high.to_numpy(); l = df.low.to_numpy()
    c = df.close.to_numpy(); sp = df.spread.to_numpy(dtype=float)
    hours = df.hour.to_numpy()
    days = df.day.to_numpy()

    trades = []
    funnel = {"days": 0, "range_ok": 0, "breakout": 0, "spread_block": 0,
              "filled": 0, "expired": 0, "invalidated": 0}
    equity = config.DEFAULT_EQUITY

    # index bars by day
    i = 0
    n = len(df)
    while i < n:
        d = days[i]
        j = i
        while j < n and days[j] == d:
            j += 1
        idx = np.arange(i, j)
        i = j
        funnel["days"] += 1
        asia = idx[(hours[idx] >= ASIA_H[0]) & (hours[idx] < ASIA_H[1])]
        win = idx[(hours[idx] >= WIN_H[0]) & (hours[idx] < WIN_H[1])]
        if len(asia) < (50 if tf == "M5" else 16) or len(win) < 4:
            continue
        ah = h[asia].max(); al = l[asia].min()
        rng = ah - al
        if rng <= 0:
            continue
        if adr_filter:
            a = adr.get(d)
            if a is None or np.isnan(a) or rng > a:
                continue
        funnel["range_ok"] += 1

        # find first body close outside range within window
        sig = None
        for k in win:
            body_hi = max(o[k], c[k]); body_lo = min(o[k], c[k])
            if c[k] > ah and body_hi > ah:
                sig = (k, "buy"); break
            if c[k] < al and body_lo < al:
                sig = (k, "sell"); break
        if sig is None:
            continue
        funnel["breakout"] += 1
        k0, side = sig
        if sp[k0] * point / pip > MAX_SPREAD_PIPS:
            funnel["spread_block"] += 1
            continue
        dirn = 1.0 if side == "buy" else -1.0
        bo_ext = h[k0] if side == "buy" else l[k0]
        trigger = bo_ext + dirn * pad
        leg = abs(bo_ext - (ah if side == "buy" else al))  # breakout leg for fib

        # pending-order phase
        fill_idx = None
        fill_price = None
        last_win_bar = win[-1]
        kk = k0
        expiry = k0 + order_bars
        dead = None
        while True:
            kk += 1
            if kk >= j or kk > last_win_bar:
                dead = "expired"; break
            if kk > expiry:
                dead = "expired"; break
            # trigger?
            hit = (h[kk] >= trigger) if side == "buy" else (l[kk] <= trigger)
            if hit:
                fill_idx = kk; fill_price = trigger; break
            # opposite-side invalidation
            if (side == "buy" and c[kk] < al) or (side == "sell" and c[kk] > ah):
                dead = "invalidated"; break
            # deep pullback: retrace 78.6-88.6% of the leg -> tighten trigger
            if leg > 0:
                retr = ((bo_ext - l[kk]) / leg) if side == "buy" else ((h[kk] - bo_ext) / leg)
                if 0.786 <= retr <= 0.886:
                    trigger = (h[kk] + pad) if side == "buy" else (l[kk] - pad)
                    expiry = kk + order_bars   # re-arm the 30-min clock on the new order
        if fill_idx is None:
            funnel[dead] += 1
            continue
        funnel["filled"] += 1

        # stop loss
        if sl_type == "opposite":
            slp = al - pad if side == "buy" else ah + pad
        else:  # swing: 12-bar extreme before fill
            w0 = max(0, fill_idx - 12)
            slp = l[w0:fill_idx].min() - pad if side == "buy" else h[w0:fill_idx].max() + pad
        r_unit = abs(fill_price - slp)
        if r_unit <= 0:
            continue
        tp = fill_price + dirn * tp_r * r_unit

        exit_price = None; reason = None
        for m in range(fill_idx, j):
            if m > fill_idx or True:
                hit_sl = (l[m] <= slp) if side == "buy" else (h[m] >= slp)
                hit_tp = (h[m] >= tp) if side == "buy" else (l[m] <= tp)
                if m == fill_idx:
                    # same-bar: pessimistic SL first (we filled intra-bar)
                    if hit_sl:
                        exit_price, reason = slp, "stop_loss"; break
                    if hit_tp:
                        exit_price, reason = tp, "take_profit"; break
                else:
                    if hit_sl:
                        exit_price, reason = slp, "stop_loss"; break
                    if hit_tp:
                        exit_price, reason = tp, "take_profit"; break
        if exit_price is None:
            exit_price, reason = c[j - 1], "eod"
        gross = (exit_price - fill_price) * dirn
        rnet = (gross - cost) / r_unit
        eb = equity
        equity = eb * (1 + rnet * risk)
        trades.append({"symbol": sym, "day": d, "side": side, "entry": fill_price,
                       "sl": slp, "tp": tp, "exit": exit_price, "r_net": rnet,
                       "reason": reason, "equity_after": equity,
                       "asian_range_pips": rng / pip})
    return pd.DataFrame(trades), funnel, equity


def summarize(tr, eq_final, risk=0.01):
    if len(tr) == 0:
        return {"n": 0}
    r = tr.r_net.to_numpy()
    eq = config.DEFAULT_EQUITY; peak = eq; mdd = 0.0
    for x in r:
        eq *= (1 + x * risk); peak = max(peak, eq); mdd = max(mdd, (peak - eq) / peak)
    gp = r[r > 0].sum(); gl = -r[r < 0].sum()
    t, p2 = stats.ttest_1samp(r, 0)
    p1 = p2 / 2 if t > 0 else 1 - p2 / 2
    rng = np.random.default_rng(5)
    boots = [rng.choice(r, len(r), replace=True).mean() for _ in range(3000)]
    lo, hi = np.percentile(boots, [2.5, 97.5])
    return {"n": len(r), "wr": 100 * (r > 0).mean(), "pf": gp / gl if gl > 0 else float("inf"),
            "exp": float(r.mean()), "dd": 100 * mdd,
            "net": 100 * (eq / config.DEFAULT_EQUITY - 1),
            "p1": float(p1), "ci": (float(lo), float(hi))}


def main():
    out = {}
    print("=" * 108)
    print("LONDON BREAKOUT — faithful spec (Asian 00-07 UK, window 07-11 UK, body close, stop order,")
    print("30-min expiry, opposite-side invalidation, deep-pullback tighten, spread<2p, 1%% risk, TP 2R)")
    print("=" * 108)
    hdr = "%-8s %-4s %-9s %-4s %4s %6s %6s %8s %7s %8s %10s %20s" % (
        "pair", "tf", "SL", "ADR", "n", "WR%", "PF", "expR", "DD%", "net%", "p(1-side)", "95% CI expR")
    for tf, months_lab in [("M5", "~8mo"), ("M15", "24mo")]:
        print("\n--- %s (%s) ---" % (tf, months_lab))
        print(hdr)
        for sym in ["GBPUSD", "EURUSD", "USDJPY", "AUDUSD", "USDCAD", "XAUUSD"]:
            for sl_type in ["opposite", "swing"]:
                for adrf in [True, False]:
                    tr, fun, eqf = run(sym, tf, sl_type, adrf)
                    m = summarize(tr, eqf)
                    key = "%s_%s_%s_%s" % (sym, tf, sl_type, "adr" if adrf else "noadr")
                    out[key] = {"m": m, "funnel": fun}
                    if m["n"] == 0:
                        continue
                    star = " *" if m["p1"] < 0.05 else ""
                    print("%-8s %-4s %-9s %-4s %4d %6.1f %6.2f %+8.3f %7.1f %+8.1f %10.3f   [%+.3f, %+.3f]%s" % (
                        sym, tf, sl_type, "on" if adrf else "off", m["n"], m["wr"], m["pf"],
                        m["exp"], m["dd"], m["net"], m["p1"], m["ci"][0], m["ci"][1], star))
        # funnel for the headline config
        tr, fun, _ = run("GBPUSD", tf, "opposite", True)
        print("  GBPUSD funnel (%s, opposite/ADR-on): %s" % (tf, fun))

    with open(os.path.join(RES, "london_breakout.json"), "w") as f:
        json.dump(out, f, indent=2, default=float)
    print("\nSaved results/london_breakout.json")


if __name__ == "__main__":
    main()
