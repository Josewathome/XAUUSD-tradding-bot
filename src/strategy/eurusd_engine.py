"""
eurusd_engine.py — London Breakout Strategy Engine for EURUSD.

Strategy edge (derived from backtested CSV data Jan–Jun 2026):
  - Define the Asian range using M1 candles from 02:00–10:00 UTC.
  - Skip the day if the range is < 8 pips (noise) or > 40 pips (news/trending).
  - Between 10:00 and 13:00 UTC watch for the first M1 candle that CLOSES
    beyond the Asian High (bullish breakout) or Asian Low (bearish breakout).
  - SL = opposite extreme of the Asian range.
  - TP1 = entry ± SL_distance × 1.5  (60 % partial close by framework)
  - TP2 = entry ± SL_distance × 2.0  (final target / broker backstop)
  - One signal per calendar day per symbol (anti-dupe enforced here).

EURUSD pip = 0.0001 (4th decimal place; 5-digit broker adds a pipette digit).
"""
from __future__ import annotations

import logging
from collections import deque
from datetime import datetime, timezone, date
from typing import Deque, Dict, List, Optional, Tuple

from src.domain.entities import Candle, Direction, Timeframe
from src.strategy.interfaces import (
    GuardianAction,
    GuardianActionType,
    GuardianInput,
    IStrategyEngine,
    ITradeGuardian,
    StrategySignal,
)

logger = logging.getLogger(__name__)
_sig_log = logging.getLogger("signal.EURUSD")

# ── Strategy constants ───────────────────────────────────────────────
_ASIAN_START_UTC  = 2    # 02:00 UTC — Asian session open
_ASIAN_END_UTC    = 10   # 10:00 UTC — Asian session close / London breakout starts
_BREAKOUT_END_UTC = 13   # 13:00 UTC — stop looking for new breakouts

_MIN_RANGE_PIPS = 8.0    # pips — skip days with a sleepy Asian range
_MAX_RANGE_PIPS = 40.0   # pips — skip days that are already trending / news-driven
_PIP            = 0.0001 # EURUSD pip size

_MIN_RANGE = _MIN_RANGE_PIPS * _PIP   # 0.0008
_MAX_RANGE = _MAX_RANGE_PIPS * _PIP   # 0.0040

_SL_BUFFER  = 0.5 * _PIP  # tiny buffer beyond range extreme
_TP1_RR     = 1.5
_TP2_RR     = 2.0
_BE_BUFFER  = 1.0 * _PIP  # breakeven buffer after TP1


def _utc_hour(dt: datetime) -> int:
    if dt.tzinfo is None:
        return dt.hour
    return dt.astimezone(timezone.utc).hour


def _utc_date(dt: datetime) -> date:
    if dt.tzinfo is None:
        return dt.date()
    return dt.astimezone(timezone.utc).date()


def _asian_range(m1_candles: List[Candle], anchor_date: date) -> Tuple[Optional[float], Optional[float]]:
    """Return (asian_high, asian_low) from candles in [02:00, 10:00) UTC on anchor_date."""
    highs, lows = [], []
    for c in m1_candles:
        d = _utc_date(c.time)
        h = _utc_hour(c.time)
        if d == anchor_date and _ASIAN_START_UTC <= h < _ASIAN_END_UTC:
            highs.append(c.high)
            lows.append(c.low)
    if not highs:
        return None, None
    return max(highs), min(lows)


class EURUSDStrategyEngine(IStrategyEngine):
    """London Breakout engine for EURUSD — one signal per day maximum."""

    def __init__(self, symbols: List[str], tick_buffer: int = 512) -> None:
        self._symbols      = list(symbols)
        self._tick_buffer  = tick_buffer
        self._candles: Dict[str, Dict[Timeframe, Deque[Candle]]] = {s: {} for s in symbols}
        self._ticks:   Dict[str, Deque[tuple]]                   = {
            s: deque(maxlen=tick_buffer) for s in symbols
        }
        # Per-symbol: date of the last signal fired (prevents duplicate same-day signals)
        self._last_signal_date: Dict[str, Optional[date]] = {s: None for s in symbols}
        logger.info("EURUSDStrategyEngine ready for %s — London Breakout mode.", symbols)

    # ── Lifecycle ────────────────────────────────────────────────────

    def seed(self, symbol: str, timeframe: Timeframe, candles: List[Candle]) -> None:
        self._candles.setdefault(symbol, {})[timeframe] = deque(candles, maxlen=512)

    def on_candle(self, candle: Candle) -> None:
        buf = self._candles.setdefault(candle.symbol, {})
        buf.setdefault(candle.timeframe, deque(maxlen=512)).append(candle)

    def push_tick(self, symbol: str, bid: float, ask: float, ts: float) -> None:
        self._ticks.setdefault(symbol, deque(maxlen=self._tick_buffer)).append((bid, ask, ts))

    # ── Core evaluation ──────────────────────────────────────────────

    def evaluate(
        self,
        symbol:          str,
        forming_m1:      Optional[Candle],
        m1_candles:      List[Candle],
        m5_candles:      List[Candle],
        current_price:   float,
        elapsed_m1_secs: float = 30.0,
    ) -> Optional[StrategySignal]:

        if symbol not in self._symbols:
            return None

        # Determine the current UTC hour and date from the most recent closed M1
        trigger = m1_candles[-1] if m1_candles else None
        if trigger is None:
            return None

        now_utc  = trigger.time
        utc_h    = _utc_hour(now_utc)
        today    = _utc_date(now_utc)

        # Only look for breakouts inside the London window
        if not (_ASIAN_END_UTC <= utc_h < _BREAKOUT_END_UTC):
            return None

        # One trade per calendar day
        if self._last_signal_date.get(symbol) == today:
            return None

        # Compute Asian range for today
        asian_high, asian_low = _asian_range(m1_candles, today)
        if asian_high is None:
            _sig_log.debug("%s %s — no Asian candles found for today.", symbol, today)
            return None

        asian_range = asian_high - asian_low

        # Range filter
        if asian_range < _MIN_RANGE:
            _sig_log.debug(
                "%s %s — range %.1f pips < %.0f pip minimum (RANGE_FILTERED).",
                symbol, today, asian_range / _PIP, _MIN_RANGE_PIPS,
            )
            return None
        if asian_range > _MAX_RANGE:
            _sig_log.debug(
                "%s %s — range %.1f pips > %.0f pip maximum (RANGE_FILTERED).",
                symbol, today, asian_range / _PIP, _MAX_RANGE_PIPS,
            )
            return None

        # Breakout detection: did the trigger candle CLOSE beyond the range?
        direction: Optional[Direction] = None
        if trigger.close > asian_high:
            direction = Direction.BULLISH
        elif trigger.close < asian_low:
            direction = Direction.BEARISH

        if direction is None:
            return None

        # Compute SL, TP1, TP2
        if direction == Direction.BULLISH:
            entry    = trigger.close
            stop     = asian_low  - _SL_BUFFER
            sl_dist  = entry - stop
            tp1      = entry + sl_dist * _TP1_RR
            tp2      = entry + sl_dist * _TP2_RR
            swing_lo = asian_low
            swing_hi = asian_high
        else:
            entry    = trigger.close
            stop     = asian_high + _SL_BUFFER
            sl_dist  = stop - entry
            tp1      = entry - sl_dist * _TP1_RR
            tp2      = entry - sl_dist * _TP2_RR
            swing_lo = asian_low
            swing_hi = asian_high

        # Reject degenerate levels
        if sl_dist <= 0:
            return None

        # Confidence: base + bonus for clean range and tight MAE at entry
        range_pips = asian_range / _PIP
        confidence = 0.50
        if 10.0 <= range_pips <= 25.0:
            confidence += 0.15   # textbook London range
        if utc_h == 10:
            confidence += 0.10   # very first London candle — cleanest
        elif utc_h == 11:
            confidence += 0.05

        tq = min(1.0, confidence)

        self._last_signal_date[symbol] = today

        sig = StrategySignal(
            symbol          = symbol,
            direction       = direction,
            confidence      = round(min(confidence, 1.0), 3),
            timestamp       = now_utc if now_utc.tzinfo else now_utc.replace(tzinfo=timezone.utc),
            proposed_entry  = round(entry, 5),
            stop_loss       = round(stop,  5),
            take_profit_1   = round(tp1,   5),
            take_profit_2   = round(tp2,   5),
            tick_velocity   = 0.0,
            tq_score        = round(tq, 3),
            last_swing_support    = round(swing_lo, 5),
            last_swing_resistance = round(swing_hi, 5),
            meta = {
                "strategy":    "london_breakout",
                "asian_high":  round(asian_high, 5),
                "asian_low":   round(asian_low,  5),
                "range_pips":  round(range_pips, 1),
                "sl_pips":     round(sl_dist / _PIP, 1),
                "breakout_h":  utc_h,
            },
        )

        _sig_log.info(
            "SIGNAL | %s | %s | entry=%.5f sl=%.5f tp1=%.5f tp2=%.5f | "
            "range=%.1f pips sl=%.1f pips | conf=%.2f",
            symbol, direction.value, entry, stop, tp1, tp2,
            range_pips, sl_dist / _PIP, confidence,
        )
        return sig

    # ── Introspection ────────────────────────────────────────────────

    def get_structure(self, symbol: str):
        return None

    def structure_summary(self, symbol: str) -> Dict[str, str]:
        last = self._last_signal_date.get(symbol)
        return {"last_signal_date": str(last) if last else "none"}

    def spread_state(self, symbol: str) -> str:
        return "normal"


class EURUSDTradeGuardian(ITradeGuardian):
    """
    Breakeven trail after TP1 is hit.
    No structural re-evaluation — the London Breakout is a set-and-monitor trade.
    """

    def evaluate(self, ctx: GuardianInput) -> List[GuardianAction]:
        actions: List[GuardianAction] = []
        for trade in ctx.trades:
            if trade.symbol != "EURUSD":
                continue
            if not trade.tp1_hit:
                continue
            # Move SL to breakeven (entry ± BE_BUFFER) if not already there
            if trade.direction == Direction.BULLISH:
                be = trade.entry + _BE_BUFFER
                if trade.current_sl < be:
                    actions.append(GuardianAction(
                        ticket  = trade.ticket,
                        type    = GuardianActionType.TRAIL_SL,
                        new_sl  = round(be, 5),
                        reason  = "eurusd_breakeven_after_tp1",
                    ))
            else:
                be = trade.entry - _BE_BUFFER
                if trade.current_sl > be:
                    actions.append(GuardianAction(
                        ticket  = trade.ticket,
                        type    = GuardianActionType.TRAIL_SL,
                        new_sl  = round(be, 5),
                        reason  = "eurusd_breakeven_after_tp1",
                    ))
        return actions
