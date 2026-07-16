#!/usr/bin/env python3
"""export_m5.py — refresh M5 CSVs (max depth ~50k bars ≈ 6 months) WITH the
per-bar spread column, for the 6 FX/metal instruments.

    docker exec -u abc xauusd-bot bash -lc \
      'export WINEPREFIX=/config/.wine HOME=/config; wine python /bot/research/box_theory/export_m5.py'
"""
import csv
import os
import sys
import datetime as dt

try:
    import MetaTrader5 as mt5
except Exception as exc:
    print("FATAL:", repr(exc)); sys.exit(2)

OUT = "/bot/research/box_theory/data"
SYMBOLS = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "XAUUSD"]


def main():
    if not mt5.initialize(timeout=20000):
        print("FATAL:", mt5.last_error()); sys.exit(2)
    for sym in SYMBOLS:
        mt5.symbol_select(sym, True)
        mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_M5, 0, 10)
        r = mt5.copy_rates_from_pos(sym, mt5.TIMEFRAME_M5, 0, 50000)
        if r is None or not len(r):
            print("  %-7s EMPTY %s" % (sym, mt5.last_error())); continue
        path = os.path.join(OUT, "%s_M5.csv" % sym)
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["epoch", "time_server", "open", "high", "low", "close", "volume", "spread"])
            for x in r:
                e = int(x["time"])
                t = dt.datetime.utcfromtimestamp(e).strftime("%Y-%m-%d %H:%M:%S")
                vol = x["real_volume"] if x["real_volume"] > 0 else x["tick_volume"]
                w.writerow([e, t, x["open"], x["high"], x["low"], x["close"], int(vol), int(x["spread"])])
        a = dt.datetime.utcfromtimestamp(int(r[0]["time"]))
        b = dt.datetime.utcfromtimestamp(int(r[-1]["time"]))
        print("  %-7s %6d bars  %s -> %s" % (sym, len(r), a.date(), b.date()))
    mt5.shutdown()
    print("DONE")


if __name__ == "__main__":
    main()
