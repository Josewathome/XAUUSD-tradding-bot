"""
test_framework.py — Regression suite for the trading FRAMEWORK that this repo
provides: value-object math, position sizing, broker-truth journaling, the
risk-management gates, and the strategy⇄framework boundary (placeholder engine,
placeholder guardian, entry-gate level validation).

Strategy-specific math (signal generation, SL/TP placement, structural exit
scoring) is intentionally NOT tested here — it does not live in this repo. It
is supplied later by implementing src/strategy/interfaces.py.

Runs with plain Python (no pytest required):

    python -m tests.test_framework

It is also pytest-compatible (every check is a `test_*` function). Dummy MT5
credentials are set before importing `src.config` so the module imports cleanly
off-broker.
"""
from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone

# Config refuses to import without these — set safe dummies for off-broker tests.
os.environ.setdefault("TRADING_ID", "1")
os.environ.setdefault("MT5_PASSWORD", "x")
os.environ.setdefault("MT5_SERVER", "x")
os.environ.setdefault("ACCOUNT_CURRENCY", "USD")

import src.config as cfg
from src.domain.entities import Candle, Direction, Timeframe
from src.domain.value_objects import (
    PipCalculator, BrokerCost, RiskParameters, pip_value_per_lot,
)
from src.application.entry_gate import EntryGate
from src.infrastructure.trade_journal import TradeJournal
from src.strategy.interfaces import (
    StrategySignal, GuardianInput, TradeContext,
    IStrategyEngine, ITradeGuardian,
)
from src.strategy.placeholder_engine import PlaceholderStrategyEngine
from src.strategy.placeholder_guardian import PlaceholderTradeGuardian


# ── Lightweight fakes ───────────────────────────────────────────────────────────

class _FakeTR:
    def get_open_positions(self): return []
    def get_free_margin(self): return 1000.0
    def get_margin_level_pct(self): return 500.0


class _FakeExec:
    def __init__(self):
        self._tr = _FakeTR()
        self._journal = TradeJournal(path=tempfile.mktemp(suffix=".json"))
        self._trade_states = {}
    def is_symbol_paused(self, s, now=None): return False
    def is_reversal_cooldown(self, s, now=None): return False


def _eg():
    return EntryGate(news_manager=None, execution=_FakeExec())


def _signal(symbol, direction, entry, sl, tp1, tp2, vel=5.0, conf=0.7):
    return StrategySignal(
        symbol=symbol, direction=direction, confidence=conf,
        timestamp=datetime.now(tz=timezone.utc), proposed_entry=entry,
        stop_loss=sl, take_profit_1=tp1, take_profit_2=tp2,
        tick_velocity=vel, tq_score=0.6,
    )


# ── 1. Pip-value (the 10× sizing fix) ──────────────────────────────────────────

def test_pip_value_per_lot():
    pc5 = PipCalculator(digits=5)
    pc3 = PipCalculator(digits=3)
    pc2 = PipCalculator(digits=2)
    assert abs(pip_value_per_lot(1.0, 0.00001, pc5.pip_size) - 10.0) < 1e-9
    assert abs(pip_value_per_lot(0.91, 0.001, pc3.pip_size) - 9.1) < 1e-6
    assert abs(pip_value_per_lot(1.0, 0.01, pc2.pip_size) - 1.0) < 1e-9
    assert pip_value_per_lot(0.0, 0.00001, 0.0001) == 0.0
    assert pip_value_per_lot(1.0, 0.0, 0.0001) == 0.0


# ── 2. BrokerCost round-trip cost (fail-closed on unknown) ──────────────────────

def test_broker_cost():
    assert abs(BrokerCost(0.5, 3.5, 10.0).round_trip_cost_pips() - 1.2) < 1e-9
    assert BrokerCost(0.5, 3.5, 0.0).round_trip_cost_pips() == float("inf")
    assert BrokerCost(1.4, 0.0, 0.0).round_trip_cost_pips() == 1.4


# ── 3. Lot sizing from money risk ───────────────────────────────────────────────

def test_lot_sizing():
    rp = RiskParameters(account_balance=200, risk_percent=0.5,
                        commission_per_lot=3.5, min_rr_ratio=1.5, account_currency="USD")
    lot = rp.lot_size(sl_pips=6.0, pip_value_per_lot=10.0)
    assert abs(6.0 * 10.0 * lot - 1.0) < 1e-9
    assert rp.lot_size(6.0, 0.0) == 0.0


# ── 4. Journal tells broker-truth (sign-based) ──────────────────────────────────

def test_journal_truth_and_rolling():
    tj = TradeJournal(path=tempfile.mktemp(suffix=".json"))
    tj.record_open(1, "XAUUSD", "bullish", 2000, 1995, 2010, 0.1, "C")
    tj.record_close(1, pnl=-7.5, outcome="loss", pips=-6.0)
    tj.record_open(2, "XAUUSD", "bullish", 2000, 1995, 2010, 0.1, "C")
    tj.record_close(2, pnl=4.2, outcome="win", pips=9.0)
    st = tj.get_stats()
    assert st["wins"] == 1 and st["losses"] == 1
    assert abs(st["total_pnl"] - (-3.3)) < 1e-9
    perf = tj.rolling_performance("XAUUSD", n=50)
    assert perf["samples"] == 2 and abs(perf["win_rate"] - 0.5) < 1e-9
    assert abs(perf["avg_win_pips"] - 9.0) < 1e-9 and abs(perf["avg_loss_pips"] - 6.0) < 1e-9


# ── 4b. Journal schema guard: legacy pip-era rows excluded from money stats ──────

def test_journal_schema_guard():
    tj = TradeJournal(path=tempfile.mktemp(suffix=".json"))
    tj._trades.append({
        "ticket": 99, "symbol": "XAUUSD", "direction": "bullish",
        "entry": 4200, "sl": 4199, "tp": 4203, "lot_size": 0.01, "grade": "C",
        "pnl": 300.0, "pips": 300.0, "outcome": "win",
        "opened_at": "2026-06-01T00:00:00+00:00", "closed_at": "2026-06-01T00:05:00+00:00",
    })
    tj.record_open(1, "GBPUSD", "bullish", 1.30, 1.299, 1.302, 0.02, "B")
    tj.record_close(1, pnl=2.50, outcome="win", pips=9.0)
    st = tj.get_stats()
    assert st["total"] == 1 and st["wins"] == 1
    assert abs(st["total_pnl"] - 2.50) < 1e-9, st
    assert abs(tj.total_realized_pnl() - 2.50) < 1e-9
    by_sym = tj.get_stats_by_symbol()
    assert "GBPUSD" in by_sym and "XAUUSD" not in by_sym, by_sym
    assert tj.rolling_performance("XAUUSD")["samples"] == 0
    assert tj.get_drawdown(initial_balance=140.0) <= 0.0 + 1e-9


# ── 5. Cost/spread gate — per-symbol caps, REALISTIC inputs ──────────────────────

def test_cost_gate_realistic():
    assert EntryGate._cost_ok("GBPUSD", 13.5, BrokerCost(0.5, 3.5, 10.0)) is True
    assert EntryGate._cost_ok("GBPUSD", 13.5, BrokerCost(45.0, 3.5, 10.0)) is False
    assert EntryGate._cost_ok("XAUUSD", 270, BrokerCost(20.0, 3.5, 1.0)) is True
    assert EntryGate._cost_ok("XAUUSD", 270, BrokerCost(90.0, 3.5, 1.0)) is False
    assert EntryGate._cost_ok("XAUUSD", 270, BrokerCost(20.0, 3.5, 0.0)) is False
    assert EntryGate._cost_ok("XAUUSD", 15.0, BrokerCost(20.0, 3.5, 1.0)) is False


# ── 6. EV gate status ────────────────────────────────────────────────────────────

def test_ev_status():
    eg = _eg()
    assert eg._ev_status("XAUUSD", 180.0, 270.0, BrokerCost(20.0, 3.5, 0.0)) == "unknown"
    assert eg._ev_status("XAUUSD", 180.0, 270.0, BrokerCost(20.0, 3.5, 1.0)) == "negative"
    assert eg._exploration_allowed("XAUUSD") is True
    assert eg._ev_status("XAUUSD", 180.0, 270.0, BrokerCost(5.0, 3.5, 1.0)) == "pass"


# ── 7. Exploration bootstrap caps + fail-closed ──────────────────────────────────

def test_exploration_caps():
    eg = _eg()
    sym = "XAUUSD"
    assert eg._exploration_allowed(sym) is True
    for _ in range(cfg.EXPLORATION_TRADES_PER_DAY):
        eg.record_exploration(sym)
    assert eg._exploration_allowed(sym) is False

    ex = _FakeExec()
    for i in range(cfg.EV_MIN_SAMPLES + 5):
        ex._journal.record_open(i, "XAUUSD", "bullish", 2000, 1995, 2010, 0.1, "C")
        ex._journal.record_close(i, 1.0 if i % 2 else -1.0,
                                 "win" if i % 2 else "loss", 2.0 if i % 2 else -2.0)
    eg2 = EntryGate(news_manager=None, execution=ex)
    assert eg2._exploration_allowed("XAUUSD") is False


# ── 8. Strategy boundary: placeholder engine emits no signals ───────────────────

def test_placeholder_engine_returns_none():
    eng = PlaceholderStrategyEngine(symbols=["XAUUSD"])
    assert isinstance(eng, IStrategyEngine)
    c = Candle(time=datetime.now(timezone.utc), open=2000, high=2001, low=1999,
               close=2000.5, volume=10, symbol="XAUUSD", timeframe=Timeframe.M1)
    eng.seed("XAUUSD", Timeframe.M1, [c])
    eng.on_candle(c)
    eng.push_tick("XAUUSD", 2000.4, 2000.6, 1.0)
    assert eng.evaluate("XAUUSD", c, [c], [c], 2000.5, 30.0) is None
    assert eng.get_structure("XAUUSD") is None
    assert isinstance(eng.spread_state("XAUUSD"), str)


# ── 9. Strategy boundary: placeholder guardian emits no actions ─────────────────

def test_placeholder_guardian_no_actions():
    g = PlaceholderTradeGuardian()
    assert isinstance(g, ITradeGuardian)
    c = Candle(time=datetime.now(timezone.utc), open=2000, high=2001, low=1999,
               close=2000.5, volume=10, symbol="XAUUSD", timeframe=Timeframe.M1)
    ctx = GuardianInput(
        symbol="XAUUSD", price=2000.5, pip_calc=PipCalculator(digits=2),
        now=datetime.now(timezone.utc), fresh_signal=None, structure=None,
        m1_candles=[c], m5_candles=[c],
        trades=[TradeContext(
            ticket=1, symbol="XAUUSD", direction=Direction.BULLISH, entry=2000,
            current_sl=1995, take_profit=2010, lot_size=0.1, initial_risk=5,
            tp1_hit=False, tp2_hit=False, mfe_price=2001,
            created_at=datetime.now(timezone.utc), grade="B")],
    )
    assert g.evaluate(ctx) == []


# ── 10. Entry gate rejects strategy-supplied levels that are geometrically wrong ─

def test_entry_gate_rejects_bad_levels():
    eg = _eg()
    now = datetime.now(tz=timezone.utc)
    pc = PipCalculator(digits=2)
    bc = BrokerCost(20.0, 3.5, 1.0)
    # Long with SL above entry → invalid geometry → rejected before any gate.
    bad = _signal("XAUUSD", Direction.BULLISH, entry=2000.0, sl=2005.0, tp1=2010.0, tp2=2015.0)
    assert eg.evaluate(bad, 2000.0, now, 1000.0, pc, pip_value=1.0, digits=2, broker_cost=bc) is None
    # pip_value <= 0 → fail closed.
    ok_levels = _signal("XAUUSD", Direction.BULLISH, entry=2000.0, sl=1995.0, tp1=2010.0, tp2=2015.0)
    assert eg.evaluate(ok_levels, 2000.0, now, 1000.0, pc, pip_value=0.0, digits=2, broker_cost=bc) is None


# ── Standalone runner (no pytest needed) ────────────────────────────────────────

def _run_all() -> int:
    funcs = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in funcs:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"  FAIL  {fn.__name__}: {exc!r}")
        except Exception as exc:
            failed += 1
            print(f"  ERROR {fn.__name__}: {type(exc).__name__}: {exc}")
    total = len(funcs)
    print(f"\n{total - failed}/{total} passed" + ("" if failed == 0 else f", {failed} FAILED"))
    return 1 if failed else 0


if __name__ == "__main__":
    import sys
    sys.exit(_run_all())
