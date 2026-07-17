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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
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
START_DATE = "2026-07-17"          # framework armed (QA tracer trades before trading start)
TRADING_START = "2026-07-20"       # metrics count trades from HERE; accounting counts ALL


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
    # metrics use only official-experiment trades; QA tracer trades (pre
    # TRADING_START) remain in trades.csv for accounting/reconciliation
    if len(T):
        T = T[pd.to_datetime(T.entry_time) >= pd.Timestamp(TRADING_START)]
    if len(T) == 0:
        return {"n": 0}
    r = T.r_multiple.to_numpy(dtype=float)
    gp = r[r > 0].sum(); gl = -r[r < 0].sum()
    eq = 1.0; peak = 1.0; mdd = 0.0
    for x in r:
        eq *= (1 + 0.01 * x); peak = max(peak, eq); mdd = max(mdd, (peak - eq) / peak)
    months = max(0.03, (dt.date.today() - dt.date.fromisoformat(TRADING_START)).days / 30.44)
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
        ("Max drawdown (1% risk)", "%.1f%%" % m["mdd"], "8.0%"),
        ("Avg |slippage|", "%.2f pips" % m["slip"], "<= %.1f pips (assumed)" % CRITERIA["slip_assumption_pips"]),
        ("Avg spread @ signal", "%.2f pips" % m["spread"], "< 2.0 pips (gate)"),
        ("Largest win / loss (R)", "%+.2f / %+.2f" % (m["big_win"], m["big_loss"]), "—"),
    ]
    out = "| Metric | Forward | Backtest baseline |\n|---|---:|---:|\n"
    for a, b, c in rows:
        out += "| %s | %s | %s |\n" % (a, b, c)
    return out


def broker_reconcile(T):
    """F4: independently verify trades.csv against the broker-truth dump.
    Returns (ok, lines). A missing/stale dump or any mismatch fails the check —
    and the ALIVE verdict is gated on this passing."""
    path = os.path.join(DATA, "broker_deals.csv")
    lines = []
    if not os.path.exists(path):
        return False, ["broker_deals.csv missing — run lb_forward.py --audit"]
    age_h = (dt.datetime.now().timestamp() - os.path.getmtime(path)) / 3600
    if age_h > 30:
        lines.append("WARNING: broker dump is %.0f h old" % age_h)
    B = pd.read_csv(path)
    if len(B) == 0:
        ok = len(T) == 0
        lines.append("broker deals: 0; recorded trades: %d -> %s" % (
            len(T), "OK" if ok else "MISMATCH"))
        return ok and age_h <= 30, lines
    # closed positions at the broker = position_ids having an OUT deal (entry==1)
    outs = B[B.entry == 1]
    broker_pos = set(outs.position_id.astype(int))
    rec_pos = set(T.position_ticket.dropna().astype(int)) if len(T) else set()
    missing = broker_pos - rec_pos          # broker has it, we never recorded it
    extra = rec_pos - broker_pos            # we recorded a trade broker doesn't know
    pnl_broker = float(B.profit.sum() + B.commission.sum() + B.swap.sum())
    pnl_rec = float(T.pnl_usd.sum()) if len(T) else 0.0
    lines.append("broker closed positions: %d | recorded: %d | missing from record: %d | phantom: %d" % (
        len(broker_pos), len(rec_pos), len(missing), len(extra)))
    lines.append("net PnL — broker: %+.2f | recorded: %+.2f | diff: %+.2f" % (
        pnl_broker, pnl_rec, pnl_broker - pnl_rec))
    if missing:
        lines.append("MISSING position_ids: %s" % sorted(missing))
    if extra:
        lines.append("PHANTOM position_ids: %s" % sorted(extra))
    ok = (not missing and not extra and abs(pnl_broker - pnl_rec) < 0.05 and age_h <= 30)
    return ok, lines


def heartbeat_age_min():
    hb = os.path.join(DATA, "heartbeat")
    if not os.path.exists(hb):
        return None
    return (dt.datetime.now().timestamp() - os.path.getmtime(hb)) / 60.0


def send_alert(subject, body):
    """Email alert using the bot's SMTP credentials from the repo .env."""
    env = {}
    envp = os.path.join(BASE, "..", "..", ".env")
    try:
        with open(envp) as f:
            for ln in f:
                ln = ln.strip()
                if "=" in ln and not ln.startswith("#"):
                    k, v = ln.split("=", 1)
                    env[k.strip()] = v.strip()
    except Exception:
        return False
    if env.get("EMAIL_ENABLED", "").lower() != "true":
        return False
    try:
        import smtplib
        from email.mime.text import MIMEText
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = env["EMAIL_USER"]
        msg["To"] = env["EMAIL_RECIPIENT"]
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=20) as s:
            s.login(env["EMAIL_USER"], env["EMAIL_PASSWORD"])
            s.send_message(msg)
        return True
    except Exception as exc:
        print("alert email failed: %r" % exc)
        return False


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
    rec_ok, rec_lines = broker_reconcile(T)
    try:
        from lb_accounting import check as conservation_check
        cons_ok, cons_text = conservation_check()
    except Exception as exc:
        cons_ok, cons_text = False, "accounting check failed to run: %r" % exc
    if v == "ALIVE (GO)" and not (rec_ok and cons_ok):
        v = "RUNNING"
        notes = ["success criteria met BUT integrity checks failed (reconcile=%s, conservation=%s)"
                 % (rec_ok, cons_ok)]
    # stuck-position alert: an open position older than 2 days means the
    # runner is failing to close/record it
    stuck = []
    sp = os.path.join(DATA, "state.json")
    if os.path.exists(sp):
        try:
            Sj = json.load(open(sp))
            for pr, sst in Sj.items():
                if isinstance(sst, dict) and sst.get("position_ticket") and sst.get("phase") != "DONE":
                    ft = sst.get("fill_time")
                    if ft and (dt.datetime.utcnow() - dt.datetime.fromisoformat(ft)).days >= 2:
                        stuck.append("%s position %s open since %s" % (pr, sst["position_ticket"], ft))
        except Exception:
            pass
    today = dt.date.today().isoformat()

    # ── liveness + integrity alerting (F18) ──
    alerts = []
    hb = heartbeat_age_min()
    wd = dt.datetime.utcnow().weekday()
    if wd < 5 and (hb is None or hb > 45):
        alerts.append("tick heartbeat is %s min old — runner may be down" %
                      ("missing" if hb is None else "%.0f" % hb))
    if not rec_ok and len(T) > 0:
        alerts.append("broker reconciliation FAILED: " + " | ".join(rec_lines))
    if not cons_ok:
        alerts.append("signal-conservation FAILED — see accounting section")
    alerts.extend(stuck)
    if len(E):
        recent = E[E.t_utc >= pd.Timestamp(dt.datetime.utcnow() - dt.timedelta(days=1))]
        fails = recent[recent.event.isin(["order_send_FAILED", "tick_ERROR",
                                          "state_file_corrupt", "orphan_order_cancelled",
                                          "orphan_position_adopted"])]
        for _, r in fails.iterrows():
            alerts.append("%s %s: %s" % (r.get("pair", "?"), r.event,
                                         {k: v2 for k, v2 in r.items()
                                          if k not in ("t_utc", "pair", "event")
                                          and pd.notna(v2)}))
    if v == "REJECT":
        alerts.append("PRE-REGISTERED REJECT CRITERIA TRIGGERED")
    if alerts and mode == "daily":
        send_alert("[LB forward test] %d alert(s) — status %s" % (len(alerts), v),
                   "\n".join(alerts) + "\n\nSee reports/STATUS.md")

    md = ["# London Breakout forward test — %s report (%s)\n" % (mode, today)]
    md.append("**Status: %s** — %s\n" % (v, "; ".join(notes)))
    if alerts:
        md.append("\n> **ALERTS:** " + " · ".join(alerts) + "\n")
    md.append("\n## Broker-truth reconciliation (%s)\n" % ("OK" if rec_ok else "FAILED"))
    md.append("```\n%s\n```\n" % "\n".join(rec_lines))
    md.append("\n## Signal conservation (%s)\n" % ("OK" if cons_ok else "FAILED"))
    md.append("```\n%s```\n" % cons_text)
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
                              "slippage_pips", "exit_reason"]].to_string(index=False))
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
                              "spread_signal_pips", "exit_reason"]].to_string(index=False))
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
