"""forensics.py — trade-level root-cause analysis of the Sneaky Pivot.

Pools trades from a fixed CANONICAL config across all pairs (for statistical
power), labels each with pre-entry features + excursion/outcome, then:
  * classifies every loss into a primary cause (priority taxonomy) + co-occurrence
  * characterises winners vs losers (effect sizes)
  * simulates pre-entry filters and measures the net effect on WR/PF/exp/DD/return
  * ranks the statistical drivers of profitability (corr + logistic + tree)

    cd research && box_theory/.venv/bin/python -m box_theory.forensics

Canonical config: M15, 24mo, literal swing, levels=both, all-day (max trades),
wick B, buffer 0.10, opposite-side target, one trade/day. All-day is used for
statistical power; the timing question was already answered (does not add edge).
"""
from __future__ import annotations

import os
import json
import numpy as np
import pandas as pd

from . import config
from .load import load_ohlc, load_d1
from .levels import build_levels
from .engine import _atr
from .conditions import daily_regime
from .sneaky_engine import run_sneaky
from .metrics import compute_metrics

RES = os.path.join(os.path.dirname(__file__), "results")
REPORT = os.path.join(os.path.dirname(__file__), "report")

CANON = {"swing_def": "literal", "levels_used": "both", "entry_model": "allday",
         "open_anchor_hour": None, "open_window_bars": 6, "wick": "B",
         "buffer_pct": 0.10, "mid_band_pct": config.MID_BAND_PCT,
         "target": ("opposite", None), "risk_pct": 0.01}
TF = "M15"
MONTHS = 24


# ── feature labeling ──────────────────────────────────────────────────
def label_pair(sym: str) -> pd.DataFrame:
    df = load_ohlc(sym, TF, months=MONTHS)
    d1 = load_d1(sym)
    lv = build_levels(d1, CANON["swing_def"])
    trades = run_sneaky(df, lv, sym, CANON)["trades"]
    if len(trades) == 0:
        return trades

    o = df["open"].to_numpy(); h = df["high"].to_numpy()
    l = df["low"].to_numpy(); c = df["close"].to_numpy()
    atr = _atr(df, 14)
    tdays = df["trading_day"].tolist()

    # per-day first/last bar + open/close
    day_first, day_last = {}, {}
    for i, d in enumerate(tdays):
        if d not in day_first:
            day_first[d] = i
        day_last[d] = i

    # daily regime / adx / trend / atr, mapped with "known at entry" = prior day
    reg = daily_regime(d1)
    d1days = d1["trading_day"].tolist()
    d1close = d1["close"].to_numpy()
    d1atr = _atr(d1, 14)
    pos_of = {d: k for k, d in enumerate(d1days)}
    prior_adx, prev_close, prior_d1atr, trend5 = {}, {}, {}, {}
    for k, d in enumerate(d1days):
        if k >= 1:
            prior_adx[d] = reg[d1days[k - 1]]["adx"]
            prev_close[d] = d1close[k - 1]
            prior_d1atr[d] = d1atr[k - 1]
        if k >= 6:
            trend5[d] = d1close[k - 1] - d1close[k - 6]   # 5-day slope up to prev day

    rows = []
    for _, t in trades.iterrows():
        ei, xi = int(t["entry_idx"]), int(t["exit_idx"])
        d = t["trading_day"]
        dirn = 1.0 if t["side"] == "buy" else -1.0
        H = t["box_height"]
        entry, ru = t["entry"], t["r_unit"]
        # excursions over the life of the trade
        seg_hi = h[ei:xi + 1]; seg_lo = l[ei:xi + 1]
        if t["side"] == "buy":
            mfe = (seg_hi.max() - entry); mae = (entry - seg_lo.min())
            broke = seg_lo.min() < (t["level_price"] - 0.10 * H)
        else:
            mfe = (entry - seg_lo.min()); mae = (seg_hi.max() - entry)
            broke = seg_hi.max() > (t["level_price"] + 0.10 * H)
        dclose = c[day_last[d]]
        adx = prior_adx.get(d, np.nan)
        tr5 = trend5.get(d, 0.0)
        against = ((t["side"] == "buy" and tr5 < 0) or (t["side"] == "sell" and tr5 > 0))
        d1a = prior_d1atr.get(d, np.nan)
        pc = prev_close.get(d, np.nan)
        first_open = o[day_first[d]]
        or_bar = day_first[d]
        or_size = (h[or_bar] - l[or_bar])
        atr_e = ei
        rows.append({
            "symbol": sym, "side": t["side"], "r_net": t["r_net"], "win": int(t["r_net"] > 0),
            "exit_reason": t["exit_reason"], "duration_min": t["duration_min"],
            "entry_time": t["entry_time"], "session": t["entry_session"],
            "utc_hour": t["entry_time"].hour, "dow": t["entry_time"].weekday(),
            "level_type": t["level_type"], "bar_in_day": t["bar_in_day"],
            # pre-entry features
            "adx_d1": adx,
            "against_fade": int(against),
            "atr_pct": float((atr[max(0, ei - 500):ei] < atr[ei]).mean()) if ei > 20 else np.nan,
            "atr_expansion": float(atr[ei] / atr[ei - 20]) if ei > 20 and atr[ei - 20] > 0 else np.nan,
            "or_size_H": float(or_size / H) if H > 0 else np.nan,
            "box_height_atr": float(H / d1a) if d1a and d1a > 0 else np.nan,
            "gap_H": float((first_open - pc) / H) if (H > 0 and not np.isnan(pc)) else np.nan,
            "r_unit_atr": float(ru / atr[ei]) if atr[ei] > 0 else np.nan,
            "dist_to_target_R": float(abs(t["tp"] - entry) / ru) if ru > 0 else np.nan,
            "dist_level_mid_H": float(abs(t["level_price"] - t["mid"]) / H) if H > 0 else np.nan,
            # excursion / outcome
            "mfe_R": float(mfe / ru) if ru > 0 else np.nan,
            "mae_R": float(mae / ru) if ru > 0 else np.nan,
            "day_close_R": float((dclose - entry) * dirn / ru) if ru > 0 else np.nan,
            "level_broken": int(bool(broke)),
        })
    return pd.DataFrame(rows)


# ── loss cause taxonomy (priority) ────────────────────────────────────
def classify_loss(r):
    if r["win"] == 1:
        return None
    gap = r["gap_H"]; adx = r["adx_d1"]; dcr = r["day_close_R"]
    if not np.isnan(gap) and abs(gap) > 0.25 and dcr <= -0.5:
        return "gap_and_go"
    if not np.isnan(adx) and adx >= 25 and r["against_fade"] == 1 and dcr <= -0.5:
        return "strong_trend_continuation"
    if r["level_broken"] == 1 and dcr <= -0.5:
        return "breakout_through_level"
    if not np.isnan(r["atr_expansion"]) and r["atr_expansion"] >= 1.8:
        return "volatility_spike_news"
    if r["mfe_R"] < 0.5 and r["duration_min"] <= 60:
        return "false_rejection_whipsaw"
    if r["exit_reason"] == "time_exit_eod":
        return "target_too_far_timedecay"
    if r["session"] == "ASIAN":
        return "low_liquidity_chop"
    return "other"


# ── metrics from an r_net series (entry-time order) ───────────────────
def metrics_from(df_trades, risk=0.01):
    d = df_trades.sort_values("entry_time")
    r = d["r_net"].to_numpy()
    if len(r) == 0:
        return {"n": 0, "wr": np.nan, "pf": np.nan, "exp": np.nan, "dd": np.nan, "net": np.nan}
    eq = config.DEFAULT_EQUITY; peak = eq; mdd = 0.0; curve = []
    for x in r:
        eq *= (1 + x * risk); curve.append(eq); peak = max(peak, eq); mdd = max(mdd, (peak - eq) / peak)
    wins = (r > 0)
    gp = r[r > 0].sum(); gl = -r[r < 0].sum()
    return {"n": len(r), "wr": 100 * wins.mean(),
            "pf": (gp / gl) if gl > 0 else float("inf"),
            "exp": float(r.mean()), "dd": 100 * mdd,
            "net": 100 * (eq / config.DEFAULT_EQUITY - 1)}


def main():
    os.makedirs(REPORT, exist_ok=True)
    parts = [label_pair(s) for s in config.SYMBOLS]
    T = pd.concat([p for p in parts if len(p)], ignore_index=True)
    T.to_csv(os.path.join(RES, "forensics_trades.csv"), index=False)
    base = metrics_from(T)
    print("=" * 88)
    print("CANONICAL CONFIG (pooled 6 pairs, M15 24mo, literal/both/allday/wickB/buf0.10/opposite)")
    print("=" * 88)
    print("  trades=%d  win_rate=%.1f%%  PF=%.2f  expectancy=%.3f R  maxDD=%.1f%%  net=%.1f%%" % (
        base["n"], base["wr"], base["pf"], base["exp"], base["dd"], base["net"]))
    print("  wins=%d losses=%d" % (int(T.win.sum()), int((1 - T.win).sum())))

    out = {"base": base, "n_trades": len(T), "wins": int(T.win.sum()),
           "losses": int((1 - T.win).sum())}

    # ---- Part 2: loss causes ----
    T["loss_cause"] = T.apply(classify_loss, axis=1)
    losses = T[T.win == 0]
    cause_counts = losses["loss_cause"].value_counts()
    print("\n" + "=" * 88)
    print("PART 2 — ROOT CAUSE OF LOSSES (primary cause, priority-assigned)")
    print("=" * 88)
    tot_loss = len(losses)
    out["loss_causes"] = {}
    for cause, cnt in cause_counts.items():
        share = 100 * cnt / tot_loss
        avg_r = losses[losses.loss_cause == cause]["r_net"].mean()
        print("  %-28s %4d  (%5.1f%% of losses)  avgR=%+.2f" % (cause, cnt, share, avg_r))
        out["loss_causes"][cause] = {"n": int(cnt), "pct": round(share, 1), "avg_r": round(float(avg_r), 3)}

    # co-occurrence (independent conditions among losses)
    print("\n  Independent condition prevalence among ALL losses:")
    conds = {
        "trend_against(adx>=25)": (losses.adx_d1 >= 25) & (losses.against_fade == 1),
        "level_broken": losses.level_broken == 1,
        "day_closed_against(>=0.5R)": losses.day_close_R <= -0.5,
        "vol_expansion(>=1.8)": losses.atr_expansion >= 1.8,
        "gap(>0.25H)": losses.gap_H.abs() > 0.25,
        "never_went_our_way(mfe<0.5R)": losses.mfe_R < 0.5,
        "asian_session": losses.session == "ASIAN",
        "time_exit": losses.exit_reason == "time_exit_eod",
    }
    out["loss_conditions"] = {}
    for name, mask in conds.items():
        pct = 100 * mask.mean()
        print("    %-32s %5.1f%%" % (name, pct))
        out["loss_conditions"][name] = round(float(pct), 1)

    # ---- Part 3: winners ----
    print("\n" + "=" * 88)
    print("PART 3 — WHY WINNERS WIN (win vs loss feature means, standardized effect)")
    print("=" * 88)
    feats = ["adx_d1", "against_fade", "atr_pct", "atr_expansion", "or_size_H",
             "box_height_atr", "gap_H", "r_unit_atr", "dist_to_target_R",
             "dist_level_mid_H", "bar_in_day", "utc_hour"]
    wins = T[T.win == 1]; loss = T[T.win == 0]
    out["win_vs_loss"] = {}
    print("  %-20s %10s %10s %8s" % ("feature", "win_mean", "loss_mean", "cohen_d"))
    rows_eff = []
    for f in feats:
        wm, lm = wins[f].mean(), loss[f].mean()
        sd = T[f].std()
        d = (wm - lm) / sd if sd and sd > 0 else np.nan
        rows_eff.append((f, wm, lm, d))
    for f, wm, lm, d in sorted(rows_eff, key=lambda x: -abs(x[3]) if not np.isnan(x[3]) else 0):
        print("  %-20s %10.3f %10.3f %8.2f" % (f, wm, lm, d))
        out["win_vs_loss"][f] = {"win_mean": round(float(wm), 3), "loss_mean": round(float(lm), 3),
                                 "cohen_d": round(float(d), 3) if not np.isnan(d) else None}
    # win rate by level_type / session / instrument
    print("\n  Win rate & expectancy by category:")
    out["by_category"] = {}
    for col in ["level_type", "session", "symbol", "against_fade"]:
        g = T.groupby(col).apply(lambda x: pd.Series(
            {"n": len(x), "wr": 100 * x.win.mean(), "expR": x.r_net.mean()}), include_groups=False)
        print("   [%s]\n%s" % (col, g.round(3).to_string()))
        out["by_category"][col] = {str(k): {"n": int(v["n"]), "wr": round(v["wr"], 1),
                                            "expR": round(v["expR"], 3)} for k, v in g.iterrows()}

    # ---- Part 4/5: statistical drivers ----
    print("\n" + "=" * 88)
    print("PART 5 — STATISTICAL DRIVERS OF PROFITABILITY")
    print("=" * 88)
    num = ["adx_d1", "atr_pct", "atr_expansion", "or_size_H", "box_height_atr",
           "gap_H", "r_unit_atr", "dist_to_target_R", "dist_level_mid_H",
           "bar_in_day", "utc_hour", "against_fade", "level_broken"]
    D = T.dropna(subset=num + ["r_net"]).copy()
    print("  (complete-case n=%d)" % len(D))
    print("  point-biserial corr with WIN and pearson with r_net:")
    out["drivers_corr"] = {}
    corrs = []
    for f in num:
        cw = np.corrcoef(D[f], D["win"])[0, 1]
        cr = np.corrcoef(D[f], D["r_net"])[0, 1]
        corrs.append((f, cw, cr))
    for f, cw, cr in sorted(corrs, key=lambda x: -abs(x[2])):
        print("    %-20s corr_win=%+.3f  corr_r=%+.3f" % (f, cw, cr))
        out["drivers_corr"][f] = {"corr_win": round(float(cw), 3), "corr_r": round(float(cr), 3)}

    try:
        from sklearn.linear_model import LogisticRegression
        from sklearn.ensemble import GradientBoostingClassifier
        from sklearn.preprocessing import StandardScaler
        X = D[num].to_numpy(); y = D["win"].to_numpy()
        Xs = StandardScaler().fit_transform(X)
        lr = LogisticRegression(max_iter=1000).fit(Xs, y)
        gb = GradientBoostingClassifier(n_estimators=150, max_depth=3, random_state=1).fit(X, y)
        print("\n  Logistic-regression standardized coefficients (sign = direction of WIN):")
        out["logit_coef"] = {}
        for f, co in sorted(zip(num, lr.coef_[0]), key=lambda x: -abs(x[1])):
            print("    %-20s %+.3f" % (f, co))
            out["logit_coef"][f] = round(float(co), 3)
        print("\n  Gradient-boosting feature importance (nonlinear):")
        out["gb_importance"] = {}
        for f, im in sorted(zip(num, gb.feature_importances_), key=lambda x: -x[1]):
            print("    %-20s %.3f" % (f, im))
            out["gb_importance"][f] = round(float(im), 3)
    except Exception as e:
        print("  [sklearn unavailable: %s]" % e)

    # ---- Part 4: filter simulation ----
    print("\n" + "=" * 88)
    print("PART 4 — FILTER FEASIBILITY (net effect of skipping trades meeting condition)")
    print("=" * 88)
    filters = {
        "skip trend-against (adx>=25 & against)": (T.adx_d1 >= 25) & (T.against_fade == 1),
        "skip vol spike (atr_exp>=1.8)": T.atr_expansion >= 1.8,
        "skip Asian session": T.session == "ASIAN",
        "skip tiny stop (r_unit_atr<0.4)": T.r_unit_atr < 0.4,
        "skip wide box (top quartile)": T.box_height_atr > T.box_height_atr.quantile(0.75),
        "skip gap days (|gap|>0.25H)": T.gap_H.abs() > 0.25,
        "trade SWING levels only": T.level_type == "range",     # skip range
        "trade NEWYORK only": T.session != "NEWYORK",           # skip non-NY
    }
    print("  base: n=%d WR=%.1f PF=%.2f exp=%.3f DD=%.1f net=%.1f" % (
        base["n"], base["wr"], base["pf"], base["exp"], base["dd"], base["net"]))
    out["filters"] = {}
    for name, skip_mask in filters.items():
        skip_mask = skip_mask.fillna(False)
        kept = T[~skip_mask]
        removed = T[skip_mask]
        m = metrics_from(kept)
        out["filters"][name] = {
            "removed": int(skip_mask.sum()),
            "losses_removed": int((removed.win == 0).sum()),
            "wins_removed": int((removed.win == 1).sum()),
            "wr": round(m["wr"], 1), "pf": round(m["pf"], 2), "exp": round(m["exp"], 3),
            "dd": round(m["dd"], 1), "net": round(m["net"], 1), "n": m["n"],
        }
        print("  %-42s remove %3d (L%3d/W%3d) -> n=%3d WR=%.1f PF=%.2f exp=%+.3f DD=%.1f net=%+.1f" % (
            name, int(skip_mask.sum()), int((removed.win == 0).sum()), int((removed.win == 1).sum()),
            m["n"], m["wr"], m["pf"], m["exp"], m["dd"], m["net"]))

    with open(os.path.join(RES, "forensics_summary.json"), "w") as f:
        json.dump(out, f, indent=2, default=float)
    print("\nSaved results/forensics_trades.csv, results/forensics_summary.json")


if __name__ == "__main__":
    main()
