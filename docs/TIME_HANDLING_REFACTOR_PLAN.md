# Time-Handling Refactor Plan — XAUUSD-tradding-bot

**Status:** Planning document only. No code has been changed yet.
**Read first:** `../../TIME_HANDLING_REFACTOR_OVERVIEW.md` (shared decision: MT5 server time is the sole authoritative clock; no true-UTC alignment; exactly one conversion boundary survives, for external news timestamps).

This plan turns every finding from the time-handling audit into a concrete change, reframed under the "MT5 time only" model. Findings that only mattered under the old "must match true UTC" framing are marked **(resolved by the new model — no code change needed)**.

---

## 1. Delete the NTP clock entirely

- **Delete** `src/infrastructure/time_sync.py` (`TrueTimeClock`). It exists to reconcile the host clock against true UTC via NTP/HTTP — a problem this bot no longer has an opinion about.
- **Rewrite** `BrokerClock` in `src/infrastructure/market_data_repo.py` (currently lines 21-52) to drop `self._true_clock = TrueTimeClock()` and instead read time directly from MT5, following the pattern already proven in New-forex-bot's `BrokerClock` (`market_data_repo.py:20-135` in that repo):
  - `now()` fetches `MT5Gateway.get_server_time()` (already exists at `mt5_gateway.py:322-326`, returns `int(tick.time)`), converts via `datetime.fromtimestamp(ts, tz=timezone.utc)` — **the `tz=timezone.utc` label is now purely a Python API requirement for having an aware datetime, not a claim about calendar UTC.** Comment this explicitly so a future reader doesn't reintroduce offset "corrections."
  - Cache briefly (~2s) to avoid hammering MT5 on every call.
  - When the tick is stale (market closed) or MT5 is briefly unavailable, dead-reckon forward from the last known-good MT5 time using a `time.monotonic()` delta — never fall back to `datetime.now(tz=timezone.utc)` silently. If there has never been a good reading at all, log at ERROR (not WARNING) and surface a degraded-state flag the caller can check, rather than quietly trading on host time.
  - `invalidate()` should actually do something now (force next call to re-fetch) — currently it's dead code (`pass` followed by an unreachable statement). Fix or remove it.

## 2. Stop treating the auto-detected broker offset as load-bearing for internal time

- `mt5_gateway.py:328-362` `detect_utc_offset()` currently computes `offset = round((server_ts - real_utc) / 3600)` against `int(_time.time())` (host clock) and mutates `config.BROKER_UTC_OFFSET_HOURS` at startup (`main_stream.py:272-278`).
- **New model:** this constant is no longer used for candles, `get_server_time_utc()`, or session-hour math at all **(resolved by the new model)**. Its only remaining legitimate use is the news-ingestion conversion (§4). Rename it `MT5_NEWS_OFFSET_HOURS`, make it a static config value verified once against the live terminal, and delete the runtime `detect_utc_offset()` call from `main_stream.py`'s startup path. Keep `detect_utc_offset()` itself only as an optional manual diagnostic (e.g. a one-off script), not wired into live config.

## 3. Candle, tick, and server-time timestamps: stop "fixing" them

- `market_data_repo.py`'s `get_candles()` (line 86), `get_candles_range()` (line 210), and `get_server_time_utc()` (lines 168-176), plus `candle_builder.py`'s `on_tick`/`_finalise_bar`/`get_forming_bar` (lines 226, 261, 276) already return raw MT5 epoch time. **No change needed to the values themselves** — they already are MT5 server time, which is now the goal.
- **Do** fix the misleading names/docstrings: `get_server_time_utc()` → rename to `get_server_time()` (drop the false "UTC" promise); update `market_data_repo.py`'s class docstring, which currently falsely claims "All timestamps are converted to UTC on the way in" — replace with "All timestamps are raw MT5 server time; no conversion is performed or intended."
- Update `Candle`'s `.time` field docstring in `domain/entities.py` similarly.

## 4. News ingestion: the one place a conversion still happens

- `mt5_news_client.py:123` (`event_time = datetime.fromtimestamp(val.time, tz=timezone.utc)`) and `news_client.py` (ForexFactory `_parse_ff_time`, MyFxBook `_parse_mfxbook_time`) currently produce timestamps on inconsistent bases — some asserted UTC, one (MyFxBook) genuinely converted via its own API metadata.
- **New requirement:** normalize every source to MT5-server-time basis at ingestion, in one place, before anything reaches `NoTradeWindow`/`composite_news_client.py`'s dedup logic:
  - If MT5's own calendar API (`calendar_value_history_range`) turns out to report true UTC (verify once, live — it may use a different convention than tick time), convert it: `event_time_mt5_basis = event_time_utc + MT5_NEWS_OFFSET_HOURS`.
  - ForexFactory and MyFxBook (true UTC/GMT sources) get the same conversion applied.
  - Add one clear log line recording which offset was applied, so a future operator debugging a news-window miss doesn't have to reverse-engineer it.
- Fix `composite_news_client.py`'s 15-minute dedup bucketing (`_bucket()`) to run *after* this normalization, not before — currently sources can land in different buckets due to basis disagreement, not just real time differences.
- Fix `news_client.py`'s silent `_parse_ff_time` UTC assumption and `_parse_mfxbook_time`'s silent regex-fallback-to-UTC — both should log a warning when they can't verify the source's stated zone, since a silent wrong guess here corrupts the one conversion this whole design depends on.

## 5. Session windows: redefine once, stop converting at runtime

- `eurusd_engine.py`'s `_utc_hour`/`_utc_date` (lines 70-77, 188, 305) and `xauusd_engine.py`'s equivalents (lines 219-229, 249-250, 677) currently subtract `self._utc_offset_hours` from candle time to approximate true UTC before comparing against session-window config.
- **New model:** delete this subtraction entirely. Redefine `SCALPER_SESSION_START_UTC`/`SCALPER_SESSION_END_UTC` and `SCALPER_SYMBOL_SESSIONS` in `config.py` directly in MT5-server-time hours (i.e., take the current UTC-intended values and add the current broker offset once, by hand, verified against the terminal). Comment the config block with the date/method of verification. Strategy engines then compare `candle.time.hour` directly, with zero runtime arithmetic.
- Fix `eurusd_engine.py:305` and `xauusd_engine.py:677` — both currently attach a signal `timestamp` inconsistently (one applies the old offset math, one doesn't). Under the new model both should simply use `trigger.time` (MT5 server time) with no adjustment, so the fix here is to make them consistent, not to "correct" either one.

## 6. Wire the single clock into everything that currently bypasses it

Every one of these currently calls `datetime.now(tz=timezone.utc)` or `time.time()` directly instead of the (soon-to-be MT5-backed) `BrokerClock`:

- `execution_service.py`:
  - `run_monitoring_only()` (line 222) — called on every tick; give it a `now` parameter and thread `clock.now()` into it from `main_stream.py`'s call sites (currently `main_stream.py:521,581` pass no time argument at all).
  - `_arm_reversal_cooldown()` (line 213), `_update_streak()` (line 807) — the "arm" side of the reversal-cooldown and loss-streak-pause timers. Use the injected `self._clock` (currently stored and never read — this is the fix: actually read it).
  - `execute_entry_candidate()` (line 470) and `_build_trade()` (line 1036) — `created_at=` should come from `self._clock.now()`, not `datetime.now(tz=timezone.utc)`.
  - `is_symbol_paused()`/`is_reversal_cooldown()` (lines 181, 196) — remove the `now: Optional[datetime] = None` fallback; make `now` required.
- `entry_gate.py`:
  - `on_new_day()` (line 103), `load_runtime_state()` (line 139), `record_trade()` (line 151), `record_fill()` (line 155), `daily_trade_count()` (line 158) — none currently accept a `now` parameter at all. Add one, and have every call site (`main_stream.py`) pass `clock.now()`. This also fixes the "arm with host clock, check with MT5 clock" mismatch in the anti-duplicate gate (line 321) and the daily-drawdown-baseline day-boundary logic.
  - `refresh_if_needed()`/`is_blocked()` equivalents in `news_manager.py` (lines 47-48, 65-66) — remove the `now or datetime.now(...)` fallback; make `now` required.
- `application/scheduler.py`:
  - Give `Scheduler.__init__` a `clock` parameter. Use `clock.now()` in `_loop()` (line 74) instead of `datetime.now(tz=timezone.utc)`.
  - Fix `_fire()` (lines 108-117) to actually pass the computed timestamp into the fired callback instead of calling `fn()` with zero arguments — this is what makes the `tick_analytics` hourly/daily/weekly lambdas' `_now or datetime.now(...)` fallback dead code today; once `_fire` passes a real value, delete the fallback in `main_stream.py`'s lambda registrations (lines 433-448).
  - Note: the Friday 21:00 "market close" cleanup wipe is inherently an MT5/broker-time concept (it's meant to align with actual market close) — it should key off MT5 time for exactly this reason, independent of the "we don't care about true UTC" simplification.
- `domain/entities.py` — `Trade`/`Zone`/`LiquidityPool.created_at` default factories (lines 101, 132, 148) currently default to `datetime.now(tz=timezone.utc)`. Either remove the default (require explicit `created_at` at every construction site) or, if a default must exist for convenience, wire it to a module-level reference to the shared clock rather than the host clock.

## 7. Cosmetic / non-trading-critical

- `api/server.py:52,532` — the dashboard `"server_time"` field is currently host clock. Either relabel it (`"host_time"`) or, since the shared MT5-time accessor is now cheap and centralized, just feed it the real value — recommended, since an operator debugging a trading issue should see the same clock the bot is trading on.
- JWT `iat`/`exp` (`api/server.py:226,290-298`) and the login rate-limiter (`api/server.py:352,364`) can keep using `time.time()` — this is legitimate here (self-consistent issuance/verification, not a trading decision) and is explicitly out of scope for this refactor.
- `trade_journal.py`, `cache_store.py`, `cleanup_service.py`, `checkpoint_service.py`'s fallback branch — host clock is acceptable for logging/retention timestamps; no change required unless you want full consistency, in which case route them through the shared clock too since it's now trivial to do.

## Verification checklist after implementing

- [ ] `grep -rn "TrueTimeClock\|time_sync" src/` → no hits.
- [ ] `grep -rn "datetime.now(tz=timezone.utc)\|time.time()" src/` → only hits remaining are in `api/server.py`'s JWT/rate-limiter code and (optionally) non-critical logging.
- [ ] `grep -rn "BROKER_UTC_OFFSET_HOURS" src/` → only hit is the news-ingestion conversion (renamed `MT5_NEWS_OFFSET_HOURS`).
- [ ] Every function that gates a trade (session, news, entry, cooldown, exit) takes `now`/`clock` as a required parameter — no `Optional[datetime] = None` left on any of them.
- [ ] Manual smoke test: force MT5 briefly unavailable and confirm the bot logs loudly and dead-reckons via monotonic delta, rather than silently switching to host time.
