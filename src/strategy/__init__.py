"""
strategy — Strategy <-> framework boundary.

The framework depends only on the abstract contracts in ``interfaces``.
Concrete trading intelligence is supplied by implementing those interfaces;
``placeholder_engine`` and ``placeholder_guardian`` provide no-op defaults so
the system runs end-to-end in observation mode until a real engine is plugged in.
"""
from src.strategy.interfaces import (
    GuardianAction,
    GuardianActionType,
    GuardianInput,
    IStrategyEngine,
    ITradeGuardian,
    StrategySignal,
    TradeContext,
)
from src.strategy.placeholder_engine import PlaceholderStrategyEngine
from src.strategy.placeholder_guardian import PlaceholderTradeGuardian

__all__ = [
    "StrategySignal",
    "IStrategyEngine",
    "ITradeGuardian",
    "GuardianAction",
    "GuardianActionType",
    "GuardianInput",
    "TradeContext",
    "PlaceholderStrategyEngine",
    "PlaceholderTradeGuardian",
]
