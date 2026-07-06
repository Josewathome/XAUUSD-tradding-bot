"""
execution_service.py — Entry execution + Trade Guardian (M1 Scalper mode).

Two monitoring entry-points:

  run_monitoring_only()     — called on every TICK.
                              Price-level checks: TP1/TP2 partial closes,
                              time-based exit, MFE tracking.

  run_trade_revaluation()   — called on every M1 close (after entry eval).
                              Structural SL trail, thesis re-evaluation,
                              reversal detection.
                              Asks at each bar: "If I had no position open,
                              would I still enter this exact trade?"

Trade Guardian decision matrix (continuation score 0.0–1.0):
  >= REEVAL_HOLD_THRESHOLD (0.55):       thesis intact — hold
  >= REEVAL_DEFENSIVE_THRESHOLD (0.40):  thesis weakening — defensive mode,
                                          tighten SL to nearest structure
  >= REEVAL_EXIT_THRESHOLD (0.25):        partial defensive exit if deep in profit
  <  REEVAL_EXIT_THRESHOLD:              thesis invalidated — close trade
  opposing signal conf >= REVERSAL_CONF: market reversed — close trade
"""
from __future__ import annotations

import logging
import math
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Deque, Dict, List, Optional

import src.config as config
from src.domain.entities import Direction, Timeframe, Trade, TradeSignal, TradeStatus, Candle
from src.domain.repositories import IMarketDataRepository, ITradeRepository
from src.domain.value_objects import BrokerCost, PipCalculator, RiskParameters
from src.application.news_manager import NewsManager
from src.infrastructure.spread_calculator import SpreadCalculator
from src.infrastructure.trade_journal import TradeJournal
from src.strategy.interfaces import (
    GuardianAction,
    GuardianActionType,
    GuardianInput,
    ITradeGuardian,
    StrategySignal,
    TradeContext,
)

logger = logging.getLogger(__name__)


# ── Per-trade runtime state ────────────────────────────────────────────────────

@dataclass
class _OpenTradeState:
    """Runtime state tracked per open trade ticket."""
    symbol:           str
    direction:        Direction
    entry:            float
    original_sl:      float
    current_sl:       float
    take_profit:      float
    tp1_price:        float
    tp2_price:        float
    tp1_hit:          bool  = False
    tp2_hit:          bool  = False
    lot_size:         float = 0.0
    initial_lot:      float = 0.0
    pip_value:        float = 0.0   # per-pip money value per 1.0 lot, account ccy
    initial_risk:     float = 0.0
    mfe_price:        float = 0.0
    accumulated_pips: float = 0.0
    realized_money:   float = 0.0   # money banked on partial closes (price-based, non-MFE)
    digits:           int   = 5
    grade:            str   = "B"
    # Defensive default only — every real construction site (execute_entry_candidate,
    # the outside-bot-position reconstruction in _monitor_open_trades, load_runtime_state)
    # passes created_at explicitly using bot time (MT5-derived). This host-clock
    # fallback should never actually be hit on the live path.
    created_at:       datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))
    sl_trailed:       bool  = False
    recent_signals:   list  = field(default_factory=list)
    mfe_pips_at_signal: float = 0.0
    # Trade Guardian
    entry_confidence:   float = 0.0   # signal confidence at trade open
    last_reeval_score:  float = 1.0   # most recent continuation score
    reeval_count:       int   = 0     # number of M1-close re-evaluations
    defensive_mode:     bool  = False # True when thesis is weakening
    # Autonomous recovery tracking
    cont_score_history: List[float] = field(default_factory=list)   # last 5 cont scores
    recent_prices:      Deque       = field(default_factory=lambda: deque(maxlen=10))

    @property
    def entry_price(self) -> float:
        return self.entry

    @property
    def stop_loss(self) -> float:
        return self.current_sl

    @property
    def risk_reward(self) -> float:
        sl_dist = abs(self.entry - self.original_sl)
        tp_dist = abs(self.take_profit - self.entry)
        return tp_dist / sl_dist if sl_dist > 0 else 0.0

    @property
    def mid(self) -> float:
        return (self.original_sl + self.take_profit) / 2


# ── Entry candidate (produced by EntryGate) ───────────────────────────────────

@dataclass
class EntryCandidate:
    """All data needed to execute a trade, collected during evaluation."""
    symbol:      str
    signal:      TradeSignal
    setup_score: int
    trade_score: int
    grade:       str
    risk_pct:    float
    tp1_price:   float
    tp2_price:   float
    broker_cost: BrokerCost
    pip_calc:    PipCalculator
    pip_value:   float          # per-pip money value per 1.0 lot, account ccy
    digits:      int
    is_phase2:   bool = False

    @property
    def risk_reward(self) -> float:
        sl_dist = abs(self.signal.entry_price - self.signal.stop_loss)
        tp_dist = abs(self.signal.take_profit - self.signal.entry_price)
        return tp_dist / sl_dist if sl_dist > 0 else 0.0


# ── ExecutionService ───────────────────────────────────────────────────────────

class ExecutionService:
    def __init__(
        self,
        market_data:       IMarketDataRepository,
        trade_repo:        ITradeRepository,
        news_manager:      NewsManager,
        spread_calculator: SpreadCalculator,
        symbols:           List[str],
        risk_pct:          float,
        commission:        float,
        min_rr:            float,
        account_currency:  str,
        journal:           TradeJournal,
        guardian:          Optional[ITradeGuardian] = None,
    ) -> None:
        self._md         = market_data
        self._tr         = trade_repo
        self._news       = news_manager
        self._spread     = spread_calculator
        self._symbols    = symbols
        self._risk_pct   = risk_pct
        self._commission = commission
        self._min_rr     = min_rr
        self._currency   = account_currency
        self._journal    = journal
        # Strategy-supplied open-trade re-evaluation brain. Placeholder = no-op,
        # so trades are managed by the mechanical layer only until a real
        # ITradeGuardian is injected.
        from src.strategy.placeholder_guardian import PlaceholderTradeGuardian
        self._guardian   = guardian or PlaceholderTradeGuardian()
        self._trade_states: Dict[int, _OpenTradeState]          = {}
        self._loss_streak:  Dict[str, int]                      = {}
        self._streak_pause: Dict[str, Optional[datetime]]       = {}
        self._reversal_cooldown: Dict[str, datetime]            = {}
        self._eval_ids:     Dict[int, str]                      = {}
        self._mfe_last_logged: Dict[int, datetime]              = {}

    # ── Helpers ───────────────────────────────────────────────────────

    def open_count_for_symbol(self, symbol: str) -> int:
        return sum(1 for p in self._tr.get_open_positions() if p.symbol == symbol)

    def is_symbol_paused(self, symbol: str, now: datetime) -> bool:
        """
        True while a symbol is in a loss-streak cooldown. Enforced by EntryGate
        as a hard gate so the bot actually stops after a run of losses (the old
        code tracked the streak but never blocked on it).
        """
        until = self._streak_pause.get(symbol)
        if until is None:
            return False
        if now >= until:
            self._streak_pause[symbol] = None
            return False
        return True

    def is_reversal_cooldown(self, symbol: str, now: datetime) -> bool:
        """
        True while a symbol is in a post-reversal cooldown — blocks re-entry churn
        after the bot has just flipped/closed a position against a reversal.
        """
        until = self._reversal_cooldown.get(symbol)
        if until is None:
            return False
        if now >= until:
            self._reversal_cooldown[symbol] = None
            return False
        return True

    def _arm_reversal_cooldown(self, symbol: str, now: datetime) -> None:
        secs = getattr(config, "REVERSAL_REENTRY_COOLDOWN_SEC", 120)
        if secs and secs > 0:
            self._reversal_cooldown[symbol] = now + timedelta(seconds=secs)

    def has_tp1_hit_for_symbol(self, symbol: str) -> bool:
        return any(s.tp1_hit for s in self._trade_states.values() if s.symbol == symbol)

    # ── Public monitoring entry-points ────────────────────────────────

    def run_monitoring_only(self, symbol: str, now: datetime) -> None:
        """Tick-level monitoring: TP hits, time exit, MFE tracking."""
        self._prune_closed_positions(now)
        if getattr(config, "MONITOR_ENABLED", True):
            self._monitor_open_trades(symbol, now)

    def run_trade_revaluation(
        self,
        symbol:       str,
        fresh_signal: Optional["StrategySignal"],
        structure:    object,
        m1_candles:   List[Candle],
        m5_candles:   List[Candle],
        now:          datetime,
    ) -> None:
        """
        M1-close re-evaluation of open trades.

        The DECISION logic (continuation scoring, structural SL placement,
        reversal detection) lives entirely in the injected ITradeGuardian
        (strategy layer). This method is pure plumbing:

          1. snapshot the open trades into read-only TradeContext objects,
          2. ask the guardian for actions,
          3. apply them mechanically (trail SL / partial close / close) and
             journal the outcome.

        With the placeholder guardian no actions are returned, so open trades
        are managed purely by run_monitoring_only() (TP1/TP2 tiering, time-exit,
        MFE tracking) and the broker-side SL/TP.

        ``fresh_signal`` may be None (no setup at this M1 close) and ``structure``
        is the opaque per-symbol object from IStrategyEngine.get_structure — both
        are passed straight through to the guardian untouched.
        """
        if not getattr(config, "MONITOR_ENABLED", True):
            return

        open_positions = self._tr.get_open_positions()
        if not open_positions:
            return

        digits   = self._md.get_symbol_digits(symbol)
        pip_calc = PipCalculator(digits=digits)
        try:
            price = self._md.get_current_price(symbol)
        except Exception as exc:
            logger.warning("Revaluation [%s] price unavailable: %s", symbol, exc)
            return

        contexts: List[TradeContext] = []
        for pos in open_positions:
            if pos.symbol != symbol:
                continue
            ticket = int(pos.mt5_ticket or pos.id)
            state  = self._trade_states.get(ticket)
            if state is None:
                continue  # trade not tracked (opened outside bot) — skip
            contexts.append(TradeContext(
                ticket=ticket, symbol=state.symbol, direction=state.direction,
                entry=state.entry, current_sl=state.current_sl,
                take_profit=state.take_profit, lot_size=state.lot_size,
                initial_risk=state.initial_risk,
                tp1_hit=state.tp1_hit, tp2_hit=state.tp2_hit,
                mfe_price=state.mfe_price, created_at=state.created_at,
                grade=state.grade,
            ))

        if not contexts:
            return

        guardian_input = GuardianInput(
            symbol=symbol, price=price, pip_calc=pip_calc, now=now,
            fresh_signal=fresh_signal, structure=structure,
            m1_candles=m1_candles, m5_candles=m5_candles, trades=contexts,
        )
        try:
            actions = self._guardian.evaluate(guardian_input) or []
        except Exception as exc:
            logger.exception("TradeGuardian.evaluate failed [%s]: %s", symbol, exc)
            return

        if not actions:
            return

        if not getattr(config, "MONITOR_ACT_ON_SIGNALS", True):
            for a in actions:
                logger.info(
                    "GUARDIAN (no-act mode) [%s] ticket=%s %s | %s",
                    symbol, a.ticket, a.type.value, a.reason,
                )
            return

        for action in actions:
            try:
                self._apply_guardian_action(symbol, action, pip_calc, now)
            except Exception as exc:
                logger.exception("Guardian action apply failed [%s] ticket=%s: %s",
                                 symbol, action.ticket, exc)

    def _apply_guardian_action(
        self,
        symbol:   str,
        action:   GuardianAction,
        pip_calc: PipCalculator,
        now:      datetime,
    ) -> None:
        """Apply one GuardianAction to the corresponding open trade."""
        ticket = action.ticket
        state  = self._trade_states.get(ticket)
        if state is None:
            return

        if action.type == GuardianActionType.TRAIL_SL:
            if action.new_sl is None:
                return
            # BUG FIXED: interfaces.py's GuardianAction docstring requires
            # new_sl to "be a risk improvement", but this method used to
            # call modify_sl() unconditionally with no check — a buggy or
            # future ITradeGuardian implementation could silently WIDEN
            # risk (e.g. move a BULLISH trade's SL further below price) and
            # the framework would apply it with no guard rail. Both current
            # guardians are no-ops so this was unexercised, but the
            # mechanical layer shouldn't trust the contract without
            # verifying it — that's the whole point of a framework boundary.
            tightens = (
                action.new_sl >= state.current_sl if state.direction == Direction.BULLISH
                else action.new_sl <= state.current_sl
            )
            if not tightens:
                logger.warning(
                    "GUARDIAN_TRAIL [%s] ticket=%s REJECTED — new_sl %.5f would loosen "
                    "risk vs current %.5f for a %s trade (%s)",
                    symbol, ticket, action.new_sl, state.current_sl,
                    state.direction.value.upper(), action.reason,
                )
                return
            if self._tr.modify_sl(ticket, action.new_sl):
                logger.info(
                    "GUARDIAN_TRAIL [%s] ticket=%s | SL %.5f -> %.5f | %s",
                    symbol, ticket, state.current_sl, action.new_sl, action.reason,
                )
                state.current_sl = action.new_sl
                state.sl_trailed = True

        elif action.type == GuardianActionType.PARTIAL_CLOSE:
            frac = action.close_fraction or 0.0
            if frac <= 0:
                return
            close_vol = max(0.01, round(math.floor(state.lot_size * frac * 100) / 100, 2))
            closed = (self._tr.partial_close_trade(ticket, close_vol, action.reason or "GUARDIAN")
                      if hasattr(self._tr, "partial_close_trade") else False)
            if closed:
                try:
                    price = self._md.get_current_price(symbol)
                except Exception:
                    price = state.entry
                live_pips = (pip_calc.price_to_pips(price - state.entry)
                             if state.direction == Direction.BULLISH
                             else pip_calc.price_to_pips(state.entry - price))
                state.realized_money  += live_pips * state.pip_value * close_vol
                state.lot_size         = max(0.0, state.lot_size - close_vol)
                state.accumulated_pips += live_pips * frac
                logger.info(
                    "GUARDIAN_PARTIAL [%s] ticket=%s | closed %.2f lots (%.0f%%) | %s",
                    symbol, ticket, close_vol, frac * 100, action.reason,
                )

        elif action.type == GuardianActionType.CLOSE:
            if self._tr.close_trade(ticket):
                self._finalize_close(ticket, state, action=action.reason or "guardian_close", now=now)
                if action.arm_cooldown:
                    self._arm_reversal_cooldown(symbol, now)
                logger.info(
                    "GUARDIAN_CLOSE [%s] ticket=%s | %s", symbol, ticket, action.reason,
                )

    # ── Trade execution ───────────────────────────────────────────────

    def execute_entry_candidate(self, candidate: EntryCandidate, now: datetime) -> Optional[Trade]:
        """Build, margin-check, and place the trade for a pre-evaluated candidate."""
        symbol = candidate.symbol
        signal = candidate.signal
        trade  = self._build_trade(
            signal, candidate.broker_cost, candidate.pip_calc,
            candidate.pip_value, now, candidate.risk_pct,
        )
        if trade is None:
            return None

        # Exact margin check using live MT5 figures (FAIL CLOSED if unknown).
        req_margin  = (self._tr.get_required_margin(trade)
                       if hasattr(self._tr, "get_required_margin") else None)
        free_margin = self._tr.get_free_margin()
        safety      = getattr(config, "MARGIN_SAFETY_FACTOR", 1.5)
        if req_margin is None or free_margin is None:
            logger.error(
                "MARGIN ABORT | %s | required/free margin unreadable (req=%s free=%s) — rejecting",
                symbol, req_margin, free_margin,
            )
            return None
        if req_margin * safety > free_margin:
            logger.warning(
                "MARGIN SKIP | %s | required=%.2f × safety=%.1f = %.2f > free=%.2f",
                symbol, req_margin, safety, req_margin * safety, free_margin,
            )
            return None
        logger.debug(
            "MARGIN OK | %s | required=%.2f free=%.2f (safety=%.1fx)",
            symbol, req_margin, free_margin, safety,
        )

        ticket = None
        try:
            ticket = self._tr.place_trade(trade)
        except Exception as exc:
            logger.error("Trade placement failed for %s: %s", symbol, exc)
            return None

        if ticket is None:
            return None

        trade.mt5_ticket = ticket
        trade.status     = TradeStatus.OPEN

        # Anchor ALL tracked levels to the ACTUAL fill price and the actual
        # broker SL/TP that place_trade set (it updates trade.* to the live fill
        # and the stop-level-nudged SL/TP). Re-anchor the software TP1/TP2 by the
        # fill slippage so the tiered targets stay at the intended distance from
        # the real entry, not the (now stale) signal-time price.
        fill_delta   = trade.entry_price - signal.entry_price
        tp1_anchored = candidate.tp1_price + fill_delta
        tp2_anchored = trade.take_profit            # broker TP2 backstop (already at fill-ref)
        initial_risk = abs(trade.entry_price - trade.stop_loss)
        if abs(fill_delta) > 0:
            logger.info(
                "FILL_ANCHOR [%s] signal=%.5f fill=%.5f Δ=%.5f | SL=%.5f TP1=%.5f TP2=%.5f",
                symbol, signal.entry_price, trade.entry_price, fill_delta,
                trade.stop_loss, tp1_anchored, tp2_anchored,
            )
        confidence   = float(candidate.trade_score) / 100.0
        state = _OpenTradeState(
            symbol=symbol, direction=signal.direction,
            entry=trade.entry_price, original_sl=trade.stop_loss,
            current_sl=trade.stop_loss, take_profit=trade.take_profit,
            tp1_price=tp1_anchored, tp2_price=tp2_anchored,
            lot_size=trade.lot_size, initial_lot=trade.lot_size,
            pip_value=candidate.pip_value, initial_risk=initial_risk,
            mfe_price=trade.entry_price,
            digits=candidate.digits, grade=candidate.grade,
            created_at=now,
            entry_confidence=confidence,
            last_reeval_score=1.0,
        )
        self._trade_states[ticket] = state

        eval_id = getattr(candidate, "_eval_id", None)
        if eval_id:
            self._eval_ids[ticket] = eval_id
            try:
                from src.application.tick_analytics import get_analytics
                get_analytics().record_trade_executed(eval_id)
            except Exception as _exc:
                logger.debug("TickAnalytics record_trade_executed failed: %s", _exc)

        try:
            self._journal.record_open(
                ticket=ticket,
                symbol=state.symbol,
                direction=state.direction.value if hasattr(state.direction, "value") else state.direction,
                entry=state.entry,
                sl=state.current_sl,
                tp=state.take_profit,
                lot_size=state.lot_size,
                grade=state.grade,
            )
        except Exception as exc:
            logger.warning("Journal record_open failed ticket=%s: %s", ticket, exc)

        phase_tag = "P2" if candidate.is_phase2 else "P1"
        logger.info(
            "TRADE %s | %s %s | score=%d | %.2f lots | entry=%.5f SL=%.5f TP=%.5f",
            phase_tag, symbol, signal.direction.value.upper(),
            candidate.trade_score, trade.lot_size,
            signal.entry_price, signal.stop_loss, signal.take_profit,
        )
        return trade

    # ── Tick-level monitoring ─────────────────────────────────────────

    def _monitor_open_trades(self, symbol: str, now: datetime) -> None:
        """
        Price-level checks on every tick: TP1/TP2 partial closes, time exit.
        Structural analysis is NOT done here (that's run_trade_revaluation).
        """
        open_positions = self._tr.get_open_positions()
        if not open_positions:
            return

        digits   = self._md.get_symbol_digits(symbol)
        pip_calc = PipCalculator(digits=digits)

        for pos in open_positions:
            if pos.symbol != symbol:
                continue

            ticket = int(pos.mt5_ticket or pos.id)
            state  = self._trade_states.get(ticket)
            if state is None:
                # Trade opened outside bot — reconstruct state for monitoring
                tp1_price = (pos.entry_price
                             + abs(pos.entry_price - pos.stop_loss)
                             * getattr(config, "TIERED_TP1_RATIO", 1.5)
                             * (1 if pos.direction == Direction.BULLISH else -1))
                tp2_price = (pos.entry_price
                             + abs(pos.entry_price - pos.stop_loss)
                             * getattr(config, "TIERED_TP2_RATIO", 2.0)
                             * (1 if pos.direction == Direction.BULLISH else -1))
                state = _OpenTradeState(
                    symbol=symbol, direction=pos.direction,
                    entry=pos.entry_price, original_sl=pos.stop_loss,
                    current_sl=pos.stop_loss, take_profit=pos.take_profit,
                    tp1_price=tp1_price, tp2_price=tp2_price,
                    lot_size=pos.lot_size, initial_lot=pos.lot_size,
                    mfe_price=pos.entry_price, digits=digits,
                    # pos.created_at is MT5-derived (trade_repo.get_open_positions()
                    # via mt5_epoch_to_datetime) — always pass it explicitly so the
                    # time-based exit below compares like-for-like against `now`
                    # (also MT5-derived). Without this it silently defaulted to the
                    # dataclass field's host-clock fallback.
                    created_at=pos.created_at,
                )
                # Per-pip value for analytics pip conversion (best-effort).
                try:
                    state.pip_value = self._md.get_pip_value(symbol)
                except Exception:
                    state.pip_value = 0.0
                self._trade_states[ticket] = state

            price = self._md.get_current_price(symbol)

            # Track recent tick prices for recovery conviction scoring
            if price:
                state.recent_prices.append(price)

            # Update MFE
            if state.direction == Direction.BULLISH:
                if price > state.mfe_price:
                    state.mfe_price = price
            else:
                if price < state.mfe_price or state.mfe_price == state.entry:
                    state.mfe_price = price

            mfe_pips  = pip_calc.price_to_pips(abs(state.mfe_price - state.entry))
            live_pips = (pip_calc.price_to_pips(price - state.entry)
                         if state.direction == Direction.BULLISH
                         else pip_calc.price_to_pips(state.entry - price))
            # Clamp MFE sign: for BULLISH mfe_price >= entry (or = entry at start),
            # for BEARISH mfe_price <= entry. Negative live_pips means trade is losing.
            if state.direction == Direction.BULLISH and state.mfe_price < state.entry:
                mfe_pips = -mfe_pips
            elif state.direction == Direction.BEARISH and state.mfe_price > state.entry:
                mfe_pips = -mfe_pips
            duration_min = (now - state.created_at).total_seconds() / 60

            _last_mfe_log = self._mfe_last_logged.get(ticket)
            if _last_mfe_log is None or (now - _last_mfe_log).total_seconds() >= 60:
                logger.info(
                    "MFE  [%s] ticket=%s %s | price=%.5f | live=%+.1fpips mfe=%.1fpips | "
                    "sl=%.5f tp1=%.5f tp2=%.5f | held=%.1fmin | cont=%.2f",
                    symbol, ticket, state.direction.value.upper(),
                    price, live_pips, mfe_pips,
                    state.current_sl, state.tp1_price, state.tp2_price,
                    duration_min, state.last_reeval_score,
                )
                self._mfe_last_logged[ticket] = now

            # ── TP1 partial close ──────────────────────────────────────
            # BUG FIXED: close_vol used to be max(0.01, ...) with no upper
            # clamp against state.lot_size. On a min-lot (0.01) trade that
            # gaps through BOTH tp1_price and tp2_price on the same tick
            # (plausible for XAUUSD in a news spike), this branch closed the
            # entire position (0.01 >= lot_size) but only set tp1_hit — so
            # the TP2 branch below fired again on the same tick against an
            # already-fully-closed position, double-crediting realized_money
            # /accumulated_pips for lots that no longer exist. Clamping
            # close_vol to what's actually left, and marking tp2_hit too
            # when TP1 consumes the whole runner, closes that gap.
            if not state.tp1_hit and state.lot_size > 0:
                tp1_reached = (
                    (state.direction == Direction.BULLISH and price >= state.tp1_price) or
                    (state.direction == Direction.BEARISH and price <= state.tp1_price)
                )
                if tp1_reached:
                    close_pct = (
                        getattr(config, "TIERED_TP1_CLOSE_PCT_A_PLUS", 0.40)
                        if state.grade == "A+" else
                        getattr(config, "TIERED_TP1_CLOSE_PCT_B",      0.50)
                        if state.grade == "B" else
                        getattr(config, "TIERED_TP1_CLOSE_PCT",        0.60)
                    )
                    close_vol = max(
                        0.01, round(math.floor(state.lot_size * close_pct * 100) / 100, 2)
                    )
                    close_vol = min(close_vol, round(state.lot_size, 2))
                    fully_closed = close_vol >= state.lot_size - 1e-9
                    closed = (self._tr.partial_close_trade(ticket, close_vol, "TP1")
                              if hasattr(self._tr, "partial_close_trade") else False)
                    if closed:
                        tp1_pips = pip_calc.price_to_pips(abs(state.tp1_price - state.entry))
                        state.realized_money  += tp1_pips * state.pip_value * close_vol
                        state.lot_size         = max(0.0, state.lot_size - close_vol)
                        state.tp1_hit          = True
                        if fully_closed:
                            # Nothing left for TP2 to act on this tick or any other.
                            state.tp2_hit = True
                        state.accumulated_pips += tp1_pips
                        # Move SL to profit-lock level (entry + fraction of initial risk)
                        profit_lock = state.initial_risk * getattr(config, "TP1_PROFIT_LOCK_R", 0.3)
                        lock_sl = (state.entry + profit_lock if state.direction == Direction.BULLISH
                                   else state.entry - profit_lock)
                        if not fully_closed and self._tr.modify_sl(ticket, lock_sl):
                            state.current_sl = lock_sl
                        logger.info(
                            "TP1 hit | ticket=%s | closed %.2f lots | profit-lock SL → %.5f | "
                            "runner=%.2f lots%s",
                            ticket, close_vol, lock_sl, state.lot_size,
                            " (fully closed — TP2 skipped)" if fully_closed else "",
                        )

            # ── TP2 partial close ──────────────────────────────────────
            if state.tp1_hit and not state.tp2_hit and state.lot_size > 0:
                tp2_reached = (
                    (state.direction == Direction.BULLISH and price >= state.tp2_price) or
                    (state.direction == Direction.BEARISH and price <= state.tp2_price)
                )
                if tp2_reached:
                    close_pct = getattr(config, "TIERED_TP2_CLOSE_PCT", 0.25)
                    close_vol = max(
                        0.01, round(math.floor(state.lot_size * close_pct * 100) / 100, 2)
                    )
                    close_vol = min(close_vol, round(state.lot_size, 2))
                    closed = (self._tr.partial_close_trade(ticket, close_vol, "TP2")
                              if hasattr(self._tr, "partial_close_trade") else False)
                    if closed:
                        tp2_pips = pip_calc.price_to_pips(abs(state.tp2_price - state.entry))
                        state.realized_money  += tp2_pips * state.pip_value * close_vol
                        state.lot_size         = max(0.0, state.lot_size - close_vol)
                        state.tp2_hit          = True
                        state.accumulated_pips += tp2_pips
                        logger.info(
                            "TP2 hit | ticket=%s | closed %.2f lots | runner=%.2f lots",
                            ticket, close_vol, state.lot_size,
                        )

            # ── Time exit (scalper hold limit) ─────────────────────────
            sl_dist      = abs(state.entry - state.current_sl)
            in_profit    = (price > state.entry if state.direction == Direction.BULLISH
                            else price < state.entry)
            profit_ratio = abs(price - state.entry) / sl_dist if sl_dist > 0 else 0.0
            duration_min = (now - state.created_at).total_seconds() / 60
            _hold_overrides = getattr(config, "SCALPER_MAX_HOLD_MINUTES_PER_SYMBOL", {})
            _max_dur        = (_hold_overrides.get(symbol, getattr(config, "SCALPER_MAX_HOLD_MINUTES", 30))
                               if isinstance(_hold_overrides, dict)
                               else getattr(config, "SCALPER_MAX_HOLD_MINUTES", 30))

            should_close = False
            action       = None
            if duration_min >= _max_dur:
                if in_profit:
                    should_close = True
                    action       = "time_exit_profit"
                elif profit_ratio > 0.5:  # deeper than -0.5R
                    should_close = True
                    action       = "time_exit_cutloss"

            if should_close and getattr(config, "MONITOR_ACT_ON_SIGNALS", True):
                if self._tr.close_trade(ticket):
                    self._finalize_close(ticket, state, action=action, now=now)
                    logger.info(
                        "TIME_EXIT [%s] action=%s ticket=%s | held=%.1fmin",
                        symbol, action, ticket, duration_min,
                    )

    # ── Internal utilities ────────────────────────────────────────────

    def _log_trade_summary(self, state: _OpenTradeState, pip_calc: PipCalculator,
                           pnl_money: float) -> None:
        sl_pips    = pip_calc.price_to_pips(abs(state.initial_risk))
        mfe_pips   = pip_calc.price_to_pips(abs(state.mfe_price - state.entry))
        pnl_pips   = self._money_to_pips(state, pnl_money)
        efficiency = pnl_pips / mfe_pips if mfe_pips > 0 else 0
        logger.info(
            "TRADE SUMMARY | %s %s | entry_conf=%.2f | SL=%.1fpips | MFE=%.1fpips | "
            "PnL=%.2f %s (≈%.1fpips) | eff=%.0f%% | TP1=%s TP2=%s | reeval=%d | last_cont=%.2f",
            state.symbol, state.direction.value.upper(),
            state.entry_confidence, sl_pips, mfe_pips,
            pnl_money, self._currency, pnl_pips, efficiency * 100,
            state.tp1_hit, state.tp2_hit,
            state.reeval_count, state.last_reeval_score,
        )

    def _prune_closed_positions(self, now: datetime) -> None:
        """
        Reconcile tracking state for positions MT5 has already closed
        (server-side SL/TP, or manual). Uses BROKER-TRUTH realised P&L — never
        an MFE/intended-level reconstruction.
        """
        open_tickets = {int(p.mt5_ticket or p.id) for p in self._tr.get_open_positions()}
        stale = [t for t in list(self._trade_states.keys()) if t not in open_tickets]
        for t in stale:
            state = self._trade_states.get(t)
            if state is None:
                continue
            self._finalize_close(t, state, action="broker_close", now=now)

    # ── Broker-truth close finalisation ───────────────────────────────

    def _finalize_close(self, ticket: int, state: "_OpenTradeState", action: str, now: datetime) -> float:
        """
        Single funnel for EVERY trade close. Reads the broker's realised net P&L
        (money, account currency) from deal history and journals THAT. Falls back
        to a real price-based estimate (never MFE) only if history is unavailable,
        and logs that loudly. Returns the realised money P&L.
        """
        pip_calc = PipCalculator(digits=state.digits)
        money: Optional[float] = None
        source = "broker"
        try:
            deal = self._tr.get_position_realized_pnl(ticket)
            if deal is not None and deal.get("profit") is not None:
                money = float(deal["profit"])
        except Exception as exc:
            logger.error("CLOSE_PNL [%s] ticket=%s realized P&L query failed: %s",
                         state.symbol, ticket, exc)

        if money is None:
            money = self._estimate_money_pnl(state)
            source = "ESTIMATE(no broker history)"
            logger.error(
                "CLOSE_PNL [%s] ticket=%s broker realised P&L unavailable — "
                "using price-based estimate=%.2f (NOT MFE)",
                state.symbol, ticket, money,
            )

        outcome    = "win" if money > 0 else "loss" if money < 0 else "breakeven"
        pips_equiv = self._money_to_pips(state, money)

        # Drop tracking BEFORE side-effects so a re-entry can't double count.
        self._trade_states.pop(ticket, None)
        self._log_trade_summary(state, pip_calc, money)
        self._journal_close(ticket, money, outcome, pips_equiv)
        self._update_streak(state.symbol, money, now)
        logger.info(
            "TRADE CLOSED action=%s | %s ticket=%s | pnl=%.2f %s [%s] | pips≈%.1f | grade=%s",
            action, state.symbol, ticket, money, self._currency, source,
            pips_equiv, state.grade,
        )
        return money

    def _estimate_money_pnl(self, state: "_OpenTradeState") -> float:
        """
        Real (non-MFE) fallback P&L estimate in account currency: money already
        banked on partial closes + mark-to-market of the remaining lot at the
        CURRENT price. Used only when broker deal history is unavailable.
        """
        try:
            price = self._md.get_current_price(state.symbol)
        except Exception:
            price = state.entry
        pip_calc = PipCalculator(digits=state.digits)
        if state.direction == Direction.BULLISH:
            live_pips = pip_calc.price_to_pips(price - state.entry) if price >= state.entry \
                        else -pip_calc.price_to_pips(state.entry - price)
        else:
            live_pips = pip_calc.price_to_pips(state.entry - price) if price <= state.entry \
                        else -pip_calc.price_to_pips(price - state.entry)
        remaining_money = live_pips * state.pip_value * max(0.0, state.lot_size)
        return state.realized_money + remaining_money

    @staticmethod
    def _money_to_pips(state: "_OpenTradeState", money: float) -> float:
        """
        Approximate pips for analytics from broker money P&L. Sign is exact;
        magnitude uses the initial lot and per-pip value. Returns 0 if unknown.
        """
        denom = state.pip_value * (state.initial_lot or state.lot_size)
        if denom <= 0:
            return 0.0
        return round(money / denom, 2)

    def _update_streak(self, symbol: str, pnl_money: float, now: datetime) -> None:
        """Track loss streak for analytics and alerting (no trading pause by default)."""
        streak = self._loss_streak.get(symbol, 0)
        if pnl_money < 0:
            streak += 1
            self._loss_streak[symbol] = streak
            max_streak = getattr(config, "MAX_CONSECUTIVE_LOSSES",  5)
            pause_h    = getattr(config, "LOSS_STREAK_PAUSE_HOURS", 0)
            if streak >= max_streak:
                if pause_h > 0:
                    self._streak_pause[symbol] = now + timedelta(hours=pause_h)
                    logger.warning(
                        "streak_pause | %s | %d consecutive losses — blocked until %s",
                        symbol, streak, self._streak_pause[symbol].isoformat(),
                    )
                else:
                    logger.warning(
                        "streak_alert | %s | %d consecutive losses — monitoring (no pause)",
                        symbol, streak,
                    )
        else:
            self._loss_streak[symbol] = 0
            self._streak_pause[symbol] = None

    def save_runtime_state(self) -> dict:
        states = {}
        for ticket, s in self._trade_states.items():
            states[str(ticket)] = {
                "symbol": s.symbol, "direction": s.direction.value,
                "entry": s.entry, "original_sl": s.original_sl,
                "current_sl": s.current_sl, "take_profit": s.take_profit,
                "tp1_price": s.tp1_price, "tp2_price": s.tp2_price,
                "tp1_hit": s.tp1_hit, "tp2_hit": s.tp2_hit,
                "lot_size": s.lot_size, "initial_lot": s.initial_lot,
                "pip_value": s.pip_value, "realized_money": s.realized_money,
                "initial_risk": s.initial_risk,
                "mfe_price": s.mfe_price, "accumulated_pips": s.accumulated_pips,
                "digits": s.digits, "grade": s.grade,
                "created_at": s.created_at.isoformat(),
                "sl_trailed": s.sl_trailed,
                "entry_confidence":  s.entry_confidence,
                "last_reeval_score": s.last_reeval_score,
                "reeval_count":      s.reeval_count,
                "defensive_mode":    s.defensive_mode,
            }
        streaks = dict(self._loss_streak)
        pauses  = {sym: dt.isoformat() if dt else None
                   for sym, dt in self._streak_pause.items()}
        return {"trade_states": states, "loss_streak": streaks, "streak_pause": pauses}

    def load_runtime_state(self, data: dict) -> None:
        for ticket_str, s in data.get("trade_states", {}).items():
            try:
                ticket  = int(ticket_str)
                created = datetime.fromisoformat(s["created_at"])
                state   = _OpenTradeState(
                    symbol=s["symbol"], direction=Direction[s["direction"].upper()],
                    entry=float(s["entry"]), original_sl=float(s["original_sl"]),
                    current_sl=float(s["current_sl"]), take_profit=float(s["take_profit"]),
                    tp1_price=float(s["tp1_price"]), tp2_price=float(s["tp2_price"]),
                    tp1_hit=bool(s["tp1_hit"]), tp2_hit=bool(s["tp2_hit"]),
                    lot_size=float(s["lot_size"]),
                    initial_lot=float(s.get("initial_lot", s["lot_size"])),
                    pip_value=float(s.get("pip_value", 0.0)),
                    realized_money=float(s.get("realized_money", 0.0)),
                    initial_risk=float(s["initial_risk"]),
                    mfe_price=float(s["mfe_price"]), accumulated_pips=float(s["accumulated_pips"]),
                    digits=int(s["digits"]), grade=s.get("grade", "B"),
                    created_at=created, sl_trailed=bool(s.get("sl_trailed", False)),
                    entry_confidence=float(s.get("entry_confidence",  0.0)),
                    last_reeval_score=float(s.get("last_reeval_score", 1.0)),
                    reeval_count=int(s.get("reeval_count", 0)),
                    defensive_mode=bool(s.get("defensive_mode", False)),
                )
                self._trade_states[ticket] = state
            except Exception as exc:
                logger.warning("load_runtime_state: skipping ticket %s: %s", ticket_str, exc)
        logger.info("Loaded %d trade states from checkpoint.", len(self._trade_states))
        for sym, count in data.get("loss_streak", {}).items():
            self._loss_streak[sym] = int(count)
        for sym, iso in data.get("streak_pause", {}).items():
            self._streak_pause[sym] = datetime.fromisoformat(iso) if iso else None

    def _journal_close(self, ticket: int, pnl_money: float, outcome: str,
                       pips: float = 0.0) -> None:
        try:
            self._journal.record_close(ticket, round(pnl_money, 2), outcome, round(pips, 2))
        except Exception as exc:
            logger.warning("Journal record_close failed ticket=%s: %s", ticket, exc)

        eval_id = self._eval_ids.pop(ticket, None)
        if eval_id:
            try:
                from src.application.tick_analytics import get_analytics
                get_analytics().record_outcome(eval_id, outcome, pips)
            except Exception as _exc:
                logger.debug("TickAnalytics record_outcome failed: %s", _exc)

    def _build_trade(
        self,
        signal:            TradeSignal,
        broker_cost:       BrokerCost,
        pip_calc:          PipCalculator,
        pip_value:         float,
        now:               datetime,
        risk_pct_override: Optional[float] = None,
    ) -> Optional[Trade]:
        """
        Size the trade from money risk using the CORRECT per-pip value, then fit
        it to real MT5 margin and enforce an absolute money-risk ceiling.

        Returns None (fail closed) when inputs are invalid or even the broker
        minimum lot cannot be financed / would exceed the risk ceiling.
        """
        symbol  = signal.symbol
        balance = self._tr.get_account_balance()
        if balance is None or balance <= 0:
            logger.error("BUILD_TRADE [%s] balance unreadable/≤0 (%s) — rejecting", symbol, balance)
            return None
        if pip_value is None or pip_value <= 0:
            logger.error("BUILD_TRADE [%s] pip_value invalid (%s) — rejecting", symbol, pip_value)
            return None

        eff_risk   = risk_pct_override if risk_pct_override is not None else self._risk_pct
        risk_params = RiskParameters(
            account_balance=balance, risk_percent=eff_risk,
            commission_per_lot=broker_cost.commission_per_lot, min_rr_ratio=self._min_rr,
            account_currency=self._currency,
        )
        sl_pips = pip_calc.price_to_pips(abs(signal.entry_price - signal.stop_loss))
        if sl_pips <= 0:
            logger.error("BUILD_TRADE [%s] sl_pips ≤ 0 — rejecting", symbol)
            return None

        raw_lot = risk_params.lot_size(sl_pips, pip_value)
        if raw_lot <= 0:
            logger.error("BUILD_TRADE [%s] computed raw_lot ≤ 0 — rejecting", symbol)
            return None

        # Broker volume constraints (min / max / step). Fail closed if unknown.
        vmin, vmax, vstep = 0.01, getattr(config, "MAX_LOT_SIZE", 10.0), 0.01
        try:
            vc = self._md.get_volume_constraints(symbol)
            if vc:
                vmin, vmax, vstep = vc
        except Exception as exc:
            logger.warning("BUILD_TRADE [%s] volume constraints unreadable: %s — using defaults", symbol, exc)
        vstep   = vstep if vstep and vstep > 0 else 0.01
        max_lot = min(getattr(config, "MAX_LOT_SIZE", 10.0), vmax)
        # Decimal places implied by the broker volume step (e.g. 0.01→2, 0.001→3)
        # so rounding never corrupts sub-0.01-step instruments.
        step_decimals = max(0, min(8, -int(math.floor(math.log10(vstep))))) if vstep < 1 else 0

        def _round_step(v: float) -> float:
            steps = math.floor(v / vstep + 1e-9)
            return round(steps * vstep, step_decimals)

        lot_size = _round_step(raw_lot)
        lot_size = max(vmin, min(max_lot, lot_size))

        # ── Fit to real MT5 margin (currency-correct, no leverage proxy) ──
        safety = getattr(config, "MARGIN_SAFETY_FACTOR", 1.5)
        try:
            free_margin = self._tr.get_free_margin()
        except Exception as exc:
            logger.error("BUILD_TRADE [%s] free margin unreadable: %s — rejecting", symbol, exc)
            return None
        if free_margin is None or free_margin <= 0:
            logger.error("BUILD_TRADE [%s] free margin %s — rejecting", symbol, free_margin)
            return None

        def _fits(volume: float) -> Optional[bool]:
            """True/False if `volume` fits within free margin × safety; None if unknown."""
            try:
                req = self._tr.get_margin_for_volume(
                    symbol, signal.direction, signal.entry_price, volume
                )
            except Exception:
                req = None
            if req is None:
                return None
            return (req * safety) <= free_margin

        # Reduce by volume step until it fits, or reject at the minimum.
        guard = 0
        while lot_size >= vmin and guard < 1000:
            fit = _fits(lot_size)
            if fit is None:
                logger.error(
                    "BUILD_TRADE [%s] MT5 margin check unavailable — rejecting (fail closed)", symbol,
                )
                return None
            if fit:
                break
            next_lot = _round_step(lot_size - vstep)
            if next_lot < vmin or next_lot >= lot_size:
                logger.info(
                    "BUILD_TRADE [%s] even min lot %.2f does not fit free_margin=%.2f×%.1f — rejecting",
                    symbol, vmin, free_margin, safety,
                )
                return None
            lot_size = next_lot
            guard += 1

        # ── Absolute money-risk ceiling (fail closed) ──────────────
        realized_risk_money = sl_pips * pip_value * lot_size
        max_risk_money      = balance * (getattr(config, "MAX_TRADE_RISK_PCT", 3.0) / 100.0)
        if realized_risk_money > max_risk_money * 1.0001:
            logger.warning(
                "BUILD_TRADE [%s] realized risk %.2f > ceiling %.2f (%.1f%% of %.2f) at min lot %.2f — rejecting",
                symbol, realized_risk_money, max_risk_money,
                getattr(config, "MAX_TRADE_RISK_PCT", 3.0), balance, lot_size,
            )
            return None

        logger.info(
            "SIZE [%s] balance=%.2f risk%%=%.2f sl=%.1fpips pip_val=%.4f → raw=%.4f lot=%.2f "
            "| realized_risk=%.2f (%.2f%%)",
            symbol, balance, eff_risk, sl_pips, pip_value, raw_lot, lot_size,
            realized_risk_money, realized_risk_money / balance * 100,
        )

        return Trade(
            id=str(uuid.uuid4()),
            symbol=symbol,
            direction=signal.direction,
            entry_price=signal.entry_price,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            lot_size=lot_size,
            status=TradeStatus.PENDING,
            created_at=now,
            commission=broker_cost.commission_per_lot * lot_size,
            spread_cost=broker_cost.spread_pips,
        )
