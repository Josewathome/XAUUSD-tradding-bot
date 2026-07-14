#!/usr/bin/env python3
"""export_data.py — pull historical OHLC from MT5 to CSV for the Box Theory study.

RUN LOCATION: inside the `xauusd-bot` container, under the SAME Wine Python that
owns the MT5 IPC (stdlib + MetaTrader5 only — no numpy/pandas here):

    docker exec -u abc xauusd-bot bash -lc \
      'export WINEPREFIX=/config/.wine HOME=/config; wine python /bot/research/box_theory/export_data.py'

The repo is bind-mounted at /bot inside the container, so files written to
/bot/research/box_theory/data/ appear on the host automatically.

Design (mirrors src/infrastructure/mt5_bridge/mt5_gateway.py:672 get_candles_range):
  * strip tzinfo before copy_rates_range (MT5 Python rejects tz-aware datetimes),
  * prime the history cache with copy_rates_from_pos(...,10) before a range query,
  * retry empty results with backoff [0, 3, 8, 15]s.
  * M5 is fetched by copy_rates_from_pos (range queries return empty past the
    terminal's ~50k-bar cache ≈ 5.7 months).

Time handling: MT5 epochs are broker-SERVER time (ICMarkets ≈ UTC+2, +3 under DST).
We store the raw epoch AND the server-naive ISO time, and record the detected
current server->UTC offset in _meta.json. Daily boxes (PDH/PDL) are offset-
independent; only session tagging needs the offset (applied in analysis, DST-aware).
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
SYMBOLS = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "XAUUSD"]

# Pull generously; the broker returns whatever it has (USDCAD starts 2025-01-02).
RANGE_START = dt.datetime(2024, 6, 1)
RANGE_END = dt.datetime(2026, 7, 15)
M5_COUNT = 50000  # terminal cache cap; ≈ 174 days

RETRY_DELAYS = [0, 3, 8, 15]


def _fields(tf_name):
    return {
        "D1": mt5.TIMEFRAME_D1,
        "M30": mt5.TIMEFRAME_M30,
        "M15": mt5.TIMEFRAME_M15,
        "M5": mt5.TIMEFRAME_M5,
    }[tf_name]


def detect_offset_hours():
    """Server->UTC offset in hours (round), from latest EURUSD tick vs wall clock."""
    tick = mt5.symbol_info_tick("EURUSD")
    if tick is None or not tick.time:
        return None
    server_ts = int(tick.time)
    real_utc = int(time.time())
    off = round((server_ts - real_utc) / 3600.0)
    return max(-12, min(14, off))


def fetch_range(symbol, tf, start, end):
    mt5.copy_rates_from_pos(symbol, tf, 0, 10)  # prime cache
    for i, delay in enumerate(RETRY_DELAYS):
        if delay:
            time.sleep(delay)
        rates = mt5.copy_rates_range(symbol, tf, start, end)
        if rates is not None and len(rates):
            return rates
    return None


def fetch_pos(symbol, tf, count):
    mt5.copy_rates_from_pos(symbol, tf, 0, 10)  # prime cache
    return mt5.copy_rates_from_pos(symbol, tf, 0, count)


def write_csv(symbol, tf_name, rates):
    path = os.path.join(OUT_DIR, "%s_%s.csv" % (symbol, tf_name))
    n = 0
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["epoch", "time_server", "open", "high", "low", "close", "volume"])
        for r in rates:
            epoch = int(r["time"])
            # server-naive time = epoch interpreted as UTC (i.e. broker wall clock)
            tserver = dt.datetime.utcfromtimestamp(epoch).strftime("%Y-%m-%d %H:%M:%S")
            vol = r["real_volume"] if r["real_volume"] > 0 else r["tick_volume"]
            w.writerow([epoch, tserver, r["open"], r["high"], r["low"], r["close"], int(vol)])
            n += 1
    first = dt.datetime.utcfromtimestamp(int(rates[0]["time"]))
    last = dt.datetime.utcfromtimestamp(int(rates[-1]["time"]))
    return path, n, first, last


def main():
    if not os.path.isdir(OUT_DIR):
        os.makedirs(OUT_DIR)
    if not mt5.initialize(timeout=20000):
        print("FATAL: mt5.initialize failed:", mt5.last_error())
        sys.exit(2)

    ai = mt5.account_info()
    offset = detect_offset_hours()
    print("CONNECTED account=%s server=%s detected_server_utc_offset_hours=%s" % (
        getattr(ai, "login", None), getattr(ai, "server", None), offset))

    meta = {
        "account": getattr(ai, "login", None),
        "server": getattr(ai, "server", None),
        "detected_server_utc_offset_hours": offset,
        "note": "time_server is broker wall-clock (UTC+offset). true_utc = server - offset hours. Offset is DST-sensitive; stored is the CURRENT offset.",
        "range_start": RANGE_START.strftime("%Y-%m-%d"),
        "range_end": RANGE_END.strftime("%Y-%m-%d"),
        "symbols": {},
        "digits": {},
    }

    for sym in SYMBOLS:
        if not mt5.symbol_select(sym, True):
            print("WARN symbol_select failed:", sym)
            continue
        si = mt5.symbol_info(sym)
        meta["digits"][sym] = si.digits if si else None
        meta["symbols"][sym] = {}
        for tf_name in ["D1", "M30", "M15", "M5"]:
            tf = _fields(tf_name)
            if tf_name == "M5":
                rates = fetch_pos(sym, tf, M5_COUNT)
            else:
                rates = fetch_range(sym, tf, RANGE_START, RANGE_END)
            if rates is None or not len(rates):
                print("  %-7s %-3s: EMPTY (%s)" % (sym, tf_name, mt5.last_error()))
                meta["symbols"][sym][tf_name] = {"bars": 0}
                continue
            path, n, first, last = write_csv(sym, tf_name, rates)
            print("  %-7s %-3s: %6d bars  %s -> %s  (%s)" % (
                sym, tf_name, n, first.date(), last.date(), os.path.basename(path)))
            meta["symbols"][sym][tf_name] = {
                "bars": n,
                "first_server": first.strftime("%Y-%m-%d %H:%M:%S"),
                "last_server": last.strftime("%Y-%m-%d %H:%M:%S"),
            }

    with open(os.path.join(OUT_DIR, "_meta.json"), "w") as f:
        json.dump(meta, f, indent=2)
    print("META written:", os.path.join(OUT_DIR, "_meta.json"))
    mt5.shutdown()
    print("DONE")


if __name__ == "__main__":
    main()
