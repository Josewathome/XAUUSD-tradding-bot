# ══════════════════════════════════════════════════════════════════
#  config.py  —  ALL settings live here. One file.
#
#  SPLIT RESPONSIBILITIES
#  ──────────────────────
#  .env (secrets only — never commit):
#    MT5 credentials, broker server, VNC password, API keys,
#    dashboard password, email credentials, account balance/currency.
#
#  config.py (everything else — edit freely, commit safely):
#    Symbols, risk %, session windows, SL/TP limits, ATR thresholds,
#    news buffers, position sizing grades, feature flags — all tunable
#    settings that are NOT secrets belong here as plain constants.
#
#  Rule: if it's a password, token, or account number → .env.
#        if it's a number you'd tune to change bot behaviour → here.
# ══════════════════════════════════════════════════════════════════

import os

# ── SECRETS (read from .env — never hardcode here) ─────────────────

_trading_id_raw = os.environ.get("TRADING_ID")
if not _trading_id_raw:
    raise RuntimeError("TRADING_ID not set. Copy env.example → .env and fill it in.")
TRADING_ID = int(_trading_id_raw)

MT5_PASSWORD = os.environ.get("MT5_PASSWORD")
if not MT5_PASSWORD:
    raise RuntimeError("MT5_PASSWORD not set in .env.")

MT5_SERVER = os.environ.get("MT5_SERVER")
if not MT5_SERVER:
    raise RuntimeError("MT5_SERVER not set in .env.")

ACCOUNT_CURRENCY = os.environ.get("ACCOUNT_CURRENCY", "USD")
ACCOUNT_BALANCE  = float(os.environ.get("ACCOUNT_BALANCE", "10000"))

# Broker/account type: ZERO_SPREAD | IC_MARKETS_RAW | PREMIUM | PRO
# Set BROKER_ACCOUNT_TYPE in .env to match your actual account.
BROKER_ACCOUNT_TYPE: str = (
    os.environ.get("BROKER_ACCOUNT_TYPE")
    or os.environ.get("HFM_ACCOUNT_TYPE")
    or "ZERO_SPREAD"
).upper()
HFM_ACCOUNT_TYPE: str = BROKER_ACCOUNT_TYPE

NEWS_API_KEY = os.environ.get("NEWS_API_KEY", "")
NEWS_API_URL = os.environ.get("NEWS_API_URL", "")

DASHBOARD_PASSWORD         = os.environ.get("DASHBOARD_PASSWORD", "")
JWT_ACCESS_EXPIRES_MINUTES = int(os.environ.get("JWT_ACCESS_EXPIRES_MINUTES", "15"))
JWT_REFRESH_EXPIRES_DAYS   = int(os.environ.get("JWT_REFRESH_EXPIRES_DAYS",   "7"))

EMAIL_USER      = os.environ.get("EMAIL_USER",      "")
EMAIL_PASSWORD  = os.environ.get("EMAIL_PASSWORD",  "")
EMAIL_RECIPIENT = os.environ.get("EMAIL_RECIPIENT", "")

# ── ACCOUNT METADATA (informational, not secrets) ──────────────────
ACCOUNT_NICKNAME        = "Demojose"
LEVERAGE                = "1:400"
MT5_PATH                = ""
BROKER_UTC_OFFSET_HOURS: int = 2

# ── SYMBOLS TO TRADE ───────────────────────────────────────────────
# Add/remove symbols here. Each symbol needs a matching entry in
# SCALPER_SYMBOL_SESSIONS, SCALPER_MIN/MAX_SL_PIPS, and SYMBOL_DIGITS below.
SYMBOLS = ["XAUUSD", "EURUSD"]

# ── IC Markets RAW commission table ────────────────────────────────
IC_COMMISSION_PER_LOT_USD: float = 3.50
IC_COMMISSION_USD: dict[str, float] = {
    "EURUSD": 3.50, "GBPUSD": 3.50, "USDJPY": 3.50,
    "AUDUSD": 3.50, "USDCHF": 3.50, "NZDUSD": 3.50,
    "EURGBP": 3.50, "USDCAD": 3.50, "EURJPY": 3.50, "GBPJPY": 3.50,
    "XAUUSD": 3.50, "XAGUSD": 3.50,
}
IC_COMMISSION_USD_DEFAULT: float = 3.50

# ── HFM Zero Spread commission (kept for backward compat in build_commission_map) ──
HFM_COMMISSION_USD: dict[str, float] = {
    "EURUSD": 3.0, "GBPUSD": 3.0, "USDJPY": 3.0,
    "AUDUSD": 3.0, "USDCHF": 3.0, "NZDUSD": 3.0,
    "EURGBP": 3.0, "USDCAD": 3.0, "EURJPY": 3.0, "GBPJPY": 3.0,
    "XAUUSD": 5.0, "XAGUSD": 5.0,
}
HFM_COMMISSION_USD_DEFAULT: float = 3.0

# ── Risk Management ────────────────────────────────────────────────
RISK_PERCENT        = 2.0
COMMISSION_PER_LOT  = 3.0  # USD/lot (per side) — default for major pairs
MIN_RR              = 1.5
# Maximum unique symbols that may have open trades simultaneously.
# A new symbol can only open a trade if active_symbols < MAX_OPEN_SYMBOLS.
# The final free slot is reserved for A-grade entries.
MAX_OPEN_SYMBOLS: int = 5

# Per-symbol trade caps.
# A/A+ trades: up to MAX_TRADES_PER_SYMBOL_A  (default 3)
# B/C/D trades: 1 additional slot beyond the A-grade trades (total cap 4)
# Hard per-symbol ceiling regardless of grade.
MAX_TRADES_PER_SYMBOL_A: int   = 3
MAX_TRADES_PER_SYMBOL:   int   = 4
MAX_LOT_SIZE:            float = 10.0

# Absolute money-risk ceiling per trade as a fraction of balance.
# Hard cap enforced AFTER lot sizing: if the realised SL risk of even the
# broker-minimum lot exceeds this, the trade is REJECTED (fail closed).
MAX_TRADE_RISK_PCT: float = 3.0

# ── Margin Safety ────────────────────────────────────────────────
MARGIN_SAFETY_FACTOR:  float = 1.5
# Minimum margin level (%) required before a new trade is allowed.
# MT5 issues a margin call at 100%; we block well above that.
MIN_MARGIN_LEVEL_PCT:  float = 200.0

# ── Portfolio Allocation ──────────────────────────────────────────
# Always keep this fraction of equity reserved (free margin) for future setups.
MARGIN_RESERVE_PCT:    float = 0.25
# Maximum total open exposure as fraction of equity.
MAX_EQUITY_EXPOSURE:   float = 0.60
# Maximum correlated-currency exposure (trades sharing the same currency leg).
MAX_CURRENCY_EXPOSURE: int   = 3

# ── Daily drawdown circuit breaker ────────────────────────────────
# If equity drops this % below the day's opening equity, all new entries
# are blocked for the rest of the UTC day. Open trades keep being managed.
DAILY_DRAWDOWN_LIMIT_PCT: float = 6.0

# ── SL buffer (pips) ─────────────────────────────────────────────
EXTRA_PIPS_SL_DEFAULT = 3.0

# ── News Guardrail ────────────────────────────────────────────────
HIGH_IMPACT_BUFFER_MINS    = 30
MEDIUM_IMPACT_BUFFER_MINS  = 0
NEWS_BUFFER_MINUTES        = 15
STRICT_NEWS_FILTER         = True

ATR_COST_THRESHOLD         = 0.25

NEWS_REFRESH_INTERVAL_MINUTES = 10

# ZeroMQ endpoint — MT5 EA publishes on this address; must match EA settings.
ZMQ_ENDPOINT: str = "tcp://127.0.0.1:5556"

# ── Symbol Metadata ───────────────────────────────────────────────
SYMBOL_DIGITS: dict[str, int] = {
    "EURUSD": 5, "GBPUSD": 5, "AUDUSD": 5, "USDCAD": 5,
    "USDCHF": 5, "NZDUSD": 5, "EURGBP": 5,
    "USDJPY": 3, "EURJPY": 3, "GBPJPY": 3,
    "XAUUSD": 2, "XAGUSD": 3,
}

TICK_VALUE_USD: dict[str, float] = {
    "EURUSD": 10.0, "GBPUSD": 10.0, "AUDUSD": 10.0,
    "USDCAD":  7.7, "USDCHF": 11.2, "NZDUSD": 10.0,
    "USDJPY":  9.1, "EURJPY":  9.1, "GBPJPY":  9.1,
    "XAUUSD": 10.0, "XAGUSD":  5.0,
}

KES_PER_USD: float = 130.0

SYMBOL_CURRENCIES: dict[str, list[str]] = {
    "EURUSD": ["EUR", "USD"],
    "XAUUSD": ["USD"],
}

# ── Trade Monitor ─────────────────────────────────────────────────
MONITOR_ENABLED             = True
MONITOR_ACT_ON_SIGNALS      = True
MONITOR_CLOSE_CONVICTION    = 2

BREAKEVEN_RR_TRIGGER        = 1.5

# Loss streak: tracked for analytics only — no trading pause enforced.
# Set LOSS_STREAK_PAUSE_HOURS = 0 to disable pause (default).
MAX_CONSECUTIVE_LOSSES:  int   = 5    # streak length before pause
LOSS_STREAK_PAUSE_HOURS: float = 2.0  # per-symbol cooldown hours; set 0 to disable

# ── Multi-level Take Profit ────────────────────────────────────────
TIERED_TP_ENABLED        = True
TIERED_TP1_RATIO         = 1.5
TIERED_TP1_CLOSE_PCT          = 0.60
TIERED_TP1_CLOSE_PCT_A_PLUS   = 0.40
TIERED_TP1_CLOSE_PCT_B        = 0.50
TIERED_TP2_RATIO         = 2.0
TIERED_TP2_CLOSE_PCT     = 0.25
FINAL_RUNNER_PCT         = 0.15

# ── TP1 PROFIT-LOCK ───────────────────────────────────────────────
TP1_PROFIT_LOCK_R = 0.3

# ── MONITOR TRAILING ──────────────────────────────────────────────
MONITOR_TRAIL_ATR_BUFFER = 0.5

# ── TRADE QUALITY SCORING ─────────────────────────────────────────
TRADE_SCORE_THRESHOLD   = 2

# ── DYNAMIC POSITION SIZING ───────────────────────────────────────
# Quality-weighted risk per grade (now wired via MarginManager):
#   A+  conf ≥ 0.85 AND tq ≥ 0.75  →  RISK_HIGH_CONVICTION
#   A   conf ≥ 0.70 OR  tq ≥ 0.65  →  RISK_MEDIUM_CONVICTION
#   B   conf ≥ 0.60 OR  tq ≥ 0.50  →  RISK_LOW_CONVICTION
#   C   otherwise                   →  RISK_MIN_CONVICTION
RISK_HIGH_CONVICTION:   float = 2.5   # A+  conf ≥ 0.85 AND tq ≥ 0.75
RISK_MEDIUM_CONVICTION: float = 1.8   # A   conf ≥ 0.70 OR  tq ≥ 0.65
RISK_LOW_CONVICTION:    float = 1.0   # B   conf ≥ 0.60 OR  tq ≥ 0.50
RISK_MIN_CONVICTION:    float = 0.5   # C   otherwise

# ── TIME-BASED EXIT ───────────────────────────────────────────────
MAX_TRADE_DURATION_MINUTES = 600

# ── Minimum SL distance ───────────────────────────────────────────
MIN_SL_PIPS: dict[str, float] = {
    "EURUSD": 3.0, "GBPUSD": 3.0, "AUDUSD": 3.0,
    "USDJPY": 5.0, "EURJPY": 5.0, "GBPJPY": 5.0,
    "XAUUSD": 150.0,   # GOLD: kept consistent with SCALPER_MIN_SL_PIPS ($1.50)
    "XAGUSD": 20.0,
    "USDCHF": 3.0,
}
MIN_SL_PIPS_DEFAULT = 3.0

# ── Candle counts for scalper ─────────────────────────────────────
M1_CANDLE_COUNT  = 60
M5_CANDLE_COUNT  = 60

# ── Retention Policies ────────────────────────────────────────────
LOG_RETENTION_DAYS   = 3
CACHE_RETENTION_DAYS = 2

# ── Checkpoint ────────────────────────────────────────────────────
CHECKPOINT_DIR           = ".checkpoints"
CHECKPOINT_INTERVAL_MIN  = 5
CHECKPOINT_RETAIN_DAYS   = 7

# ── Dashboard ─────────────────────────────────────────────────────
DASHBOARD_ENABLED = True
DASHBOARD_PORT    = 8080

# Informational only — STREAM mode is event-driven (no fixed poll interval).
# Surfaced/editable on the dashboard config tab; not used by the live loop.
LOOP_INTERVAL_SECONDS:    int   = 1
BACKTEST_INITIAL_BALANCE: float = ACCOUNT_BALANCE

# ── Email ─────────────────────────────────────────────────────────
EMAIL_ENABLED   = True
EMAIL_SMTP_HOST = "smtp.gmail.com"
EMAIL_SMTP_PORT = 587
# EMAIL_USER / EMAIL_PASSWORD / EMAIL_RECIPIENT are read from .env above

# ══════════════════════════════════════════════════════════════════
#  STRATEGY-ENGINE CONFIG (PLACEHOLDER DEFAULTS)
#  ────────────────────────────────────────────────────────────────
#  Everything from here down is tuning for the STRATEGY ENGINE you plug
#  in via src/strategy/interfaces.py (signal generation, SL/TP placement,
#  structural exits). The framework reads only a subset of these for its
#  risk gates (session windows, min-SL floor, spread caps, hold limits,
#  velocity floor). The signal-quality / structural thresholds are inert
#  until a real IStrategyEngine / ITradeGuardian is wired in — they are
#  kept here as sensible XAUUSD defaults for that engine.
# ══════════════════════════════════════════════════════════════════
#
#  SCALPER MODE — tighter targeting on M1/M5 momentum (engine-side)
# ══════════════════════════════════════════════════════════════════

SCALPER_MODE: bool = True

# Legacy flat session window — overridden by SCALPER_SYMBOL_SESSIONS per-symbol entries.
SCALPER_SESSION_START_UTC: int = 6
SCALPER_SESSION_END_UTC:   int = 16

# ── Per-symbol session windows (UTC hour start, UTC hour end) ─────────────
# Rationale per symbol:
#   GBPUSD  06-16  Pure London/NY pair — illiquid and choppy outside these hours
#   XAUUSD  06-20  Gold: London + NY + EU afterhours (genuine institutional flow)
#   USDJPY  00-16  Active Asian session (Tokyo) + London + NY
#   AUDUSD  00-12  Active Asian session (Sydney/Tokyo) + London open
#   USDCHF  06-16  European pair — follows GBPUSD pattern, dead in Asian hours
#
# Override any symbol via env var: SCALPER_SESSION_GBPUSD_START=8
# (not yet wired — edit here directly if you need to adjust)
SCALPER_SYMBOL_SESSIONS: dict = {
    "GBPUSD": (5,  19),   # Pre-London open → NY afternoon (extended from 17 — NY active until ~19 UTC)
    "XAUUSD": (12, 21),   # GOLD: London-PM + NY/COMEX core only (cleanest gold flow)
    "USDJPY": (0,  19),   # Full Asian + London + NY afternoon (extended from 17)
    "AUDUSD": (22, 13),   # Sydney open (wraps midnight) → London midday
    "USDCHF": (5,  19),   # European pair — extended to match GBPUSD NY afternoon
    "EURUSD": (10, 13),   # London Breakout window only (Asian range 02–10, breakout 10–13)
}

# ── Tick velocity gate ────────────────────────────────────────────────────
# Minimum ticks/second required before an entry is allowed.
# Measured over the last 5 seconds by TickFeatureCalculator.tick_velocity().
# Blocks dead/illiquid markets without hardcoding session hours.
#
# Calibration reference (IC Markets RAW, GBPUSD):
#   Active session (London/NY):     4–12 ticks/sec
#   Thin but tradeable (EU close):  1.5–4 ticks/sec
#   Low liquidity (Asian for GBP):  0.2–1.0 ticks/sec
#   Dead market (weekend gap):      0–0.1 ticks/sec
#
# 1.5 allows thin-but-moving markets (e.g. USDJPY at 01:00 UTC) while
# blocking genuinely dead price action.  Raise to 2.5 if you see too
# many low-quality Asian session signals during live observation.
SCALPER_MIN_TICK_VELOCITY: float = 1.0  # ticks/sec — raise to 2.5 if too many low-quality signals appear

SCALPER_MIN_SL_PIPS: dict = {
    "GBPUSD": 3.0,
    "XAUUSD": 150.0,   # GOLD: $1.50 floor — survives normal M1 noise (a "pip" is $0.01)
    "USDJPY": 3.0,
    "AUDUSD": 3.0,
    "USDCHF": 3.0,
    "EURUSD": 3.0,
}
SCALPER_MIN_SL_PIPS_DEFAULT: float = 3.0

SCALPER_MAX_SL_PIPS: dict = {
    "GBPUSD": 12.0,
    "XAUUSD": 500.0,   # GOLD: $5.00 ceiling — reject setups needing a wider stop
    "USDJPY": 12.0,
    "AUDUSD": 12.0,
    "USDCHF": 12.0,
    "EURUSD": 12.0,
}
SCALPER_MAX_SL_PIPS_DEFAULT: float = 12.0

# ══════════════════════════════════════════════════════════════════
#  GOLD (XAUUSD) — DEDICATED ADAPTIVE PROFILE
#  Gold's "pip" is $0.01, so FX-calibrated fixed pip stops/targets are
#  economically tiny and get destroyed by noise + cost. Gold therefore sizes
#  its stop from its OWN M5 ATR (volatility-adaptive), clamped to absolute
#  bounds, and carries its own spread cap, session, and hold limit. These
#  settings affect ONLY symbols listed in SCALPER_ATR_PRIMARY_SYMBOLS — every
#  other symbol keeps the unchanged swing-first / fixed-pip behaviour.
# ══════════════════════════════════════════════════════════════════
# Symbols that size their stop from ATR as the PRIMARY method (not just fallback).
SCALPER_ATR_PRIMARY_SYMBOLS: set = {"XAUUSD"}
# ATR multipliers for the adaptive stop (applied to M5 ATR):
#   floor  = ATR × MIN_MULT   (stop never tighter than this — survive noise)
#   ceiling= ATR × MAX_MULT   (stop never wider than this — cap risk)
#   default= ATR × DEFAULT_MULT (used when no structural swing is available)
# The result is ALSO clamped to the absolute SCALPER_MIN/MAX_SL_PIPS guards above.
SCALPER_ATR_SL_MIN_MULT:     float = 0.7
SCALPER_ATR_SL_MAX_MULT:     float = 1.5
SCALPER_ATR_SL_DEFAULT_MULT: float = 1.0

# Per-symbol absolute spread cap (pips). Falls back to the global MAX_SPREAD_PIPS.
# Gold spreads are naturally wide in pip terms ($0.01 pips), so it needs its own.
SCALPER_MAX_SPREAD_PIPS: dict = {
    "XAUUSD": 80.0,   # $0.80 — gold raw spread is wide in 0.01-pip terms
}

# Per-symbol maximum hold minutes. Falls back to SCALPER_MAX_HOLD_MINUTES.
SCALPER_MAX_HOLD_MINUTES_PER_SYMBOL: dict = {
    "XAUUSD": 20,     # gold moves fast — shorter scalp window
}

SCALPER_TP1_RR: float = 1.5
SCALPER_TP2_RR: float = 2.0

SCALPER_MAX_HOLD_MINUTES: int = 30

# No hard daily trade cap — frequency is bounded by margin + EV gate only.
# Set to a positive integer to cap trades per day per symbol.
SCALPER_MAX_DAILY_TRADES: int = 0   # 0 = no cap

# Legacy — no longer used by MarginManager (controlled by MAX_OPEN_SYMBOLS above).
SCALPER_MAX_OPEN_TRADES: int = 20

# Signal-quality gate thresholds.
SCALPER_MIN_ALIGNMENT_SCORE: float = 0.55
SCALPER_MIN_TICK_SCORE:      float = 0.50
SCALPER_MIN_CANDLE_SCORE:    float = 0.30

# Entry context scorer thresholds.
ENTRY_TICK_CONFIRMS_MIN_SCORE: float = 0.25
ENTRY_TICK_CONFIRMS_MIN_BIAS:  float = 0.20
ENTRY_BURST_MOVE_THRESHOLD:    float = 0.40

SCALPER_M5_EMA_PERIOD:    int   = 10
SCALPER_M1_CONSENSUS_MIN: int   = 3
SCALPER_ATR_SL_FRACTION:  float = 0.4

# ── TP ratios (for entry_gate.py) ─────────────────────────────────
TP1_RR_RATIO: float = SCALPER_TP1_RR
TP2_RR_RATIO: float = SCALPER_TP2_RR

# ── Structure-aware take-profit ───────────────────────────────────
# A blind 1.5R/2.0R target frequently sits past where price actually turns
# (day-12: only 2/13 hit TP1 while 6/13 reached ≥5 pips of favourable move).
# When enabled, TP1/TP2 are pulled in to the NEAREST reachable target:
#   min( fixed RR , next opposing swing − buffer , ATR horizon cap ).
TP_STRUCTURE_AWARE:    bool  = True
TP1_ATR_CAP_MULT:      float = 1.5
TP_STRUCT_BUFFER_PIPS: float = 1.0
MIN_RR_FLOOR:          float = 1.0

# ── Entry gate — live trading switch ─────────────────────────────
# SAFE DEFAULT: false = observation mode.  The bot logs every signal but
# places NO orders until you change this to True (or set STRATEGY_GATE_ENABLED=true
# in .env to override without editing code).
STRATEGY_GATE_ENABLED: bool = os.environ.get("STRATEGY_GATE_ENABLED", "false").lower() == "true"

# ── Tick Analytics ────────────────────────────────────────────────
TICK_ANALYTICS_ENABLED:  bool = True
TICK_ANALYTICS_SAVE:     bool = True
TICK_ANALYTICS_SCHEDULE: bool = True

# ── EV (Expected Value) gate ──────────────────────────────────────
# EV = (win_rate × avg_win_pips) − ((1 − win_rate) × avg_loss_pips)
# Switches from ASSUMED_WIN_RATE to measured rolling rate once EV_MIN_SAMPLES
# closed trades are recorded per symbol.
ASSUMED_WIN_RATE:         float = 0.50
ASSUMED_WIN_RATE_HAIRCUT: float = 0.05   # bootstrap haircut
EV_MIN_PIPS:              float = 0.10
EV_ROLLING_WINDOW:        int   = 50
EV_MIN_SAMPLES:           int   = 30
RUNNER_EXIT_RR:           float = SCALPER_TP1_RR

# ── Exploration bootstrap ─────────────────────────────────────────
# Allows a small number of minimum-risk trades per day that bypass ONLY the
# EV requirement while the system builds its first real sample of outcomes.
# Every other gate (news, spread, margin, R:R, session, velocity) still applies.
EXPLORATION_ENABLED:        bool = True
EXPLORATION_TRADES_PER_DAY: int  = 8   # per symbol

# ── Reversal re-entry cooldown ────────────────────────────────────
REVERSAL_REENTRY_COOLDOWN_SEC: int = 120

# ── Ranging-regime suppression ────────────────────────────────────
RANGING_ALIGN_PENALTY: float = 0.10

# ── Spread / cost gate ────────────────────────────────────────────
# Round-trip cost (spread + commission) must be no more than this fraction of
# the TP1 target distance, otherwise the trade is rejected — a scalp whose
# target is eaten by cost has no edge. Also rejects on missing/insane spread.
COST_MAX_FRACTION_OF_TARGET: float = float(os.environ.get("COST_MAX_FRACTION_OF_TARGET", "0.40"))
MAX_SPREAD_PIPS: float = float(os.environ.get("MAX_SPREAD_PIPS", "40.0"))

# ── News data freshness (fail-closed) ─────────────────────────────
# If the news cache has not refreshed successfully within this many minutes,
# the news gate blocks ALL entries (we cannot prove we are clear of news).
NEWS_MAX_STALENESS_MIN: float = float(os.environ.get("NEWS_MAX_STALENESS_MIN", "30"))
# If True, REQUIRE a real external news feed (NEWS_API_KEY). When no external
# feed is configured the only source is MT5's built-in calendar, whose coverage
# is best-effort; setting this True makes the news gate block all entries until
# an external feed is present (explicit fail-closed). Default False (the startup
# logs a prominent warning so the operator knows coverage is best-effort).
NEWS_REQUIRE_EXTERNAL_FEED: bool = os.environ.get("NEWS_REQUIRE_EXTERNAL_FEED", "false").lower() == "true"

# ── Duplicate-trade guard ─────────────────────────────────────────
# Prevents re-entering the same symbol in the same direction within
# ANTI_DUPE_SECONDS seconds of the last fill.  Blocks runaway loops
# without capping total daily volume.
ANTI_DUPE_SECONDS:       int   = int(os.environ.get("ANTI_DUPE_SECONDS",         "60"))

# ── Trade Guardian (continuous re-evaluation) ─────────────────────
# cont_score = M1_structure*0.40 + signal_alignment*0.35 + pnl_ratio*0.25
#
# Score bands:
#   >= REEVAL_HOLD_THRESHOLD      → thesis intact, hold (clear defensive mode)
#   >= REEVAL_DEFENSIVE_THRESHOLD → weakening, enter defensive mode, tighten SL
#   >= REEVAL_EXIT_THRESHOLD      → partial defensive exit when in significant profit
#   <  REEVAL_EXIT_THRESHOLD      → thesis invalidated, close trade
#
# Reversal: opposing signal with confidence >= REEVAL_REVERSAL_CONF closes trade.
REEVAL_HOLD_THRESHOLD:      float = float(os.environ.get("REEVAL_HOLD_THRESHOLD",      "0.55"))
REEVAL_DEFENSIVE_THRESHOLD: float = float(os.environ.get("REEVAL_DEFENSIVE_THRESHOLD", "0.40"))
REEVAL_EXIT_THRESHOLD:      float = float(os.environ.get("REEVAL_EXIT_THRESHOLD",      "0.25"))
REEVAL_REVERSAL_CONF:       float = float(os.environ.get("REEVAL_REVERSAL_CONF",       "0.70"))

# Buffer pips below/above swing point when computing a new structural SL.
TRAIL_SL_BUFFER_PIPS: float = float(os.environ.get("TRAIL_SL_BUFFER_PIPS", "1.5"))
