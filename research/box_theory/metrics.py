"""metrics.py — performance statistics from a trades DataFrame.

Expected trades columns: entry_time, exit_time, direction, r_net, ret_frac,
pnl_usd, equity_before, equity_after, duration_min, exit_reason.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd


def _max_drawdown_pct(equity_series: np.ndarray) -> float:
    """Max peak-to-trough drawdown (%) over an equity curve (peak-walk)."""
    if len(equity_series) == 0:
        return 0.0
    peak = equity_series[0]
    max_dd = 0.0
    for v in equity_series:
        if v > peak:
            peak = v
        dd = (peak - v) / peak if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd
    return max_dd * 100.0


def compute_metrics(trades: pd.DataFrame, initial_equity: float) -> dict:
    n = len(trades)
    base = {
        "total_trades": n, "wins": 0, "losses": 0, "win_rate": 0.0,
        "profit_factor": float("nan"), "avg_r": float("nan"),
        "expectancy_r": float("nan"), "expectancy_usd": float("nan"),
        "net_return_pct": 0.0, "final_equity": initial_equity,
        "max_drawdown_pct": 0.0, "sharpe_per_trade": float("nan"),
        "sharpe_annual": float("nan"), "avg_duration_min": float("nan"),
        "gross_profit_usd": 0.0, "gross_loss_usd": 0.0,
    }
    if n == 0:
        return base

    pnl = trades["pnl_usd"].to_numpy()
    r = trades["r_net"].to_numpy()
    ret = trades["ret_frac"].to_numpy()
    wins = int((pnl > 0).sum())
    losses = int((pnl < 0).sum())
    gross_profit = float(pnl[pnl > 0].sum())
    gross_loss = float(-pnl[pnl < 0].sum())  # positive magnitude

    equity_curve = np.concatenate([[initial_equity], trades["equity_after"].to_numpy()])
    final_equity = float(equity_curve[-1])

    # annualization from actual calendar span of trades
    span_days = (trades["exit_time"].iloc[-1] - trades["entry_time"].iloc[0]).total_seconds() / 86400.0
    trades_per_year = (n / span_days * 365.25) if span_days > 0 else float("nan")

    std_ret = float(np.std(ret, ddof=1)) if n > 1 else float("nan")
    sharpe_pt = (float(np.mean(ret)) / std_ret) if (std_ret and std_ret > 0) else float("nan")
    sharpe_annual = (sharpe_pt * math.sqrt(trades_per_year)
                     if (not math.isnan(sharpe_pt) and not math.isnan(trades_per_year)) else float("nan"))

    return {
        "total_trades": n,
        "wins": wins,
        "losses": losses,
        "win_rate": 100.0 * wins / n,
        "profit_factor": (gross_profit / gross_loss) if gross_loss > 0 else float("inf"),
        "avg_r": float(np.mean(r)),
        "expectancy_r": float(np.mean(r)),           # expected R per trade
        "expectancy_usd": float(np.mean(pnl)),
        "net_return_pct": 100.0 * (final_equity / initial_equity - 1.0),
        "final_equity": final_equity,
        "max_drawdown_pct": _max_drawdown_pct(equity_curve),
        "sharpe_per_trade": sharpe_pt,
        "sharpe_annual": sharpe_annual,
        "avg_duration_min": float(trades["duration_min"].mean()),
        "gross_profit_usd": gross_profit,
        "gross_loss_usd": gross_loss,
    }
