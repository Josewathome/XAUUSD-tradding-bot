# PM / Adversarial-Auditor Protocol (frozen 2026-07-17)

Role: skeptical portfolio manager. Goal: determine whether the historical edge
survives real forward conditions. The experiment is guilty until proven
innocent. No rule changes, no parameter tuning, no new filters, no goalpost
moves, no cherry-picking, no early stopping on favorable evidence.

## Automated (cron, no judgment required)
- Every 5 min: tick engine + heartbeat.
- Daily 16:20 EAT: broker dump → reconcile → conservation → metrics vs
  baselines → Decision Dashboard → alerts email on any anomaly or REJECT.
- Weekly Sat 14:00 EAT: deliverable A — trades taken/missed, WR, PF,
  expectancy, slippage stats, drawdown, signal funnel, dashboard.

## On-demand judgment reviews (run with Claude, ~weekly and monthly)
Weekly adversarial pass (deliverable C):
  1. Read reports/STATUS.md + weekly_*.md + DASHBOARD.md.
  2. For every positive: propose the false-positive explanation (variance,
     demo-fill kindness, favorable regime week, missing losers → check
     conservation + reconcile first).
  3. For every negative: compute whether it is distinguishable from H1/H2
     variance at current n (the dashboard posterior does this formally).
  4. Verify operational: heartbeat, funnel has range_set ≈ trading days ×2,
     tick_ERROR = 0, missed_fast_move rate < ~10% of signals.
Monthly research review (deliverable B):
  - Tracking error vs backtest baseline and stressed baseline.
  - Trend of 20-trade rolling expectancy (improving/stable/deteriorating).
  - Execution costs vs assumptions (avg spread at signal, avg |slippage|,
    commission per trade) — flag if slippage > 1p average or spread gate
    binding more than ~2% of signals.
  - Overfitting check: live WR and payoff shape vs backtest distribution
    (KS distance on R distribution once n ≥ 40).

## Decision rules (pre-registered; identical to frozen criteria)
- ALIVE (GO): n≥60, ≥3 months, PF>1.10, exp>+0.05R, |slip|≤1p, reconcile AND
  conservation clean → recommend GO to small live capital with slippage
  re-measured live before scaling.
- REJECT: exp≤0 or PF<1.00 after 60 trades, or rolling-20 |slip|>2p →
  terminate, archive, write post-mortem comparing live vs backtest attribution.
- Undecided at 6 months → extend only if dashboard P(ALIVE) ≥ 25%; else wind down.

## Baselines (frozen)
| Source | ExpR | PF | WR |
|---|---|---|---|
| Backtest GBP+EUR M15 24mo | +0.125 | 1.30 | 49.1% |
| Adversarial stressed (recent-12mo, cost+50%, slip 1p) | +0.017 | ~1.04 | ~45% |
| Dashboard priors | H1 20% / H2 45% / H3 35% | | |
