# London Breakout — Demo Forward Test (Phase 11)

A controlled scientific experiment. The strategy is FROZEN at the validated
configuration (see `lb_forward.py` header). No optimization, no new filters,
no intervention until the pre-registered criteria resolve.

## Question
Does the London Breakout retain a statistically meaningful edge in current
live market conditions after real execution effects (spread, slippage,
commission, fills) are included?

## Pre-registered criteria (frozen 2026-07-17)
- **ALIVE / GO:** ≥60 completed trades AND ≥3 months AND PF > 1.10 AND
  expectancy > +0.05 R AND avg |slippage| ≤ 1.0 pip.
- **REJECT:** expectancy ≤ 0 R or PF < 1.00 after 60 trades, or rolling-20
  |slippage| > 2.0 pips (the adversarially-measured tolerance).

## Moving parts
- `lb_forward.py` — tick engine (container Wine Python; native stop orders
  with server-side 30-min expiry; magic **771015**). Cron: every 5 min, 24×5.
- `lb_reportgen.py` — daily/weekly/status reports vs the backtest baseline
  (+0.125 R / PF 1.30 / WR 49.1%) and the adversarial stressed floor (+0.017 R).
- `run_tick.sh` / `run_report.sh` — cron wrappers.
- `data/` — state.json, trades.csv (full instrumentation), events.jsonl,
  cron.log (gitignored, live experiment records).
- `reports/` — daily_*.md, weekly_*.md, STATUS.md (gitignored, regenerable).
- Live-bot guard: `ExecutionService` skips foreign-magic positions
  (`BOT_MAGIC`), so the idle XAUUSD bot cannot touch experiment trades.

## Notes
- Sessions anchored to SERVER time (UK+2 in both DST regimes; ±1h during the
  2–3-week US/EU DST mismatch windows — same convention as the backtest).
- Demo equity ~$198: at 1% risk the 0.01 min-lot floor sometimes binds; the
  actual risk taken is recorded per trade (`planned_risk_pct`). R-multiple
  measurement is unaffected.
- Instrumentation per trade: requested vs fill price (slippage from broker
  deal records), spread at signal/entry/exit, commission, swap, ATR/ADR,
  Asian-range and breakout-candle geometry, fill hour, day of week.
