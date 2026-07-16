"""sweep_lab_analyze.py — Phases 2-7 of the fade-vs-follow investigation.

    cd research && box_theory/.venv/bin/python -m box_theory.sweep_lab_analyze
"""
from __future__ import annotations

import os
import json

import numpy as np
import pandas as pd
from scipy import stats

from . import config
from .sweep_lab import FEATURES, RR

RES = os.path.join(os.path.dirname(__file__), "results")


def perf(r, risk=0.005):
    r = np.asarray([x for x in r if x is not None and not np.isnan(x)], dtype=float)
    if len(r) == 0:
        return {"n": 0}
    eq = config.DEFAULT_EQUITY; peak = eq; mdd = 0.0
    for x in r:
        eq *= (1 + x * risk); peak = max(peak, eq); mdd = max(mdd, (peak - eq) / peak)
    gp = r[r > 0].sum(); gl = -r[r < 0].sum()
    sharpe = (r.mean() / r.std()) * np.sqrt(len(r)) if r.std() > 0 else float("nan")
    return {"n": len(r), "wr": 100 * (r > 0).mean(), "pf": gp / gl if gl > 0 else float("inf"),
            "exp": float(r.mean()), "dd": 100 * mdd, "net": 100 * (eq / config.DEFAULT_EQUITY - 1),
            "sharpe": float(sharpe)}


def line(ch="="):
    print(ch * 90)


def main():
    T = pd.read_csv(os.path.join(RES, "sweep_events.csv"), parse_dates=["entry_time"])
    T = T.sort_values("entry_time").reset_index(drop=True)
    T["wick_body"] = T["wick_body"].clip(upper=20)   # doji bodies blow up the ratio
    AB = T[T.label != "U"].copy()
    AB["y"] = (AB.label == "B").astype(int)   # 1 = continuation/breakout
    out = {}

    line()
    print("PHASE 1 — CLASSIFICATION  (events=%d)" % len(T))
    line()
    n_a = (T.label == "A").sum(); n_b = (T.label == "B").sum(); n_u = (T.label == "U").sum()
    print("  A failed-sweep (revert first): %d (%.1f%%)" % (n_a, 100 * n_a / len(T)))
    print("  B true-breakout (continue first): %d (%.1f%%)" % (n_b, 100 * n_b / len(T)))
    print("  U ambiguous (same-bar): %d" % n_u)
    print("  => base rate of continuation among decided events: %.1f%%" % (100 * AB.y.mean()))
    out["counts"] = {"A": int(n_a), "B": int(n_b), "U": int(n_u), "base_B": round(100 * AB.y.mean(), 1)}

    line()
    print("PHASE 2/3 — A vs B FEATURE COMPARISON & PREDICTIVE POWER")
    line()
    print("  %-18s %9s %9s %8s %9s %10s" % ("feature", "meanA", "meanB", "cohen_d", "AUC", "MWU_p"))
    rows = []
    for f in FEATURES:
        a = AB[AB.y == 0][f].dropna(); b = AB[AB.y == 1][f].dropna()
        if len(a) < 20 or len(b) < 20:
            continue
        sd = AB[f].std()
        d = (b.mean() - a.mean()) / sd if sd > 0 else np.nan
        try:
            u, p = stats.mannwhitneyu(a, b, alternative="two-sided")
            auc = u / (len(a) * len(b))           # AUC for A>B; flip so >0.5 means predicts B
            auc = 1 - auc
        except ValueError:
            p, auc = np.nan, np.nan
        rows.append((f, a.mean(), b.mean(), d, auc, p))
    for f, ma, mb, d, auc, p in sorted(rows, key=lambda x: -abs(x[4] - 0.5) if not np.isnan(x[4]) else 0):
        star = "***" if p < 0.001 else "**" if p < 0.01 else "*" if p < 0.05 else ""
        print("  %-18s %9.3f %9.3f %8.2f %9.3f %10.1e %s" % (f, ma, mb, d, auc, p, star))
    out["feature_stats"] = {r[0]: {"meanA": round(r[1], 3), "meanB": round(r[2], 3),
                                   "cohen_d": round(r[3], 3), "auc": round(r[4], 3),
                                   "p": r[5]} for r in rows}
    print("  (AUC>0.5 => higher value predicts CONTINUATION; 0.5 = no power. * p<.05 ** p<.01 *** p<.001)")

    line()
    print("PHASE 4 — FADE-ONLY vs CONTINUATION-ONLY (symmetric %.0fR, filled trades, 0.5%% risk)" % RR)
    line()
    fade = perf(T[T.fade_filled == 1]["fade_r"])
    cont = perf(T[T.cont_filled == 1]["cont_r"])
    for nm, m in [("FADE-only", fade), ("CONTINUATION-only", cont)]:
        print("  %-18s n=%4d WR=%.1f%% PF=%.2f exp=%+.3fR Sharpe=%.2f DD=%.0f%% net=%+.0f%%" % (
            nm, m["n"], m["wr"], m["pf"], m["exp"], m["sharpe"], m["dd"], m["net"]))
    out["phase4"] = {"fade": fade, "cont": cont}

    line()
    print("PHASE 5 — REVERSE THE LOSERS (take FADE losers; trade the continuation instead)")
    line()
    fade_losers = T[(T.fade_filled == 1) & (T.fade_r < 0)]
    rev = perf(fade_losers["cont_r"])                 # the opposite trade on those same events
    print("  fade losers: %d   their continuation-trade performance:" % len(fade_losers))
    print("  REVERSED     n=%4d WR=%.1f%% PF=%.2f exp=%+.3fR DD=%.0f%% net=%+.0f%%" % (
        rev["n"], rev["wr"], rev["pf"], rev["exp"], rev["dd"], rev["net"]))
    naive = perf(-fade_losers["fade_r"])
    print("  (naive -fade_r reference: exp=%+.3fR WR=%.1f%%)" % (naive["exp"], naive["wr"]))
    out["phase5"] = {"reversed": rev, "naive_neg": naive}

    line()
    print("PHASE 7 — ML FEASIBILITY (time-split 70/30 OOS; predict continuation)")
    line()
    D = AB.dropna(subset=FEATURES).copy()
    cut = int(len(D) * 0.7)
    tr, te = D.iloc[:cut], D.iloc[cut:]
    Xtr, ytr = tr[FEATURES].to_numpy(), tr.y.to_numpy()
    Xte, yte = te[FEATURES].to_numpy(), te.y.to_numpy()
    try:
        from sklearn.linear_model import LogisticRegression
        from sklearn.ensemble import GradientBoostingClassifier
        from sklearn.preprocessing import StandardScaler
        from sklearn.metrics import roc_auc_score, precision_score, recall_score, accuracy_score
        sc = StandardScaler().fit(Xtr)
        lr = LogisticRegression(max_iter=2000).fit(sc.transform(Xtr), ytr)
        gb = GradientBoostingClassifier(n_estimators=200, max_depth=3, random_state=1).fit(Xtr, ytr)
        for nm, model, Xt in [("logistic", lr, sc.transform(Xte)), ("grad-boost", gb, Xte)]:
            proba = model.predict_proba(Xt)[:, 1]
            pred = (proba >= 0.5).astype(int)
            print("  %-11s OOS  AUC=%.3f  acc=%.3f  precision=%.3f  recall=%.3f  (base=%.3f)" % (
                nm, roc_auc_score(yte, proba), accuracy_score(yte, pred),
                precision_score(yte, pred, zero_division=0), recall_score(yte, pred, zero_division=0),
                yte.mean()))
        out["phase7"] = {
            "n_train": len(tr), "n_test": len(te),
            "logit_auc": round(float(roc_auc_score(yte, lr.predict_proba(sc.transform(Xte))[:, 1])), 3),
            "gb_auc": round(float(roc_auc_score(yte, gb.predict_proba(Xte)[:, 1])), 3),
            "test_base": round(float(yte.mean()), 3),
        }
        print("\n  Gradient-boost feature importance (top predictors of fade-vs-continue):")
        imp = sorted(zip(FEATURES, gb.feature_importances_), key=lambda x: -x[1])
        out["gb_importance"] = {}
        for f, im in imp[:10]:
            print("    %-18s %.3f" % (f, im))
            out["gb_importance"][f] = round(float(im), 3)

        line()
        print("PHASE 6 — HYBRID REGIME MODEL (use OOS classifier to switch fade/continue)")
        line()
        # On the TEST set: choose continuation trade when model says B, else fade.
        te = te.copy()
        te["pB"] = gb.predict_proba(Xte)[:, 1]
        fade_test = perf(te[te.fade_filled == 1]["fade_r"])
        cont_test = perf(te[te.cont_filled == 1]["cont_r"])
        for thr in [0.5, 0.55, 0.6]:
            chosen = []
            for _, r in te.iterrows():
                if r["pB"] >= thr and r["cont_filled"] == 1:
                    chosen.append(r["cont_r"])
                elif r["pB"] < thr and r["fade_filled"] == 1:
                    chosen.append(r["fade_r"])
            hy = perf(chosen)
            print("  hybrid(thr=%.2f) n=%4d WR=%.1f%% PF=%.2f exp=%+.3fR net=%+.0f%%" % (
                thr, hy["n"], hy["wr"], hy["pf"], hy["exp"], hy["net"]))
        print("  ref FADE-only(test)  exp=%+.3fR  |  CONT-only(test) exp=%+.3fR" % (
            fade_test["exp"], cont_test["exp"]))
        out["phase6"] = {"fade_test_exp": round(fade_test["exp"], 3),
                         "cont_test_exp": round(cont_test["exp"], 3)}
    except Exception as e:
        print("  [sklearn error: %s]" % e)

    with open(os.path.join(RES, "sweep_lab_summary.json"), "w") as f:
        json.dump(out, f, indent=2, default=float)
    print("\nSaved results/sweep_lab_summary.json")


if __name__ == "__main__":
    main()
