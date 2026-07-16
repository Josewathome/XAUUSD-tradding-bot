#!/usr/bin/env python3
"""export_data2.py — v2 export: adds per-bar SPREAD column + cross-asset instruments.

Same Wine-Python runtime rules as export_data.py:

    docker exec -u abc xauusd-bot bash -lc \
      'export WINEPREFIX=/config/.wine HOME=/config; wine python /bot/research/box_theory/export_data2.py'

Exports D1 + M15 (24mo) for the 6 FX/metal originals plus US500, USTEC, DE40,
XAGUSD, XTIUSD. The MT5 rates struct's `spread` field (points at bar open) is
kept — it is the only historical cost record the broker provides.
"""
import csv
import json
import os
import sys
import time
import datetime as dt

try:
    import MetaTrader5 as mt5
except Exception as exc:
    print("FATAL: cannot import MetaTrader5:", repr(exc))
    sys.exit(2)

OUT_DIR = "/bot/research/box_theory/data"
SYMBOLS = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "XAUUSD",
           "US500", "USTEC", "DE40", "XAGUSD", "XTIUSD"]
RANGE_START = dt.datetime(2024, 6, 1)
RANGE_END = dt.datetime(2026, 7, 16)
RETRY = [0, 3, 8, 15]


def fetch(symbol, tf):
    mt5.copy_rates_from_pos(symbol, tf, 0, 10)
    for d in RETRY:
        if d:
            time.sleep(d)
        r = mt5.copy_rates_range(symbol, tf, RANGE_START, RANGE_END)
        if r is not None and len(r):
            return r
    return None


def write_csv(symbol, tf_name, rates):
    path = os.path.join(OUT_DIR, "%s_%s.csv" % (symbol, tf_name))
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["epoch", "time_server", "open", "high", "low", "close", "volume", "spread"])
        for r in rates:
            epoch = int(r["time"])
            t = dt.datetime.utcfromtimestamp(epoch).strftime("%Y-%m-%d %H:%M:%S")
            vol = r["real_volume"] if r["real_volume"] > 0 else r["tick_volume"]
            w.writerow([epoch, t, r["open"], r["high"], r["low"], r["close"],
                        int(vol), int(r["spread"])])
    first = dt.datetime.utcfromtimestamp(int(rates[0]["time"]))
    last = dt.datetime.utcfromtimestamp(int(rates[-1]["time"]))
    return len(rates), first, last


def main():
    if not mt5.initialize(timeout=20000):
        print("FATAL:", mt5.last_error()); sys.exit(2)
    meta = {"digits": {}, "point": {}}
    for sym in SYMBOLS:
        if not mt5.symbol_select(sym, True):
            print("WARN select failed", sym); continue
        si = mt5.symbol_info(sym)
        meta["digits"][sym] = si.digits
        meta["point"][sym] = si.point
        for tf_name, tf in [("D1", mt5.TIMEFRAME_D1), ("M15", mt5.TIMEFRAME_M15)]:
            r = fetch(sym, tf)
            if r is None:
                print("  %-7s %-3s EMPTY %s" % (sym, tf_name, mt5.last_error())); continue
            n, a, b = write_csv(sym, tf_name, r)
            print("  %-7s %-3s %6d bars  %s -> %s" % (sym, tf_name, n, a.date(), b.date()))
    with open(os.path.join(OUT_DIR, "_meta2.json"), "w") as f:
        json.dump(meta, f, indent=2)
    mt5.shutdown()
    print("DONE")


if __name__ == "__main__":
    main()
