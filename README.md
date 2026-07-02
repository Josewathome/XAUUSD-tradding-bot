# XAUUSD Trading Bot — Execution Framework

A complete, production-shaped **MT5 trading infrastructure** for XAUUSD (gold),
replicated from the `scraper-forex-bot` operational framework. It contains the
entire connectivity, execution, monitoring, risk-management and operations
stack — **but no trading strategy**. The "brain" (signal generation, indicators,
entry/exit math, structural analysis) is deliberately left out and replaced with
clean, well-documented interfaces and no-op placeholders.

Out of the box the bot connects to MT5, streams ticks/candles, manages
sessions, runs all its risk gates, journals, checkpoints, serves a dashboard,
and recovers from disconnects — it simply **never opens a trade**, because the
strategy engine is a placeholder. Plug in a real strategy and it trades.

> **Why this split?** The expensive, fiddly, easy-to-get-wrong part of an
> automated trading system is the *infrastructure* (broker connectivity, order
> lifecycle, margin/risk safety, crash recovery). That is fully built and
> battle-tested here. The *strategy* is the part you iterate on — so it lives
> behind a stable interface you can swap freely without touching plumbing.

---

## Architecture

Clean/layered architecture. Dependencies point inward; the framework depends
only on abstract contracts at the strategy boundary.

```
                         ┌──────────────────────────────────────┐
   MT5 Terminal (Wine)   │  ZoneBotBridge.mq5 (Expert Advisor)   │
   + Python in one        │  PUB tcp://127.0.0.1:5556 (ticks,     │
   Docker container       │  candle closes, trade events)         │
                         └───────────────────┬──────────────────┘
                                              │ ZeroMQ (Wine loopback)
                                              ▼
┌─────────────────────────────────────────────────────────────────────────┐
│  main_stream.py  — event loop (TICK / CANDLE_CLOSED / TRADE / HEARTBEAT)  │
│      build_engine()  ─────────────►  IStrategyEngine   (THE seam)         │
│      build_guardian() ────────────►  ITradeGuardian    (THE seam)         │
└───────────────┬───────────────────────────────────────┬──────────────────┘
                │                                         │
        APPLICATION layer                          STRATEGY boundary
   ┌────────────┴─────────────┐            ┌──────────────┴───────────────┐
   │ ExecutionService          │            │ interfaces.py  (contracts)   │
   │   • order placement       │            │   StrategySignal             │
   │   • TP1/TP2 tiering        │◄───────────┤   IStrategyEngine            │
   │   • time-exit, MFE         │  Guardian  │   ITradeGuardian             │
   │   • position sizing        │  actions   │   GuardianAction / Input     │
   │   • broker-truth P&L       │            ├──────────────────────────────┤
   │ EntryGate (risk gates)     │            │ placeholder_engine.py  (no   │
   │ MarginManager (portfolio)  │            │   signals)                    │
   │ NewsManager / Scheduler    │            │ placeholder_guardian.py (no  │
   │ EmailService / TickAnalytics│           │   exits)                      │
   └────────────┬─────────────┘            └──────────────────────────────┘
                │
       INFRASTRUCTURE layer
   ┌────────────┴───────────────────────────────────────────────────────┐
   │ MT5Gateway (connect/reconnect, orders, margin, history)             │
   │ MT5TradeRepository · MT5MarketDataRepository · BrokerClock/NTP      │
   │ Streaming: ZmqFeed · CandleBuilder · StreamingMarketDataRepo        │
   │ News: ForexNewsClient · MT5NewsClient · CompositeNewsClient · cache │
   │ SpreadCalculator · TradeJournal · CheckpointService · CleanupService│
   │ TimeSync · Dashboard API (Flask)                                    │
   └─────────────────────────────────────────────────────────────────────┘
                │
          DOMAIN layer
   ┌────────────┴───────────────────────────────────────────────────────┐
   │ entities (Candle, Trade, TradeSignal, Direction, …)                 │
   │ value_objects (PipCalculator, BrokerCost, RiskParameters)           │
   │ repositories (IMarketDataRepository, ITradeRepository, …)           │
   └─────────────────────────────────────────────────────────────────────┘
```

### What is included (the framework — fully implemented)

| Area | Component(s) |
|------|--------------|
| **MT5 connectivity** | `MT5Gateway` — attach-without-relogin, auto-reconnect on every call, UTC-offset detection, filling-mode resolution |
| **Order execution** | place / close / partial-close / modify-SLTP, broker stop-level pre-flight nudging, fill-slippage handling |
| **Trade modification** | structural/defensive SL trailing applied mechanically from guardian decisions, TP1/TP2 profit-lock |
| **Position management** | TP1/TP2 tiered partial exits, time-based exit, MFE tracking, open-trade reconciliation with broker |
| **Session handling** | per-symbol UTC session windows, NTP-calibrated broker clock, broker UTC offset auto-detect |
| **Connection recovery** | reconnect with backoff, ZMQ feed auto-restart, checkpoint restore on restart, clean SIGTERM shutdown |
| **Data flow** | ZeroMQ tick/candle/trade stream, `CandleBuilder` aggregation, streaming repo with MT5 fallback |
| **Risk management hooks** | news, session, tick-velocity, daily-drawdown circuit breaker, portfolio allocation, margin level, spread/cost, expected-value, anti-duplicate, min-SL, R:R, money-risk ceiling, position sizing |
| **Logging / monitoring** | structured logging, `TradeJournal` (broker-truth P&L), `TickAnalytics` execution tracking, Flask dashboard |
| **Operations** | `Scheduler` (cleanup/analytics), `EmailService` alerts, `CheckpointService`, `CleanupService`, log/cache retention |
| **Deployment** | Docker (`gmag11/metatrader5_vnc`), Wine, `override_start.sh`, EA injection tools |

### What is a placeholder (the strategy — implement later)

| Removed brain | Replaced by |
|---------------|-------------|
| Signal generation, indicators, multi-TF alignment, tick microstructure, candle scoring, market-structure (BOS/CHoCH), trade-quality scoring | `IStrategyEngine` + `PlaceholderStrategyEngine` (returns no signals) |
| SL/TP price math (swing-based, ATR-adaptive, structure-aware targets) | Supplied by the strategy on each `StrategySignal` (`stop_loss`, `take_profit_1/2`) |
| Open-trade re-evaluation: continuation scoring, structural SL placement, reversal detection, recovery conviction | `ITradeGuardian` + `PlaceholderTradeGuardian` (returns no actions) |

---

## The strategy boundary

Everything the strategy must provide lives in **`src/strategy/interfaces.py`**:

- **`StrategySignal`** — the entry contract: direction, confidence, proposed
  entry, and the proposed `stop_loss` / `take_profit_1` / `take_profit_2`
  (the entry/exit math is the strategy's job). The framework's `EntryGate`
  validates and sizes it; it never invents levels.
- **`IStrategyEngine`** — `seed / on_candle / push_tick / evaluate`. `evaluate()`
  returns a `StrategySignal` or `None`. Optional `get_structure()` returns an
  opaque object handed back to the guardian.
- **`ITradeGuardian`** — `evaluate(GuardianInput) -> [GuardianAction]`. Decides
  whether to trail SL, partially close, or close each open trade. The
  `ExecutionService` applies the returned actions mechanically and journals them.

### Plugging in a strategy

1. Implement `IStrategyEngine` (and optionally `ITradeGuardian`) in a new module
   under `src/strategy/`.
2. Swap the two factory functions in `src/main_stream.py`:

   ```python
   def build_engine(symbols) -> IStrategyEngine:
       from src.strategy.xauusd_engine import XauusdEngine
       return XauusdEngine(symbols=symbols)

   def build_guardian() -> ITradeGuardian:
       from src.strategy.xauusd_guardian import XauusdGuardian
       return XauusdGuardian()
   ```

3. Set `STRATEGY_GATE_ENABLED=true` in `.env` when you are ready to place live
   trades. Nothing else in the framework changes.

See **`docs/STRATEGY_INTEGRATION.md`** for a step-by-step guide and a minimal
reference implementation.

---

## Running

### Docker (recommended — bundles MT5 + Wine)

```bash
cp env.example .env          # then edit credentials in .env
docker compose up -d
# Dashboard:  http://localhost:8002
# MT5 (VNC):  http://localhost:3002
```

### Local (requires an MT5 bridge — `mt5linux` or Windows `MetaTrader5`)

```bash
pip install -r src/requirements.txt
export TRADING_ID=... MT5_PASSWORD=... MT5_SERVER=...
python -m src.main_stream
```

### Tests (no broker needed)

```bash
python -m tests.test_framework
```

---

## Project layout

```
src/
  config.py                     # all settings (framework + strategy-engine placeholders)
  main_stream.py                # event loop + build_engine()/build_guardian() seams
  domain/                       # entities, value objects, repository interfaces
  infrastructure/               # MT5 gateway, repos, streaming, news, journal, EA
    mt5_bridge/mt5_gateway.py
    mt5_bridge/ea/ZoneBotBridge.mq5
    stream/                     # zmq_feed, candle_builder, streaming repo
  application/                  # execution_service, entry_gate, margin_manager,
                                # news_manager, scheduler, email_service, tick_analytics
  api/server.py                 # Flask dashboard
  strategy/
    interfaces.py               # ◄── THE strategy/framework contracts
    placeholder_engine.py       # no-op IStrategyEngine
    placeholder_guardian.py     # no-op ITradeGuardian
tests/test_framework.py
docker-compose.yml · override_start.sh · env.example
docs/STRATEGY_INTEGRATION.md
```

---

## Safety defaults

- **Observation mode** by default (`STRATEGY_GATE_ENABLED=false`) — and the
  placeholder engine emits no signals regardless.
- **Fail-closed** everywhere: missing margin/pip-value/cost data rejects the
  trade rather than guessing.
- Use a **demo account** until your strategy is validated.
