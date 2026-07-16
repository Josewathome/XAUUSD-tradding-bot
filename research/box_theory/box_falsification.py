"""box_falsification.py — falsification test of the EXACT original Box Theory.

Rules under test (nothing else):
  * Box = Previous Day High / Previous Day Low / midpoint (no swing levels)
  * John Wick rejection candle at the extreme (faithful def A: wick >= 2x body,
    per the user's own "at least 2-3x" definition; B tested in the ablation)
  * No trades in the middle 30% of the box (mid_band_pct = 0.15 -> +/-15%)
  * Enter at the close of the rejection candle (the transcript's "hit the button
    when the John Wick appears")
  * One trade per box, flat at end of day; exits: RR 1:2 / midpoint / opposite side

Per pair (M15, 24 months): n, WR, PF, expectancy, maxDD, one-sided t-test p
(H0: mean r_net <= 0) and bootstrap 95% CI of expectancy. Then:
  Q1 per-pair significance (with Bonferroni note)
  Q2 Gold vs pooled forex (two-sample Welch t)
  Q3 session dependence (expR by entry session)
  Q4 midpoint-rule ablation (mid_band 0.15 vs 0.00)
  Q5 wick ablation (A vs B vs ANY=every touch)

    cd research && box_theory/.venv/bin/python -m box_theory.box_falsification
"""
from __future__ import annotations

import os
import json

import numpy as np
import pandas as pd
from scipy import stats

from . import config
from .load import load_ohlc, load_d1
from .boxes import build_boxes
from .engine import run_backtest

RES = os.path.join(os.path.dirname(__file__), "results")
TF = "M15"
MONTHS = 24
EXITS = [("rr", 2.0), ("midpoint", None), ("opposite", None)]
BASE = {"entry_method": 1, "buffer_pct": 0.10, "mid_band_pct": 0.15,
        "risk_pct": 0.01, "one_trade_per_box": True, "hold_to_eod": True}


def run_cfg(sym, df, boxes, wick, exit_model, mid_band=0.15):
    p = dict(BASE, wick=wick, exit=exit_model, mid_band_pct=mid_band)
    return run_backtest(df, boxes, sym, p, initial_equity=config.DEFAULT_EQUITY)["trades"]


def summarize(tr, risk=0.01):
    r = tr["r_net"].to_numpy() if len(tr) else np.array([])
    if len(r) == 0:
        return {"n": 0}
    eq = config.DEFAULT_EQUITY; peak = eq; mdd = 0.0
    for x in r:
        eq *= (1 + x * risk); peak = max(peak, eq); mdd = max(mdd, (peak - eq) / peak)
    gp = r[r > 0].sum(); gl = -r[r < 0].sum()
    t, p2 = stats.ttest_1samp(r, 0.0)
    p1 = p2 / 2 if t > 0 else 1 - p2 / 2          # one-sided H1: mean > 0
    rng = np.random.default_rng(3)
    boots = [rng.choice(r, len(r), replace=True).mean() for _ in range(4000)]
    lo, hi = np.percentile(boots, [2.5, 97.5])
    return {"n": len(r), "wr": 100 * (r > 0).mean(),
            "pf": gp / gl if gl > 0 else float("inf"),
            "exp": float(r.mean()), "dd": 100 * mdd,
            "p_one_sided": float(p1), "ci_lo": float(lo), "ci_hi": float(hi)}


def main():
    data = {s: (load_ohlc(s, TF, months=MONTHS), build_boxes(load_d1(s))) for s in config.SYMBOLS}
    out = {"per_pair": {}, "sessions": {}, "ablation_mid": {}, "ablation_wick": {}}

    print("=" * 100)
    print("EXACT BOX THEORY — PDH/PDL/mid only, wick A (>=2x), entry at rejection close,")
    print("middle-30%% no-trade zone, one trade/box, EOD flat. M15, 24 months.")
    print("=" * 100)
    header = "%-8s %-9s %5s %6s %6s %8s %7s %10s %18s" % (
        "pair", "exit", "n", "WR%", "PF", "expR", "DD%", "p(1-sided)", "95% CI of expR")
    all_r = {}
    for sym in config.SYMBOLS:
        df, boxes = data[sym]
        print(header)
        for ex in EXITS:
            tr = run_cfg(sym, df, boxes, "A", ex)
            m = summarize(tr)
            key = "%s_%s" % (sym, ex[0])
            out["per_pair"][key] = m
            all_r[key] = tr
            sig = " *" if m.get("p_one_sided", 1) < 0.05 else ""
            print("%-8s %-9s %5d %6.1f %6.2f %+8.3f %7.1f %10.3f    [%+.3f, %+.3f]%s" % (
                sym, ex[0], m["n"], m["wr"], m["pf"], m["exp"], m["dd"],
                m["p_one_sided"], m["ci_lo"], m["ci_hi"], sig))
        print("-" * 100)

    # Q2 — Gold vs pooled forex (per exit)
    print("\nQ2 — GOLD vs FOREX (Welch two-sample t on r_net)")
    for ex in EXITS:
        g = all_r["XAUUSD_%s" % ex[0]]["r_net"].to_numpy()
        fx = np.concatenate([all_r["%s_%s" % (s, ex[0])]["r_net"].to_numpy()
                             for s in config.SYMBOLS if s != "XAUUSD"])
        t, p = stats.ttest_ind(g, fx, equal_var=False)
        print("  exit=%-9s gold expR=%+.3f (n=%d)  forex expR=%+.3f (n=%d)  diff p=%.3f" % (
            ex[0], g.mean(), len(g), fx.mean(), len(fx), p))
        out["ablation_mid"]["gold_%s" % ex[0]] = {"gold": float(g.mean()), "fx": float(fx.mean()),
                                                  "p": float(p)}

    # Q3 — sessions (pooled, per exit)
    print("\nQ3 — SESSION DEPENDENCE (pooled across pairs)")
    for ex in EXITS:
        pooled = pd.concat([all_r["%s_%s" % (s, ex[0])] for s in config.SYMBOLS])
        gr = pooled.groupby("entry_session")["r_net"].agg(["mean", "count"])
        cells = "  ".join("%s: %+.3f (n=%d)" % (i, row["mean"], row["count"])
                          for i, row in gr.iterrows())
        print("  exit=%-9s %s" % (ex[0], cells))
        out["sessions"][ex[0]] = {i: {"expR": round(float(row["mean"]), 3), "n": int(row["count"])}
                                  for i, row in gr.iterrows()}

    # Q4 — midpoint-rule ablation (opposite exit; pooled + per pair)
    print("\nQ4 — MIDPOINT-AVOIDANCE ABLATION (exit=opposite, wick A): mid-30%% zone ON vs OFF")
    for sym in config.SYMBOLS:
        df, boxes = data[sym]
        on = summarize(run_cfg(sym, df, boxes, "A", ("opposite", None), mid_band=0.15))
        off = summarize(run_cfg(sym, df, boxes, "A", ("opposite", None), mid_band=0.0))
        out["ablation_mid"][sym] = {"on": on, "off": off}
        print("  %-8s ON : n=%4d expR=%+.3f | OFF: n=%4d expR=%+.3f  -> rule %s" % (
            sym, on["n"], on["exp"], off["n"], off["exp"],
            "helps" if on["exp"] > off["exp"] else "hurts/neutral"))

    # Q5 — wick ablation (A vs B vs ANY; exit=opposite, pooled per pair)
    print("\nQ5 — JOHN WICK ABLATION (exit=opposite): A(>=2x) vs B(>=3x) vs ANY touch")
    for sym in config.SYMBOLS:
        df, boxes = data[sym]
        row = {}
        cells = []
        for wick in ["ANY", "A", "B"]:
            m = summarize(run_cfg(sym, df, boxes, wick, ("opposite", None)))
            row[wick] = m
            cells.append("%s: n=%4d expR=%+.3f" % (wick, m["n"], m["exp"]))
        out["ablation_wick"][sym] = row
        print("  %-8s %s" % (sym, "  ".join(cells)))

    with open(os.path.join(RES, "box_falsification.json"), "w") as f:
        json.dump(out, f, indent=2, default=float)
    print("\nSaved results/box_falsification.json")


if __name__ == "__main__":
    main()
