# Box Theory Model — Backtest & Validation Study

Standalone quantitative research module that tests the **"Box Theory"** strategy
(fade the previous day's High/Low on a "John Wick" rejection candle) on 6 FX/metal
pairs. It is **independent of the live execution framework** and does not modify it.

**Verdict: the theory is rejected** — no robust, tradeable edge. Full write-up in
[`report/BOX_THEORY_REPORT.md`](report/BOX_THEORY_REPORT.md). Headline: the core
"reverses at PDH/PDL" premise is a coin flip (~50%), 87% of 16,128 configurations
lose money, and walk-forward selection pools to ≈0 R.

## Pipeline

| Step | Command | Output |
|---|---|---|
| 1. Export data (in container) | `docker exec -u abc xauusd-bot bash -lc 'export WINEPREFIX=/config/.wine HOME=/config; wine python /bot/research/box_theory/export_data.py'` | `data/*.csv` |
| 2. Unit tests | `cd research && box_theory/.venv/bin/python -m box_theory.tests.test_core` | 33 tests |
| 3. Phase-1 validation | `... -m box_theory.run_phase1` | sample trades, equity |
| 4. Full sweep | `... -m box_theory.sweep` | `results/master_results.csv` |
| 5. Sweep analysis | `... -m box_theory.analyze_sweep` | console summary |
| 6. Robustness | `... -m box_theory.robustness` | `results/robustness.json` |
| 7. Report + charts | `... -m box_theory.report` | `report/` |

> `data/`, `results/master_results.csv`, and `.venv/` are gitignored (heavy /
> reproducible). The `report/` and small evidence tables are committed.

## Environment
Host Python 3.13 has no pip; analysis runs in a bootstrapped venv at `.venv`
(numpy/pandas/scipy/matplotlib/pyarrow). Data is pulled from the live MT5 terminal
in the `xauusd-bot` container via a read-only IPC session. Data depth: M15/M30/D1
≈ 24 months (USDCAD ~18); M5 capped at ~5.7 months by the broker.

## Method notes
- P&L in R-multiples; each trade sized so a full stop = −1R = `risk_pct` of equity,
  so `$ = R × risk_amount` (asset-agnostic). Costs = per-symbol round-turn
  spread+commission in `config.py` (IC Markets Raw assumptions).
- Engine: bar-by-bar, no look-ahead (signal at close, managed next bar),
  pessimistic SL-first intrabar ties, compounding equity, one trade per box,
  flat overnight. A seeded random-entry control confirms the harness adds no edge.

## Files
`config` · `load` · `boxes` · `wick` · `engine` · `metrics` · `conditions` ·
`sweep` · `analyze_sweep` · `robustness` · `report` · `export_data` · `tests/`.
