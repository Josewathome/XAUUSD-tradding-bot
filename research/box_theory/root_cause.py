"""root_cause.py — WHY do first-hour PDH/PDL sweeps revert ~58%?

One generic scanner, parameterised by the day-boundary hour and the level
definition, drives every hypothesis:

  H1 rollover/spread  — per-bar spread (v2 export) by hour; net vs gross edge;
                        does P(rev) track the spread itself?
  H2 liquidity vacuum — tick-volume-by-hour as participation proxy; P(rev) vs
                        event-bar volume percentile inside the early window
  H3 session vs reset — BOUNDARY-SHIFT test: redefine the "day" at server hours
                        {0,3,6,9,12,15,18,21} and see if the early-reversion
                        effect follows the boundary or stays at one clock time
  H4 mean reversion   — stretch features (distance from prev close / window
                        VWAP, in ATR); does timing survive controlling for them?
  H5 stop-hunting     — sweep depth interaction inside the early window
  Cross-instrument    — US500, USTEC, DE40, XAGUSD, XTIUSD (same scanner)
  Placebo             — pseudo-levels at prevH+0.35*ATR / prevL-0.35*ATR
  Destroy battery     — quarters, sides, pairs, high-vol exclusion

    cd research && box_theory/.venv/bin/python -m box_theory.root_cause
"""
from __future__ import annotations

import os
import json

import numpy as np
import pandas as pd
from scipy import stats

from . import config

RES = os.path.join(os.path.dirname(__file__), "results")
DATA = os.path.join(os.path.dirname(__file__), "data")

FX = ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "XAUUSD"]
XA = ["US500", "USTEC", "DE40", "XAGUSD", "XTIUSD"]
EARLY_BARS = 4          # "first hour" on M15


def load_raw(sym):
    df = pd.read_csv(os.path.join(DATA, "%s_M15.csv" % sym))
    df["time_server"] = pd.to_datetime(df["time_server"])
    if "spread" not in df.columns:
        df["spread"] = np.nan
    return df.sort_values("epoch").reset_index(drop=True)


def _atr14(h, l, c):
    prev = np.concatenate([[c[0]], c[:-1]])
    tr = np.maximum(h - l, np.maximum(np.abs(h - prev), np.abs(l - prev)))
    return pd.Series(tr).rolling(14, min_periods=1).mean().to_numpy()


def scan(sym, boundary=0, pseudo_k=None, full_feats=False):
    """Events for day windows starting at server hour `boundary`.
    Level = prev-window High/Low, or pseudo levels prevH+k*ATRd / prevL-k*ATRd.
    Returns event DataFrame with label A/B, bars_from_boundary, and (optionally)
    spread / volume / stretch / depth features."""
    df = load_raw(sym)
    o = df["open"].to_numpy(); h = df["high"].to_numpy()
    l = df["low"].to_numpy(); c = df["close"].to_numpy()
    v = df["volume"].to_numpy(dtype=float)
    sp = df["spread"].to_numpy(dtype=float)
    atr = _atr14(h, l, c)
    ts = df["time_server"]
    day_id = (ts - pd.Timedelta(hours=boundary)).dt.date.to_numpy()

    # window index ranges
    idx_first = {}
    idx_last = {}
    for i, d in enumerate(day_id):
        if d not in idx_first:
            idx_first[d] = i
        idx_last[d] = i
    days = sorted(idx_first)
    prevHL = {}
    prevC = {}
    prevATRd = {}
    for k in range(1, len(days)):
        a, b = idx_first[days[k - 1]], idx_last[days[k - 1]]
        prevHL[days[k]] = (h[a:b + 1].max(), l[a:b + 1].min())
        prevC[days[k]] = c[b]
        prevATRd[days[k]] = (h[a:b + 1].max() - l[a:b + 1].min())

    rows = []
    done = set()
    vol_sorted = None
    for i in range(200, len(df)):
        d = day_id[i]
        if d not in prevHL or atr[i] <= 0 or i >= idx_last[d]:
            continue
        ph, pl = prevHL[d]
        if pseudo_k is not None:
            ph = ph + pseudo_k * prevATRd[d]
            pl = pl - pseudo_k * prevATRd[d]
        for side, level in (("sell", ph), ("buy", pl)):
            hit = (h[i] > level) if side == "sell" else (l[i] < level)
            if not hit or (d, side) in done:
                continue
            done.add((d, side))
            ref = c[i]; band = atr[i]
            rev_bar = ref - band if side == "sell" else ref + band
            con_bar = ref + band if side == "sell" else ref - band
            label = "U"
            for k in range(i + 1, idx_last[d] + 1):
                hr = (l[k] <= rev_bar) if side == "sell" else (h[k] >= rev_bar)
                hc = (h[k] >= con_bar) if side == "sell" else (l[k] <= con_bar)
                if hr and hc:
                    label = "U"; break
                if hr:
                    label = "A"; break
                if hc:
                    label = "B"; break
            if label == "U":
                continue
            row = {"symbol": sym, "side": side, "label": label,
                   "bars_fb": i - idx_first[d], "server_hour": ts.iloc[i].hour,
                   "t": ts.iloc[i]}
            if full_feats:
                j0 = idx_first[d]
                tp = (h[j0:i + 1] + l[j0:i + 1] + c[j0:i + 1]) / 3.0
                w = v[j0:i + 1]
                vwap = float((tp * w).sum() / max(w.sum(), 1e-9))
                depth = (h[i] - level) if side == "sell" else (level - l[i])
                row.update({
                    "spread_pts": sp[i],
                    "atr_price": atr[i],
                    "vol_pctile": float((v[i - 200:i] < v[i]).mean()),
                    "depth_atr": depth / atr[i],
                    "dist_prevclose_atr": (c[i] - prevC[d]) / atr[i] * (1 if side == "sell" else -1),
                    "dist_vwap_atr": (c[i] - vwap) / atr[i] * (1 if side == "sell" else -1),
                    "gap_atr": (o[j0] - prevC[d]) / atr[i],
                })
            rows.append(row)
    T = pd.DataFrame(rows)
    if len(T):
        T["rev"] = (T.label == "A").astype(int)
    return T


def prate(T, mask=None):
    s = T if mask is None else T[mask]
    if len(s) == 0:
        return np.nan, 0, 1.0
    p = s.rev.mean()
    bp = stats.binomtest(int(s.rev.sum()), len(s), 0.5).pvalue
    return p, len(s), bp


def line(ch="="):
    print(ch * 100)


def main():
    out = {}

    # ── build FX events at native boundary with full features ──
    fx = pd.concat([scan(s, 0, full_feats=True) for s in FX], ignore_index=True)
    fx = fx.sort_values("t").reset_index(drop=True)
    early = fx.bars_fb < EARLY_BARS
    late = ~early

    line()
    print("1) ROBUSTNESS RE-CONFIRMATION (fresh v2 data, 6 FX, M15 24mo)")
    line()
    p_e, n_e, bp_e = prate(fx, early)
    p_l, n_l, _ = prate(fx, late)
    print("  early(<%d bars) P(rev)=%.3f n=%d p=%.5f  |  late P(rev)=%.3f n=%d" % (
        EARLY_BARS, p_e, n_e, bp_e, p_l, n_l))
    qlab = pd.qcut(fx.t.rank(method="first"), 4, labels=False)
    for q in range(4):
        pq, nq, _ = prate(fx, early & (qlab == q))
        print("    quarter %d: early P(rev)=%.3f (n=%d)" % (q + 1, pq, nq))
    for side in ["sell", "buy"]:
        ps, ns, _ = prate(fx, early & (fx.side == side))
        print("    %s-side sweeps: early P(rev)=%.3f (n=%d)" % (side, ps, ns))
    for sym in FX:
        ps, ns, _ = prate(fx, early & (fx.symbol == sym))
        print("    %-7s early P(rev)=%.3f (n=%d)" % (sym, ps, ns))
    calm = fx.atr_price < fx.groupby("symbol").atr_price.transform(lambda x: x.quantile(0.8))
    pc, nc, _ = prate(fx, early & calm)
    print("    excluding top-vol quintile: early P(rev)=%.3f (n=%d)" % (pc, nc))
    out["confirm"] = {"early_p": p_e, "early_n": n_e, "early_binom": bp_e, "late_p": p_l}

    # ── sub-hour localization ──
    line()
    print("2) LOCALIZATION — P(rev) by exact bar after boundary / by server hour")
    line()
    for b in range(0, 8):
        pb, nb, _ = prate(fx, fx.bars_fb == b)
        print("    bar %d after reset: P(rev)=%.3f (n=%d)" % (b, pb, nb))
    hr_tab = {}
    for hr in sorted(fx.server_hour.unique()):
        ph_, nh_, _ = prate(fx, fx.server_hour == hr)
        hr_tab[int(hr)] = (round(float(ph_), 3), int(nh_))
    print("    by server hour:", {k: v for k, v in list(hr_tab.items())[:8]}, "...")
    out["by_bar"] = {int(b): prate(fx, fx.bars_fb == b)[0] for b in range(8)}

    # ── H3: boundary-shift test (the causal centerpiece) ──
    line()
    print("3) H3 BOUNDARY-SHIFT — does the effect follow the day RESET or the CLOCK?")
    print("   (redefine the day at server hour b; early = first %d bars after that boundary)" % EARLY_BARS)
    line()
    out["boundary"] = {}
    for b in [0, 3, 6, 9, 12, 15, 18, 21]:
        Tb = pd.concat([scan(s, b) for s in FX], ignore_index=True)
        e = Tb.bars_fb < EARLY_BARS
        pe, ne, bpe = prate(Tb, e)
        pl, nl, _ = prate(Tb, ~e)
        utc_h = (b + 21) % 24   # server = UTC+3
        print("  boundary=server %02d:00 (UTC %02d:00)  early P(rev)=%.3f (n=%4d, p=%.4f)  late=%.3f  diff=%+.3f" % (
            b, utc_h, pe, ne, bpe, pl, pe - pl))
        out["boundary"][b] = {"early_p": round(float(pe), 3), "n": int(ne),
                              "binom_p": float(bpe), "late_p": round(float(pl), 3)}

    # ── H1: spread/rollover ──
    line()
    print("4) H1 ROLLOVER/SPREAD — cost anatomy and whether P(rev) tracks the spread")
    line()
    meta2 = json.load(open(os.path.join(DATA, "_meta2.json")))
    print("  avg event-bar spread (points): early=%.1f  late=%.1f" % (
        fx[early].spread_pts.mean(), fx[late].spread_pts.mean()))
    # per-event cost in ATR units: spread(price) + commission(cost_price-spread proxy)
    pts = fx.symbol.map(meta2["point"])
    fx["spread_atr"] = fx.spread_pts * pts / fx.atr_price
    comm = fx.symbol.map({s: config.SYMBOL_SPEC[s]["cost_price"] for s in FX})
    fx["cost_atr"] = fx.spread_atr + comm / fx.atr_price   # spread at event + normal r/t cost
    ge = 2 * p_e - 1
    med_cost_e = float(fx[early].cost_atr.median())
    med_cost_l = float(fx[late].cost_atr.median())
    print("  GROSS edge early = 2p-1 = %+.3f ATR/event" % ge)
    print("  median event cost early = %.3f ATR | late = %.3f ATR" % (med_cost_e, med_cost_l))
    print("  NET edge early (median cost) = %+.3f ATR/event" % (ge - med_cost_e))
    # does the reversal itself track spread? split early events by spread
    e_df = fx[early].copy()
    hi_sp = e_df.spread_atr > e_df.spread_atr.median()
    p_hi, n_hi, _ = prate(e_df, hi_sp)
    p_lo, n_lo, _ = prate(e_df, ~hi_sp)
    print("  early events split by event-bar spread: high-spread P(rev)=%.3f (n=%d) | low-spread P(rev)=%.3f (n=%d)" % (
        p_hi, n_hi, p_lo, n_lo))
    out["h1"] = {"gross_atr": round(float(ge), 3), "cost_early": med_cost_e,
                 "cost_late": med_cost_l, "p_hi_spread": round(float(p_hi), 3),
                 "p_lo_spread": round(float(p_lo), 3)}

    # ── H2: liquidity ──
    line()
    print("5) H2 LIQUIDITY VACUUM — participation proxy (tick volume)")
    line()
    vole = fx[early].vol_pctile
    print("  early-event volume percentile: median=%.2f (day-normal=0.50)" % vole.median())
    lo_v = e_df.vol_pctile < e_df.vol_pctile.median()
    p_lv, n_lv, _ = prate(e_df, lo_v)
    p_hv, n_hv, _ = prate(e_df, ~lo_v)
    print("  early split by volume: thin P(rev)=%.3f (n=%d) | thick P(rev)=%.3f (n=%d)" % (
        p_lv, n_lv, p_hv, n_hv))
    out["h2"] = {"vol_med": round(float(vole.median()), 2),
                 "p_thin": round(float(p_lv), 3), "p_thick": round(float(p_hv), 3)}

    # ── H4: mean reversion / stretch ──
    line()
    print("6) H4 MEAN REVERSION — is timing just a proxy for overextension?")
    line()
    D = fx.dropna(subset=["dist_prevclose_atr", "dist_vwap_atr", "gap_atr"]).copy()
    D["early"] = (D.bars_fb < EARLY_BARS).astype(int)
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    Xcols = ["early", "dist_prevclose_atr", "dist_vwap_atr", "gap_atr", "depth_atr"]
    X = StandardScaler().fit_transform(D[Xcols])
    lr = LogisticRegression(max_iter=2000).fit(X, D.rev)
    coefs = dict(zip(Xcols, lr.coef_[0]))
    print("  logistic P(rev) ~ features (standardized coefs):")
    for k2, v2 in sorted(coefs.items(), key=lambda x: -abs(x[1])):
        print("    %-20s %+.3f" % (k2, v2))
    # stratified: within stretch terciles does early still matter?
    D["stretch_t"] = pd.qcut(D.dist_prevclose_atr, 3, labels=False, duplicates="drop")
    print("  within stretch terciles (dist from prev close):")
    for t in sorted(D.stretch_t.dropna().unique()):
        s = D[D.stretch_t == t]
        pe2, ne2, _ = prate(s, s.early == 1)
        pl2, nl2, _ = prate(s, s.early == 0)
        print("    tercile %d: early P(rev)=%.3f (n=%d) vs late %.3f (n=%d)" % (t, pe2, ne2, pl2, nl2))
    out["h4_coefs"] = {k2: round(float(v2), 3) for k2, v2 in coefs.items()}

    # ── H5: stop-hunt depth interaction ──
    line()
    print("7) H5 STOP-HUNT — sweep depth inside the early window")
    line()
    e_df["depth_t"] = pd.qcut(e_df.depth_atr, 3, labels=False, duplicates="drop")
    for t in sorted(e_df.depth_t.dropna().unique()):
        pt_, nt_, _ = prate(e_df, e_df.depth_t == t)
        print("    early depth tercile %d: P(rev)=%.3f (n=%d)" % (t, pt_, nt_))

    # ── cross-instrument ──
    line()
    print("8) CROSS-INSTRUMENT — indices, oil, silver (same scanner, native boundary)")
    line()
    out["cross"] = {}
    for sym in XA:
        Tx = scan(sym, 0)
        if len(Tx) < 50:
            print("  %-7s insufficient events (%d)" % (sym, len(Tx))); continue
        e2 = Tx.bars_fb < EARLY_BARS
        pe3, ne3, bpe3 = prate(Tx, e2)
        pl3, nl3, _ = prate(Tx, ~e2)
        print("  %-7s early P(rev)=%.3f (n=%3d, p=%.3f) | late=%.3f (n=%4d)" % (
            sym, pe3, ne3, bpe3, pl3, nl3))
        out["cross"][sym] = {"early_p": None if np.isnan(pe3) else round(float(pe3), 3),
                             "n": int(ne3), "binom_p": float(bpe3),
                             "late_p": round(float(pl3), 3)}

    # ── placebo pseudo-levels ──
    line()
    print("9) PLACEBO — pseudo-levels at prevH+0.35*range / prevL-0.35*range (FX)")
    line()
    Tp = pd.concat([scan(s, 0, pseudo_k=0.35) for s in FX], ignore_index=True)
    ep = Tp.bars_fb < EARLY_BARS
    pp_, np_, bpp = prate(Tp, ep)
    plp, nlp, _ = prate(Tp, ~ep)
    print("  pseudo-level early P(rev)=%.3f (n=%d, p=%.4f) | late=%.3f (n=%d)" % (
        pp_, np_, bpp, plp, nlp))
    out["placebo"] = {"early_p": round(float(pp_), 3), "n": int(np_), "binom_p": float(bpp),
                      "late_p": round(float(plp), 3)}

    fx.to_csv(os.path.join(RES, "root_cause_events.csv"), index=False)
    with open(os.path.join(RES, "root_cause_summary.json"), "w") as f:
        json.dump(out, f, indent=2, default=float)
    print("\nSaved results/root_cause_events.csv, results/root_cause_summary.json")


if __name__ == "__main__":
    main()
