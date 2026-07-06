"""
zmq_feed.py — File-Tailing Feed (EA appends to a file; Python polls it).

WHY FILES INSTEAD OF SOCKETS
ZoneBotBridge v3 used MT5's built-in SocketCreate/SocketConnect to avoid the
DLL-import permission dialog. But SocketConnect() — even to 127.0.0.1 — still
requires the target in "Allow WebRequest for listed URL", and this MT5 build
does not persist that permission across ANY process restart (confirmed via
live testing: neither a forced kill nor a graceful shutdown with a flush
window preserves it, and an exhaustive search of every ini/dat/reg file in
the Wine prefix found no trace of it anywhere). That meant a human had to
re-click the setting after every restart — not viable for unattended trading.

FileOpen/FileWrite to the EA's own MQL5/Files/ sandbox needs no permission
beyond "Allow algorithmic trading" (confirmed to survive restarts), so v4
of the EA appends events to a file there instead of sending over a socket.
This class tails that file by byte offset instead of running a TCP server.

Message format is unchanged: newline-delimited "TOPIC {JSON}" strings.
The ZmqFeed class name and public API are preserved so main_stream.py is
unchanged — same pattern the ZMQ-to-TCP migration used before this one.
"""
from __future__ import annotations

import json
import logging
import queue
import threading
import time
from typing import Callable, Dict, Optional

from src.domain.entities import Timeframe
from src.infrastructure.stream.candle_builder import CandleBuilder

logger = logging.getLogger(__name__)

_TF_MAP: Dict[str, Timeframe] = {
    "M1":  Timeframe.M1,
    "M5":  Timeframe.M5,
    "M30": Timeframe.M30,
    "H1":  Timeframe.H1,
    "H4":  Timeframe.H4,
}

FEED_STOPPED = object()

_POLL_INTERVAL_S = 0.05

# MT5 runs in portable mode here (Config/ and MQL5/ live alongside the
# terminal executable, confirmed by inspection), so the EA's sandboxed
# MQL5/Files/ directory is under the install path, not per-user AppData.
# Python runs inside the same Wine prefix as MT5 (main_stream.py notes
# os.name == "nt" under Wine), so a Windows-style path resolves correctly.
_DEFAULT_FEED_FILE = r"C:\Program Files\MetaTrader 5\MQL5\Files\zonebot_feed.jsonl"


class ZmqFeed:
    """
    Tails the file the MT5 EA appends newline-delimited messages to.

    Drop-in replacement for the previous TCP-server implementation.
    Same public API: start() / stop() / is_alive / FEED_STOPPED sentinel.
    The `endpoint` parameter is kept for call-site compatibility but is no
    longer used to open a socket — pass `feed_file` to override the path.
    """

    def __init__(
        self,
        endpoint:    str,
        builders:    Dict[str, CandleBuilder],
        event_queue: queue.Queue,
        feed_file:   str = _DEFAULT_FEED_FILE,
    ) -> None:
        self._feed_file = feed_file
        self._builders  = builders
        self._q         = event_queue
        self._stop      = threading.Event()
        self._thread: Optional[threading.Thread] = None

        for sym in list(builders.keys()):
            def _make_cb(s: str) -> Callable:
                def _cb(candle) -> None:
                    self._q.put({"type": "CANDLE_CLOSED", "candle": candle})
                return _cb
            builders[sym]._on_close = _make_cb(sym)

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="file-feed", daemon=True)
        self._thread.start()
        logger.info("FileFeed: tailing %s for EA events", self._feed_file)

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)
        logger.info("FileFeed: stopped")

    @property
    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _run(self) -> None:
        # Binary mode + manual decode: guarantees the byte offset is exact
        # across separate open() calls (text-mode tell()/seek() cookies are
        # only formally portable when reused on the *same* file object).
        #
        # `offset is None` means "haven't connected to this file yet" — on
        # that first connection (process startup, or the EA file reappearing
        # after being missing) we jump straight to end-of-file instead of
        # reading from byte 0. The EA only truncates this file on its OWN
        # restart, not on every Python-side restart, so it can accumulate a
        # day+ of backlog between EA restarts. Replaying that backlog here
        # served no purpose — main_stream.py already re-seeds every
        # CandleBuilder with historical candles straight from MT5 on startup
        # — and it actively corrupted the live clock: BrokerClock/heartbeat
        # would report whatever ancient timestamp the backlog was currently
        # replaying, and CandleBuilder would discard those replayed candles
        # as "out of order" against the fresher MT5-seeded cache, for as
        # long as the fast-forward replay took to catch up to real time.
        offset: Optional[int] = None
        buf = b""
        connected = False
        while not self._stop.is_set():
            try:
                with open(self._feed_file, "rb") as fh:
                    fh.seek(0, 2)
                    size = fh.tell()
                    if offset is None:
                        offset = size
                        logger.info(
                            "FileFeed: skipping %d bytes of existing backlog — "
                            "starting from the live tail (history already seeded from MT5).",
                            size,
                        )
                    elif size < offset:
                        # EA rotated (truncated) the file, or a fresh EA
                        # session started — restart from the top of the new,
                        # much smaller file (this is recent content, not a
                        # multi-hour backlog, so reading all of it is fine).
                        offset = 0
                        buf = b""
                        logger.info("FileFeed: feed file shrank — EA restarted, resetting offset")
                    if not connected:
                        connected = True
                        logger.info("FileFeed: EA feed file found — reading events")
                    if size > offset:
                        fh.seek(offset)
                        buf += fh.read(size - offset)
                        offset = size
                        while b"\n" in buf:
                            line, buf = buf.split(b"\n", 1)
                            text = line.decode("utf-8", errors="replace").strip()
                            if text:
                                self._dispatch(text)
            except FileNotFoundError:
                if connected:
                    logger.info("FileFeed: feed file disappeared — waiting for EA to recreate it")
                    connected = False
                    buf = b""
                offset = None  # re-arm "skip existing backlog" for whatever file appears next
            except Exception as exc:
                logger.warning("FileFeed: read error: %s", exc)
            time.sleep(_POLL_INTERVAL_S)

        self._q.put(FEED_STOPPED)
        logger.info("FileFeed: thread exiting cleanly")

    def _dispatch(self, raw: str) -> None:
        space = raw.find(" ")
        if space < 0:
            logger.warning("FileFeed: malformed message: %r", raw[:80])
            return
        topic   = raw[:space]
        payload = raw[space + 1:]
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            logger.warning("FileFeed: bad JSON topic=%s: %r", topic, payload[:80])
            return

        if topic == "TICK":
            self._handle_tick(data)
        elif topic == "BAR_CLOSE":
            self._handle_bar_close(data)
        elif topic == "BAR_OPEN":
            self._q.put({"type": "BAR_OPEN",  **data})
        elif topic == "TRADE":
            self._q.put({"type": "TRADE",     **data})
        elif topic == "HEARTBEAT":
            self._q.put({"type": "HEARTBEAT", **data})
        else:
            logger.debug("FileFeed: unknown topic %s", topic)

    def _handle_tick(self, data: dict) -> None:
        sym = data.get("sym", "")
        bid = data.get("bid", 0.0)
        ask = data.get("ask", 0.0)
        t   = data.get("time", 0)
        builder = self._builders.get(sym)
        if builder:
            builder.on_tick(bid=bid, ask=ask, tick_time=t)
        self._q.put({"type": "TICK", "sym": sym, "bid": bid, "ask": ask, "time": t})

    def _handle_bar_close(self, data: dict) -> None:
        # No separate "BAR_CLOSE" queue event here. This mirrors Scraper-forex-bot
        # and New-forex-bot's original design, which queued this raw EA message
        # AND called on_bar_close_from_ea() for every BAR_CLOSE — both fed the
        # same main_stream.py block that runs strategy evaluation, so one EA
        # message could trigger it twice on its own, on top of the tick-built
        # path triggering it a third time (all three sibling bots have this).
        #
        # Confirmed the raw event added nothing on_bar_close_from_ea() doesn't
        # already cover: the one place its sibling CANDLE_CLOSED events forward
        # a Candle that a raw BAR_CLOSE event never did — strategy.on_candle()
        # populating each engine's self._candles buffer — that buffer is written
        # but never read anywhere (evaluate() takes m1_candles/m5_candles as
        # explicit args sourced from CandleBuilder's own cache instead), so
        # skipping it changes nothing observable. on_bar_close_from_ea() already
        # re-queues a CANDLE_CLOSED event via the builder's _on_close callback
        # whenever the EA's bar is genuinely new (not a duplicate of the
        # tick-built one) — that's the real "EA report as a fallback for missed
        # ticks" path, and it's untouched here.
        sym  = data.get("sym", "")
        tf_s = data.get("tf", "")
        tf   = _TF_MAP.get(tf_s)
        if tf is None:
            logger.debug("FileFeed: BAR_CLOSE unknown tf %s", tf_s)
            return
        builder = self._builders.get(sym)
        if builder:
            builder.on_bar_close_from_ea(
                timeframe=tf,
                bar_time= data["time"],
                open_p=   data["open"],
                high_p=   data["high"],
                low_p=    data["low"],
                close_p=  data["close"],
                volume=   int(data.get("vol", 0)),
            )
