"""lb_adversarial.py — adversarial validation of the London Breakout.

Premise: assume the +0.13R GBPUSD / +0.075R pooled result is a false positive
and try to prove it. Ten attacks: walk-forward, parameter sensitivity, rule
ablations, cost stress, 10k Monte Carlo, timeframes, pair split, recency,
failure modes, and a combined worst-case survival test.

    cd research && box_theory/.venv/bin/python -m box_theory.lb_adversarial
"""
from __future__ import annotations

import os
import json
import datetime as dt

import numpy as np
import pandas as pd
from scipy import stats

from . import config
from .london_breakout import run

RES = os.path.join(os.path.dirname(__file__), "results")
PAIRS = ["GBPUSD", "EURUSD", "USDJPY", "AUDUSD", "USDCAD", "XAUUSD"]


def stat(r):
    r = np.asarray(r, dtype=float)
    if len(r) == 0:
        return {"n": 0, "exp": np.nan, "p": np.nan, "wr": np.nan, "pf": np.nan}
    t, p2 = stats.ttest_1samp(r, 0)
    p1 = p2 / 2 if t > 0 else 1 - p2 / 2
    gp = r[r > 0].sum(); gl = -r[r < 0].sum()
    return {"n": len(r), "exp": float(r.mean()), "p": float(p1),
            "wr": 100 * (r > 0).mean(), "pf": gp / gl if gl > 0 else float("inf")}


def pooled(tf="M15", pairs=PAIRS, **kw):
    parts = []
    for s in pairs:
        tr, _, _ = run(s, tf, "opposite", True, **kw)
        if len(tr):
            tr = tr.copy(); tr["day"] = pd.to_datetime(tr.day); tr["sym"] = s
            parts.append(tr)
    if not parts:
        return pd.DataFrame()
    return pd.concat(parts).sort_values("day").reset_index(drop=True)


def line(ch="="):
    print(ch * 104)


def main():
    out = {}
    base = pooled()                      # 6 pairs, M15, faithful params
    base_ge = base[base.sym.isin(["GBPUSD", "EURUSD"])]
    b_all = stat(base.r_net); b_ge = stat(base_ge.r_net)
    line()
    print("BASELINE (M15 24mo, faithful spec):  pooled-6 exp=%+.4f n=%d p=%.4f | GBP+EUR exp=%+.4f n=%d p=%.4f" % (
        b_all["exp"], b_all["n"], b_all["p"], b_ge["exp"], b_ge["n"], b_ge["p"]))
    out["baseline"] = {"all": b_all, "ge": b_ge}

    # ── 1. walk-forward ──
    line()
    print("1) WALK-FORWARD (fixed rules — temporal stability; plus param-fit Y1->Y2)")
    line()
    mid = base.day.min() + (base.day.max() - base.day.min()) / 2
    for name, seg in [("Year1 -> test Year2", base[base.day >= mid]),
                      ("Year2 -> test Year1 (reversed)", base[base.day < mid])]:
        s = stat(seg.r_net)
        print("  %-32s exp=%+.4f n=%d p=%.4f" % (name, s["exp"], s["n"], s["p"]))
    # rolling 6-month windows
    t0, t1 = base.day.min(), base.day.max()
    w = pd.Timedelta(days=182)
    print("  rolling 6-month windows (pooled 6):")
    roll = []
    cur = t0
    while cur + w <= t1 + pd.Timedelta(days=1):
        seg = base[(base.day >= cur) & (base.day < cur + w)]
        s = stat(seg.r_net)
        roll.append(s["exp"])
        print("    %s..%s  exp=%+.4f n=%d" % (cur.date(), (cur + w).date(), s["exp"], s["n"]))
        cur += pd.Timedelta(days=91)
    out["rolling"] = roll
    # mini param-fit walk-forward: choose best RR on Y1, apply to Y2 (GBP+EUR)
    rrs = [1.5, 2.0, 2.5, 3.0]
    y1best, y1val = None, -9
    for rr in rrs:
        T = pooled(pairs=["GBPUSD", "EURUSD"], tp_r=rr)
        s = stat(T[T.day < mid].r_net)
        if s["exp"] > y1val:
            y1best, y1val = rr, s["exp"]
    T = pooled(pairs=["GBPUSD", "EURUSD"], tp_r=y1best)
    s2 = stat(T[T.day >= mid].r_net)
    print("  param-fit WF: best RR on Y1 = 1:%.1f (exp %+.3f) -> Y2 exp=%+.4f n=%d p=%.4f" % (
        y1best, y1val, s2["exp"], s2["n"], s2["p"]))
    out["wf_paramfit"] = {"rr": y1best, "y2": s2}

    # ── 2. parameter sensitivity (GBP+EUR pooled, one-at-a-time) ──
    line()
    print("2) PARAMETER SENSITIVITY (GBP+EUR, M15; vary one, hold rest)")
    line()
    grids = {
        "asia_end (UK 06/07/08)": [("06:00", {"asia_end": 8}), ("07:00*", {}), ("08:00", {"asia_end": 10})],
        "win_end (UK 10/11/12)": [("10:00", {"win_end": 12}), ("11:00*", {}), ("12:00", {"win_end": 14})],
        "stop offset (pips)": [("1", {"pad_pips": 1.0}), ("1.5*", {}), ("2", {"pad_pips": 2.0}), ("3", {"pad_pips": 3.0})],
        "expiry (min)": [("15", {"expiry_min": 15}), ("30*", {}), ("45", {"expiry_min": 45}), ("60", {"expiry_min": 60})],
        "RR": [("1.5", {"tp_r": 1.5}), ("2.0*", {}), ("2.5", {"tp_r": 2.5}), ("3.0", {"tp_r": 3.0})],
    }
    out["sensitivity"] = {}
    for gname, cells in grids.items():
        vals = []
        for lab, kw in cells:
            T = pooled(pairs=["GBPUSD", "EURUSD"], **kw)
            s = stat(T.r_net)
            vals.append((lab, s["exp"], s["n"]))
        cellstr = "  ".join("%s: %+.3f(n=%d)" % v for v in vals)
        allpos = all(v[1] > 0 for v in vals)
        print("  %-24s %s  -> %s" % (gname, cellstr, "ALL POSITIVE" if allpos else "SIGN FLIPS"))
        out["sensitivity"][gname] = vals

    # ── 3. rule ablations ──
    line()
    print("3) RULE ABLATIONS (GBP+EUR, M15; remove one rule at a time)")
    line()
    ablations = [
        ("faithful (all rules)*", {}),
        ("no body-close (wick pierce ok)", {"body_close": False}),
        ("no stop-order (market @ close)", {"use_stop_order": False}),
        ("no 30-min expiry", {"use_expiry": False}),
        ("no ADR filter", {"adr_filter": False}),
    ]
    out["ablation"] = {}
    for lab, kw in ablations:
        adrf = kw.pop("adr_filter", True)
        parts = []
        for s_ in ["GBPUSD", "EURUSD"]:
            tr, _, _ = run(s_, "M15", "opposite", adrf, **kw)
            parts.append(tr)
        T = pd.concat(parts)
        s = stat(T.r_net)
        print("  %-34s exp=%+.4f n=%4d WR=%.1f%% PF=%.2f p=%.4f" % (
            lab, s["exp"], s["n"], s["wr"], s["pf"], s["p"]))
        out["ablation"][lab] = s
    print("  (spread filter: inert on Raw pricing — never triggered; ablation is a no-op)")

    # ── 4. cost stress ──
    line()
    print("4) COST STRESS (GBP+EUR and pooled-6, M15)")
    line()
    out["cost"] = {}
    for lab, kw in [("baseline*", {}), ("cost +25%", {"cost_mult": 1.25}),
                    ("cost +50%", {"cost_mult": 1.5}), ("cost +100%", {"cost_mult": 2.0}),
                    ("slippage +1 pip", {"slip_pips": 1.0}), ("slippage +2 pips", {"slip_pips": 2.0}),
                    ("slippage +3 pips", {"slip_pips": 3.0})]:
        ge = stat(pooled(pairs=["GBPUSD", "EURUSD"], **kw).r_net)
        al = stat(pooled(**kw).r_net)
        print("  %-18s GBP+EUR exp=%+.4f p=%.3f | pooled-6 exp=%+.4f p=%.3f" % (
            lab, ge["exp"], ge["p"], al["exp"], al["p"]))
        out["cost"][lab] = {"ge": ge, "all": al}

    # ── 5. Monte Carlo 10k ──
    line()
    print("5) MONTE CARLO (10,000 bootstrap resamples, 1% risk)")
    line()
    rng = np.random.default_rng(9)
    out["mc"] = {}
    for lab, T in [("GBP+EUR", base_ge), ("pooled-6", base)]:
        r = T.r_net.to_numpy()
        nets = np.empty(10000); dds = np.empty(10000)
        for s_ in range(10000):
            samp = rng.choice(r, len(r), replace=True)
            eq = 400.0; pk = eq; mdd = 0.0
            for x in samp:
                eq *= 1 + x * 0.01; pk = max(pk, eq); mdd = max(mdd, (pk - eq) / pk)
            nets[s_] = 100 * (eq / 400 - 1); dds[s_] = 100 * mdd
        print("  %-9s P(profit)=%5.1f%%  net median=%+.0f%% [p5 %+.0f%%, p95 %+.0f%%]  maxDD median=%.1f%% p95=%.1f%%" % (
            lab, 100 * (nets > 0).mean(), np.median(nets), np.percentile(nets, 5),
            np.percentile(nets, 95), np.median(dds), np.percentile(dds, 95)))
        out["mc"][lab] = {"p_profit": float(100 * (nets > 0).mean()),
                          "net_med": float(np.median(nets)), "net_p5": float(np.percentile(nets, 5)),
                          "dd_med": float(np.median(dds)), "dd_p95": float(np.percentile(dds, 95))}

    # ── 6. timeframes ──
    line()
    print("6) TIMEFRAMES (pooled-6, faithful params; expiry 30min in bars per TF)")
    line()
    out["tf"] = {}
    for tf, note in [("M5", "~8mo only"), ("M15", "24mo"), ("M30", "24mo")]:
        s = stat(pooled(tf=tf).r_net)
        print("  %-4s (%s): exp=%+.4f n=%d WR=%.1f%% PF=%.2f p=%.4f" % (
            tf, note, s["exp"], s["n"], s["wr"], s["pf"], s["p"]))
        out["tf"][tf] = s

    # ── 7. pairs ── (from baseline)
    line()
    print("7) PAIR VALIDATION (M15 24mo)")
    line()
    for sym in ["GBPUSD", "EURUSD"]:
        s = stat(base[base.sym == sym].r_net)
        print("  %-8s exp=%+.4f n=%d p=%.4f" % (sym, s["exp"], s["n"], s["p"]))
    print("  GBP+EUR  exp=%+.4f n=%d p=%.4f" % (b_ge["exp"], b_ge["n"], b_ge["p"]))

    # ── 8. recent 6 months ──
    line()
    print("8) RECENT 6 MONTHS")
    line()
    cut6 = base.day.max() - pd.Timedelta(days=182)
    out["recent"] = {}
    for lab, T in [("GBP+EUR", base_ge), ("pooled-6", base)]:
        s = stat(T[T.day >= cut6].r_net)
        print("  %-9s last-6mo exp=%+.4f n=%d p=%.4f" % (lab, s["exp"], s["n"], s["p"]))
        out["recent"][lab] = s
    for sym in PAIRS:
        s = stat(base[(base.sym == sym) & (base.day >= cut6)].r_net)
        if s["n"]:
            print("    %-8s exp=%+.4f n=%d" % (sym, s["exp"], s["n"]))

    # ── 9. failure modes ──
    line()
    print("9) FAILURE MODES (GBP+EUR, M15; why losers lose)")
    line()
    L = base_ge[base_ge.r_net < 0]; W = base_ge[base_ge.r_net > 0]
    print("  losers=%d winners=%d" % (len(L), len(W)))
    print("  loser exit reasons: %s" % L.reason.value_counts().to_dict())
    print("  losers that NEVER went favorable (MFE<0.25R): %.0f%%   went >1R first then died: %.0f%%" % (
        100 * (L.mfe_r < 0.25).mean(), 100 * (L.mfe_r >= 1.0).mean()))
    print("  median r_unit (stop distance): losers %.1f pips vs winners %.1f pips" % (
        L.r_unit_pips.median(), W.r_unit_pips.median()))
    print("  median asian range: losers %.1f pips vs winners %.1f" % (
        L.asian_range_pips.median(), W.asian_range_pips.median()))
    print("  loss rate by fill hour(server): %s" % {
        int(h): round(float((base_ge[base_ge.fill_hour == h].r_net < 0).mean()), 2)
        for h in sorted(base_ge.fill_hour.unique())})
    print("  side split: buy exp=%+.3f (n=%d) | sell exp=%+.3f (n=%d)" % (
        base_ge[base_ge.side == "buy"].r_net.mean(), (base_ge.side == "buy").sum(),
        base_ge[base_ge.side == "sell"].r_net.mean(), (base_ge.side == "sell").sum()))

    # ── 10. combined survival test ──
    line()
    print("10) SURVIVAL — all realistic stresses SIMULTANEOUSLY:")
    print("    cost +50%, slippage +1 pip, most recent 12 months only, pooled-6 and GBP+EUR")
    line()
    Ts = pooled(cost_mult=1.5, slip_pips=1.0)
    cut12 = Ts.day.max() - pd.Timedelta(days=365)
    out["survival"] = {}
    for lab, T in [("pooled-6", Ts[Ts.day >= cut12]),
                   ("GBP+EUR", Ts[(Ts.day >= cut12) & (Ts.sym.isin(["GBPUSD", "EURUSD"]))])]:
        s = stat(T.r_net)
        print("  %-9s exp=%+.4f n=%d WR=%.1f%% PF=%.2f p=%.4f" % (
            lab, s["exp"], s["n"], s["wr"], s["pf"], s["p"]))
        out["survival"][lab] = s

    with open(os.path.join(RES, "lb_adversarial.json"), "w") as f:
        json.dump(out, f, indent=2, default=float)
    print("\nSaved results/lb_adversarial.json")


if __name__ == "__main__":
    main()
