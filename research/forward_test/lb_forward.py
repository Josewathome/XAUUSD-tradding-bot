#!/usr/bin/env python3
"""lb_forward.py — London Breakout demo-forward-test runner (Phase 11).

A controlled scientific experiment: the validated configuration, frozen.
NO optimization, NO new filters, NO intervention. Success/failure criteria
are pre-registered in README.md and evaluated by lb_reportgen.py.

Runs under the container's Wine Python 3.9 (stdlib + MetaTrader5 ONLY):

    docker exec -u abc xauusd-bot bash -lc \
      'export WINEPREFIX=/config/.wine HOME=/config; wine python /bot/research/forward_test/lb_forward.py [--dry]'

Invoked every 5 minutes by host cron (see run_tick.sh). Stateless between
invocations except for data/state.json; safe to re-run (idempotent per step).

FROZEN RULES (validated config; server clock = UK+2 both DST regimes):
  pairs        EURUSD (primary), GBPUSD (secondary)
  timeframe    M15
  Asian range  server 02:00-08:59  (= 00:00-07:00 UK)
  window       server 09:00-12:59  (= 07:00-11:00 UK)
  signal       first M15 BODY close outside the range (wick-only ignored)
  entry        native stop order 1.5 pips beyond breakout candle extreme
  expiry       30 minutes (server-side ORDER_TIME_SPECIFIED) + 13:00 cancel
  invalidation candle close on the opposite side of the range -> cancel
  SL           opposite side of the Asian range (-/+ 1.5 pips)
  TP           fixed 2R
  risk         1% of equity (min-lot floor recorded when it binds)
  spread gate  < 2.0 pips at signal
  frequency    one setup per pair per day; EOD backstop close 23:45 server
"""
import calendar
import csv
import json
import os
import sys
import time
import datetime as dt

try:
    import MetaTrader5 as mt5
except Exception as exc:
    print("FATAL: MetaTrader5 import failed: %r" % exc)
    sys.exit(2)

BASE = "/bot/research/forward_test"
DATA = os.path.join(BASE, "data")
STATE_F = os.path.join(DATA, "state.json")
TRADES_F = os.path.join(DATA, "trades.csv")
EVENTS_F = os.path.join(DATA, "events.jsonl")

PAIRS = ["EURUSD", "GBPUSD"]
MAGIC = 771015
RISK_PCT = 0.01
PAD_PIPS = 1.5
EXPIRY_SEC = 30 * 60
TP_R = 2.0
MAX_SPREAD_PIPS = 2.0
ASIA = (2, 9)          # server hours [start, end)
WINDOW = (9, 13)
EOD_H, EOD_M = 23, 45
PIP = 0.0001           # both pairs are 5-digit

DRY = "--dry" in sys.argv

TRADE_COLS = [
    "pair", "date", "direction", "signal_time", "entry_time", "exit_time",
    "asian_high", "asian_low", "asian_range_pips", "breakout_candle_size_pips",
    "requested_entry", "fill_price", "slippage_pips", "sl_price", "tp_price",
    "stop_distance_pips", "target_distance_pips", "r_multiple", "pnl_usd",
    "commission_usd", "swap_usd", "volume", "planned_risk_usd", "planned_risk_pct",
    "spread_signal_pips", "spread_entry_pips", "spread_exit_pips",
    "atr14_pips", "adr20_pips", "fill_hour_server", "dow", "exit_reason",
    "order_ticket", "position_ticket", "equity_after",
]


# ── persistence helpers ───────────────────────────────────────────────
def load_state():
    if os.path.exists(STATE_F):
        try:
            with open(STATE_F) as f:
                return json.load(f)
        except Exception as exc:
            # F3: never silently reset the day — preserve evidence and log it
            bad = STATE_F + ".corrupt-" + dt.datetime.utcnow().strftime("%Y%m%dT%H%M%S")
            try:
                os.replace(STATE_F, bad)
            except Exception:
                pass
            event("SYS", "state_file_corrupt", backup=bad, err=repr(exc))
    return {}


def save_state(st):
    tmp = STATE_F + ".tmp"
    with open(tmp, "w") as f:
        json.dump(st, f, indent=1, default=str)
    os.replace(tmp, STATE_F)


def event(pair, kind, **kw):
    rec = {"t_utc": dt.datetime.utcnow().isoformat(timespec="seconds"),
           "pair": pair, "event": kind}
    rec.update(kw)
    with open(EVENTS_F, "a") as f:
        f.write(json.dumps(rec, default=str) + "\n")
    print("[%s] %s %s %s" % (rec["t_utc"], pair, kind,
                             " ".join("%s=%s" % kv for kv in kw.items())))


def append_trade(row):
    new = not os.path.exists(TRADES_F)
    with open(TRADES_F, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=TRADE_COLS)
        if new:
            w.writeheader()
        w.writerow(row)


# ── market helpers ────────────────────────────────────────────────────
def spread_pips(sym):
    t = mt5.symbol_info_tick(sym)
    if t is None or t.ask <= 0:
        return None
    return (t.ask - t.bid) / PIP


def server_now():
    for s in PAIRS:
        t = mt5.symbol_info_tick(s)
        if t is not None and t.time:
            return dt.datetime.utcfromtimestamp(int(t.time))
    return None


def m15(sym, count=200):
    r = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_M15, 0, count)
    return [] if r is None else list(r)


def atr14_pips(sym):
    r = m15(sym, 20)
    if len(r) < 16:
        return None
    trs = []
    for k in range(1, len(r)):
        h, l, pc = r[k]["high"], r[k]["low"], r[k - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return (sum(trs[-14:]) / 14.0) / PIP


def adr20_pips(sym):
    r = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_D1, 1, 20)  # last 20 closed days
    if r is None or len(r) < 5:
        return None
    return (sum(x["high"] - x["low"] for x in r) / len(r)) / PIP


def sized_volume(sym, sl_dist):
    ai = mt5.account_info()
    si = mt5.symbol_info(sym)
    risk_money = ai.equity * RISK_PCT
    vpl = si.trade_tick_value / si.trade_tick_size      # $ per price-unit per lot
    loss_per_lot = sl_dist * vpl
    vol = risk_money / loss_per_lot if loss_per_lot > 0 else 0.0
    step = si.volume_step
    vol = max(si.volume_min, int(vol / step + 1e-9) * step)  # F11: float-floor guard
    vol = min(vol, si.volume_max)
    actual_risk = vol * loss_per_lot
    return round(vol, 2), risk_money, actual_risk, ai.equity


def send_order(req, tries=("IOC", "RETURN", "FOK")):
    fmap = {"IOC": mt5.ORDER_FILLING_IOC, "RETURN": mt5.ORDER_FILLING_RETURN,
            "FOK": mt5.ORDER_FILLING_FOK}
    last = None
    for t in tries:
        req["type_filling"] = fmap[t]
        res = mt5.order_send(req)
        last = res
        if res is not None and res.retcode in (mt5.TRADE_RETCODE_DONE, mt5.TRADE_RETCODE_PLACED):
            return res
    return last


# ── per-pair state machine ────────────────────────────────────────────
def reconcile(pair, st, now):
    """Detect fills, expiries, closes; record completed trades.

    AUDIT FIX F1: resolution is HISTORY-FIRST. An order can fill AND its
    position close within one cron interval; concluding 'expired' from the
    mere absence of a live position would silently drop such trades (they are
    disproportionately fast losers -> survivorship bias -> false positive).
    """
    # pending order → filled / expired / still working?
    if st.get("order_ticket") and not st.get("position_ticket"):
        ticket = int(st["order_ticket"])
        pending = mt5.orders_get(ticket=ticket)
        if pending:
            # soft expiry for GTC-fallback orders (broker rejected SPECIFIED)
            if st.get("expiry_epoch") and calendar.timegm(now.timetuple()) > int(st["expiry_epoch"]):
                res = send_order({"action": mt5.TRADE_ACTION_REMOVE, "order": ticket})
                if res is not None and res.retcode == mt5.TRADE_RETCODE_DONE:
                    st["phase"] = "DONE"; st["outcome"] = "expired_unfilled"
                    event(pair, "order_cancelled", reason="soft_expiry")
            return
        # order gone from the book — consult broker HISTORY before concluding
        pos_id = None
        hord = mt5.history_orders_get(ticket=ticket)
        if hord:
            ho = hord[0]
            if ho.state == mt5.ORDER_STATE_FILLED and getattr(ho, "position_id", 0):
                pos_id = int(ho.position_id)
        if pos_id is None:
            live = [p for p in (mt5.positions_get(symbol=pair) or []) if p.magic == MAGIC]
            if live:
                pos_id = int(live[0].ticket)
        if pos_id is None:
            if hord:   # broker confirms it was never filled
                st["phase"] = "DONE"
                st["outcome"] = "expired_unfilled"
                event(pair, "order_expired_or_cancelled", ticket=ticket)
            # else: history not synced yet — draw no conclusion, retry next tick
            return
        # filled: take fill details from the entry DEAL (broker truth)
        deals = mt5.history_deals_get(position=pos_id) or []
        ins = [d for d in deals if d.entry == mt5.DEAL_ENTRY_IN]
        live = mt5.positions_get(ticket=pos_id)
        if ins:
            fp, ftime = float(ins[0].price), int(ins[0].time)
        elif live:
            fp, ftime = float(live[0].price_open), int(live[0].time)
        else:
            return  # deal not visible yet; retry next tick
        st["position_ticket"] = pos_id
        st["fill_price"] = fp
        st["fill_time"] = dt.datetime.utcfromtimestamp(ftime).isoformat()
        st["spread_entry_pips"] = spread_pips(pair)
        slp = (fp - st["requested_entry"]) / PIP
        st["slippage_pips"] = slp if st["direction"] == "buy" else -slp
        event(pair, "filled", price=fp, requested=st["requested_entry"],
              slippage_pips=round(st["slippage_pips"], 2))
        # NO return: fall through so an already-closed position records NOW

    # open position → closed?
    if st.get("position_ticket"):
        pt = int(st["position_ticket"])
        pos = mt5.positions_get(ticket=pt)
        if pos:
            # EOD backstop (also fires on weekend spillover ticks, F7)
            if (now.hour > EOD_H or (now.hour == EOD_H and now.minute >= EOD_M)
                    or now.weekday() >= 5):
                p = pos[0]
                typ = mt5.ORDER_TYPE_SELL if p.type == mt5.POSITION_TYPE_BUY else mt5.ORDER_TYPE_BUY
                tick = mt5.symbol_info_tick(pair)
                px = tick.bid if typ == mt5.ORDER_TYPE_SELL else tick.ask
                if not DRY:
                    send_order({"action": mt5.TRADE_ACTION_DEAL, "symbol": pair,
                                "volume": p.volume, "type": typ, "position": pt,
                                "price": px, "deviation": 30, "magic": MAGIC,
                                "comment": "LBfwd EOD"})
                event(pair, "eod_close_sent")
            return
        # position gone → find closing deal
        deals = mt5.history_deals_get(position=pt)
        if not deals:
            frm = dt.datetime.utcnow() - dt.timedelta(days=7)
            deals = [d for d in (mt5.history_deals_get(frm, dt.datetime.utcnow()) or [])
                     if d.position_id == pt]
        ins = [d for d in deals if d.entry == mt5.DEAL_ENTRY_IN]
        outs = [d for d in deals if d.entry == mt5.DEAL_ENTRY_OUT]
        if not outs:
            return  # history not yet synced; retry next tick
        out = outs[-1]
        pnl = sum(d.profit for d in deals)
        comm = sum(d.commission for d in deals)
        swap = sum(d.swap for d in deals)
        vol = ins[0].volume if ins else float(st.get("volume", 0))
        entry = st.get("fill_price") or (ins[0].price if ins else None)
        exit_px = out.price
        dirn = 1.0 if st["direction"] == "buy" else -1.0
        r_unit = abs(st["requested_entry"] - st["sl_price"])
        planned_risk = float(st.get("planned_risk_usd", 0)) or 1e-9
        r_mult = (pnl + comm + swap) / float(st.get("actual_risk_usd", planned_risk))
        reason = "take_profit" if (exit_px - st["tp_price"]) * dirn >= -2 * PIP else \
                 ("stop_loss" if (exit_px - st["sl_price"]) * dirn <= 2 * PIP else "manual_or_eod")
        ai = mt5.account_info()
        row = {
            "pair": pair, "date": st["date"], "direction": st["direction"],
            "signal_time": st.get("signal_time"), "entry_time": st.get("fill_time"),
            "exit_time": dt.datetime.utcfromtimestamp(int(out.time)).isoformat(),
            "asian_high": st["asian_high"], "asian_low": st["asian_low"],
            "asian_range_pips": st["asian_range_pips"],
            "breakout_candle_size_pips": st.get("bo_candle_pips"),
            "requested_entry": st["requested_entry"], "fill_price": entry,
            "slippage_pips": round(float(st.get("slippage_pips", 0) or 0), 2),
            "sl_price": st["sl_price"], "tp_price": st["tp_price"],
            "stop_distance_pips": round(r_unit / PIP, 1),
            "target_distance_pips": round(abs(st["tp_price"] - st["requested_entry"]) / PIP, 1),
            "r_multiple": round(r_mult, 3), "pnl_usd": round(pnl + comm + swap, 2),
            "commission_usd": round(comm, 2), "swap_usd": round(swap, 2),
            "volume": vol, "planned_risk_usd": round(planned_risk, 2),
            "planned_risk_pct": st.get("planned_risk_pct"),
            "spread_signal_pips": st.get("spread_signal_pips"),
            "spread_entry_pips": st.get("spread_entry_pips"),
            "spread_exit_pips": spread_pips(pair),
            "atr14_pips": st.get("atr14_pips"), "adr20_pips": st.get("adr20_pips"),
            "fill_hour_server": (st.get("fill_time") or "T00")[11:13],
            "dow": st.get("dow"), "exit_reason": reason,
            "order_ticket": st.get("order_ticket"), "position_ticket": pt,
            "equity_after": round(ai.equity, 2),
        }
        append_trade(row)
        st["phase"] = "DONE"
        st["outcome"] = reason
        event(pair, "trade_closed", r=row["r_multiple"], pnl=row["pnl_usd"], reason=reason)


def step(pair, st, now):
    today = now.strftime("%Y-%m-%d")
    if st.get("date") != today:
        if st.get("order_ticket") or st.get("position_ticket"):
            return st            # yesterday's business still live; reconcile handles it
        st = {"date": today, "phase": "IDLE", "dow": now.weekday()}

    reconcile(pair, st, now)
    if st.get("phase") in ("DONE",) or now.weekday() >= 5:
        return st
    hr = now.hour + now.minute / 60.0

    # (1) establish the Asian range once the window opens
    if st["phase"] == "IDLE" and WINDOW[0] <= hr < WINDOW[1]:
        rates = m15(pair, 120)
        bars = [r for r in rates
                if dt.datetime.utcfromtimestamp(int(r["time"])).strftime("%Y-%m-%d") == today
                and ASIA[0] <= dt.datetime.utcfromtimestamp(int(r["time"])).hour < ASIA[1]]
        if len(bars) < 20:
            event(pair, "range_insufficient_bars", n=len(bars))
            st["phase"] = "DONE"; st["outcome"] = "no_range"
            return st
        ah = max(r["high"] for r in bars); al = min(r["low"] for r in bars)
        st.update({"phase": "RANGE_SET", "asian_high": float(ah), "asian_low": float(al),
                   "asian_range_pips": round((ah - al) / PIP, 1),
                   "atr14_pips": round(atr14_pips(pair) or 0, 1),
                   "adr20_pips": round(adr20_pips(pair) or 0, 1),
                   "last_scanned": 0})
        event(pair, "range_set", high=ah, low=al, pips=st["asian_range_pips"])

    # (2) scan completed window bars for the first body close outside
    if st["phase"] == "RANGE_SET":
        if hr >= WINDOW[1]:
            st["phase"] = "DONE"; st["outcome"] = "no_breakout"
            event(pair, "window_closed_no_breakout")
            return st
        now_epoch = calendar.timegm(now.timetuple())
        for r in m15(pair, 40):
            bt = int(r["time"])
            bdt = dt.datetime.utcfromtimestamp(bt)
            if bdt.strftime("%Y-%m-%d") != today or not (WINDOW[0] <= bdt.hour < WINDOW[1]):
                continue
            if bt <= int(st.get("last_scanned", 0)) or bt + 900 > now_epoch:
                continue
            st["last_scanned"] = bt
            side = None
            if r["close"] > st["asian_high"]:
                side = "buy"
            elif r["close"] < st["asian_low"]:
                side = "sell"
            if side is None:
                continue
            sp = spread_pips(pair)
            if sp is None or sp >= MAX_SPREAD_PIPS:
                st["phase"] = "DONE"; st["outcome"] = "spread_block"
                event(pair, "spread_block", spread=sp)
                return st
            pad = PAD_PIPS * PIP
            if side == "buy":
                trig = float(r["high"]) + pad
                slp = st["asian_low"] - pad
                otype = mt5.ORDER_TYPE_BUY_STOP
            else:
                trig = float(r["low"]) - pad
                slp = st["asian_high"] + pad
                otype = mt5.ORDER_TYPE_SELL_STOP
            r_unit = abs(trig - slp)
            tp = trig + (TP_R * r_unit if side == "buy" else -TP_R * r_unit)
            vol, plan_risk, actual_risk, equity = sized_volume(pair, r_unit)
            st.update({"direction": side, "requested_entry": round(trig, 5),
                       "sl_price": round(slp, 5), "tp_price": round(tp, 5),
                       "signal_time": bdt.isoformat(), "signal_epoch": bt,
                       "spread_signal_pips": round(sp, 2),
                       "bo_candle_pips": round((r["high"] - r["low"]) / PIP, 1),
                       "volume": vol, "planned_risk_usd": round(plan_risk, 2),
                       "actual_risk_usd": round(actual_risk, 2),
                       "planned_risk_pct": round(100 * actual_risk / equity, 2)})
            if DRY:
                st["phase"] = "DONE"; st["outcome"] = "dry_run_signal"
                event(pair, "DRY_signal", side=side, trigger=trig, sl=slp, tp=tp, vol=vol)
                return st
            # F2 guard: never place if a magic-tagged order/position already exists
            have = [o for o in (mt5.orders_get(symbol=pair) or []) if o.magic == MAGIC] + \
                   [p for p in (mt5.positions_get(symbol=pair) or []) if p.magic == MAGIC]
            if have:
                event(pair, "placement_blocked_existing", n=len(have))
                st["phase"] = "DONE"; st["outcome"] = "blocked_existing"
                return st
            exp_epoch = int(now_epoch) + EXPIRY_SEC
            req = {"action": mt5.TRADE_ACTION_PENDING, "symbol": pair,
                   "volume": vol, "type": otype, "price": round(trig, 5),
                   "sl": round(slp, 5), "tp": round(tp, 5),
                   "type_time": mt5.ORDER_TIME_SPECIFIED,
                   "expiration": exp_epoch, "magic": MAGIC, "comment": "LBfwd"}
            res = send_order(dict(req))
            rc = getattr(res, "retcode", None)
            if rc == 10022:  # TRADE_RETCODE_INVALID_EXPIRATION -> GTC + soft expiry (F14)
                req.pop("expiration", None)
                req["type_time"] = mt5.ORDER_TIME_GTC
                res = send_order(dict(req))
                rc = getattr(res, "retcode", None)
                if res is not None and rc in (mt5.TRADE_RETCODE_DONE, mt5.TRADE_RETCODE_PLACED):
                    st["expiry_epoch"] = exp_epoch
                    event(pair, "gtc_fallback_used", soft_expiry=exp_epoch)
            if res is None or rc not in (mt5.TRADE_RETCODE_DONE, mt5.TRADE_RETCODE_PLACED):
                # 10015/10016: price already through the trigger (late cron) — a
                # missed fill the backtest would have taken; tag distinctly (F5)
                outcome = "missed_fast_move" if rc in (10015, 10016) else "order_rejected"
                event(pair, "order_send_FAILED", retcode=rc,
                      msg=getattr(res, "comment", None), outcome=outcome)
                st["phase"] = "DONE"; st["outcome"] = outcome
                return st
            st["order_ticket"] = int(res.order)
            st["phase"] = "ORDER_PLACED"
            event(pair, "order_placed", ticket=res.order, side=side,
                  trigger=trig, sl=slp, tp=tp, vol=vol,
                  risk_pct=st["planned_risk_pct"], spread=sp)
            return st

    # (3) pending-order maintenance: invalidation + 13:00 cancel
    if st["phase"] == "ORDER_PLACED" and st.get("order_ticket") and not st.get("position_ticket"):
        cancel = False; why = ""
        if hr >= WINDOW[1]:
            cancel, why = True, "window_end"
        else:
            # F6: check EVERY closed bar after the signal bar, not just the last
            now_epoch = calendar.timegm(now.timetuple())
            sig_epoch = int(st.get("signal_epoch", 0))
            for r in m15(pair, 12):
                bt = int(r["time"])
                if bt <= sig_epoch or bt + 900 > now_epoch:
                    continue
                if st["direction"] == "buy" and r["close"] < st["asian_low"]:
                    cancel, why = True, "invalidated_opposite_close"; break
                if st["direction"] == "sell" and r["close"] > st["asian_high"]:
                    cancel, why = True, "invalidated_opposite_close"; break
        if cancel:
            # F15: only conclude if the broker confirms removal — a failed REMOVE
            # usually means the order just filled; reconcile picks that up next tick
            res = None if DRY else send_order({"action": mt5.TRADE_ACTION_REMOVE,
                                               "order": int(st["order_ticket"])})
            if DRY or (res is not None and res.retcode == mt5.TRADE_RETCODE_DONE):
                event(pair, "order_cancelled", reason=why)
                st["phase"] = "DONE"; st["outcome"] = why
            else:
                event(pair, "order_cancel_failed_probably_filled",
                      retcode=getattr(res, "retcode", None))
    return st


EXPERIMENT_START = dt.datetime(2026, 7, 17)


def audit_dump():
    """--audit: dump ALL magic-tagged broker history (deals + orders) to CSV.
    This is the independent, broker-truth record the reports reconcile against
    (F4): even a runner bug cannot hide a trade from this dump."""
    to = dt.datetime.utcnow() + dt.timedelta(days=2)
    deals = [d for d in (mt5.history_deals_get(EXPERIMENT_START, to) or [])
             if d.magic == MAGIC]
    with open(os.path.join(DATA, "broker_deals.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["position_id", "order", "symbol", "entry", "type", "time",
                    "price", "volume", "profit", "commission", "swap", "comment"])
        for d in deals:
            w.writerow([d.position_id, d.order, d.symbol, d.entry, d.type,
                        dt.datetime.utcfromtimestamp(int(d.time)).isoformat(),
                        d.price, d.volume, d.profit, d.commission, d.swap, d.comment])
    orders = [o for o in (mt5.history_orders_get(EXPERIMENT_START, to) or [])
              if o.magic == MAGIC]
    with open(os.path.join(DATA, "broker_orders.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ticket", "symbol", "type", "state", "price_open", "sl", "tp",
                    "volume_initial", "time_setup", "time_done", "position_id"])
        for o in orders:
            w.writerow([o.ticket, o.symbol, o.type, o.state, o.price_open, o.sl,
                        o.tp, o.volume_initial,
                        dt.datetime.utcfromtimestamp(int(o.time_setup)).isoformat(),
                        dt.datetime.utcfromtimestamp(int(o.time_done)).isoformat()
                        if o.time_done else "", getattr(o, "position_id", "")])
    print("audit: %d deals, %d orders dumped" % (len(deals), len(orders)))


def orphan_sweep(state, now):
    """F2/F3: reconcile broker-side magic orders/positions the state does not
    know about (crash mid-placement, lost state). Unknown pending orders are
    cancelled; unknown positions are adopted for management + recording."""
    known_orders = set(); known_pos = set()
    for p in PAIRS:
        stp = state.get(p, {})
        if stp.get("order_ticket"):
            known_orders.add(int(stp["order_ticket"]))
        if stp.get("position_ticket"):
            known_pos.add(int(stp["position_ticket"]))
    for o in (mt5.orders_get() or []):
        if o.magic == MAGIC and int(o.ticket) not in known_orders:
            if not DRY:
                send_order({"action": mt5.TRADE_ACTION_REMOVE, "order": int(o.ticket)})
            event(o.symbol, "orphan_order_cancelled", ticket=int(o.ticket))
    for p in (mt5.positions_get() or []):
        if p.magic == MAGIC and int(p.ticket) not in known_pos and p.symbol in PAIRS:
            si = mt5.symbol_info(p.symbol)
            vpl = (si.trade_tick_value / si.trade_tick_size) if si and si.trade_tick_size else 1e5
            r_unit = abs(p.price_open - p.sl) if p.sl else 0.0
            risk = r_unit * vpl * p.volume if r_unit else 1e-9
            state[p.symbol] = {
                "date": now.strftime("%Y-%m-%d"), "phase": "FILLED_RECOVERED",
                "dow": now.weekday(), "recovered": True,
                "direction": "buy" if p.type == mt5.POSITION_TYPE_BUY else "sell",
                "requested_entry": float(p.price_open), "fill_price": float(p.price_open),
                "fill_time": dt.datetime.utcfromtimestamp(int(p.time)).isoformat(),
                "sl_price": float(p.sl), "tp_price": float(p.tp),
                "position_ticket": int(p.ticket), "order_ticket": None,
                "asian_high": None, "asian_low": None, "asian_range_pips": None,
                "signal_time": None, "spread_signal_pips": None, "bo_candle_pips": None,
                "atr14_pips": None, "adr20_pips": None, "slippage_pips": None,
                "volume": p.volume, "planned_risk_usd": round(risk, 2),
                "actual_risk_usd": round(risk, 2), "planned_risk_pct": None,
            }
            event(p.symbol, "orphan_position_adopted", ticket=int(p.ticket))


def main():
    try:  # heartbeat first: proves cron+wine+python are alive (F18)
        with open(os.path.join(DATA, "heartbeat"), "w") as f:
            f.write(dt.datetime.utcnow().isoformat())
    except Exception:
        pass
    if not mt5.initialize(timeout=20000):
        print("FATAL mt5.initialize: %s" % str(mt5.last_error()))
        sys.exit(1)
    if "--audit" in sys.argv:
        audit_dump(); mt5.shutdown(); return
    ti = mt5.terminal_info()
    if ti is None or not ti.connected:
        print("terminal not connected — skipping tick"); mt5.shutdown(); return
    now = server_now()
    if now is None:
        print("no server time (market closed?)"); mt5.shutdown(); return
    # stale-quote guard: server (UK+2) vs host UTC differ by a stable offset;
    # if the freshest tick is >30min behind that expectation, do not act.
    host_utc = dt.datetime.utcnow()
    expect = host_utc + dt.timedelta(hours=3)   # summer UK+2 = UTC+3 (winter +2)
    lag_min = (expect - now).total_seconds() / 60.0
    if lag_min > 90:                            # tolerant of the DST-mismatch hour
        print("stale quotes (lag %.0f min) — skipping tick" % lag_min)
        mt5.shutdown(); return
    state = load_state()
    try:
        orphan_sweep(state, now)
    except Exception as exc:
        event("SYS", "orphan_sweep_ERROR", err=repr(exc))
    for pair in PAIRS:
        st = state.get(pair, {})
        try:
            state[pair] = step(pair, st, now)
        except Exception as exc:
            event(pair, "tick_ERROR", err=repr(exc))
    save_state(state)
    mt5.shutdown()


if __name__ == "__main__":
    main()
