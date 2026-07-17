#!/usr/bin/env python3
"""lb_reportgen.py — daily/weekly forward-test reports + pre-registered verdict.

Runs on the HOST venv (pandas available):

    cd research && box_theory/.venv/bin/python forward_test/lb_reportgen.py daily|weekly|status
"""
from __future__ import annotations

import json
import os
import sys
import datetime as dt

import numpy as np
import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
REPORTS = os.path.join(BASE, "reports")

# ── pre-registered baselines & criteria (FROZEN — do not edit mid-test) ──
BACKTEST = {"exp": 0.1246, "wr": 49.1, "pf": 1.30, "n": 582,
            "label": "GBP+EUR M15 24mo backtest (costs incl.)"}
STRESSED = {"exp": 0.017, "label": "recent-12mo + cost+50% + slip 1p (adversarial)"}
CRITERIA = {
    "min_trades": 60, "min_months": 3.0,
    "alive_pf": 1.10, "alive_exp": 0.05, "slip_assumption_pips": 1.0,
    "reject_exp": 0.0, "reject_pf": 1.00, "slip_reject_pips": 2.0,
}
START_DATE = "2026-07-17"


def load():
    tpath = os.path.join(DATA, "trades.csv")
    T = pd.read_csv(tpath, parse_dates=["signal_time", "entry_time", "exit_time"]) \
        if os.path.exists(tpath) else pd.DataFrame()
    ev = []
    epath = os.path.join(DATA, "events.jsonl")
    if os.path.exists(epath):
        with open(epath) as f:
            ev = [json.loads(x) for x in f if x.strip()]
    E = pd.DataFrame(ev)
    if len(E):
        E["t_utc"] = pd.to_datetime(E["t_utc"])
    return T, E


def metrics(T):
    if len(T) == 0:
        return {"n": 0}
    r = T.r_multiple.to_numpy(dtype=float)
    gp = r[r > 0].sum(); gl = -r[r < 0].sum()
    eq = 1.0; peak = 1.0; mdd = 0.0
    for x in r:
        eq *= (1 + 0.01 * x); peak = max(peak, eq); mdd = max(mdd, (peak - eq) / peak)
    months = max(0.03, (dt.date.today() - dt.date.fromisoformat(START_DATE)).days / 30.44)
    return {"n": len(r), "wr": 100 * (r > 0).mean(),
            "pf": gp / gl if gl > 0 else float("inf"),
            "exp": float(r.mean()), "mdd": 100 * mdd, "months": months,
            "slip": float(T.slippage_pips.abs().mean()),
            "slip20": float(T.slippage_pips.abs().tail(20).mean()),
            "spread": float(T.spread_signal_pips.mean()),
            "big_win": float(r.max()), "big_loss": float(r.min())}


def verdict(m):
    if m["n"] == 0:
        return "RUNNING", ["no trades yet"]
    notes = []
    if m["n"] >= CRITERIA["min_trades"]:
        if m["exp"] <= CRITERIA["reject_exp"]:
            return "REJECT", ["expectancy <= 0 after %d trades" % m["n"]]
        if m["pf"] < CRITERIA["reject_pf"]:
            return "REJECT", ["profit factor < 1.00 after %d trades" % m["n"]]
    if m["n"] >= 20 and m["slip20"] > CRITERIA["slip_reject_pips"]:
        return "REJECT", ["rolling-20 |slippage| %.2fp exceeds %.1fp" % (
            m["slip20"], CRITERIA["slip_reject_pips"])]
    alive = (m["n"] >= CRITERIA["min_trades"] and m["months"] >= CRITERIA["min_months"]
             and m["pf"] > CRITERIA["alive_pf"] and m["exp"] > CRITERIA["alive_exp"]
             and m["slip"] <= CRITERIA["slip_assumption_pips"])
    if alive:
        return "ALIVE (GO)", ["all pre-registered success criteria met"]
    need = []
    if m["n"] < CRITERIA["min_trades"]:
        need.append("trades %d/%d" % (m["n"], CRITERIA["min_trades"]))
    if m["months"] < CRITERIA["min_months"]:
        need.append("months %.1f/%.1f" % (m["months"], CRITERIA["min_months"]))
    if m["pf"] <= CRITERIA["alive_pf"]:
        need.append("PF %.2f<=%.2f" % (m["pf"], CRITERIA["alive_pf"]))
    if m["exp"] <= CRITERIA["alive_exp"]:
        need.append("exp %+.3f<=%.2f" % (m["exp"], CRITERIA["alive_exp"]))
    if m["slip"] > CRITERIA["slip_assumption_pips"]:
        need.append("|slip| %.2f>%.1fp" % (m["slip"], CRITERIA["slip_assumption_pips"]))
    return "RUNNING", need


def table(m):
    if m["n"] == 0:
        return "_No completed trades yet._\n"
    rows = [
        ("Completed trades", "%d" % m["n"], "%d" % BACKTEST["n"]),
        ("Win rate", "%.1f%%" % m["wr"], "%.1f%%" % BACKTEST["wr"]),
        ("Profit factor", "%.2f" % m["pf"], "%.2f" % BACKTEST["pf"]),
        ("Expectancy (R)", "%+.3f" % m["exp"], "%+.3f" % BACKTEST["exp"]),
        ("Max drawdown (1%% risk)", "%.1f%%" % m["mdd"], "8.0%"),
        ("Avg |slippage|", "%.2f pips" % m["slip"], "<= %.1f pips (assumed)" % CRITERIA["slip_assumption_pips"]),
        ("Avg spread @ signal", "%.2f pips" % m["spread"], "< 2.0 pips (gate)"),
        ("Largest win / loss (R)", "%+.2f / %+.2f" % (m["big_win"], m["big_loss"]), "—"),
    ]
    out = "| Metric | Forward | Backtest baseline |\n|---|---:|---:|\n"
    for a, b, c in rows:
        out += "| %s | %s | %s |\n" % (a, b, c)
    return out


def day_funnel(E, day):
    if len(E) == 0:
        return {}
    D = E[E.t_utc.dt.strftime("%Y-%m-%d") == day]
    def n(kind):
        return int((D.event == kind).sum())
    return {"ranges_set": n("range_set"), "orders_placed": n("order_placed"),
            "filled": n("filled"), "expired": n("order_expired_or_cancelled"),
            "cancelled": n("order_cancelled"), "spread_blocks": n("spread_block"),
            "closed": n("trade_closed"), "errors": n("tick_ERROR")}


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "status"
    os.makedirs(REPORTS, exist_ok=True)
    T, E = load()
    m = metrics(T)
    v, notes = verdict(m)
    today = dt.date.today().isoformat()

    md = ["# London Breakout forward test — %s report (%s)\n" % (mode, today)]
    md.append("**Status: %s** — %s\n" % (v, "; ".join(notes)))
    md.append("Experiment start: %s · pre-registered criteria: >=%d trades, >=%.0f months, "
              "PF>%.2f, exp>%+0.2fR, |slip|<=%.1fp. Reject: exp<=0 or PF<1.00 after %d trades, "
              "or rolling-20 |slip|>%.1fp.\n" % (
                  START_DATE, CRITERIA["min_trades"], CRITERIA["min_months"],
                  CRITERIA["alive_pf"], CRITERIA["alive_exp"], CRITERIA["slip_assumption_pips"],
                  CRITERIA["min_trades"], CRITERIA["slip_reject_pips"]))
    md.append("\n## Cumulative performance vs backtest\n")
    md.append(table(m))
    md.append("\n_Reference:_ adversarial stressed expectation (recent regime + costs): "
              "%+.3fR — forward result landing between this and the backtest %+.3fR is "
              "consistent with history.\n" % (STRESSED["exp"], BACKTEST["exp"]))

    if mode == "daily":
        md.append("\n## Today's funnel\n")
        f = day_funnel(E, today)
        md.append("```\n%s\n```\n" % json.dumps(f, indent=1))
        if len(T):
            td = T[T.exit_time.dt.strftime("%Y-%m-%d") == today]
            if len(td):
                md.append("\n## Trades closed today\n")
                md.append(td[["pair", "direction", "entry_time", "exit_time", "r_multiple",
                              "slippage_pips", "exit_reason"]].to_markdown(index=False))
        out = os.path.join(REPORTS, "daily_%s.md" % today)
    elif mode == "weekly":
        md.append("\n## This week's trades\n")
        if len(T):
            wk = T[T.exit_time >= pd.Timestamp(dt.date.today() - dt.timedelta(days=7))]
            mw = metrics(wk) if len(wk) else {"n": 0}
            if mw["n"]:
                md.append("n=%d  WR=%.1f%%  exp=%+.3fR  PF=%.2f  avg|slip|=%.2fp\n" % (
                    mw["n"], mw["wr"], mw["exp"], mw["pf"], mw["slip"]))
                md.append(wk[["pair", "date", "direction", "r_multiple", "slippage_pips",
                              "spread_signal_pips", "exit_reason"]].to_markdown(index=False))
            else:
                md.append("_No trades closed this week._")
        out = os.path.join(REPORTS, "weekly_%s.md" % today)
    else:
        out = os.path.join(REPORTS, "STATUS.md")

    with open(out, "w") as f:
        f.write("\n".join(md))
    # always refresh STATUS.md too
    with open(os.path.join(REPORTS, "STATUS.md"), "w") as f:
        f.write("\n".join(md))
    print("wrote %s  [status: %s]" % (out, v))


if __name__ == "__main__":
    main()
