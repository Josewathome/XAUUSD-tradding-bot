#!/usr/bin/env python3
"""lb_dashboard.py — Decision Dashboard (Deliverable D).

Maintains continuously-updated probabilities that the London Breakout edge is
real and that the experiment ends ALIVE vs REJECT — computed, not opined.

Model (FROZEN 2026-07-17 — do not tune mid-experiment):
  Three hypotheses about the current regime, with pre-registered priors from
  the research program (adversarial validation, recency decomposition):
    H1 backtest-strength edge  (mean +0.1246 R)   prior 0.20
    H2 stressed/weak edge      (mean +0.017 R)    prior 0.45
    H3 no edge after costs     (mean -0.050 R)    prior 0.35
  Each hypothesis' trade distribution = the frozen backtest R distribution
  (baseline_r.json) location-shifted to the hypothesis mean — same shape,
  different center (shape risk is what the experiment itself resolves).

  Posterior: P(H|data) ∝ P(H) · Normal(obs_mean; μ_H, σ_H/√n).
  P(ALIVE)/P(REJECT): Monte Carlo — bootstrap future trades from each H at the
  observed trade rate (fallback: backtest rate 24.3/mo) out to the 3-month
  gate (+3mo tail for undecided), applying the FROZEN criteria verbatim.

    cd research && box_theory/.venv/bin/python forward_test/lb_dashboard.py
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np
import pandas as pd

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
REPORTS = os.path.join(BASE, "reports")
TRADING_START = pd.Timestamp("2026-07-20")

HYP = [("H1 backtest-strength", 0.1246, 0.20),
       ("H2 stressed/weak", 0.017, 0.45),
       ("H3 no edge", -0.050, 0.35)]
CRIT = {"min_trades": 60, "min_months": 3.0, "alive_pf": 1.10, "alive_exp": 0.05,
        "reject_exp": 0.0, "reject_pf": 1.00}
BT_RATE_PER_MONTH = 24.3          # GBP+EUR backtest trade rate
SIMS = 4000
SEED = 20260717


def load_live():
    p = os.path.join(DATA, "trades.csv")
    if not os.path.exists(p):
        return np.array([])
    T = pd.read_csv(p, parse_dates=["entry_time"])
    T = T[T.entry_time >= TRADING_START]
    return T.r_multiple.to_numpy(dtype=float)


def crit_outcome(r):
    """Apply the frozen criteria to a completed-experiment trade series."""
    n = len(r)
    if n < CRIT["min_trades"]:
        return "UNDECIDED"
    exp = r.mean()
    gp = r[r > 0].sum(); gl = -r[r < 0].sum()
    pf = gp / gl if gl > 0 else np.inf
    if exp <= CRIT["reject_exp"] or pf < CRIT["reject_pf"]:
        return "REJECT"
    if pf > CRIT["alive_pf"] and exp > CRIT["alive_exp"]:
        return "ALIVE"
    return "UNDECIDED"


def main():
    ref = json.load(open(os.path.join(BASE, "baseline_r.json")))
    base_r = np.array(ref["r"], dtype=float)
    sigma = base_r.std()
    live = load_live()
    n_live = len(live)
    obs_mean = live.mean() if n_live else None

    # ── posterior over hypotheses ──
    posts = []
    for name, mu, prior in HYP:
        if n_live >= 5:
            se = sigma / np.sqrt(n_live)
            lik = np.exp(-0.5 * ((obs_mean - mu) / se) ** 2) / se
        else:
            lik = 1.0        # too little data: posterior = prior
        posts.append(prior * lik)
    posts = np.array(posts) / sum(posts)

    # ── Monte Carlo completion ──
    rng = np.random.default_rng(SEED)
    months_elapsed = max(0.0, (pd.Timestamp.now() - TRADING_START).days / 30.44)
    rate = (n_live / months_elapsed) if months_elapsed > 0.5 and n_live >= 10 else BT_RATE_PER_MONTH
    outcomes = {"ALIVE": 0.0, "REJECT": 0.0, "UNDECIDED": 0.0}
    for (name, mu, _), post in zip(HYP, posts):
        shifted = base_r - base_r.mean() + mu
        for s in range(SIMS):
            horizon = max(CRIT["min_months"], months_elapsed)
            n_future = rng.poisson(rate * (horizon - months_elapsed))
            r = np.concatenate([live, rng.choice(shifted, n_future, replace=True)])
            out = crit_outcome(r)
            if out == "UNDECIDED":   # run the 3-month extension tail
                n2 = rng.poisson(rate * 3.0)
                r = np.concatenate([r, rng.choice(shifted, n2, replace=True)])
                out = crit_outcome(r)
            outcomes[out] += post / SIMS

    p_edge = float(posts[0] + posts[1])           # any positive edge in regime
    p_alive = float(outcomes["ALIVE"])
    p_reject = float(outcomes["REJECT"])

    lines = []
    A = lines.append
    A("## Decision Dashboard (model frozen 2026-07-17)")
    A("")
    A("| Quantity | Estimate |")
    A("|---|---:|")
    A("| Live trades counted (from %s) | %d |" % (TRADING_START.date(), n_live))
    if n_live:
        A("| Live expectancy so far | %+.3f R |" % obs_mean)
    A("| P(edge is real in current regime) | **%.0f%%** |" % (100 * p_edge))
    A("| ├─ P(backtest-strength edge) | %.0f%% |" % (100 * posts[0]))
    A("| ├─ P(weak/stressed edge) | %.0f%% |" % (100 * posts[1]))
    A("| └─ P(no edge after costs) | %.0f%% |" % (100 * posts[2]))
    A("| P(experiment ends ALIVE) | **%.0f%%** |" % (100 * p_alive))
    A("| P(experiment ends REJECT) | **%.0f%%** |" % (100 * p_reject))
    A("| P(still undecided at +6 months) | %.0f%% |" % (100 * outcomes["UNDECIDED"]))
    A("")
    A("_P(edge survives REAL live execution) < P(ALIVE): demo fills are kinder than"
      " live — measured demo slippage is a lower bound._")
    text = "\n".join(lines)
    os.makedirs(REPORTS, exist_ok=True)
    with open(os.path.join(REPORTS, "DASHBOARD.md"), "w") as f:
        f.write(text + "\n")
    print(text)


if __name__ == "__main__":
    main()
