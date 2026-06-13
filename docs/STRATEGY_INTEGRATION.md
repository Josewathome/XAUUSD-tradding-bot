# Strategy Integration Guide

This framework is strategy-agnostic. All trading intelligence is supplied by
implementing the two interfaces in [`src/strategy/interfaces.py`](../src/strategy/interfaces.py).
This guide shows exactly what to implement and how to wire it in.

## The two seams

```
main_stream.build_engine()    -> IStrategyEngine    (signal generation)
main_stream.build_guardian()  -> ITradeGuardian     (open-trade management)
```

The framework calls the engine on every tick / candle / M1-close, validates any
signal it produces through the risk gates, sizes the position, places the order,
and then asks the guardian on every M1-close whether to trail/scale/close.

## 1. Implement `IStrategyEngine`

```python
from datetime import datetime, timezone
from typing import List, Optional
from src.domain.entities import Candle, Direction, Timeframe
from src.strategy.interfaces import IStrategyEngine, StrategySignal


class XauusdEngine(IStrategyEngine):
    def __init__(self, symbols):
        self._symbols = list(symbols)
        # build your per-symbol indicator/structure state here

    def seed(self, symbol, timeframe, candles):
        ...  # warm up indicators with historical candles at startup

    def on_candle(self, candle):
        ...  # update structure/indicators on each closed bar

    def push_tick(self, symbol, bid, ask, ts):
        ...  # update tick microstructure / velocity

    def evaluate(self, symbol, forming_m1, m1_candles, m5_candles,
                 current_price, elapsed_m1_secs=30.0) -> Optional[StrategySignal]:
        # run your gates; return None for "no setup".
        # When you DO want to trade, compute the entry/exit MATH here:
        if not self._is_a_setup(symbol):
            return None
        direction = Direction.BULLISH
        sl  = self._compute_stop(symbol, current_price, direction)      # your SL math
        tp1 = self._compute_target(symbol, current_price, direction, 1) # your TP math
        tp2 = self._compute_target(symbol, current_price, direction, 2)
        return StrategySignal(
            symbol=symbol, direction=direction, confidence=0.72,
            timestamp=datetime.now(tz=timezone.utc),
            proposed_entry=current_price,
            stop_loss=sl, take_profit_1=tp1, take_profit_2=tp2,
            tick_velocity=self._velocity(symbol),   # used by the velocity gate
            tq_score=0.65,                           # used for risk weighting
            last_swing_support=..., last_swing_resistance=...,  # optional
        )

    # Optional — hand structure to the guardian for re-evaluation:
    def get_structure(self, symbol):
        return self._structure[symbol]
```

**Important:** the framework `EntryGate` validates your `stop_loss` /
`take_profit_1` / `take_profit_2` for correct geometry (SL/TP on the right side
of price, TP2 ≥ TP1), then checks min-SL distance, R:R floor, spread/cost, EV,
news, session, margin, drawdown and anti-duplicate before sizing. You only
provide the levels and a confidence; risk safety is handled for you.

## 2. Implement `ITradeGuardian` (optional but recommended)

The guardian is asked on every M1-close to re-evaluate open trades. It returns
mechanical actions; the `ExecutionService` applies and journals them.

```python
from typing import List
from src.domain.entities import Direction
from src.strategy.interfaces import (
    ITradeGuardian, GuardianInput, GuardianAction, GuardianActionType,
)


class XauusdGuardian(ITradeGuardian):
    def __init__(self):
        self._history = {}   # the guardian owns its analytical state, keyed by ticket

    def evaluate(self, ctx: GuardianInput) -> List[GuardianAction]:
        actions = []
        for t in ctx.trades:
            cont = self._continuation_score(t, ctx)      # your thesis score
            if cont < 0.25:
                actions.append(GuardianAction(
                    ticket=t.ticket, type=GuardianActionType.CLOSE,
                    reason="thesis_invalidated", arm_cooldown=True))
            elif self._should_trail(t, ctx):
                new_sl = self._structural_sl(t, ctx)      # your SL math
                actions.append(GuardianAction(
                    ticket=t.ticket, type=GuardianActionType.TRAIL_SL,
                    new_sl=new_sl, reason="structural_trail"))
        return actions
```

Action types:
- `TRAIL_SL` — set `new_sl` (the framework only applies genuine improvements).
- `PARTIAL_CLOSE` — set `close_fraction` (0–1 of the current lot).
- `CLOSE` — full close; set `arm_cooldown=True` to block immediate re-entry.

If you skip the guardian, open trades are still managed mechanically by the
framework: software TP1/TP2 tiering, time-based exit, MFE tracking, and the
broker-side SL/TP.

## 3. Wire it in

Edit the factories in [`src/main_stream.py`](../src/main_stream.py):

```python
def build_engine(symbols):
    from src.strategy.xauusd_engine import XauusdEngine
    return XauusdEngine(symbols=symbols)

def build_guardian():
    from src.strategy.xauusd_guardian import XauusdGuardian
    return XauusdGuardian()
```

## 4. Configure & go live

- Strategy-engine tuning lives in the lower half of `src/config.py` (clearly
  marked). The framework reads a subset for its gates (session windows, min-SL
  floor, spread caps, hold limits, velocity floor).
- Set `STRATEGY_GATE_ENABLED=true` only when you are ready to place live trades.
- Always validate on a **demo account** first.

## Data your engine receives

- `evaluate()` gets the forming M1 bar, closed M1 and M5 candles, the current
  price, and seconds elapsed in the forming bar.
- The framework provides `PipCalculator`, `BrokerCost`, real per-pip values and
  margin figures via the repositories — reuse them rather than reimplementing.

## Contract summary

| You implement | The framework guarantees |
|---------------|--------------------------|
| Direction, confidence, SL/TP levels | Geometry/min-SL/R:R validation |
| Tick velocity, trade-quality score | Velocity gate, risk-weighted sizing |
| Continuation/exit decisions (guardian) | Mechanical SL-modify / partial / close + journaling |
| Indicators, structure, microstructure | Tick/candle/trade data delivery, seeding |
| — | Connectivity, recovery, margin, news, sessions, checkpoints, dashboard |
