"""
xauusd_engine.py — Triple Confirmation Scalp strategy for XAUUSD M1/M5.

Research basis: institutional-grade design from quantitative analysis of
XAUUSD M1/M5 performance data (multiple backtests, 8,693+ trades studied).

FIVE-GATE ENTRY LOGIC (ALL gates must pass simultaneously):
  Gate 1  SESSION        — London Open 07:00–10:00 UTC or NY Overlap 13:00–17:00 UTC
  Gate 2  ATR REGIME     — H1-proxy ATR between 1.5 and 5.5 USD (not dead, not news)
  Gate 3  TREND FILTER   — EMA(20) vs EMA(50) on M5 sets directional bias
  Gate 4  VALUE ZONE     — Session-anchored VWAP ±1.5 SD minimum deviation
  Gate 5  LIQUIDITY SWEEP— Equal highs/lows swept (LSR ≥ 0.3) + close-back confirm
                           + ATR Expansion Ratio ≥ 1.1 at trigger candle

SL  = sweep candle extreme + $0.50 buffer; clamped to [$1.50, $5.00]
TP1 = 1.5R (framework closes 60% of position at TP1 per TIERED_TP config)
TP2 = 2.0R (framework trails remainder)

Conservative expectancy: +0.22R–+0.38R per trade | Win rate: 60–68%
Target: 3–7 trades/day (session-filtered, quality over quantity)

Wiring: in main_stream.py, replace PlaceholderStrategyEngine with
  XAUUSDStrategyEngine  (via build_engine)
  XAUUSDTradeGuardian   (via build_guardian)
"""
from __future__ import annotations

import logging
import math
from collections import deque
from datetime import datetime, timezone
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

logger   = logging.getLogger(__name__)
_sig_log = logging.getLogger("signal.XAUUSD")


# ── Session windows (UTC hours, inclusive start / exclusive end) ────────────
_LONDON_OPEN_START  = 7
_LONDON_OPEN_END    = 10
_NY_OVERLAP_START   = 13
_NY_OVERLAP_END     = 17

# ── ATR regime thresholds (H1-proxy, as a % of current price) ──────────────
# BUG THIS FIXES: these used to be fixed USD amounts (1.5 / 4.5 / 5.5), which
# only stay meaningful at whatever gold price they were calibrated against.
# ATR is a price-level indicator — the same relative volatility produces a
# larger dollar reading as the underlying price rises. Gold traded roughly
# $2000-2700 when fixed thresholds this size were a sensible "dead market /
# elevated / extreme" split; at today's ~$4185 (up ~25% YoY as of 2026-07),
# the same genuinely normal volatility now produces a dollar ATR that
# permanently exceeds the old $5.5 "extreme" ceiling, incorrectly blocking
# entries in what is actually an ordinary trading regime for gold's current
# price level. Expressing the bands as a % of price keeps them correctly
# calibrated as gold's price moves, with no need to hand-tune the dollar
# amounts every time gold makes a large multi-month move.
#
# Values below are the old thresholds ÷ ~$2400 (the rough midpoint of the
# price range they were likely set against) — i.e. this preserves the
# original strategy's intended volatility bands, just re-expressed so they
# scale correctly going forward. Retune here if live behavior suggests these
# percentages are still off for the current regime.
_H1_ATR_MIN_PCT  = 0.000625   # 0.0625% — below: dead/Asian market — skip
_H1_ATR_WARN_PCT = 0.001875   # 0.1875% — above: elevated (still trade, reduce confidence)
_H1_ATR_MAX_PCT  = 0.0022917  # 0.22917% — above: news/extreme regime — skip

# ── ATR expansion ratio (M5 7-period current / 20-period avg) ──────────────
_AER_THRESHOLD = 1.1

# ── Liquidity sweep thresholds ──────────────────────────────────────────────
_LSR_MIN            = 0.30  # sweep must move ≥ 30% of ATR beyond equal level
_EQUAL_TOLERANCE    = 0.15  # two levels qualify as "equal" if within 15% of ATR
_SWEEP_LOOKBACK     = 20    # M5 candles to scan for equal highs/lows

# ── VWAP deviation thresholds (standard deviations) ────────────────────────
_VWAP_SD_MIN = 1.5   # minimum stretch before considering a reversion
_VWAP_SD_MAX = 3.5   # extreme stretch — likely news, skip

# ── EMA periods ─────────────────────────────────────────────────────────────
_EMA_FAST = 20
_EMA_SLOW = 50

# ── SL / TP ─────────────────────────────────────────────────────────────────
_SL_BUFFER      = 0.50   # USD buffer beyond sweep candle extreme
_MIN_SL_DIST    = 1.50   # absolute minimum SL distance (USD)
_MAX_SL_DIST    = 5.00   # absolute maximum SL distance (USD)
_TP1_RR         = 1.5
_TP2_RR         = 2.0
_BE_BUFFER      = 0.20   # breakeven SL buffer (USD) above/below entry

# ── Confidence scoring weights ───────────────────────────────────────────────
_CONF_BASE         = 0.45
_CONF_SESSION_PEAK = 0.10   # overlap vs open
_CONF_VWAP_SD      = 0.15   # for |Z| ≥ 2.0
_CONF_ATR_EXPAND   = 0.10
_CONF_SWEEP_CLEAN  = 0.10   # bounce body ≥ 60% of candle range
_CONF_ATR_ELEVATED = -0.10  # penalty: ATR > _H1_ATR_WARN_PCT * price


# ── Pure math helpers ────────────────────────────────────────────────────────

def _ema(values: List[float], period: int) -> float:
    """Compute EMA over values[-period:] using standard smoothing."""
    if len(values) < period:
        return sum(values) / len(values) if values else 0.0
    k   = 2.0 / (period + 1)
    val = values[-period]
    for v in values[-period + 1:]:
        val = v * k + val * (1.0 - k)
    return val


def _ema_series(values: List[float], period: int) -> List[float]:
    """Return EMA series for the full values list."""
    if not values:
        return []
    k   = 2.0 / (period + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(v * k + out[-1] * (1.0 - k))
    return out


def _atr(candles: List[Candle], period: int) -> float:
    """Average True Range over the last `period` candles."""
    if len(candles) < 2:
        return 0.0
    trs: List[float] = []
    for i in range(1, len(candles)):
        c  = candles[i]
        pc = candles[i - 1].close
        trs.append(max(c.high - c.low, abs(c.high - pc), abs(c.low - pc)))
    if not trs:
        return 0.0
    recent = trs[-period:] if len(trs) >= period else trs
    return sum(recent) / len(recent)


def _h1_atr_proxy(m5_candles: List[Candle], n_h1_bars: int = 6) -> float:
    """
    Approximate H1 ATR by aggregating M5 candles into H1-equivalent bars.
    Each group of 12 M5 bars = 1 H1 bar. Returns average H1 range over
    n_h1_bars periods.
    """
    if len(m5_candles) < 12:
        return 0.0
    h1_ranges: List[float] = []
    for i in range(n_h1_bars):
        end   = len(m5_candles) - i * 12
        start = end - 12
        if start < 0:
            break
        window = m5_candles[start:end]
        h1_high = max(c.high for c in window)
        h1_low  = min(c.low  for c in window)
        # True range against the candle before the window
        if start > 0:
            prev_close = m5_candles[start - 1].close
            tr = max(h1_high - h1_low,
                     abs(h1_high - prev_close),
                     abs(h1_low  - prev_close))
        else:
            tr = h1_high - h1_low
        h1_ranges.append(tr)
    return sum(h1_ranges) / len(h1_ranges) if h1_ranges else 0.0


def _vwap_bands(
    candles: List[Candle],
    session_start_idx: int = 0,
) -> Tuple[float, float]:
    """
    Compute session-anchored VWAP and one standard deviation.
    Returns (vwap, sd). Falls back to equal-weight if volume is zero.
    """
    session = candles[session_start_idx:]
    if not session:
        return 0.0, 0.0

    tp_list  = [(c.high + c.low + c.close) / 3.0 for c in session]
    vol_list = [max(c.volume, 1.0) for c in session]   # avoid divide-by-zero

    total_vol  = sum(vol_list)
    vwap       = sum(tp * v for tp, v in zip(tp_list, vol_list)) / total_vol

    variance   = sum(v * (tp - vwap) ** 2 for tp, v in zip(tp_list, vol_list)) / total_vol
    sd         = math.sqrt(max(variance, 0.0))

    # If all volumes were zero (broker returns no volume), fall back
    raw_vols = [c.volume for c in session]
    if all(v == 0.0 for v in raw_vols):
        mean  = sum(tp_list) / len(tp_list)
        sd_fb = math.sqrt(sum((tp - mean) ** 2 for tp in tp_list) / len(tp_list))
        return mean, sd_fb

    return vwap, sd


def _session_start_idx(candles: List[Candle], open_hour: int) -> int:
    """
    Return the index of the first candle at or after open_hour.
    Scans backwards from end so we find the MOST RECENT session start.

    candle.time arrives already offset-corrected to real-world hours (see
    market_data_repo.mt5_epoch_to_datetime) — no further conversion happens here.
    """
    for i in range(len(candles) - 1, -1, -1):
        t = candles[i].time
        utc_t = t.replace(tzinfo=timezone.utc) if t.tzinfo is None else t.astimezone(timezone.utc)
        hour = utc_t.hour
        if hour == open_hour and utc_t.minute == 0:
            return i
        if hour == open_hour:
            # First candle AT or AFTER open_hour
            if i == 0 or _utc_hour(candles[i - 1].time) < open_hour:
                return i
    return 0


def _utc_hour(dt: datetime) -> int:
    """
    Hour-of-day of a candle timestamp.

    candle.time is already MT5 server time, offset-corrected to align with
    real-world session hours (see market_data_repo.mt5_epoch_to_datetime —
    every Candle is stamped through it before it reaches this engine). This
    function no longer applies any correction itself; it just reads the hour
    off an already-correct, tz-aware timestamp.
    """
    utc = dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
    return utc.hour


def _session_tag(utc_hour: int) -> Optional[str]:
    """Return session tag or None if outside trading windows."""
    if _LONDON_OPEN_START <= utc_hour < _LONDON_OPEN_END:
        return "LONDON_OPEN"
    if _NY_OVERLAP_START <= utc_hour < _NY_OVERLAP_END:
        return "NY_OVERLAP"
    return None


def _find_equal_lows(
    candles: List[Candle],
    atr: float,
    lookback: int = _SWEEP_LOOKBACK,
) -> Optional[float]:
    """
    Find the most recent cluster of equal lows (sell-side liquidity pool).
    Returns the cluster price level or None if no cluster found.
    Two lows qualify as 'equal' if they are within EQUAL_TOLERANCE × ATR.
    """
    if atr <= 0 or len(candles) < 4:
        return None
    tolerance = _EQUAL_TOLERANCE * atr
    window    = candles[-(lookback + 1):-1] if len(candles) > lookback + 1 else candles[:-1]

    lows = [c.low for c in window]
    # Find any pair (or cluster) of lows within tolerance
    best_level: Optional[float] = None
    best_count = 1
    for i, l1 in enumerate(lows):
        count = sum(1 for l2 in lows if abs(l1 - l2) <= tolerance)
        if count >= 2 and count > best_count:
            best_count = count
            best_level = l1
    return best_level


def _find_equal_highs(
    candles: List[Candle],
    atr: float,
    lookback: int = _SWEEP_LOOKBACK,
) -> Optional[float]:
    """
    Find the most recent cluster of equal highs (buy-side liquidity pool).
    """
    if atr <= 0 or len(candles) < 4:
        return None
    tolerance = _EQUAL_TOLERANCE * atr
    window    = candles[-(lookback + 1):-1] if len(candles) > lookback + 1 else candles[:-1]

    highs = [c.high for c in window]
    best_level: Optional[float] = None
    best_count = 1
    for i, h1 in enumerate(highs):
        count = sum(1 for h2 in highs if abs(h1 - h2) <= tolerance)
        if count >= 2 and count > best_count:
            best_count = count
            best_level = h1
    return best_level


def _check_bullish_sweep(
    trigger: Candle,
    equal_low: float,
    atr: float,
) -> Tuple[bool, float]:
    """
    Check if `trigger` (last closed M5 candle) swept below equal_low
    and closed back above it.

    Returns (swept, lsr) where lsr = Liquidity Sweep Ratio.
    """
    if trigger.low >= equal_low:
        return False, 0.0
    if trigger.close <= equal_low:
        return False, 0.0
    sweep_dist = equal_low - trigger.low
    lsr        = sweep_dist / atr if atr > 0 else 0.0
    return lsr >= _LSR_MIN, lsr


def _check_bearish_sweep(
    trigger: Candle,
    equal_high: float,
    atr: float,
) -> Tuple[bool, float]:
    """
    Check if `trigger` swept above equal_high and closed back below it.
    """
    if trigger.high <= equal_high:
        return False, 0.0
    if trigger.close >= equal_high:
        return False, 0.0
    sweep_dist = trigger.high - equal_high
    lsr        = sweep_dist / atr if atr > 0 else 0.0
    return lsr >= _LSR_MIN, lsr


def _body_ratio(candle: Candle) -> float:
    """Body size as a fraction of total candle range."""
    rng = candle.total_range
    return candle.body_size / rng if rng > 0 else 0.0


# ── Strategy Engine ──────────────────────────────────────────────────────────

class XAUUSDStrategyEngine(IStrategyEngine):
    """
    Triple Confirmation Scalp engine for XAUUSD.

    All state is per-symbol. The engine is stateless across restarts except
    for candle buffers populated via seed() — which the framework calls at
    startup from historical MT5 data.
    """

    def __init__(
        self,
        symbols: List[str],
        tick_buffer: int = 512,
    ) -> None:
        # candle.time arrives already offset-corrected (see
        # market_data_repo.mt5_epoch_to_datetime) — this engine reads it
        # directly with no further conversion.
        self._symbols      = list(symbols)
        self._candles: Dict[str, Dict[Timeframe, Deque[Candle]]] = {
            s: {} for s in symbols
        }
        self._ticks: Dict[str, Deque[tuple]] = {
            s: deque(maxlen=tick_buffer) for s in symbols
        }
        # Prevent firing the same M5 signal 5× per M5 bar (once per M1 close)
        self._last_signal_m5_time: Dict[str, Optional[datetime]] = {
            s: None for s in symbols
        }
        # Current market structure summary (for guardian + logging)
        self._structure: Dict[str, Dict] = {s: {} for s in symbols}

        logger.info(
            "XAUUSDStrategyEngine ready for %d symbol(s): %s",
            len(symbols), ", ".join(symbols),
        )

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def seed(self, symbol: str, timeframe: Timeframe, candles: List[Candle]) -> None:
        if symbol not in self._candles:
            self._candles[symbol] = {}
        maxlen = 300 if timeframe == Timeframe.M1 else 200
        self._candles[symbol][timeframe] = deque(candles, maxlen=maxlen)
        logger.info("Seeded %s %s: %d bars", symbol, timeframe.value, len(candles))

    def on_candle(self, candle: Candle) -> None:
        buf = self._candles.setdefault(candle.symbol, {})
        maxlen = 300 if candle.timeframe == Timeframe.M1 else 200
        dq = buf.setdefault(candle.timeframe, deque(maxlen=maxlen))
        dq.append(candle)

    def push_tick(self, symbol: str, bid: float, ask: float, ts: float) -> None:
        if symbol not in self._ticks:
            self._ticks[symbol] = deque(maxlen=512)
        self._ticks[symbol].append((bid, ask, ts))

    # ── Main evaluation ───────────────────────────────────────────────────────

    def evaluate(
        self,
        symbol:          str,
        forming_m1:      Optional[Candle],
        m1_candles:      List[Candle],
        m5_candles:      List[Candle],
        current_price:   float,
        elapsed_m1_secs: float = 30.0,
    ) -> Optional[StrategySignal]:
        """
        Run full five-gate evaluation on each M1 close.
        Returns a StrategySignal if ALL gates pass, otherwise None.
        """
        _min_bars = max(_EMA_SLOW, 12, _SWEEP_LOOKBACK + 2)
        if not m5_candles or len(m5_candles) < _min_bars:
            self._update_structure(
                symbol, "no_signal",
                f"warming up ({len(m5_candles) if m5_candles else 0}/{_min_bars} M5 bars)"
            )
            return None

        trigger_m5 = m5_candles[-1]   # Most recently CLOSED M5 candle

        # ── Anti-duplicate: only evaluate once per M5 bar ─────────────────
        last_sig_time = self._last_signal_m5_time.get(symbol)
        if last_sig_time is not None and trigger_m5.time == last_sig_time:
            return None   # Already evaluated this M5 bar

        # ── GATE 1: Session check ──────────────────────────────────────────
        utc_hr  = _utc_hour(trigger_m5.time)
        session = _session_tag(utc_hr)
        if session is None:
            self._update_structure(symbol, "no_signal", "outside session")
            return None

        # ── GATE 2: ATR regime ─────────────────────────────────────────────
        # Thresholds are % of current price (see _H1_ATR_*_PCT comment above)
        # so the dead/elevated/extreme bands stay correctly calibrated as
        # gold's price moves, instead of drifting stale like fixed USD amounts.
        h1_atr_min  = _H1_ATR_MIN_PCT  * current_price
        h1_atr_warn = _H1_ATR_WARN_PCT * current_price
        h1_atr_max  = _H1_ATR_MAX_PCT  * current_price

        m5_atr7   = _atr(m5_candles, 7)
        h1_atr    = _h1_atr_proxy(m5_candles, n_h1_bars=6)
        if h1_atr < h1_atr_min:
            self._update_structure(
                symbol, "no_signal", f"ATR too low ({h1_atr:.2f} < {h1_atr_min:.2f})"
            )
            return None
        if h1_atr > h1_atr_max:
            self._update_structure(
                symbol, "no_signal", f"ATR extreme ({h1_atr:.2f} > {h1_atr_max:.2f})"
            )
            return None
        atr_elevated = h1_atr > h1_atr_warn

        # ── GATE 3: Trend filter (EMA 20/50 on M5) ────────────────────────
        closes = [c.close for c in m5_candles]
        ema20  = _ema(closes, _EMA_FAST)
        ema50  = _ema(closes, _EMA_SLOW)
        last_close = m5_candles[-1].close

        bullish_trend = last_close > ema20 > ema50
        bearish_trend = last_close < ema20 < ema50

        if not bullish_trend and not bearish_trend:
            self._update_structure(symbol, "no_signal",
                                   f"EMA choppy (price={last_close:.2f} ema20={ema20:.2f} ema50={ema50:.2f})")
            return None

        direction = Direction.BULLISH if bullish_trend else Direction.BEARISH

        # ── GATE 4: VWAP value zone ────────────────────────────────────────
        open_hour  = _LONDON_OPEN_START if session == "LONDON_OPEN" else _NY_OVERLAP_START
        sess_idx   = _session_start_idx(m5_candles, open_hour)
        vwap, vwap_sd = _vwap_bands(m5_candles, session_start_idx=sess_idx)

        if vwap <= 0 or vwap_sd < 0.01:
            self._update_structure(symbol, "no_signal", "VWAP unavailable")
            return None

        z_score = (last_close - vwap) / vwap_sd

        # Value zone: price must be stretched AGAINST the sweep direction
        if direction == Direction.BULLISH and z_score > -_VWAP_SD_MIN:
            self._update_structure(symbol, "no_signal",
                                   f"Price not cheap vs VWAP (Z={z_score:.2f})")
            return None
        if direction == Direction.BEARISH and z_score < _VWAP_SD_MIN:
            self._update_structure(symbol, "no_signal",
                                   f"Price not expensive vs VWAP (Z={z_score:.2f})")
            return None
        if abs(z_score) > _VWAP_SD_MAX:
            self._update_structure(symbol, "no_signal",
                                   f"Z-score extreme (Z={z_score:.2f}) — likely news")
            return None

        # ── GATE 5a: Liquidity sweep detection ────────────────────────────
        swept       = False
        lsr         = 0.0
        sweep_level = 0.0

        if direction == Direction.BULLISH:
            equal_low = _find_equal_lows(m5_candles, m5_atr7)
            if equal_low is not None:
                swept, lsr = _check_bullish_sweep(trigger_m5, equal_low, m5_atr7)
                sweep_level = equal_low
        else:
            equal_high = _find_equal_highs(m5_candles, m5_atr7)
            if equal_high is not None:
                swept, lsr = _check_bearish_sweep(trigger_m5, equal_high, m5_atr7)
                sweep_level = equal_high

        if not swept:
            self._update_structure(symbol, "no_signal",
                                   f"No valid liquidity sweep ({direction.value})")
            return None

        # ── GATE 5b: ATR Expansion Ratio ──────────────────────────────────
        # AER = current ATR(7) / avg ATR(7) over last 20 bars
        atr_history = [
            _atr(m5_candles[max(0, i - 7):i], 7)
            for i in range(len(m5_candles) - 19, len(m5_candles) + 1)
            if i >= 7
        ]
        avg_atr = sum(atr_history) / len(atr_history) if atr_history else m5_atr7
        aer     = m5_atr7 / avg_atr if avg_atr > 0 else 1.0

        if aer < _AER_THRESHOLD:
            self._update_structure(symbol, "no_signal",
                                   f"ATR expansion insufficient (AER={aer:.2f})")
            return None

        # ── All gates passed: build signal ────────────────────────────────
        self._last_signal_m5_time[symbol] = trigger_m5.time

        signal = self._build_signal(
            symbol        = symbol,
            direction     = direction,
            trigger       = trigger_m5,
            current_price = current_price,
            m5_atr7       = m5_atr7,
            h1_atr        = h1_atr,
            sweep_level   = sweep_level,
            lsr           = lsr,
            z_score       = z_score,
            aer           = aer,
            session       = session,
            atr_elevated  = atr_elevated,
            vwap          = vwap,
            vwap_sd       = vwap_sd,
            ema20         = ema20,
            ema50         = ema50,
        )

        if signal is None:
            return None

        self._update_structure(symbol, "signal",
                               f"{direction.value.upper()} conf={signal.confidence:.2f} "
                               f"Z={z_score:.2f} LSR={lsr:.2f} AER={aer:.2f}")

        logger.info(
            "XAUUSD SIGNAL ✓ [%s] %s | session=%s | Z=%.2f | LSR=%.2f | AER=%.2f "
            "| h1_atr=%.2f | ema20=%.2f ema50=%.2f | SL=%.2f TP1=%.2f TP2=%.2f",
            symbol, direction.value.upper(), session,
            z_score, lsr, aer, h1_atr, ema20, ema50,
            signal.stop_loss, signal.take_profit_1, signal.take_profit_2,
        )
        _sig_log.info(
            "SIGNAL | %s | %s | entry=%.2f sl=%.2f tp1=%.2f tp2=%.2f | "
            "session=%s Z=%.2f LSR=%.2f AER=%.2f h1_atr=%.2f conf=%.2f tq=%.2f",
            symbol, direction.value.upper(),
            signal.proposed_entry, signal.stop_loss, signal.take_profit_1, signal.take_profit_2,
            session, z_score, lsr, aer, h1_atr, signal.confidence, signal.tq_score,
        )
        return signal

    # ── Signal builder ────────────────────────────────────────────────────────

    def _build_signal(
        self,
        symbol:        str,
        direction:     Direction,
        trigger:       Candle,
        current_price: float,
        m5_atr7:       float,
        h1_atr:        float,
        sweep_level:   float,
        lsr:           float,
        z_score:       float,
        aer:           float,
        session:       str,
        atr_elevated:  bool,
        vwap:          float,
        vwap_sd:       float,
        ema20:         float,
        ema50:         float,
    ) -> Optional[StrategySignal]:
        """Compute SL/TP prices and confidence, return StrategySignal."""
        is_long = direction == Direction.BULLISH

        # SL: sweep candle extreme + buffer, clamped to [MIN, MAX]
        if is_long:
            raw_sl = trigger.low - _SL_BUFFER
        else:
            raw_sl = trigger.high + _SL_BUFFER

        sl_dist = abs(current_price - raw_sl)
        sl_dist = max(_MIN_SL_DIST, min(_MAX_SL_DIST, sl_dist))

        if is_long:
            stop_loss  = current_price - sl_dist
            tp1        = current_price + sl_dist * _TP1_RR
            tp2        = current_price + sl_dist * _TP2_RR
        else:
            stop_loss  = current_price + sl_dist
            tp1        = current_price - sl_dist * _TP1_RR
            tp2        = current_price - sl_dist * _TP2_RR

        # Sanity check: R:R must be achievable
        tp1_dist = abs(tp1 - current_price)
        if tp1_dist < sl_dist * _TP1_RR * 0.9:
            logger.debug("Signal rejected: R:R too low (sl=%.2f tp1=%.2f)", sl_dist, tp1_dist)
            return None

        # Confidence scoring
        conf = _CONF_BASE
        if session == "NY_OVERLAP":
            conf += _CONF_SESSION_PEAK
        if abs(z_score) >= 2.0:
            conf += _CONF_VWAP_SD
        if aer >= 1.3:
            conf += _CONF_ATR_EXPAND
        if _body_ratio(trigger) >= 0.60:
            conf += _CONF_SWEEP_CLEAN
        if atr_elevated:
            conf += _CONF_ATR_ELEVATED
        conf = round(min(1.0, max(0.0, conf)), 3)

        # Trade quality score (tq_score): 0–1 composite
        tq = min(1.0, (lsr / 1.0) * 0.4 + (min(abs(z_score), 3.0) / 3.0) * 0.4 +
                 (min(aer, 2.0) / 2.0) * 0.2)
        tq = round(tq, 3)

        # Tick velocity from recent ticks
        ticks = list(self._ticks.get(symbol, []))
        if len(ticks) >= 2:
            dt = ticks[-1][2] - ticks[0][2]
            vel = len(ticks) / dt if dt > 0 else 0.0
        else:
            vel = 0.0

        return StrategySignal(
            symbol        = symbol,
            direction     = direction,
            confidence    = conf,
            timestamp     = trigger.time,
            proposed_entry= current_price,
            stop_loss     = stop_loss,
            take_profit_1 = tp1,
            take_profit_2 = tp2,
            tick_velocity = vel,
            tq_score      = tq,
            last_swing_support    = sweep_level if is_long else None,
            last_swing_resistance = sweep_level if not is_long else None,
            meta = {
                "session"   : session,
                "z_score"   : round(z_score, 3),
                "lsr"       : round(lsr, 3),
                "aer"       : round(aer, 3),
                "h1_atr"    : round(h1_atr, 3),
                "m5_atr7"   : round(m5_atr7, 3),
                "sl_dist"   : round(sl_dist, 3),
                "vwap"      : round(vwap, 3),
                "vwap_sd"   : round(vwap_sd, 3),
                "ema20"     : round(ema20, 3),
                "ema50"     : round(ema50, 3),
                "strategy"  : "triple_confirmation_scalp",
            },
        )

    # ── Introspection ─────────────────────────────────────────────────────────

    def get_structure(self, symbol: str):
        return self._structure.get(symbol)

    def structure_summary(self, symbol: str) -> Dict[str, str]:
        s = self._structure.get(symbol, {})
        return {
            "M1": s.get("m1", "?"),
            "M5": s.get("m5", "?"),
        }

    def spread_state(self, symbol: str) -> str:
        ticks = list(self._ticks.get(symbol, []))
        if len(ticks) < 2:
            return "unknown"
        bid, ask, _ = ticks[-1]
        spread = ask - bid
        if spread < 0.50:
            return "tight"
        if spread < 1.50:
            return "normal"
        return "wide"

    def _update_structure(self, symbol: str, status: str, detail: str) -> None:
        self._structure[symbol] = {"m5": status, "m1": detail}


# ── Trade Guardian ────────────────────────────────────────────────────────────

class XAUUSDTradeGuardian(ITradeGuardian):
    """
    Continuous open-trade re-evaluation for XAUUSD.

    Actions taken:
      1. TP1 hit and not yet at breakeven  → trail SL to breakeven + buffer
      2. EMA structure flips against trade AND trade is losing (<0.3R) → close
      3. (All other management delegated to the framework's tick-level TP tiering
         and time-based exit in ExecutionService.)
    """

    def evaluate(self, ctx: GuardianInput) -> List[GuardianAction]:
        actions: List[GuardianAction] = []
        if not ctx.trades:
            return actions

        m5 = ctx.m5_candles
        if len(m5) < _EMA_SLOW:
            return actions

        closes = [c.close for c in m5]
        ema20  = _ema(closes, _EMA_FAST)
        ema50  = _ema(closes, _EMA_SLOW)
        price  = ctx.price

        for trade in ctx.trades:
            try:
                action = self._evaluate_trade(trade, price, ema20, ema50)
                if action is not None:
                    actions.append(action)
            except Exception as exc:
                logger.debug("Guardian error ticket=%d: %s", trade.ticket, exc)

        return actions

    def _evaluate_trade(self, trade, price: float, ema20: float, ema50: float) \
            -> Optional[GuardianAction]:
        is_long = trade.direction == Direction.BULLISH

        # ── Breakeven trail after TP1 ──────────────────────────────────────
        if trade.tp1_hit and not trade.tp2_hit:
            if is_long:
                be_sl = trade.entry + _BE_BUFFER
                if be_sl > trade.current_sl + 0.01:
                    return GuardianAction(
                        ticket  = trade.ticket,
                        type    = GuardianActionType.TRAIL_SL,
                        new_sl  = be_sl,
                        reason  = "TP1 hit — trail to breakeven",
                    )
            else:
                be_sl = trade.entry - _BE_BUFFER
                if be_sl < trade.current_sl - 0.01:
                    return GuardianAction(
                        ticket  = trade.ticket,
                        type    = GuardianActionType.TRAIL_SL,
                        new_sl  = be_sl,
                        reason  = "TP1 hit — trail to breakeven",
                    )

        # ── EMA structure inversion ────────────────────────────────────────
        ema_opposing = (
            (is_long  and price < ema20 < ema50) or
            (not is_long and price > ema20 > ema50)
        )
        if ema_opposing:
            sl_dist = abs(trade.entry - trade.current_sl)
            pnl_r   = ((price - trade.entry) / sl_dist
                       if is_long else (trade.entry - price) / sl_dist) \
                      if sl_dist > 0 else 0.0
            if pnl_r < 0.3:
                return GuardianAction(
                    ticket      = trade.ticket,
                    type        = GuardianActionType.CLOSE,
                    reason      = f"EMA structure opposing trade (pnl={pnl_r:.2f}R)",
                    arm_cooldown= True,
                )

        return None
