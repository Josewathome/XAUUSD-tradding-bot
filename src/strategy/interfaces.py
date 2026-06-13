"""
interfaces.py — Strategy ⇄ Framework boundary contracts.

This module defines the SEAM between the reusable trading INFRASTRUCTURE
(MT5 connectivity, order execution, position monitoring, risk gates,
journaling, checkpoints, streaming) and the STRATEGY ENGINE (signal
generation, entry/exit math, structural analysis, trade-guardian decisions).

The infrastructure depends ONLY on the abstract types defined here.  A concrete
XAUUSD strategy is plugged in later by implementing two interfaces:

  • IStrategyEngine  — consumes ticks/candles and produces StrategySignal
                       objects (with proposed SL/TP) on each M1 close.
  • ITradeGuardian   — re-evaluates open trades on each M1 close and returns
                       GuardianAction objects (trail SL / partial close / close)
                       that the ExecutionService applies mechanically.

Placeholder implementations (placeholder_engine.py / placeholder_guardian.py)
satisfy these contracts with NO trading intelligence, so the entire framework
runs end-to-end in observation mode before any strategy exists.

──────────────────────────────────────────────────────────────────────────────
WHAT BELONGS ON EACH SIDE OF THE SEAM
──────────────────────────────────────────────────────────────────────────────
FRAMEWORK (this repo, already implemented):
    - MT5 connection / reconnection / session handling
    - Order placement, modification, partial/full close, margin checks
    - Tick-level monitoring mechanics (TP1/TP2 tiering, time-exit, MFE tracking)
    - Risk-management gates (news, session, velocity, drawdown, margin,
      portfolio allocation, expected-value, cost, anti-duplicate, min-SL, R:R)
    - Position-size calculation from money risk
    - Journaling, checkpointing, analytics, scheduling, alerting, dashboard

STRATEGY ENGINE (implement later — these placeholders intentionally do nothing):
    - Signal generation / direction / confidence
    - Indicators, market-structure detection, tick microstructure
    - Stop-loss and take-profit price math
    - Trade-guardian continuation scoring / structural exits / reversal logic
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from src.domain.entities import Candle, Direction, Timeframe
from src.domain.value_objects import PipCalculator


# ── Signal contract (produced by IStrategyEngine, consumed by EntryGate) ───────

@dataclass
class StrategySignal:
    """
    The single object a strategy engine emits when it wants to enter a trade.

    The strategy is responsible for ALL trading intelligence captured here:
    the direction, a confidence score, and the proposed entry/SL/TP levels
    (the entry/exit math). The framework EntryGate then validates this signal
    against its risk gates and sizes the position — it never invents levels.

    Fields
    ------
    symbol            : instrument symbol (e.g. "XAUUSD")
    direction         : Direction.BULLISH / Direction.BEARISH
    confidence        : 0.0–1.0 combined conviction score
    timestamp         : signal creation time (UTC)
    proposed_entry    : intended entry price (current mid at signal time)
    stop_loss         : proposed SL price (strategy exit math)
    take_profit_1     : first (partial) target price
    take_profit_2     : second/final target price (also broker-side backstop)
    tick_velocity     : measured ticks/sec at signal time (used by velocity gate)
    tq_score          : optional 0.0–1.0 trade-quality score (risk weighting)
    last_swing_support: nearest swing low (optional, for diagnostics)
    last_swing_resistance: nearest swing high (optional, for diagnostics)
    meta              : free-form strategy diagnostics (logged, never acted on)
    """
    symbol:        str
    direction:     Direction
    confidence:    float
    timestamp:     datetime
    proposed_entry: float
    stop_loss:     float
    take_profit_1: float
    take_profit_2: float
    tick_velocity: float = 0.0
    tq_score:      float = 0.0
    last_swing_support:    Optional[float] = None
    last_swing_resistance: Optional[float] = None
    meta:          Dict[str, Any] = field(default_factory=dict)

    @property
    def is_long(self) -> bool:
        return self.direction == Direction.BULLISH


# ── Strategy engine interface ──────────────────────────────────────────────────

class IStrategyEngine(ABC):
    """
    The brain. Receives market data from the event loop and produces signals.

    Lifecycle (called from main_stream.py):
      seed()        once at startup with historical candles
      push_tick()   on every TICK event
      on_candle()   on every closed candle (any timeframe)
      evaluate()    on every M1 close — returns a StrategySignal or None
    """

    @abstractmethod
    def seed(self, symbol: str, timeframe: Timeframe, candles: List[Candle]) -> None:
        """Seed historical candles for one symbol/timeframe at startup."""

    @abstractmethod
    def on_candle(self, candle: Candle) -> None:
        """Feed a freshly closed candle to the engine (structure update)."""

    @abstractmethod
    def push_tick(self, symbol: str, bid: float, ask: float, ts: float) -> None:
        """Push a raw tick into the engine (microstructure update)."""

    @abstractmethod
    def evaluate(
        self,
        symbol:          str,
        forming_m1:      Optional[Candle],
        m1_candles:      List[Candle],
        m5_candles:      List[Candle],
        current_price:   float,
        elapsed_m1_secs: float = 30.0,
    ) -> Optional[StrategySignal]:
        """Run full signal evaluation for one symbol on an M1 close."""

    # ── Optional introspection (default no-ops; override in a real engine) ──

    def get_structure(self, symbol: str) -> Any:
        """
        Return an opaque per-symbol structure object handed to the
        ITradeGuardian on each re-evaluation. The framework never inspects it.
        """
        return None

    def structure_summary(self, symbol: str) -> Dict[str, str]:
        """Human-readable structure snapshot for logging."""
        return {}

    def spread_state(self, symbol: str) -> str:
        """Current spread classification for logging (e.g. 'normal')."""
        return "normal"


# ── Trade Guardian interface (open-trade re-evaluation) ────────────────────────

class GuardianActionType(str, Enum):
    """What the guardian wants the ExecutionService to do to an open trade."""
    TRAIL_SL      = "trail_sl"       # move SL to new_sl (must be a risk improvement)
    PARTIAL_CLOSE = "partial_close"  # close close_fraction of the current lot
    CLOSE         = "close"          # close the whole position


@dataclass
class GuardianAction:
    """A single instruction the guardian returns for one open ticket."""
    ticket:         int
    type:           GuardianActionType
    new_sl:         Optional[float] = None   # required for TRAIL_SL
    close_fraction: Optional[float] = None   # 0.0–1.0, required for PARTIAL_CLOSE
    reason:         str  = ""                 # logged + journaled
    arm_cooldown:   bool = False              # CLOSE only: arm reversal re-entry cooldown


@dataclass
class TradeContext:
    """
    Read-only snapshot of one open trade, handed to the guardian.

    The guardian owns its OWN analytical state (continuation history, etc.)
    keyed by ticket — the framework only exposes broker/position facts here.
    """
    ticket:        int
    symbol:        str
    direction:     Direction
    entry:         float
    current_sl:    float
    take_profit:   float
    lot_size:      float
    initial_risk:  float
    tp1_hit:       bool
    tp2_hit:       bool
    mfe_price:     float
    created_at:    datetime
    grade:         str


@dataclass
class GuardianInput:
    """Everything the guardian needs to re-evaluate one symbol's open trades."""
    symbol:       str
    price:        float
    pip_calc:     PipCalculator
    now:          datetime
    fresh_signal: Optional[StrategySignal]   # signal at this M1 close (may be None)
    structure:    Any                        # opaque object from IStrategyEngine.get_structure
    m1_candles:   List[Candle]
    m5_candles:   List[Candle]
    trades:       List[TradeContext]


class ITradeGuardian(ABC):
    """
    Continuous open-trade re-evaluation. Called on every M1 close with fresh
    market context. Returns a list of mechanical actions; ExecutionService
    applies them (modify SL, partial/full close) and journals the outcome.

    All exit DECISION logic — continuation scoring, structural SL placement,
    reversal detection — lives behind this interface. The placeholder returns
    no actions (trades ride their broker SL/TP and the framework's tick-level
    TP tiering / time-exit mechanics).
    """

    @abstractmethod
    def evaluate(self, ctx: GuardianInput) -> List[GuardianAction]:
        """Return zero or more actions for the open trades in ctx.trades."""
