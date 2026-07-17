# Forward-Test Framework — Independent QA & Forensic Audit (2026-07-17)

Mandate: assume the experiment contains hidden flaws; try to prove it wrong.
Scope: lb_forward.py, lb_reportgen.py, cron, MT5 integration, magic-guard,
state/logging/reporting. Method: line-by-line code audit vs the validated
backtest spec, live execution drills on the demo account, failure-mode drills,
and independent broker-truth reconciliation. All fixes below are to the
measurement apparatus only — strategy rules and parameters are untouched.

## Findings

| # | Severity | Finding | Location | Could invalidate? | Status |
|---|---|---|---|---|---|
| F1 | **CRITICAL** | Order that filled AND closed within one cron interval was recorded as `expired_unfilled` — the trade (disproportionately a fast **loser**) vanished from the record → survivorship bias → **false-ALIVE machine** | `reconcile()` pending-order branch | **YES** | **FIXED**: history-first resolution via `history_orders_get` state + `position_id`; falls through to record already-closed positions in the same tick |
| F2 | HIGH | SIGKILL (`timeout 110`) between `order_send` and `save_state` → orphan untracked order + duplicate placement next tick | `step()` placement / `run_tick.sh` | YES (duplicates + unrecorded fills) | **FIXED**: startup `orphan_sweep()` cancels unknown magic orders / adopts unknown magic positions; pre-placement guard blocks if any magic order/position exists. **Verified by live drill** (order 1814424304 orphaned via state deletion → swept & cancelled) |
| F3 | HIGH | Corrupt/lost state silently reset the day (duplicate trading, lost reconciliation) | `load_state()` | YES | **FIXED**: corrupt file backed up + logged; orphan sweep covers consequences |
| F4 | HIGH | No independent check of recorded trades vs broker — a runner bug could drop/duplicate trades invisibly | (absent) | YES | **FIXED**: `--audit` dumps all magic-771015 deals+orders from broker history; report reconciles counts + net PnL; **ALIVE verdict now gated on reconcile-clean** |
| F14 | HIGH→resolved | `ORDER_TIME_SPECIFIED` + int epoch + filling mode untested on this broker | placement request | YES (all orders rejected) | **VERIFIED by live drill**: IOC accepted, int expiration accepted (broker rounds expiry down to whole minute, ≤59 s early — immaterial); GTC+soft-expiry fallback added anyway |
| F5 | MEDIUM | Late cron → order placed late; if price already through trigger, broker rejects → setup lost that the backtest took | placement retcodes 10015/16 | No (bias is **conservative** — loses momentum winners; cannot create false ALIVE) | Tagged `missed_fast_move` so reports can count them |
| F6 | MEDIUM | Invalidation checked only the LAST closed bar (missed opposite-closes under cron gaps) | maintenance block | Minor rule drift | **FIXED**: every closed bar since the signal bar |
| F15 | MEDIUM | Cancel-vs-fill race: failed REMOVE (order just filled) was treated as cancelled → unmanaged, unrecorded fill | maintenance block | YES (rare) | **FIXED**: DONE only on confirmed removal; reconcile catches the fill next tick |
| F7 | MEDIUM | EOD backstop reliant on 23:45–23:55 tick; Friday-night winter spillover | `reconcile()` EOD | Rule drift only (trade still recorded) | **IMPROVED**: also attempts close on weekend-spillover ticks; residual: a missed 10-min window rides the weekend — visible in records |
| F18 | MEDIUM | No liveness/failure alerting — runner could die silently for days | (absent) | Experiment-duration risk | **FIXED**: per-tick heartbeat; daily report checks heartbeat age, reconcile status, order failures, state corruption; **email alerts (channel live-tested)** |
| F11 | LOW | Float-floor in lot sizing could undersize one step | `sized_volume()` | No | **FIXED** (epsilon) |
| F9 | LOW | `spread_entry_pips` sampled at fill *detection* (≤5 min late) | `reconcile()` | No (slippage itself is broker-deal-exact) | Documented |
| F12 | LOW | Slippage criterion uses \|slippage\| — favorable slippage counts against the threshold | reportgen | No (conservative) | Documented |
| F19 | LOW | `1%%` literal in report table | reportgen | No | Fixed |

## Verified clean (no finding)

- **Rule fidelity**: every frozen rule matches the validated backtest engine
  (range hours, body-close test `close > high/low of range`, ±1.5-pip pad,
  30-min expiry, opposite-side SL, 2R TP, spread gate, one setup/pair/day,
  window cancel at 13:00 server). No optimization or drift anywhere.
- **Timezone**: server = UK+2 verified live both directions; all session logic
  runs on tick-derived server time (immune to host TZ); epoch handling uses
  `timegm` consistently; DST-mismatch weeks (~2–3/yr, ±1 h in UK terms) are the
  SAME convention as the backtest — consistent, documented.
- **R / expectancy / PF math**: `r = (profit+commission+swap) / actual_risk_usd`
  with denominator from requested-entry stop distance × actual volume —
  identical convention to the backtest; slippage flows into R correctly.
- **Magic-number protection**: the bot's only position-mutation paths are
  guardian actions (placeholder, inert) and `_monitor_open_trades` (now skips
  foreign magic); no close-all/flatten sweeps exist; framework regression 11/11.
- **One-trade-per-day, spread gate, sizing formula** (tick_value/tick_size),
  weekend guards, funnel logging: all verified line-by-line.

## Failure simulations

| Scenario | Behavior |
|---|---|
| MT5 disconnect | tick skips (connected guard); heartbeat continues; >45 min gap during trading days → email alert |
| Container restart | orders/SL/TP live server-side; next tick reconciles from history (F1 fix); stale-quote guard blocks acting on stale feed |
| Host reboot / missed crons | same as above — state machine is idempotent; late signals either place late or tag `missed_fast_move` |
| Crash mid-placement | orphan sweep cancels/adopts (live-drilled) |
| State corruption | backed up, logged, alerted; broker reconcile catches any resulting gap |
| Weekend/DST transitions | weekend guard + spillover EOD; DST convention documented |
| Silent metric drift | impossible without also corrupting broker history: reports reconcile against the broker's own deal records daily, and ALIVE is gated on it |

## The trust question

**"If the experiment reaches ALIVE, can I trust it?"**

**Yes — with three documented caveats.** After the fixes: every completed trade
is cross-checked daily against the broker's own deal history (count and net
PnL), and ALIVE cannot be declared unless that reconciliation is clean — so a
false positive would now require the broker's records themselves to be wrong.
The remaining measurement biases run in the **conservative** direction (missed
fast-move fills lose winners; |slippage| penalizes favorable fills). Caveats
that stand: (1) single broker, demo feed — demo fills are typically kinder than
live; treat slippage numbers as a lower bound; (2) min-lot quantization at
~$198 equity means per-trade risk sometimes exceeds 1% (recorded; R-multiples
unaffected); (3) ~2–3 DST-mismatch weeks/year shift the window 1 h in UK terms
(same as backtest). None of these can manufacture a passing result.
