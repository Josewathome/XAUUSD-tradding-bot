"""micro_analyze.py — statistical forensics on the sweep-event dataset.

Sections: base rates, per-feature AUC with BH-FDR correction, 3-candle
sequences, volume quintiles + climax/absorption questions, rejection-candle
KMeans clusters, buyer/seller control flags, session/regime conditioning,
ML feasibility (LR/RF/GB, time-split OOS + TimeSeriesSplit CV), and
destroy-tests (OOS + per-pair sign consistency) for every nominal finding.

    cd research && box_theory/.venv/bin/python -m box_theory.micro_analyze
"""
from __future__ import annotations

import os
import json

import numpy as np
import pandas as pd
from scipy import stats

RES = os.path.join(os.path.dirname(__file__), "results")

NUMERIC = ["body_atr", "range_atr", "upwick_atr", "lowick_atr", "rejwick_atr",
           "wick_body", "close_pos", "open_pos", "range_pctile",
           "sweep_depth_atr", "close_back_inside",
           "n_bull_3", "net_toward_3", "range_slope_3",
           "n_bull_5", "net_toward_5", "range_slope_5",
           "n_bull_10", "net_toward_10", "range_slope_10",
           "momentum_accel", "consec_dir",
           "vol_pctile", "vol_ratio3", "vol_slope5", "vol_climax",
           "bars_to_level", "dist_traveled_atr", "velocity3_atr",
           "atr_expansion", "atr_pctile",
           "adx_d1", "box_height_atr", "with_d1_trend", "side_is_pdh",
           "utc_hour", "dow"]


def bh_fdr(pvals):
    """Benjamini-Hochberg adjusted p-values."""
    p = np.asarray(pvals); n = len(p)
    order = np.argsort(p); ranked = p[order] * n / (np.arange(n) + 1)
    ranked = np.minimum.accumulate(ranked[::-1])[::-1]
    out = np.empty(n); out[order] = np.minimum(ranked, 1.0)
    return out


def line(ch="="):
    print(ch * 96)


def auc_of(x, y):
    a = x[y == 0]; b = x[y == 1]
    if len(a) < 20 or len(b) < 20:
        return np.nan, np.nan
    try:
        u, p = stats.mannwhitneyu(a, b, alternative="two-sided")
        return 1 - u / (len(a) * len(b)), p
    except ValueError:
        return np.nan, np.nan


def main():
    T = pd.read_csv(os.path.join(RES, "micro_events_M15.csv"), parse_dates=["entry_time"])
    T = T[T.label != "U"].sort_values("entry_time").reset_index(drop=True)
    T["y"] = (T.label == "B").astype(int)
    T5 = pd.read_csv(os.path.join(RES, "micro_events_M5.csv"), parse_dates=["entry_time"])
    T5 = T5[T5.label != "U"].sort_values("entry_time").reset_index(drop=True)
    T5["y"] = (T5.label == "B").astype(int)
    out = {}

    line()
    print("BASE RATES — is there a raw directional bias after a PDH/PDL sweep?")
    line()
    n = len(T); b = T.y.mean()
    binom_p = stats.binomtest(int(T.y.sum()), n, 0.5).pvalue
    print("  M15 24mo: n=%d  P(continuation)=%.3f  binomial p vs 0.5 = %.4f" % (n, b, binom_p))
    n5 = len(T5); b5 = T5.y.mean()
    p5 = stats.binomtest(int(T5.y.sum()), n5, 0.5).pvalue
    print("  M5   6mo: n=%d  P(continuation)=%.3f  binomial p vs 0.5 = %.4f" % (n5, b5, p5))
    for sub, nm in [(T[T.side_is_pdh == 1], "PDH sweeps"), (T[T.side_is_pdh == 0], "PDL sweeps")]:
        bp = stats.binomtest(int(sub.y.sum()), len(sub), 0.5).pvalue
        print("  M15 %s: n=%d P(cont)=%.3f p=%.4f" % (nm, len(sub), sub.y.mean(), bp))
    out["base"] = {"n": n, "p_cont": round(float(b), 3), "binom_p": float(binom_p),
                   "n_m5": n5, "p_cont_m5": round(float(b5), 3)}

    # ---- per-feature screening with FDR ----
    line()
    print("FEATURE SCREEN — AUC for predicting CONTINUATION, BH-FDR corrected (M15)")
    line()
    res = []
    for f in NUMERIC:
        x = T[f].to_numpy(dtype=float)
        mask = ~np.isnan(x)
        auc, p = auc_of(x[mask], T.y.to_numpy()[mask])
        if not np.isnan(auc):
            res.append([f, auc, p])
    ps = [r[2] for r in res]
    fdr = bh_fdr(ps)
    for r, q in zip(res, fdr):
        r.append(q)
    res.sort(key=lambda r: -abs(r[1] - 0.5))
    print("  %-20s %7s %10s %10s  %s" % ("feature", "AUC", "raw_p", "FDR_q", "verdict"))
    survivors = []
    for f, auc, p, q in res:
        verdict = "SURVIVES FDR" if q < 0.05 else ("nominal" if p < 0.05 else "")
        if q < 0.05:
            survivors.append((f, auc, p, q))
        print("  %-20s %7.3f %10.2e %10.3f  %s" % (f, auc, p, q, verdict))
    out["screen"] = [{"f": f, "auc": round(a, 3), "p": p, "q": q} for f, a, p, q in res]
    out["survivors"] = [{"f": f, "auc": round(a, 3), "p": p, "q": q} for f, a, p, q in survivors]

    # ---- destroy-test survivors: OOS split + per-pair consistency + M5 replication ----
    if survivors:
        line()
        print("DESTROY-TEST OF FDR SURVIVORS (time-split halves, per-pair sign, M5 replication)")
        line()
        cut = int(len(T) * 0.5)
        out["destroy"] = {}
        for f, auc, p, q in survivors:
            a1, p1 = auc_of(T.iloc[:cut][f].dropna().to_numpy(),
                            T.iloc[:cut].dropna(subset=[f]).y.to_numpy())
            a2, p2 = auc_of(T.iloc[cut:][f].dropna().to_numpy(),
                            T.iloc[cut:].dropna(subset=[f]).y.to_numpy())
            signs = []
            for sym in T.symbol.unique():
                s = T[T.symbol == sym].dropna(subset=[f])
                aa, _ = auc_of(s[f].to_numpy(), s.y.to_numpy())
                if not np.isnan(aa):
                    signs.append(np.sign(aa - 0.5))
            consist = max((np.array(signs) > 0).mean(), (np.array(signs) < 0).mean())
            s5 = T5.dropna(subset=[f])
            a5, p5f = auc_of(s5[f].to_numpy(), s5.y.to_numpy())
            same5 = (np.sign(a5 - 0.5) == np.sign(auc - 0.5)) if not np.isnan(a5) else None
            print("  %-20s half1 AUC=%.3f  half2 AUC=%.3f  pair-sign-consistency=%.0f%%  M5 AUC=%.3f same-dir=%s" % (
                f, a1, a2, 100 * consist, a5 if not np.isnan(a5) else float("nan"), same5))
            out["destroy"][f] = {"h1": round(float(a1), 3), "h2": round(float(a2), 3),
                                 "pair_consistency": round(float(consist), 2),
                                 "m5_auc": None if np.isnan(a5) else round(float(a5), 3),
                                 "m5_same_dir": bool(same5) if same5 is not None else None}

    # ---- 3-candle sequences ----
    line()
    print("3-CANDLE SEQUENCES before the sweep (U=up candle, D=down): P(continuation)")
    line()
    seq = T.groupby("seq3").agg(n=("y", "size"), p_cont=("y", "mean")).sort_values("p_cont")
    chi2, chi_p, _, _ = stats.chi2_contingency(pd.crosstab(T.seq3, T.y))
    for s, row in seq.iterrows():
        print("  %s  n=%4d  P(cont)=%.3f" % (s, row.n, row.p_cont))
    print("  chi-square across all 8 sequences: p=%.3f" % chi_p)
    out["seq3"] = {s: {"n": int(r.n), "p_cont": round(float(r.p_cont), 3)} for s, r in seq.iterrows()}
    out["seq3_chi_p"] = float(chi_p)

    # ---- volume questions ----
    line()
    print("VOLUME — quintile of event-candle volume percentile vs outcome (M15)")
    line()
    T["vq"] = pd.qcut(T.vol_pctile, 5, labels=False, duplicates="drop")
    g = T.groupby("vq").agg(n=("y", "size"), p_cont=("y", "mean"))
    for q_, row in g.iterrows():
        print("  vol quintile %d: n=%4d  P(cont)=%.3f" % (q_, row.n, row.p_cont))
    chi2v, chi_pv, _, _ = stats.chi2_contingency(pd.crosstab(T.vq, T.y))
    print("  chi-square: p=%.3f" % chi_pv)
    clim = T.groupby("vol_climax").agg(n=("y", "size"), p_cont=("y", "mean"))
    print("  volume climax (event vol = 10-bar max): no P(cont)=%.3f (n=%d) | yes P(cont)=%.3f (n=%d)" % (
        clim.loc[0, "p_cont"], clim.loc[0, "n"], clim.loc[1, "p_cont"], clim.loc[1, "n"]))
    dry = T[T.vol_slope5 < T.vol_slope5.quantile(0.2)]
    print("  volume drying up into sweep (bottom quintile slope): P(cont)=%.3f (n=%d) vs base %.3f" % (
        dry.y.mean(), len(dry), b))
    out["volume"] = {"quintiles": {int(k): {"n": int(r.n), "p": round(float(r.p_cont), 3)}
                                   for k, r in g.iterrows()},
                     "chi_p": float(chi_pv),
                     "climax_p_cont": round(float(clim.loc[1, "p_cont"]), 3),
                     "dry_p_cont": round(float(dry.y.mean()), 3)}

    # ---- rejection-candle clustering ----
    line()
    print("REJECTION-CANDLE CLUSTERS (KMeans k=5 on event-candle anatomy)")
    line()
    from sklearn.cluster import KMeans
    from sklearn.preprocessing import StandardScaler
    feats = ["body_atr", "range_atr", "rejwick_atr", "wick_body", "close_pos",
             "sweep_depth_atr", "close_back_inside"]
    D = T.dropna(subset=feats).copy()
    X = StandardScaler().fit_transform(D[feats])
    km = KMeans(n_clusters=5, n_init=10, random_state=2).fit(X)
    D["cluster"] = km.labels_
    prof = D.groupby("cluster")[feats + ["y"]].mean()
    cnt = D.groupby("cluster").size()
    for cl, row in prof.iterrows():
        print("  C%d n=%4d P(cont)=%.3f | body=%.2f rng=%.2f rejw=%.2f w/b=%.1f cpos=%.2f depth=%.2f inside=%.2f" % (
            cl, cnt[cl], row.y, row.body_atr, row.range_atr, row.rejwick_atr,
            row.wick_body, row.close_pos, row.sweep_depth_atr, row.close_back_inside))
    chi2c, chi_pc, _, _ = stats.chi2_contingency(pd.crosstab(D.cluster, D.y))
    print("  chi-square across clusters: p=%.3f" % chi_pc)
    # OOS stability of the best/worst cluster
    cut = int(len(D) * 0.5)
    d1_, d2_ = D.iloc[:cut], D.iloc[cut:]
    ext = prof.y.idxmax(), prof.y.idxmin()
    for cl in ext:
        p1 = d1_[d1_.cluster == cl].y.mean(); p2 = d2_[d2_.cluster == cl].y.mean()
        print("  cluster C%d P(cont): half1=%.3f  half2=%.3f" % (cl, p1, p2))
    out["clusters"] = {"chi_p": float(chi_pc),
                       "profiles": {int(cl): {"n": int(cnt[cl]), "p_cont": round(float(row.y), 3)}
                                    for cl, row in prof.iterrows()}}

    # ---- buyer vs seller control flags ----
    line()
    print("BUYER/SELLER CONTROL FLAGS — P(continuation) vs base %.3f" % b)
    line()
    flags = {
        "exhaustion (accel<0 & vol_slope<0 & consec>=3)":
            (T.momentum_accel < 0) & (T.vol_slope5 < 0) & (T.consec_dir >= 3),
        "climax (vol_climax & range_pctile>0.9)":
            (T.vol_climax == 1) & (T.range_pctile > 0.9),
        "absorption (vol_pctile>0.8 & body_atr<0.3)":
            (T.vol_pctile > 0.8) & (T.body_atr < 0.3),
        "trapped (deep sweep & close back inside)":
            (T.sweep_depth_atr > 0.5) & (T.close_back_inside == 1),
        "aggressive approach (velocity3 top quintile)":
            T.velocity3_atr > T.velocity3_atr.quantile(0.8),
        "slow grind approach (velocity3 bottom quintile)":
            T.velocity3_atr < T.velocity3_atr.quantile(0.2),
    }
    out["flags"] = {}
    for nm, mask in flags.items():
        sub = T[mask.fillna(False)]
        if len(sub) < 30:
            continue
        bp = stats.binomtest(int(sub.y.sum()), len(sub), b).pvalue
        cut = int(len(T) * 0.5)
        s1 = T.iloc[:cut][mask.iloc[:cut].fillna(False)]
        s2 = T.iloc[cut:][mask.iloc[cut:].fillna(False)]
        print("  %-46s n=%4d P(cont)=%.3f p=%.3f | halves %.3f / %.3f" % (
            nm, len(sub), sub.y.mean(), bp,
            s1.y.mean() if len(s1) else float("nan"),
            s2.y.mean() if len(s2) else float("nan")))
        out["flags"][nm] = {"n": int(len(sub)), "p_cont": round(float(sub.y.mean()), 3),
                            "p": float(bp)}

    # ---- session / regime conditioning ----
    line()
    print("SESSION & REGIME — P(continuation) by bucket")
    line()
    for col, cuts in [("session", None), ("with_d1_trend", None)]:
        g = T.groupby(col).agg(n=("y", "size"), p=("y", "mean"))
        print("  [%s] " % col + "  ".join("%s: %.3f (n=%d)" % (k, r.p, r.n) for k, r in g.iterrows()))
    T["vol_regime"] = np.where(T.atr_pctile > 0.5, "highvol", "lowvol")
    T["trend_regime"] = np.where(T.adx_d1 >= 25, "trend", "range")
    for col in ["vol_regime", "trend_regime"]:
        g = T.groupby(col).agg(n=("y", "size"), p=("y", "mean"))
        print("  [%s] " % col + "  ".join("%s: %.3f (n=%d)" % (k, r.p, r.n) for k, r in g.iterrows()))

    # ---- ML feasibility ----
    line()
    print("MACHINE LEARNING — predict continuation from all features (M15)")
    line()
    from sklearn.linear_model import LogisticRegression
    from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier
    from sklearn.preprocessing import StandardScaler as SS
    from sklearn.model_selection import TimeSeriesSplit
    from sklearn.metrics import roc_auc_score, precision_score, recall_score
    D = T.dropna(subset=NUMERIC).reset_index(drop=True)
    X = D[NUMERIC].to_numpy(); y = D.y.to_numpy()
    cut = int(len(D) * 0.7)
    Xtr, Xte, ytr, yte = X[:cut], X[cut:], y[:cut], y[cut:]
    sc = SS().fit(Xtr)
    models = {
        "logistic": LogisticRegression(max_iter=3000),
        "rand-forest": RandomForestClassifier(n_estimators=300, max_depth=6, random_state=1),
        "grad-boost": GradientBoostingClassifier(n_estimators=250, max_depth=3, random_state=1),
    }
    out["ml"] = {}
    for nm, mdl in models.items():
        Xt_tr = sc.transform(Xtr) if nm == "logistic" else Xtr
        Xt_te = sc.transform(Xte) if nm == "logistic" else Xte
        mdl.fit(Xt_tr, ytr)
        proba = mdl.predict_proba(Xt_te)[:, 1]
        pred = (proba >= 0.5).astype(int)
        auc = roc_auc_score(yte, proba)
        # 5-fold time-series CV AUC for stability
        cv_aucs = []
        for tri, tei in TimeSeriesSplit(n_splits=5).split(X):
            m2 = type(mdl)(**mdl.get_params())
            Xa, Xb = X[tri], X[tei]
            if nm == "logistic":
                s2 = SS().fit(Xa); Xa, Xb = s2.transform(Xa), s2.transform(Xb)
            m2.fit(Xa, y[tri])
            cv_aucs.append(roc_auc_score(y[tei], m2.predict_proba(Xb)[:, 1]))
        print("  %-12s OOS AUC=%.3f  prec=%.3f  rec=%.3f | 5-fold CV AUC=%.3f±%.3f" % (
            nm, auc, precision_score(yte, pred, zero_division=0),
            recall_score(yte, pred, zero_division=0), np.mean(cv_aucs), np.std(cv_aucs)))
        out["ml"][nm] = {"oos_auc": round(float(auc), 3),
                         "cv_auc_mean": round(float(np.mean(cv_aucs)), 3),
                         "cv_auc_std": round(float(np.std(cv_aucs)), 3)}
    gb = models["grad-boost"]
    imp = sorted(zip(NUMERIC, gb.feature_importances_), key=lambda x: -x[1])[:12]
    print("  grad-boost top importances: " + ", ".join("%s %.3f" % (f, i) for f, i in imp[:6]))
    out["ml"]["gb_importance"] = {f: round(float(i), 3) for f, i in imp}

    with open(os.path.join(RES, "micro_summary.json"), "w") as f:
        json.dump(out, f, indent=2, default=float)
    print("\nSaved results/micro_summary.json")


if __name__ == "__main__":
    main()
