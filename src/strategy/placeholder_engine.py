"""
placeholder_engine.py — No-op strategy engine.

This is the integration point for the XAUUSD strategy. It implements
IStrategyEngine but contains NO trading intelligence: it ingests ticks and
candles (so the data plumbing is exercised end-to-end) and always returns
``None`` from ``evaluate()`` — meaning "no setup".

The full framework (MT5 connectivity, execution, monitoring, risk gates,
journaling, dashboard, analytics) runs normally with this engine in place;
it simply never generates an entry. Replace this class with the real XAUUSD
engine when the strategy is ready — nothing else in the framework needs to
change, because everything depends only on the interfaces in interfaces.py.

TODO(strategy): implement signal generation here, or in a new module that
subclasses IStrategyEngine, and wire it in main_stream.py via build_engine().
"""
from __future__ import annotations

import logging
from collections import deque
from typing import Deque, Dict, List, Optional

from src.domain.entities import Candle, Timeframe
from src.strategy.interfaces import IStrategyEngine, StrategySignal

logger = logging.getLogger(__name__)


class PlaceholderStrategyEngine(IStrategyEngine):
    """Ingests market data and emits no signals (observation mode)."""

    def __init__(self, symbols: List[str], tick_buffer: int = 512) -> None:
        self._symbols = list(symbols)
        # Lightweight per-symbol buffers so a real engine can be slotted in with
        # the same data already flowing. The placeholder only counts them.
        self._candles: Dict[str, Dict[Timeframe, Deque[Candle]]] = {
            s: {} for s in symbols
        }
        self._ticks: Dict[str, Deque[tuple]] = {
            s: deque(maxlen=tick_buffer) for s in symbols
        }
        self._tick_buffer = tick_buffer
        self._warned_once = False
        logger.info(
            "PlaceholderStrategyEngine active for %d symbols — NO signals will be "
            "generated (observation mode). Plug in a real IStrategyEngine to trade.",
            len(symbols),
        )

    # ── Lifecycle ─────────────────────────────────────────────────────

    def seed(self, symbol: str, timeframe: Timeframe, candles: List[Candle]) -> None:
        if symbol not in self._candles:
            self._candles[symbol] = {}
        self._candles[symbol][timeframe] = deque(candles, maxlen=300)

    def on_candle(self, candle: Candle) -> None:
        buf = self._candles.setdefault(candle.symbol, {})
        dq = buf.setdefault(candle.timeframe, deque(maxlen=300))
        dq.append(candle)

    def push_tick(self, symbol: str, bid: float, ask: float, ts: float) -> None:
        if symbol not in self._ticks:
            self._ticks[symbol] = deque(maxlen=self._tick_buffer)
        self._ticks[symbol].append((bid, ask, ts))

    # ── Evaluation ────────────────────────────────────────────────────

    def evaluate(
        self,
        symbol:          str,
        forming_m1:      Optional[Candle],
        m1_candles:      List[Candle],
        m5_candles:      List[Candle],
        current_price:   float,
        elapsed_m1_secs: float = 30.0,
    ) -> Optional[StrategySignal]:
        # No strategy => no signal. Logged once at debug to confirm wiring.
        if not self._warned_once:
            logger.info(
                "PlaceholderStrategyEngine.evaluate() called for %s — returning "
                "None (no strategy implemented).", symbol,
            )
            self._warned_once = True
        return None

    # ── Introspection ─────────────────────────────────────────────────

    def get_structure(self, symbol: str):
        return None

    def structure_summary(self, symbol: str) -> Dict[str, str]:
        return {"M1": "n/a", "M5": "n/a"}

    def spread_state(self, symbol: str) -> str:
        return "normal"
