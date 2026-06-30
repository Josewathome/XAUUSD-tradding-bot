"""
multi_engine.py — Multi-symbol strategy dispatcher.

Routes IStrategyEngine calls to the correct per-symbol engine based on
the symbol argument.  A single MultiSymbolEngine instance is returned by
build_engine() in main_stream.py; the framework never needs to know that
multiple concrete engines are active.

Wired engines:
  XAUUSD  →  XAUUSDStrategyEngine   (Five-Gate Triple Confirmation Scalp)
  EURUSD  →  EURUSDStrategyEngine   (London Breakout)

Any symbol not claimed by a concrete engine falls through to a no-op stub
so the framework continues without errors.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from src.domain.entities import Candle, Timeframe
from src.strategy.interfaces import (
    GuardianAction,
    GuardianInput,
    IStrategyEngine,
    ITradeGuardian,
    StrategySignal,
)

logger = logging.getLogger(__name__)


class MultiSymbolEngine(IStrategyEngine):
    """Dispatches strategy calls to per-symbol engines."""

    def __init__(self, engines: Dict[str, IStrategyEngine]) -> None:
        # engines: {"XAUUSD": XAUUSDStrategyEngine(...), "EURUSD": EURUSDStrategyEngine(...)}
        self._engines = engines
        logger.info(
            "MultiSymbolEngine active — routing: %s",
            ", ".join(f"{s}→{type(e).__name__}" for s, e in engines.items()),
        )

    def _engine(self, symbol: str) -> Optional[IStrategyEngine]:
        return self._engines.get(symbol)

    # ── Lifecycle ────────────────────────────────────────────────────

    def seed(self, symbol: str, timeframe: Timeframe, candles: List[Candle]) -> None:
        eng = self._engine(symbol)
        if eng:
            eng.seed(symbol, timeframe, candles)

    def on_candle(self, candle: Candle) -> None:
        eng = self._engine(candle.symbol)
        if eng:
            eng.on_candle(candle)

    def push_tick(self, symbol: str, bid: float, ask: float, ts: float) -> None:
        eng = self._engine(symbol)
        if eng:
            eng.push_tick(symbol, bid, ask, ts)

    def evaluate(
        self,
        symbol:          str,
        forming_m1:      Optional[Candle],
        m1_candles:      List[Candle],
        m5_candles:      List[Candle],
        current_price:   float,
        elapsed_m1_secs: float = 30.0,
    ) -> Optional[StrategySignal]:
        eng = self._engine(symbol)
        if eng is None:
            return None
        return eng.evaluate(symbol, forming_m1, m1_candles, m5_candles,
                            current_price, elapsed_m1_secs)

    # ── Introspection ────────────────────────────────────────────────

    def get_structure(self, symbol: str) -> Any:
        eng = self._engine(symbol)
        return eng.get_structure(symbol) if eng else None

    def structure_summary(self, symbol: str) -> Dict[str, str]:
        eng = self._engine(symbol)
        return eng.structure_summary(symbol) if eng else {}

    def spread_state(self, symbol: str) -> str:
        eng = self._engine(symbol)
        return eng.spread_state(symbol) if eng else "normal"


class MultiSymbolGuardian(ITradeGuardian):
    """Dispatches guardian calls to per-symbol guardians."""

    def __init__(self, guardians: List[ITradeGuardian]) -> None:
        self._guardians = guardians

    def evaluate(self, ctx: GuardianInput) -> List[GuardianAction]:
        actions: List[GuardianAction] = []
        for g in self._guardians:
            actions.extend(g.evaluate(ctx))
        return actions
