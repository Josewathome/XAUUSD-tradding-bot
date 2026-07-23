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

---

# Addendum — "Trust the Instrument" Audit (2026-07-17, second pass)

Method: live tracer-bullet trade (real fill, magic 771015) driven through
injected state, state deletion, container restart, and manual close — plus
broker-history stress tests and a formal signal-conservation checker.

## New findings (all fixed and re-verified live)

| # | Severity | Finding | Proof of fix |
|---|---|---|---|
| G1 | **HIGH** | `orphan_sweep` adopted positions spawned by KNOWN pending orders — every normal between-tick fill would have had its state overwritten, destroying signal metadata and the slippage measurement (slippage=None on ~all trades → slippage criterion unevaluable) | Fixed (known order tickets count as known position ids); tracer re-run recorded slippage **+0.6 pips** via the history-first path |
| G2 | **HIGH** | Post-completion ticks re-recorded the closed trade (`reconcile` ran before the DONE gate) → duplicate rows; crash between CSV-append and state-save had the same effect | DONE-guard in `reconcile` + idempotency guard in `append_trade` (position_ticket never written twice); verified: two extra live ticks after recording → still exactly 1 row. Accounting checker now treats duplicates as a hard violation |
| G3 | MEDIUM | Daily report CRASHED whenever a trade closed that day (`to_markdown` requires missing `tabulate`) → stale STATUS on exactly the interesting days | Replaced with `to_string` + dependency installed; daily run verified with a closed trade present |

## Area 1 — Disaster recovery: proof table

| Scenario | Drill performed | Result |
|---|---|---|
| State deletion w/ pending order | real order + `rm state.json` + real tick | orphan cancelled, logged (ticket 1814424304) |
| State corruption | garbage JSON written + real tick | backed up (`state.json.corrupt-…`), SYS event, clean re-init, alert queued |
| Fill between ticks | tracer BUY_STOP filled while no tick ran | history-first reconcile recorded fill + slippage +0.6p |
| State deletion w/ OPEN position | `rm state.json` + tick | position adopted (`recovered=True`), managed onward |
| MT5/container restart w/ open position | `docker restart xauusd-bot` mid-trade | position still tracked post-restart; trade closed & recorded end-to-end |
| VPS reboot | policy check | `unless-stopped` on all containers; crontab persists; cron autostarts |
| Cron interruption | 6.5 h real gap earlier today + idempotent design | no-op ticks, no duplication; late signals degrade to `missed_fast_move` (conservative) |
| Multiple simultaneous | deletion + restart chained on one live trade | full recovery; broker reconcile matched to the cent |

## Area 2 — Broker history trust

- History **survives terminal restart** (proven: ticket lookups + range queries
  return pre-restart orders/deals after `docker restart`).
- Canceled orders never create deals (proven) — QA orders cannot pollute
  deal-based reconciliation.
- `history_*_get` range queries use explicit from/to (no pagination in the MT5
  Python API); our window is EXPERIMENT_START → now+2d, immune to weekend/DST
  boundary effects.
- Residual risk: broker-side history pruning or demo-account reset. Mitigated:
  daily dated snapshots in `data/audit_history/` preserve every dump; the
  append-only `trades.csv`/`events.jsonl` are independent records.
- Broker rounds order expiration down to the whole minute (≤59 s early —
  measured; immaterial).

## Area 3 — Signal accounting

State machine (every signal ends in exactly one CAPS terminal):
```
day ──> NO_RANGE | NO_BREAKOUT | (weekend/holiday)
signal ──> SPREAD_BLOCK | MISSED_FAST_MOVE | ORDER_REJECTED | BLOCKED_EXISTING | placed
placed ──> EXPIRED_UNFILLED | INVALIDATED | WINDOW_END | filled
filled ──> TAKE_PROFIT | STOP_LOSS | MANUAL_OR_EOD  (exactly one trades.csv row)
(adopted positions enter at `filled`, flagged recovered)
```
`lb_accounting.py` verifies conservation on the REAL logs by unique ticket:
every placed ticket terminates exactly once; every fill ticket is exactly one
trade row or currently open; no phantom rows; duplicates are violations. It is
run inside every report, and **the ALIVE verdict is gated on BOTH broker
reconciliation AND conservation passing**. Backtest-scale conservation:
GBPUSD funnel 478 breakouts = 313 filled + 163 expired + 2 invalidated (exact).

## Answer to the key question

*"Can any trade, order, signal, or event disappear from the experiment
without being recorded somewhere?"*

Attempts to make one disappear: fast fill+close between ticks (F1), crash
mid-placement (F2), state deletion (drilled), state corruption (drilled),
container restart mid-trade (drilled), duplicate recording (G2 — found &
fixed), history amnesia after restart (disproven), silent report failure (G3 —
found & fixed). **Every attempt now fails.** A disappearance would require the
broker's own deal history to be wrong AND the dated snapshots AND the
append-only local logs to all be wrong together. The framework is trustworthy
within the documented demo-execution caveats.

QA tracer trade (position 1814453758, −$0.03) remains permanently in
trades.csv/broker history for accounting; metrics count only trades from
TRADING_START = 2026-07-20.

---

# Addendum 2 — Week-1 Live Incident (2026-07-23, PM review discovery)

Discovered during the first scheduled PM/adversarial review (not by automated
alerting — see G5 below, now fixed).

## G4 — CRITICAL — Day-rollover deadlock (found LIVE, in production)

**The bug:** `step()`'s date-rollover check bailed out (`return st`) whenever
`order_ticket`/`position_ticket` was truthy, BEFORE calling `reconcile()`, and
neither field was ever cleared once a trade reached `DONE`. Consequence: once
a pair completed **one single trade**, it could **never take another signal
again** — the day never rolled forward.

**Impact, quantified exactly:**
- **EURUSD**: state stuck at `date=2026-07-17` (the QA tracer trade from the
  prior audit pass) from before trading even started. **Took zero signals**
  on 2026-07-20, 21, 22, 23 — its entire first week.
- **GBPUSD**: took its first (real) signal Monday 2026-07-20 as designed,
  closed at 14:50 (stop-loss, -1.022R) — then stuck. **Took zero signals**
  on 07-21, 22, 23.
- Additionally: today's (07-23) trading window (server 09:00-13:00) had
  **already closed** (server time was 14:19) by the time the fix was
  diagnosed and deployed, so the fix's first live effect is tomorrow,
  2026-07-24.
- **The already-recorded Monday GBPUSD trade is unaffected and valid** — it
  was fully executed and reconciled before the bug's effect (which only
  blocks *future* signal evaluation, not past recording).

**Root cause of root cause:** the bug was latent since the runner was first
written (the day-rollover logic was never exercised past a single completed
trade in any earlier drill — all prior drills tested mid-trade recovery, not
next-day continuation). It was *triggered* for EURUSD specifically by the
QA tracer trade left over from the previous audit pass, one week before
trading start — a process lesson: **test/QA activity must be followed by a
clean-state verification immediately before the official start**, which
was not done.

**Fix:** the day-rollover now only defers to "yesterday's business" when the
phase is genuinely non-terminal (not `DONE`) and a ticket is outstanding; in
that case `reconcile()` is still called (with `now` already on the new day)
so it can resolve/EOD it. A `DONE` phase always rolls to a fresh `IDLE` day
regardless of stale ticket fields. **Verified live**: both pairs immediately
reset to `{date: 2026-07-23, phase: IDLE}` on the next tick after the patch.

**Could this have manufactured a false ALIVE?** No — in the opposite
direction: this bug can only produce **fewer** trades than the true signal
rate, which delays reaching n=60 and, if anything, biases toward RUNNING/
under-observed rather than a false positive. It could, however, have
produced a false REJECT-by-starvation (never reaching 60 trades) or a
badly underpowered ALIVE (if by chance the few trades taken happened to
qualify) — both now moot since it is fixed in week 1.

## G5 — HIGH — Monitoring blind spot: connectivity failures were invisible

Found while investigating 4 `FATAL` lines in `cron.log` (3× `IPC timeout`,
1× `Terminal: Authorization failed`, all apparently transient/self-recovered
via the 5-min retry cadence). Root cause: `main()` wrote the process
heartbeat *before* attempting the MT5 connection, and connection failures
were only `print()`-ed to stdout, never logged via `event()`. Consequence:
**the heartbeat could stay "fresh" indefinitely while MT5 connectivity
silently failed every single tick** — a runner that never once reaches its
own trading logic would look identical, from the outside, to one working
normally.

**Fix:** connection failures now emit `mt5_connect_FAILED` events (stage-
tagged: initialize / terminal_info / server_time / stale_quotes). A second,
narrower heartbeat (`mt5_heartbeat`) updates only after a confirmed live
connection + time check; the daily report now alerts if it goes stale
(>60 min on a trading day) even while the process heartbeat looks fine, and
separately alerts on ≥6 connectivity failures in 24h. A `stale_pair_days()`
check was also added, directly targeting G4-shaped bugs: any pair whose
state `date` lags today by ≥2 days with a terminal phase on a weekday now
triggers an immediate alert — this specific incident would have been caught
on 2026-07-21, its first full missed day, had this check existed.

## Trust question, re-answered

The previous audit's claim — "a false positive would require the broker's
own history to be wrong" — **still holds for data integrity**: nothing was
lost, duplicated, or misrecorded. But this incident shows integrity is not
sufficient; **availability** (the experiment actually generating trades) had
a real, un-alarmed failure mode. That gap is now closed (G5). The instrument
is trustworthy for what it records; it required this addendum to also be
trustworthy for what it *should have recorded but silently didn't*.
