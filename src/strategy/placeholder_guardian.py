"""
placeholder_guardian.py — No-op Trade Guardian.

Implements ITradeGuardian with NO exit intelligence: it returns an empty
action list on every re-evaluation, so open trades are managed purely by the
framework's mechanical layer (broker-side SL/TP, software TP1/TP2 tiering,
time-exit, MFE tracking in ExecutionService).

The real XAUUSD guardian replaces this class and implements:
  - a continuation score (is the thesis still valid?)
  - structural stop-loss trailing (where does the SL logically belong now?)
  - defensive tightening and reversal exits

It does so by inspecting ctx.fresh_signal, ctx.structure (the opaque object
from IStrategyEngine.get_structure), the candle history and per-trade context,
and returning GuardianAction objects that ExecutionService applies.

TODO(strategy): implement open-trade re-evaluation here.
"""
from __future__ import annotations

import logging
from typing import List

from src.strategy.interfaces import GuardianAction, GuardianInput, ITradeGuardian

logger = logging.getLogger(__name__)


class PlaceholderTradeGuardian(ITradeGuardian):
    """Returns no actions — trades ride the framework's mechanical management."""

    def __init__(self) -> None:
        self._logged_once = False

    def evaluate(self, ctx: GuardianInput) -> List[GuardianAction]:
        if ctx.trades and not self._logged_once:
            logger.info(
                "PlaceholderTradeGuardian active — open trades on %s are managed "
                "by mechanical TP/time/SL only (no structural re-evaluation).",
                ctx.symbol,
            )
            self._logged_once = True
        return []
