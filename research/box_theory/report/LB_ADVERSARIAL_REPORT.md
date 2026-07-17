# London Breakout — Adversarial Validation

Premise: assume the positive result (+0.125 R GBP+EUR, p=0.0035; +0.075 R pooled-6,
p=0.005; M15 24mo, costs included) is a false positive and try to prove it.
Ten attacks. Raw numbers in `results/lb_adversarial.json`; reproduce with
`python -m box_theory.lb_adversarial`.

## Attack results

| # | Attack | Result | Verdict |
|---|---|---|---|
| 1 | Walk-forward (Y1→Y2) | +0.056 (p=0.08) | survived (weakly) |
| 1 | Reversed (Y2→Y1) | +0.094 (p=0.013) | survived |
| 1 | Rolling 6mo windows | 6 of 7 positive (one −0.025 in 2025H2) | survived |
| 1 | Param-fit WF (RR fit on Y1) | Y2 +0.063 (p=0.17) | survived (point estimate) |
| 2 | Sensitivity: 18 cells across session end / window end / stop offset / expiry / RR | **all 18 positive** (+0.034…+0.137), no cliffs | **survived cleanly** |
| 3 | Ablations (one rule at a time) | all variants stay positive; see below | survived |
| 4 | Cost +100% | GBP+EUR +0.094 (p=0.021) | survived |
| 4 | Slippage +1 pip | +0.092 (p=0.023) | survived |
| 4 | Slippage +2 pips | +0.060 (p=0.098) | **wounded** |
| 4 | Slippage +3 pips | +0.027 (p=0.28); pooled-6 negative | **failed** |
| 5 | Monte Carlo 10,000 | P(profit) 99.6%, net median +100%, maxDD p95 22.6% | survived |
| 6 | M30 replication | +0.067, n=1214, p=0.009 | **survived (independent TF)** |
| 6 | M5 | +0.008 (recent-8mo window only) | inconclusive (data limit) |
| 7 | Pair split | GBPUSD +0.134 (p=0.018); EURUSD +0.114 (p=0.046) | survived |
| 8 | Recent 6 months | pooled-6 +0.111 (p=0.03); GBP+EUR +0.075 (p=0.20, n=128) | survived pooled; spec-pairs underpowered |
| 10 | **Combined: cost+50% AND slippage+1p AND recent-12mo** | pooled-6 **+0.012 (p=0.39)**; GBP+EUR **+0.017 (p=0.40)** | **FAILED to stay significant** |

## Ablations — where the edge actually lives

| Variant | ExpR | n | p |
|---|---:|---:|---:|
| Faithful (all rules) | **+0.125** | 582 | 0.0035 |
| No body-close (wick pierce ok) | +0.110 | 524 | 0.014 |
| **No stop-order (market at close)** | **+0.047** | 914 | 0.11 |
| No 30-min expiry | +0.110 | 727 | 0.004 |
| No ADR filter | +0.122 | 595 | 0.004 |

**The stop-order confirmation is the load-bearing rule** — removing it cuts the edge
by nearly two-thirds. Requiring price to push past the breakout candle's extreme
within 30 minutes is what filters dead breakouts; the body-close, expiry, and ADR
rules are secondary trims (~0.01–0.015 R each). No single rule *creates* fake
profitability (removing any one leaves the sign positive) — the profile of a real
effect, not a curve-fit artifact.

## Failure modes (GBP+EUR: 296 losers vs 286 winners)

Losers: 202 stop-outs + 94 end-of-day fizzles. Only 38% never went favorable
(MFE < 0.25R); 11% reached +1R and then died (the fixed 2R target forgoes banking
partial winners). Loser stop distances, Asian-range sizes, and fill hours are
statistically identical to winners' — **losing days are not identifiable in advance;
losses are the base rate of false breakouts (~50%), and the edge is the asymmetric
2R payoff on the true ones.** Buy breakouts outperform sells (+0.177 vs +0.068).

## Verdict

**Not destroyed — but wounded in a specific, quantified way.**

- **The 2024–2025 edge was almost certainly real** (~85% confidence): flat across all
  18 parameter cells, robust to every single-rule ablation, doubles of commission,
  replication on M30, both pair halves, both year halves, 99.6% Monte-Carlo P(profit).
  A data-mining artifact does not have that shape.
- **Exploitability today is uncertain** (~40–50% confidence): the combined stress —
  current 12 months + cost +50% + 1 pip slippage — leaves a point estimate of
  +0.01–0.02 R that is statistically zero. The two killers are (a) regime decay
  concentrated in GBPUSD (its quarterly expectancy fell +0.27 → ~0) and (b) slippage
  sensitivity intrinsic to stop-order entries (tolerance ≈ 2 pips).
- **No hidden rule or assumption manufactured the profit.** The honest attribution:
  ~60% of headline profitability came from the 2024–25 regime, ~100% of the
  *mechanism* is the stop-order momentum confirmation, and the margin over costs is
  ~2 pips of slippage wide.

**Consequence for the plan:** the demo forward test is not merely still justified —
it is precisely the experiment that measures the two open variables (live fills and
current regime) that backtesting cannot. Deploy nothing until it reports.
