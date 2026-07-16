"""artifact_page.py — build the self-contained HTML tearsheet (all four parts).

Charts are base64-embedded from report/. Output path is passed as argv[1]
(defaults to report/box_theory_report.html).

    cd research && box_theory/.venv/bin/python -m box_theory.artifact_page [out.html]
"""
from __future__ import annotations

import base64
import os
import sys

RPT = os.path.join(os.path.dirname(__file__), "report")


def img(name):
    with open(os.path.join(RPT, name), "rb") as f:
        return "data:image/png;base64," + base64.b64encode(f.read()).decode()


FIG = {k: img(v) for k, v in {
    "zone": "fig_zone_reversal.png",
    "dist": "fig_edge_distribution.png",
    "dims": "fig_dimensions.png",
    "equity": "fig_equity_best.png",
    "oos": "fig_oos.png",
    "wf": "fig_walkforward.png",
    "buffer": "fig_buffer_sensitivity.png",
    "s_dist": "fig_sneaky_distribution.png",
    "s_timing": "fig_sneaky_timing.png",
    "s_wf": "fig_sneaky_walkforward.png",
    "f_causes": "fig_forensic_loss_causes.png",
    "f_effects": "fig_forensic_effects.png",
    "f_filters": "fig_forensic_filters.png",
    "f_category": "fig_forensic_category.png",
    "l_split": "fig_sweeplab_split.png",
    "l_auc": "fig_sweeplab_auc.png",
    "l_models": "fig_sweeplab_models.png",
}.items()}

perpair = [
    ("XAUUSD", "M15 6mo · wickD · e3 · opposite · b0.05", 35, 42.9, 2.39, 0.944, 36.8, 6.3),
    ("EURUSD", "M15 6mo · wickC · e2 · opposite · b0.05", 99, 28.3, 2.25, 1.025, 150.9, 14.6),
    ("USDJPY", "M15 6mo · wickD · e3 · opposite · b0.05", 38, 28.9, 2.08, 0.798, 32.3, 7.6),
    ("AUDUSD", "M15 6mo · wickA · e2 · opposite · b0.15", 121, 27.3, 1.66, 0.572, 85.0, 18.1),
    ("GBPUSD", "M5 6mo · wickB · e3 · opposite · b0.05", 110, 20.9, 1.47, 0.565, 69.1, 17.4),
    ("USDCAD", "M5 6mo · wickA · e3 · opposite · b0.20", 127, 15.7, 1.08, 0.168, 12.0, 23.4),
]
mc = [
    ("EURUSD opposite", 51.5, 1.9, -57.9, 45.0, 68.3),
    ("XAUUSD opposite", 60.8, 8.1, -31.1, 22.8, 39.6),
    ("USDJPY opposite", 91.3, 55.6, -8.3, 18.3, 31.5),
    ("AUDUSD opposite", 82.2, 97.3, -40.2, 42.7, 65.5),
    ("GBPUSD opposite", 89.1, 65.5, -14.0, 21.7, 37.9),
    ("USDCAD opposite", 59.0, 10.2, -43.3, 32.5, 53.4),
]
sneaky_pp = [
    ("EURUSD", "M5 6mo · fractal · allday · swing · wB · opposite", 32, 28.1, 3.46, 2.265, 6.7),
    ("AUDUSD", "M30 24mo · literal · open@broker · swing · wA · opposite", 30, 33.3, 2.97, 1.898, 7.7),
    ("GBPUSD", "M15 24mo · literal · open@London · both · wD · opposite", 31, 38.7, 2.76, 1.199, 4.3),
    ("USDCAD", "M30 6mo · fractal · open@broker · both · wC · opposite", 36, 33.3, 2.27, 1.141, 4.8),
    ("USDJPY", "M15 24mo · literal · open@broker · swing · wA · opposite", 36, 19.4, 2.06, 1.129, 9.4),
    ("XAUUSD", "M15 12mo · fractal · open@broker · range · wB · opposite", 34, 20.6, 2.16, 1.050, 7.9),
]
filter_rows_data = [
    ("Skip wide prior-range days", "492 / 133", "+0.085", "+99%", "helps", "pos"),
    ("Skip gap days", "138 / 39", "+0.061", "+38%", "helps", "pos"),
    ("Skip tiny-stop trades", "7 / 2", "+0.048", "+4%", "neutral", ""),
    ("Skip trend-against days", "533 / 144", "+0.041", "−10%", "hurts", "neg"),
    ("Skip Asian session", "795 / 177", "+0.001", "−49%", "destroys", "neg"),
    ("Skip volatility spikes", "218 / 91", "+0.003", "−61%", "destroys", "neg"),
]
lab_models = [
    ("Fade-only (reversion)", 2281, 33.7, 0.76, "−0.175", "−87%"),
    ("Continuation-only (breakout)", 1763, 29.2, 0.64, "−0.288", "−92%"),
    ("Reverse the fade losers", 1006, 22.7, 0.45, "−0.484", "−91%"),
    ("ML hybrid (fade/follow switch, OOS)", 592, 35.0, 0.84, "−0.119", "−31%"),
]
lab_auc = [
    ("utc_hour", 0.479, "0.06"), ("ma_slope_atr", 0.520, "0.06"),
    ("wick_atr", 0.519, "0.09"), ("adx_d1", 0.516, "0.16"),
    ("against_d1trend", 0.514, "0.13"),
]


def rows_pp():
    out = []
    for p, cfg, t, w, pf, e, net, dd in perpair:
        cls = "pos" if e > 0 else "neg"
        out.append(f'<tr><td class="tk">{p}</td><td class="cfg">{cfg}</td>'
                   f'<td class="num">{t}</td><td class="num">{w:.1f}</td>'
                   f'<td class="num">{pf:.2f}</td><td class="num {cls}">{e:+.3f}</td>'
                   f'<td class="num {cls}">{net:+.1f}</td><td class="num">{dd:.1f}</td></tr>')
    return "\n".join(out)


def rows_mc():
    out = []
    for cfg, pp, nm, p5, ddm, dd95 in mc:
        out.append(f'<tr><td class="cfg">{cfg}</td><td class="num">{pp:.1f}%</td>'
                   f'<td class="num">{nm:+.1f}%</td><td class="num neg">{p5:+.1f}%</td>'
                   f'<td class="num">{ddm:.1f}%</td><td class="num">{dd95:.1f}%</td></tr>')
    return "\n".join(out)


def rows_spp():
    out = []
    for p, cfg, t, w, pf, e, dd in sneaky_pp:
        out.append(f'<tr><td class="tk">{p}</td><td class="cfg">{cfg}</td>'
                   f'<td class="num">{t}</td><td class="num">{w:.1f}</td>'
                   f'<td class="num">{pf:.2f}</td><td class="num pos">{e:+.3f}</td>'
                   f'<td class="num">{dd:.1f}</td></tr>')
    return "\n".join(out)


def rows_filters():
    out = []
    for name, rem, exp, net, verd, cls in filter_rows_data:
        out.append(f'<tr><td>{name}</td><td class="cfg">{rem}</td>'
                   f'<td class="num">{exp}</td><td class="num {cls}">{net}</td>'
                   f'<td class="{cls}">{verd}</td></tr>')
    return "\n".join(out)


def rows_lab_models():
    out = []
    for name, n, wr, pf, exp, net in lab_models:
        out.append(f'<tr><td>{name}</td><td class="num">{n}</td><td class="num">{wr:.1f}</td>'
                   f'<td class="num">{pf:.2f}</td><td class="num neg">{exp}</td>'
                   f'<td class="num neg">{net}</td></tr>')
    return "\n".join(out)


def rows_lab_auc():
    out = []
    for f, auc, p in lab_auc:
        out.append(f'<tr><td class="cfg">{f}</td><td class="num">{auc:.3f}</td>'
                   f'<td class="num">{p}</td><td>not significant</td></tr>')
    return "\n".join(out)


HTML = f'''<title>Box Theory — Validation Report</title>
<style>
:root {{
  --paper:#FBFCFD; --panel:#FFFFFF; --ink:#14181D; --muted:#5A6673;
  --line:#E3E8ED; --line-strong:#CBD4DC;
  --accent:#20707F; --accent-soft:#E4F0F2;
  --pos:#2F7D52; --neg:#B23A30; --neg-bg:#F7E7E5;
  --serif:ui-serif,Georgia,"Iowan Old Style",Cambria,serif;
  --sans:system-ui,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  --mono:ui-monospace,"SF Mono",SFMono-Regular,Menlo,Consolas,monospace;
}}
@media (prefers-color-scheme: dark) {{
  :root {{
    --paper:#0E1216; --panel:#161B22; --ink:#E6EDF3; --muted:#94A3B0;
    --line:#232B34; --line-strong:#333E49;
    --accent:#54B4C4; --accent-soft:#122A30;
    --pos:#57B37E; --neg:#E0685C; --neg-bg:#2A1614;
  }}
}}
:root[data-theme="light"] {{
  --paper:#FBFCFD; --panel:#FFFFFF; --ink:#14181D; --muted:#5A6673;
  --line:#E3E8ED; --line-strong:#CBD4DC; --accent:#20707F; --accent-soft:#E4F0F2;
  --pos:#2F7D52; --neg:#B23A30; --neg-bg:#F7E7E5;
}}
:root[data-theme="dark"] {{
  --paper:#0E1216; --panel:#161B22; --ink:#E6EDF3; --muted:#94A3B0;
  --line:#232B34; --line-strong:#333E49; --accent:#54B4C4; --accent-soft:#122A30;
  --pos:#57B37E; --neg:#E0685C; --neg-bg:#2A1614;
}}
* {{ box-sizing:border-box; }}
.wrap {{ background:var(--paper); color:var(--ink); font-family:var(--sans);
  line-height:1.6; -webkit-font-smoothing:antialiased; padding:0 20px 80px; }}
.inner {{ max-width:880px; margin:0 auto; }}
.eyebrow {{ font-family:var(--mono); font-size:12px; letter-spacing:.18em;
  text-transform:uppercase; color:var(--accent); margin:0 0 10px; }}
h1 {{ font-size:clamp(30px,5vw,46px); line-height:1.08; letter-spacing:-.02em;
  text-wrap:balance; margin:0 0 14px; font-weight:680; }}
.masthead {{ padding:64px 0 26px; border-bottom:1px solid var(--line); }}
.dek {{ font-size:18px; color:var(--muted); max-width:60ch; margin:0; }}
.meta {{ font-family:var(--mono); font-size:12px; color:var(--muted);
  margin-top:22px; display:flex; flex-wrap:wrap; gap:6px 22px; }}
.verdict {{ display:flex; gap:18px; align-items:flex-start; margin:30px 0 6px;
  padding:22px 24px; background:var(--neg-bg); border:1px solid var(--neg);
  border-radius:10px; }}
.verdict .tag {{ font-family:var(--mono); font-weight:700; font-size:13px;
  letter-spacing:.1em; color:#fff; background:var(--neg); padding:5px 11px;
  border-radius:5px; white-space:nowrap; margin-top:3px; }}
.verdict p {{ margin:0; font-family:var(--serif); font-size:20px; line-height:1.45; }}
.kpis {{ display:grid; grid-template-columns:repeat(4,1fr); gap:1px;
  background:var(--line); border:1px solid var(--line); border-radius:10px;
  overflow:hidden; margin:34px 0 8px; }}
.kpi {{ background:var(--panel); padding:20px 18px; }}
.kpi .v {{ font-family:var(--mono); font-size:30px; font-weight:600;
  letter-spacing:-.01em; font-variant-numeric:tabular-nums; }}
.kpi .l {{ font-size:12.5px; color:var(--muted); margin-top:6px; line-height:1.35; }}
.kpi .neg {{ color:var(--neg); }}
section {{ padding-top:52px; }}
h2 {{ font-size:13px; font-family:var(--mono); letter-spacing:.14em;
  text-transform:uppercase; color:var(--accent); margin:0 0 4px;
  padding-bottom:12px; border-bottom:1px solid var(--line); }}
h3 {{ font-size:23px; letter-spacing:-.01em; margin:22px 0 10px; font-weight:640;
  text-wrap:balance; }}
p.body {{ font-size:16.5px; max-width:66ch; margin:12px 0; }}
figure {{ margin:22px 0 6px; border:1px solid var(--line); border-radius:10px;
  overflow:hidden; background:var(--panel); }}
figure img {{ display:block; width:100%; height:auto; }}
figcaption {{ font-size:13px; color:var(--muted); padding:12px 16px;
  border-top:1px solid var(--line); }}
.tablewrap {{ overflow-x:auto; margin:20px 0; border:1px solid var(--line);
  border-radius:10px; }}
table {{ border-collapse:collapse; width:100%; font-size:14px; min-width:560px; }}
th, td {{ text-align:left; padding:11px 14px; border-bottom:1px solid var(--line);
  white-space:nowrap; }}
thead th {{ font-family:var(--mono); font-size:11px; letter-spacing:.08em;
  text-transform:uppercase; color:var(--muted); background:var(--panel); }}
tbody tr:last-child td {{ border-bottom:none; }}
td.num {{ font-family:var(--mono); text-align:right; font-variant-numeric:tabular-nums; }}
td.tk {{ font-family:var(--mono); font-weight:600; }}
td.cfg {{ font-family:var(--mono); font-size:12.5px; color:var(--muted); white-space:nowrap; }}
.pos {{ color:var(--pos); }} .neg {{ color:var(--neg); }}
.callout {{ border-left:3px solid var(--accent); background:var(--accent-soft);
  padding:14px 18px; border-radius:0 8px 8px 0; margin:20px 0; font-size:15.5px; }}
ul.clean {{ padding-left:0; list-style:none; margin:14px 0; }}
ul.clean li {{ position:relative; padding-left:22px; margin:9px 0; font-size:16px;
  max-width:66ch; }}
ul.clean li::before {{ content:"—"; position:absolute; left:0; color:var(--accent);
  font-family:var(--mono); }}
.foot {{ margin-top:60px; padding-top:22px; border-top:1px solid var(--line);
  font-family:var(--mono); font-size:12px; color:var(--muted); line-height:1.7; }}
.two {{ display:grid; grid-template-columns:1fr 1fr; gap:18px; }}
.card {{ border:1px solid var(--line); border-radius:10px; padding:18px 20px;
  background:var(--panel); }}
.card .h {{ font-family:var(--mono); font-size:11px; letter-spacing:.1em;
  text-transform:uppercase; margin:0 0 8px; }}
.card.win .h {{ color:var(--pos); }} .card.lose .h {{ color:var(--neg); }}
.card .cfg {{ font-family:var(--mono); font-size:13px; }}
.card .st {{ font-size:14px; color:var(--muted); margin-top:8px; }}
@media (max-width:640px) {{
  .kpis {{ grid-template-columns:repeat(2,1fr); }}
  .two {{ grid-template-columns:1fr; }}
  .verdict {{ flex-direction:column; gap:12px; }}
}}
</style>

<div class="wrap"><div class="inner">

<header class="masthead">
  <p class="eyebrow">Quantitative Strategy Validation</p>
  <h1>The Box Theory doesn&rsquo;t hold up</h1>
  <p class="dek">A full statistical test of the previous-day-high/low reversal
  strategy across six instruments, three timeframes, and 64,000+ configurations &mdash;
  plus a trade-level forensic autopsy and a fade-vs-follow direction study.
  The data was asked to prove the edge, not assume it.</p>
  <div class="meta">
    <span>BROKER · IC MARKETS DEMO 52942033</span>
    <span>PAIRS · EUR GBP JPY AUD CAD XAU</span>
    <span>TF · M5 / M15 / M30</span>
    <span>WINDOW · 6&ndash;24 MONTHS</span>
  </div>
  <div class="verdict">
    <span class="tag">REJECTED</span>
    <p>Price does not preferentially reverse at the previous day&rsquo;s high or
    low &mdash; it&rsquo;s a coin flip. No configuration survives honest
    out-of-sample validation, no filter separates failure from profit, and the
    post-sweep direction itself is unpredictable. There is no tradeable edge here.</p>
  </div>
</header>

<div class="kpis">
  <div class="kpi"><div class="v neg">87%</div><div class="l">of 16,128 configs lose money (&ge;30 trades)</div></div>
  <div class="kpi"><div class="v">~50%</div><div class="l">reversal rate at PDH/PDL &mdash; a coin flip</div></div>
  <div class="kpi"><div class="v neg">&minus;0.02R</div><div class="l">walk-forward expectancy, pooled over 24 folds</div></div>
  <div class="kpi"><div class="v">0.52</div><div class="l">best ML AUC for predicting sweep direction (0.5 = random)</div></div>
</div>

<section>
  <h2>01 · The Premise</h2>
  <h3>Does price actually reverse at the previous day&rsquo;s extremes?</h3>
  <p class="body">The whole theory rests on one claim: that the previous day&rsquo;s
  high and low are reliable reversal points. This is directly measurable without
  trading anything &mdash; just count, for every day the extreme was touched, how
  often price closed back inside the box.</p>
  <figure><img src="{FIG['zone']}" alt="Reversal rates at PDH and PDL by pair, all near 50 percent">
    <figcaption>Reversal rates hug the 50% line on every pair. On USDJPY and AUDUSD
    the extreme breaks <em>more</em> often than it holds. The premise fails its own test.</figcaption>
  </figure>
  <div class="callout">The one real tendency in the data runs the other way: after
  touching an extreme, price reaches the <strong>midpoint</strong> ~62&ndash;66% of
  the time &mdash; a mild pull toward the center, not a hard rejection at the edge.</div>
</section>

<section>
  <h2>02 · The Distribution</h2>
  <h3>Real edge, or the winning tail of random noise?</h3>
  <p class="body">With thousands of parameter combinations, some will look profitable
  by pure chance. The honest question is where the <em>whole distribution</em> sits.
  A strategy with an edge centers above break-even. This one sits firmly below it.</p>
  <figure><img src="{FIG['dist']}" alt="Histogram of expectancy across configs, centered below zero">
    <figcaption>Expectancy across all valid configurations. Median &minus;0.15R;
    only 13% are profitable &mdash; consistent with noise, not signal.</figcaption>
  </figure>
  <figure><img src="{FIG['dims']}" alt="Average expectancy by timeframe, entry method, and exit model, all negative">
    <figcaption>Every design choice averages negative. Least-bad: M30 over M15 over
    M5 (costs dominate the fast timeframe), confirmation entries, and &mdash; tellingly
    &mdash; the &ldquo;exit at opposite side&rdquo; rule, which isn&rsquo;t the stated thesis.</figcaption>
  </figure>
</section>

<section>
  <h2>03 · The &ldquo;Winners&rdquo;</h2>
  <h3>The best configurations have the fingerprint of curve-fitting</h3>
  <p class="body">Every pair&rsquo;s top configuration uses the opposite-side exit
  (a tight stop with a box-width target &mdash; a lottery payoff), and the strongest
  are all on the recent 6-month window. Not one 24-month config reaches the leaderboard.
  If the edge were real, the configs with 4&times; more data would dominate it, not vanish.</p>
  <div class="tablewrap"><table>
    <thead><tr><th>Pair</th><th>Best configuration</th><th>Trades</th><th>Win%</th>
      <th>PF</th><th>ExpR</th><th>Net%</th><th>MaxDD%</th></tr></thead>
    <tbody>{rows_pp()}</tbody>
  </table></div>
  <figure><img src="{FIG['equity']}" alt="Equity curve of the best configuration with in/out-of-sample split marked">
    <figcaption>The single best config of all 16,128. Almost the entire rise happens
    after the 70/30 split (red line) &mdash; the &ldquo;edge&rdquo; is concentrated in
    one recent regime, not steady compounding.</figcaption>
  </figure>
</section>

<section>
  <h2>04 · The Verdict Test</h2>
  <h3>Walk-forward: what an honest optimizer would actually earn</h3>
  <p class="body">On each training window, pick the best configuration; then trade it
  on the <em>next, unseen</em> window. Repeat. It simulates exactly what a real quant
  does &mdash; and it removes hindsight.</p>
  <figure><img src="{FIG['wf']}" alt="Walk-forward out-of-sample expectancy by pair and fold, pooling near zero">
    <figcaption>Pooled out-of-sample expectancy: &minus;0.018R &mdash; indistinguishable
    from zero, with violent fold-to-fold swings. No durable edge.</figcaption>
  </figure>
  <h3>Monte Carlo: even the hand-picked winners are lottery tickets</h3>
  <div class="tablewrap"><table>
    <thead><tr><th>Config</th><th>P(profit)</th><th>Net median</th><th>Net p5</th>
      <th>MaxDD median</th><th>MaxDD p95</th></tr></thead>
    <tbody>{rows_mc()}</tbody>
  </table></div>
  <figure><img src="{FIG['buffer']}" alt="Top config expectancy collapsing as buffer size increases">
    <figcaption>Fragility: the top config&rsquo;s expectancy collapses from ~1.0R to
    ~0.2R when the entry buffer moves just 5%&rarr;15%. Robust edges don&rsquo;t do that.</figcaption>
  </figure>
</section>

<section>
  <h2>Part II &middot; The Sneaky Pivot</h2>
  <h3>The fuller variant &mdash; does adding structure rescue it?</h3>
  <p class="body">The strategy was then refined to the complete Sneaky Pivot form and
  re-tested across <strong>48,384</strong> configurations. It adds three things the
  basic test lacked: <strong>swing levels</strong> beyond the previous-day range,
  a <strong>session-open entry sequence</strong> (opening candle &rarr; &ldquo;sneaky&rdquo;
  pivot &rarr; enter on the break, anchored to the broker rollover, London, or NY open),
  and an explicit <strong>opposite-side target</strong> with the stop beyond the pivot wick.</p>
  <figure><img src="{FIG['s_dist']}" alt="Sneaky Pivot expectancy distribution, still below break-even">
    <figcaption>A genuine improvement: 30% of configs are profitable (vs 13% for the
    basic box) and the median rises to &minus;0.11 R (from &minus;0.15). But it still
    sits below break-even &mdash; 70% lose.</figcaption>
  </figure>
  <h3>Does the session-open timing add edge?</h3>
  <figure><img src="{FIG['s_timing']}" alt="Mean expectancy by timing anchor, all negative">
    <figcaption>The signature claim &mdash; and the answer is no on average. Every anchor
    is negative. One pair/config (EURUSD, NY open, swing levels) looked strong at +0.77 R
    in isolation but did not generalise; that is exactly why the full sweep matters.</figcaption>
  </figure>
  <div class="tablewrap"><table>
    <thead><tr><th>Pair</th><th>Best Sneaky Pivot config</th><th>Trades</th><th>Win%</th>
      <th>PF</th><th>ExpR</th><th>MaxDD%</th></tr></thead>
    <tbody>{rows_spp()}</tbody>
  </table></div>
  <p class="body">The payoff profile is the tell: 20&ndash;40% win rates with high PF from
  the large target, on <strong>30&ndash;60-trade</strong> samples &mdash; too thin to trust.</p>
  <figure><img src="{FIG['s_wf']}" alt="Sneaky Pivot walk-forward, pooling near zero">
    <figcaption>Walk-forward selection pools to &minus;0.05 R &mdash; again ~zero. Out-of-sample
    leaves only 7&ndash;30 trades per config, so single trades swing the result. No durable edge.</figcaption>
  </figure>
  <div class="callout"><strong>Combined verdict.</strong> The Sneaky Pivot is the
  better-constructed idea &mdash; near break-even rather than clearly negative, and it
  points at the one real tendency in the data (a pull toward the range centre/opposite
  side, not a hard rejection at the edge). But neither strategy delivers a statistically
  robust edge: both pool to ~0 R in walk-forward.</div>
</section>

<section>
  <h2>Part III &middot; Forensic Autopsy</h2>
  <h3>Why do the trades actually win and lose?</h3>
  <p class="body">Beyond pass/fail: 2,498 trades from a canonical config (pooled across
  all six pairs) were labelled with 15 pre-entry features plus excursion and outcome
  data, then dissected for root cause. Raw performance is near break-even in-sample
  (PF 1.05, +0.05 R, 20% win rate) &mdash; a low-hit-rate, big-target profile.</p>
  <figure><img src="{FIG['f_causes']}" alt="Root causes of losing trades">
    <figcaption>Losses are not caused by news or gaps. In <strong>55%</strong> of losses
    the day closed against the fade and in <strong>42%</strong> the faded level was broken
    and price kept going. The strategy is fading real directional movement and getting run over.</figcaption>
  </figure>
  <h3>Winners share the losers&rsquo; setups</h3>
  <figure><img src="{FIG['f_effects']}" alt="Effect sizes separating winners from losers">
    <figcaption>The separators are weak and mostly mechanical &mdash; winners enter later in
    the day and in higher-volatility regimes. No strong hidden variable exists (max correlation
    with the R outcome is 0.12).</figcaption>
  </figure>
  <figure><img src="{FIG['f_category']}" alt="Expectancy by session and instrument">
    <figcaption>Profit concentrates in quiet Asian/off hours and in AUD/XAU/JPY; London (a
    trending session) is negative; USDCAD is a persistent loser.</figcaption>
  </figure>
  <h3>The filter paradox &mdash; the central result</h3>
  <figure><img src="{FIG['f_filters']}" alt="Filter feasibility, most filters reduce expectancy">
    <figcaption>Only &ldquo;skip wide prior-range days&rdquo; and &ldquo;skip gaps&rdquo; beat baseline.</figcaption>
  </figure>
  <div class="tablewrap"><table>
    <thead><tr><th>Filter</th><th>Removed (L/W)</th><th>Expectancy</th><th>Net</th><th>Verdict</th></tr></thead>
    <tbody>{rows_filters()}</tbody>
  </table></div>
  <div class="callout"><strong>The intuitive filters destroy the strategy.</strong> Skipping
  volatility spikes cuts net to &minus;61%, skipping the Asian session to &minus;49%. Why?
  <strong>The winners live in the same conditions as the losers</strong> &mdash; the volatility
  that breaks levels is the same volatility that powers the big reversals. Only avoiding
  abnormally wide prior-range (expansion/trend) days cleanly helps, lifting expectancy
  +0.05&rarr;+0.09 R and cutting drawdown.</div>
</section>

<section>
  <h2>Part IV &middot; Fade or Follow?</h2>
  <h3>After a liquidity sweep, which direction is right &mdash; and can we know in advance?</h3>
  <p class="body">The final question: is the strategy failing because it mixes two market
  behaviours (failed sweeps that revert vs true breakouts that continue) into one rule?
  Every sweep event (2,869 across all pairs, M15, 24 months) was labelled by a
  <strong>symmetric &plusmn;1-ATR barrier race</strong> from the decision price &mdash;
  whichever side price touched first decides the category &mdash; and <em>both</em> a fade
  trade and a continuation trade were simulated on every event with identical geometry.</p>
  <figure><img src="{FIG['l_split']}" alt="Sweep outcomes split almost exactly 50/50">
    <figcaption>49.9% continue vs 50.1% revert. After a sweep of a prior-day level, the
    next 1-ATR move is a literal coin flip. There is no directional bias to exploit.</figcaption>
  </figure>
  <figure><img src="{FIG['l_auc']}" alt="AUC of every pre-entry feature, all in the noise band">
    <figcaption>19 pre-entry variables &mdash; trend, momentum, volatility, sweep size and
    velocity, wick anatomy, prior touches, session, day-of-week &mdash; tested for the power
    to predict direction. Every AUC sits in 0.48&ndash;0.52; nothing is statistically
    significant. ML classifiers (logistic + gradient boosting) reach only 0.52 AUC out-of-sample.</figcaption>
  </figure>
  <div class="tablewrap"><table>
    <thead><tr><th>Direction model</th><th>n</th><th>Win%</th><th>PF</th><th>ExpR</th><th>Net</th></tr></thead>
    <tbody>{rows_lab_models()}</tbody>
  </table></div>
  <figure><img src="{FIG['l_models']}" alt="All four direction models have negative expectancy">
    <figcaption>Every direction model loses with identical symmetric geometry. Fading is the
    least-bad; following the breakout is worst; reversing the fade losers is worst of all
    (hindsight isn&rsquo;t a strategy); the ML-switched hybrid narrows the loss but stays negative.</figcaption>
  </figure>
  <div class="callout"><strong>The definitive answer.</strong> The losing trades do not
  contain a hidden breakout strategy &mdash; reversing them loses <em>more</em> (&minus;0.48 R),
  because both directions pay the same costs on the same coin-flip event. The strategy is not
  mixing two tradeable behaviours; it is trading <strong>one genuinely unpredictable event</strong>.
  Mixing isn&rsquo;t the disease &mdash; randomness is. If a sweep edge exists, it lives at levels
  and timescales not tested here, not in re-slicing this dataset.</div>
</section>

<section>
  <h2>05 · Where It Works &amp; Fails</h2>
  <div class="two">
    <div class="card win"><p class="h">Least bad</p>
      <ul class="clean" style="margin:0">
        <li>M30 timeframe, longer windows</li>
        <li>Confirmation entries (break / next candle)</li>
        <li>Opposite-side exit, tight buffers</li>
        <li>Quiet Asian/off hours; AUD, XAU, JPY</li>
        <li>Skip wide prior-range (expansion) days</li>
      </ul>
      <p class="st">&hellip; and even here, ~0 after honest validation.</p>
    </div>
    <div class="card lose"><p class="h">Fails hardest</p>
      <ul class="clean" style="margin:0">
        <li>M5 &mdash; costs and noise dominate</li>
        <li>Trailing stops, wide buffers</li>
        <li>London session; USDCAD</li>
        <li>Breakout-following after sweeps (worst direction)</li>
        <li>The <em>stated</em> thesis: small fade + fixed RR</li>
      </ul>
      <p class="st">Worst config: USDJPY M5, PF 0.18, &minus;64%, 7% win rate.</p>
    </div>
  </div>
</section>

<section>
  <h2>06 · Conclusion</h2>
  <p class="body">The data rejects the Box Theory as a source of edge. The false
  assumption is the central one &mdash; price does not preferentially reverse at PDH/PDL.
  What the setup actually trades is a liquidity-sweep event whose subsequent direction is
  a 50/50 barrier race that no measured variable tilts. The rejection-candle filters add
  only marginal signal that transaction costs erase; the one profitable-looking variant
  abandons the thesis and still fails walk-forward; and neither fading, following,
  reversing the losers, nor an ML-switched hybrid produces positive expectancy.</p>
  <div class="callout"><strong>No configuration is recommended for live trading.</strong>
  The only defensible research thread &mdash; regime-filtered swing-level fades to the
  opposite side (skip wide-range days) &mdash; tested at break-even and would need
  materially more data to confirm before any capital risk.</div>
</section>

<p class="foot">
  METHOD · R-multiple accounting, per-symbol spread+commission costs, no look-ahead
  (signal at close &rarr; managed next bar), pessimistic intrabar tie-breaking,
  compounding equity. Harness integrity: 47 unit tests, tick-level manual audit, and a
  seeded random-entry control confirming zero manufactured edge.<br>
  REPRODUCE · export_data &rarr; sweep / sneaky_sweep &rarr; robustness &rarr; forensics
  &rarr; sweep_lab &rarr; report · research/box_theory/ · independent of the live
  execution framework.
</p>

</div></div>'''


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(RPT, "box_theory_report.html")
    with open(out, "w") as f:
        f.write(HTML)
    print("wrote", out, os.path.getsize(out), "bytes")


if __name__ == "__main__":
    main()
