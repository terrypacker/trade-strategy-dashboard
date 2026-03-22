"""
War Trade Backtest  ·  backtest.py
────────────────────────────────────────────────────────────────────────────
Runs every strategy in strategy.REGISTRY across all configured conflict
windows, then writes a self-contained HTML dashboard to backtest_report.html.

Usage:
    python3 backtest.py

Dependencies:
    pip install yfinance pandas numpy
"""

import json
import math
import sys
from datetime import datetime

import numpy as np
import pandas as pd
import yfinance as yf

from strategy import (
    REGISTRY,
    BuyOnlyOilWarStrategy,
    Signals,
)

# ═══════════════════════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════════════════════
EQUITY_TICKER   = "VTI"      # swap for SWTSX, SPY, etc.
OIL_TICKER      = "CL=F"     # WTI crude futures
INITIAL_CAPITAL = 100_000

# Each event: (fetch_start, bt_start, bt_end, hex_colour)
# fetch_start is earlier than bt_start to warm up the 20-day lookback.
EVENTS = {
    "Gulf War 1990":        ("1990-05-01", "1990-07-01", "1991-04-01", "#e84040"),
    "Iraq War 2003":        ("2002-09-01", "2002-11-01", "2003-09-01", "#e8a020"),
    "Libya 2011":           ("2010-11-01", "2011-01-01", "2011-10-01", "#e8c020"),
    "ISIS Surge 2014":      ("2014-03-01", "2014-05-01", "2015-01-01", "#7ddcf0"),
    "Tanker Attacks 2019":  ("2019-01-01", "2019-03-01", "2019-11-01", "#b07dfc"),
    "Ukraine 2022":         ("2021-09-01", "2021-11-01", "2022-09-01", "#3ecf6a"),
    "Gaza Oct 2023":        ("2023-07-01", "2023-09-01", "2024-04-01", "#fc7d7d"),
}

OUTPUT_FILE = "backtest_report.html"

# ═══════════════════════════════════════════════════════════════════════════
# DATA FETCHING
# ═══════════════════════════════════════════════════════════════════════════

def fetch(ticker: str, start: str, end: str) -> pd.Series:
    """Download Close prices; handles yfinance MultiIndex columns."""
    raw = yf.download(ticker, start=start, end=end, progress=False, auto_adjust=True)
    if raw.empty:
        raise ValueError(f"No data for {ticker} [{start} → {end}]")
    if isinstance(raw.columns, pd.MultiIndex):
        return raw[("Close", ticker)].dropna()
    return raw["Close"].dropna()


# ═══════════════════════════════════════════════════════════════════════════
# BACKTEST ENGINE
# ═══════════════════════════════════════════════════════════════════════════

def _build_signals(df: pd.DataFrame, i: int) -> Signals:
    """Compute the Signals dataclass for bar i (requires i >= 20)."""
    P   = float(df["eq"].iat[i])
    H   = float(df["eq"].iloc[i - 20:i].max())
    D   = (H - P) / H if H > 0 else 0.0
    O   = float(df["oil"].iat[i])
    O5  = float(df["oil"].iloc[i - 5:i].mean())
    spk = (O - O5) / O5 if O5 > 0 else 0.0
    P3  = float(df["eq"].iat[i - 3]) if i >= 3 else P
    r3  = (P / P3) - 1 if P3 > 0 else 0.0
    return Signals(
        price=P, high_20d=H, drawdown=D,
        return_3d=r3, oil_price=O, oil_5d_avg=O5, oil_spike=spk,
    )


def run_backtest(fetch_start: str, bt_start: str, bt_end: str) -> dict:
    """
    Run all strategies in REGISTRY over the window [bt_start, bt_end].

    Returns a dict keyed by strategy.name, each containing pd.Series for:
      equity        — portfolio value (rebased to INITIAL_CAPITAL)
      allocation    — daily allocation or cumulative deployment fraction
      unrealized    — present-value of accumulated shares (BuyOnly)
      cash_deployed — raw cash spent so far (BuyOnly; NaN for others)
    """
    eq  = fetch(EQUITY_TICKER, fetch_start, bt_end)
    oil = fetch(OIL_TICKER,    fetch_start, bt_end)

    df = pd.DataFrame({"eq": eq, "oil": oil}).dropna()
    df["eq_ret"] = df["eq"].pct_change().fillna(0)

    bt_start_dt = pd.Timestamp(bt_start)
    n = len(df)

    # Per-strategy lists  (length = n, prepend with initial value for equity)
    ec:  dict[str, list] = {}   # equity curve
    ac:  dict[str, list] = {}   # allocation curve
    uc:  dict[str, list] = {}   # unrealized value curve
    cdc: dict[str, list] = {}   # cash deployed curve

    for strat in REGISTRY:
        strat.reset()
        if isinstance(strat, BuyOnlyOilWarStrategy):
            strat.set_initial_capital(INITIAL_CAPITAL)
        ec[strat.name]  = [INITIAL_CAPITAL]
        ac[strat.name]  = []
        uc[strat.name]  = [INITIAL_CAPITAL]
        cdc[strat.name] = [0.0]

    for i in range(n):
        if i < 20:
            for strat in REGISTRY:
                ac[strat.name].append(float("nan"))
                ec[strat.name].append(ec[strat.name][-1])
                uc[strat.name].append(uc[strat.name][-1])
                cdc[strat.name].append(cdc[strat.name][-1])
            continue

        sigs   = _build_signals(df, i)
        eq_ret = float(df["eq_ret"].iat[i])

        for strat in REGISTRY:
            if isinstance(strat, BuyOnlyOilWarStrategy):
                # Buy-only: spend incremental capital, accumulate shares
                inc   = strat.next_allocation(sigs)
                spend = inc * INITIAL_CAPITAL
                remaining = INITIAL_CAPITAL - strat.cash_deployed
                spend = min(spend, max(remaining, 0.0))

                if spend > 0 and sigs.price > 0:
                    strat.record_purchase(spend / sigs.price, spend)

                unreal       = strat.unrealized_value(sigs.price)
                cum_deployed = strat.cash_deployed

                ac[strat.name].append(round(cum_deployed / INITIAL_CAPITAL, 4))
                ec[strat.name].append(unreal)
                uc[strat.name].append(unreal)
                cdc[strat.name].append(cum_deployed)

            else:
                # Standard: rebalance to target allocation each bar
                alloc   = strat.next_allocation(sigs)
                new_val = ec[strat.name][-1] * (1.0 + alloc * eq_ret)

                ac[strat.name].append(alloc)
                ec[strat.name].append(new_val)
                uc[strat.name].append(new_val)
                cdc[strat.name].append(float("nan"))

            strat.on_bar(sigs)

    # Assemble series, slice to window, rebase equity
    idx = df.index
    results: dict[str, dict] = {}

    for strat in REGISTRY:
        eq_s  = pd.Series(ec[strat.name][1:],  index=idx)
        al_s  = pd.Series(ac[strat.name],       index=idx)
        unr_s = pd.Series(uc[strat.name][1:],   index=idx)
        cd_s  = pd.Series(cdc[strat.name][1:],  index=idx)

        eq_s  = eq_s[idx  >= bt_start_dt]
        al_s  = al_s[idx  >= bt_start_dt]
        unr_s = unr_s[idx >= bt_start_dt]
        cd_s  = cd_s[idx  >= bt_start_dt]

        base = eq_s.iloc[0]
        if base and not math.isnan(base) and base != 0:
            eq_s  = eq_s  / base * INITIAL_CAPITAL
            unr_s = unr_s / base * INITIAL_CAPITAL

        results[strat.name] = {
            "equity":        eq_s,
            "allocation":    al_s,
            "unrealized":    unr_s,
            "cash_deployed": cd_s,
        }

    return results


# ═══════════════════════════════════════════════════════════════════════════
# METRICS
# ═══════════════════════════════════════════════════════════════════════════

def _max_drawdown(s: pd.Series) -> float:
    s = s.dropna()
    if len(s) < 2:
        return 0.0
    peak = s.cummax()
    return float(((s - peak) / peak).min())


def compute_metrics(equity: pd.Series) -> dict:
    s = equity.dropna()
    if len(s) < 2:
        return {}
    ret    = float(s.iloc[-1] / s.iloc[0] - 1)
    mdd    = _max_drawdown(s)
    vol    = float(s.pct_change().dropna().std() * np.sqrt(252))
    sharpe = ret / vol if vol > 0 else 0.0
    return {
        "return":  round(ret,    4),
        "max_dd":  round(mdd,    4),
        "sharpe":  round(sharpe, 2),
        "vol":     round(vol,    4),
        "days":    len(s),
        "final":   round(float(s.iloc[-1]), 2),
    }


def _to_pts(s: pd.Series) -> list:
    return [
        {"x": ts.strftime("%Y-%m-%d"), "y": round(float(v), 2)}
        for ts, v in s.items()
        if not (isinstance(v, float) and math.isnan(v))
    ]


# ═══════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════

print("=" * 64)
print(f"  WAR TRADE BACKTEST  ·  {datetime.today().strftime('%Y-%m-%d')}")
print(f"  Equity: {EQUITY_TICKER}   Oil: {OIL_TICKER}")
print(f"  Strategies: {', '.join(s.name for s in REGISTRY)}")
print("=" * 64)

payload = []

for event_name, (fetch_start, bt_start, bt_end, color) in EVENTS.items():
    print(f"\n  ▶ {event_name}  [{bt_start} → {bt_end}]", flush=True)
    try:
        run = run_backtest(fetch_start, bt_start, bt_end)

        strats_out = []
        for strat in REGISTRY:
            r    = run[strat.name]
            mets = compute_metrics(r["equity"])
            print(f"     {strat.name:<24}  return={mets.get('return',0):+.1%}  maxdd={mets.get('max_dd',0):.1%}")
            strats_out.append({
                "name":          strat.name,
                "color":         strat.color,
                "metrics":       mets,
                "equity":        _to_pts(r["equity"]),
                "allocation":    _to_pts(r["allocation"].fillna(0)),
                "unrealized":    _to_pts(r["unrealized"]),
                "cash_deployed": _to_pts(r["cash_deployed"].fillna(0)),
            })

        payload.append({
            "name":       event_name,
            "color":      color,
            "strategies": strats_out,
        })

    except Exception as exc:
        print(f"     ✗ SKIPPED — {exc}")

print(f"\n  {len(payload)} events completed.")


# ═══════════════════════════════════════════════════════════════════════════
# HTML DASHBOARD  (self-contained, no server required)
# ═══════════════════════════════════════════════════════════════════════════

HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>War Trade Backtest Report</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.3/dist/chart.umd.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chartjs-adapter-date-fns@3.0.0/dist/chartjs-adapter-date-fns.bundle.min.js"></script>
<style>
@import url('https://fonts.googleapis.com/css2?family=Share+Tech+Mono&family=Barlow+Condensed:wght@300;400;600;700;900&display=swap');
:root{
  --bg:#0a0c0e;--bg2:#111417;--bg3:#181c20;
  --border:#242830;--dim:#2e333b;
  --text:#c8cfd8;--muted:#4a5260;
  --accent:#e8a020;--green:#3ecf6a;--red:#e84040;--yellow:#e8c020;
  --mono:'Share Tech Mono',monospace;--sans:'Barlow Condensed',sans-serif;
}
*{box-sizing:border-box;margin:0;padding:0}
html,body{background:var(--bg);color:var(--text);font-family:var(--sans);min-height:100vh}
body{padding:28px 24px}
body::before{content:'';position:fixed;inset:0;pointer-events:none;z-index:0;
  background-image:repeating-linear-gradient(0deg,transparent,transparent 39px,var(--border) 39px,var(--border) 40px),
    repeating-linear-gradient(90deg,transparent,transparent 39px,var(--border) 39px,var(--border) 40px);
  background-size:40px 40px;opacity:.5}
.wrap{max-width:1280px;margin:0 auto;position:relative;z-index:1}

.header{display:flex;align-items:flex-end;justify-content:space-between;flex-wrap:wrap;gap:12px;
  border-bottom:2px solid var(--accent);padding-bottom:14px;margin-bottom:28px}
.header h1{font-weight:900;font-size:2.4rem;letter-spacing:.06em;text-transform:uppercase;color:#fff;line-height:1}
.header h1 span{color:var(--accent)}
.header .sub{font-family:var(--mono);font-size:.68rem;color:var(--muted);letter-spacing:.14em;margin-top:4px}
.ts{font-family:var(--mono);font-size:.72rem;color:var(--muted)}
.badges{display:flex;gap:8px;margin-top:6px;justify-content:flex-end}
.badge{background:var(--accent);color:#000;font-weight:700;font-size:.8rem;padding:2px 10px;letter-spacing:.08em}
.badge.oil{background:var(--bg3);color:var(--text);border:1px solid var(--dim)}

.legend-strip{display:flex;flex-wrap:wrap;gap:20px;margin-bottom:22px;
  background:var(--bg2);border:1px solid var(--border);padding:12px 18px}
.legend-item{display:flex;align-items:center;gap:8px;font-size:.88rem;font-weight:600;letter-spacing:.04em}
.leg-line{width:28px;height:3px;border-radius:2px}
.leg-line.dashed{background:repeating-linear-gradient(90deg,var(--c) 0,var(--c) 6px,transparent 6px,transparent 10px)}
.leg-desc{font-family:var(--mono);font-size:.6rem;color:var(--muted);margin-top:2px;max-width:220px}

.summary-row{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:12px;margin-bottom:26px}
.kpi{background:var(--bg2);border:1px solid var(--border);padding:14px 16px;border-top:2px solid var(--dim)}
.kpi.hi{border-top-color:var(--accent)}
.kpi-label{font-family:var(--mono);font-size:.6rem;letter-spacing:.16em;color:var(--muted);margin-bottom:8px}
.kpi-val{font-family:var(--mono);font-size:1.5rem;color:#fff}
.kpi-val.pos{color:var(--green)}.kpi-val.neg{color:var(--red)}
.kpi-sub{font-size:.75rem;color:var(--muted);margin-top:4px}

.tab-row{display:flex;gap:0;flex-wrap:wrap;border-bottom:1px solid var(--border)}
.tab{font-family:var(--sans);font-weight:600;font-size:.9rem;letter-spacing:.04em;
  padding:9px 18px;cursor:pointer;border:1px solid transparent;border-bottom:none;
  color:var(--muted);background:transparent;transition:all .15s;text-transform:uppercase}
.tab:hover{color:var(--text)}
.tab.active{background:var(--bg2);border-color:var(--border);color:#fff;position:relative;top:1px}
.tab-dot{display:inline-block;width:7px;height:7px;border-radius:50%;margin-right:6px;vertical-align:middle}

.panel{display:none;background:var(--bg2);border:1px solid var(--border);border-top:none;padding:22px}
.panel.active{display:block}

.chart-grid{display:grid;grid-template-columns:2fr 1fr 1fr;gap:14px;margin-bottom:14px}
@media(max-width:860px){.chart-grid{grid-template-columns:1fr}}
.chart-box{background:var(--bg3);border:1px solid var(--dim);padding:14px}
.chart-title{font-family:var(--mono);font-size:.6rem;letter-spacing:.14em;color:var(--muted);margin-bottom:10px;text-transform:uppercase}
.chart-box canvas{max-height:220px}

.strat-table{width:100%;border-collapse:collapse;font-family:var(--mono);font-size:.82rem;margin-bottom:14px}
.strat-table th{text-align:left;padding:6px 10px 8px;font-size:.6rem;letter-spacing:.14em;
  color:var(--muted);border-bottom:1px solid var(--dim)}
.strat-table td{padding:9px 10px;border-bottom:1px solid var(--bg3)}
.strat-table tr:last-child td{border-bottom:none}
.strat-table tr:hover td{background:rgba(255,255,255,.02)}
.sw{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:8px;vertical-align:middle}

.unreal-box{background:rgba(62,207,106,.06);border:1px solid rgba(62,207,106,.22);
  padding:14px 18px;margin-bottom:14px}
.unreal-title{font-family:var(--mono);font-size:.62rem;letter-spacing:.16em;color:#3ecf6a;
  margin-bottom:10px;text-transform:uppercase}
.unreal-row{display:flex;flex-wrap:wrap;gap:0}
.us{flex:1;min-width:120px;padding:8px 14px;border-right:1px solid rgba(62,207,106,.12)}
.us:last-child{border-right:none}
.us-label{font-family:var(--mono);font-size:.58rem;letter-spacing:.14em;color:var(--muted);margin-bottom:4px}
.us-val{font-family:var(--mono);font-size:1.05rem;color:#fff}
.us-val.pos{color:var(--green)}.us-val.neg{color:var(--red)}
.us-note{font-size:.72rem;color:var(--muted);line-height:1.4}

.ov-table{width:100%;border-collapse:collapse;font-family:var(--mono);font-size:.8rem}
.ov-table th{text-align:left;padding:8px 10px;font-size:.6rem;letter-spacing:.14em;
  color:var(--muted);border-bottom:1px solid var(--dim)}
.ov-table td{padding:9px 10px;border-bottom:1px solid var(--bg3)}
.ov-table tr:last-child td{border-bottom:none}
.ov-table tr:hover td{background:rgba(255,255,255,.02)}
.pos-val{color:var(--green)}.neg-val{color:var(--red)}.neu-val{color:var(--text)}

.footer{font-family:var(--mono);font-size:.6rem;color:var(--muted);
  border-top:1px solid var(--border);padding-top:14px;margin-top:8px;line-height:1.9}
</style>
</head>
<body>
<div class="wrap">

<div class="header">
  <div>
    <h1>WAR<span>TRADE</span> BACKTEST</h1>
    <div class="sub">MULTI-STRATEGY · OIL · EQUITY · CONFLICT TIMING</div>
  </div>
  <div style="text-align:right">
    <div class="ts" id="ts"></div>
    <div class="badges">
      <div class="badge" id="eqBadge">—</div>
      <div class="badge oil" id="oilBadge">—</div>
    </div>
  </div>
</div>

<div class="legend-strip" id="legendStrip"></div>
<div class="summary-row"  id="summaryRow"></div>
<div class="tab-row"      id="tabRow"></div>
<div id="panelArea"></div>

<div class="footer">
  DISCLAIMER · Research only — not financial advice. &nbsp;|&nbsp;
  <strong>OilWar Active</strong>: rebalances daily to drawdown/oil target (buys &amp; sells). &nbsp;|&nbsp;
  <strong>OilWar Buy-Only</strong>: accumulates shares on buy signals, never sells.
  <em>Unrealized value</em> = shares held × current price — what you'd receive if you sold today.
  Cash deployment chart shows % of initial capital committed over time. &nbsp;|&nbsp;
  <strong>Buy &amp; Hold</strong>: 100% invested from day one, passive benchmark. &nbsp;|&nbsp;
  All equity curves re-based to $100,000 at window start for fair comparison.
</div>
</div>

<script>
const EQUITY = "<<EQUITY>>";
const OIL    = "<<OIL>>";
const DATA   = <<DATA>>;
const BO_NAME = "OilWar Buy-Only";

document.getElementById('ts').textContent =
  new Date().toLocaleString('en-US',{weekday:'short',month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'});
document.getElementById('eqBadge').textContent  = EQUITY;
document.getElementById('oilBadge').textContent = OIL + ' (WTI)';

const pct = (v,d=1) => (v>=0?'+':'')+(v*100).toFixed(d)+'%';
const usd = v => '$'+Number(v).toLocaleString('en-US',{maximumFractionDigits:0});
const sgn = v => v >= 0 ? 'pos' : 'neg';

function baseChart() {
  return {
    responsive:true, maintainAspectRatio:false, animation:{duration:500},
    plugins:{
      legend:{labels:{color:'#5a6370',font:{family:"'Share Tech Mono'"},boxWidth:10}},
      tooltip:{backgroundColor:'#181c20',borderColor:'#2e333b',borderWidth:1,
               titleColor:'#fff',bodyColor:'#c8cfd8',
               callbacks:{label:ctx=>' $'+ctx.parsed.y.toLocaleString()}}
    },
    scales:{
      x:{type:'time',time:{unit:'month'},grid:{color:'#181c20'},
         ticks:{color:'#3a4048',font:{family:"'Share Tech Mono'",size:10}}},
      y:{grid:{color:'#1c2028'},
         ticks:{color:'#3a4048',font:{family:"'Share Tech Mono'",size:10},callback:v=>'$'+(v/1000).toFixed(0)+'k'}}
    }
  };
}
function pctChart() {
  return {
    responsive:true, maintainAspectRatio:false, animation:{duration:500},
    plugins:{legend:{display:false},
      tooltip:{backgroundColor:'#181c20',borderColor:'#2e333b',borderWidth:1,
               titleColor:'#fff',bodyColor:'#c8cfd8',
               callbacks:{label:ctx=>' '+(ctx.parsed.y*100).toFixed(0)+'%'}}},
    scales:{
      x:{type:'time',time:{unit:'month'},grid:{color:'#181c20'},
         ticks:{color:'#3a4048',font:{family:"'Share Tech Mono'",size:10}}},
      y:{min:0,max:1,grid:{color:'#1c2028'},
         ticks:{color:'#3a4048',font:{family:"'Share Tech Mono'",size:10},callback:v=>(v*100).toFixed(0)+'%'}}
    }
  };
}

// LEGEND
function buildLegend() {
  if (!DATA.length) return;
  const strip = document.getElementById('legendStrip');
  const descs = {
    'OilWar Active':   'Rebalances daily to target. Buys & sells based on drawdown + oil signals.',
    'OilWar Buy-Only': 'Buys at signals, never sells. Shows unrealized value if you sold today.',
    'Buy & Hold':      'Passive benchmark. 100% invested from day one.',
  };
  strip.innerHTML = DATA[0].strategies.map(s => {
    const dashed = s.name==='Buy & Hold';
    return `<div class="legend-item">
      <div>
        <div class="leg-line${dashed?' dashed':''}" style="${dashed?'--c:'+s.color+';':'background:'+s.color}"></div>
      </div>
      <div>
        <div>${s.name}</div>
        <div class="leg-desc">${descs[s.name]||''}</div>
      </div>
    </div>`;
  }).join('');
}

// SUMMARY KPIs
function buildSummary() {
  if (!DATA.length) return;
  const names = DATA[0].strategies.map(s=>s.name).filter(n=>n!=='Buy & Hold');
  const totRet={}, totAlpha={}, wins={};
  names.forEach(n=>{totRet[n]=0;totAlpha[n]=0;wins[n]=0;});

  DATA.forEach(ev=>{
    const byName={};
    ev.strategies.forEach(s=>byName[s.name]=s);
    const bhRet = byName['Buy & Hold']?.metrics?.return||0;
    names.forEach(n=>{
      const r = byName[n]?.metrics?.return||0;
      totRet[n]   += r;
      totAlpha[n] += r - bhRet;
      if (r - bhRet > 0) wins[n]++;
    });
  });

  const n = DATA.length;
  const row = document.getElementById('summaryRow');
  const kpis = [{label:'Events',val:n,fmt:v=>v,cls:'',sub:'conflict windows',hi:true}];
  names.forEach(name=>{
    kpis.push({label:name+' avg return',val:totRet[name]/n,fmt:v=>pct(v),cls:sgn(totRet[name]/n),sub:'across all windows'});
    kpis.push({label:name+' avg alpha', val:totAlpha[name]/n,fmt:v=>pct(v),cls:sgn(totAlpha[name]/n),sub:`vs B&H · ${wins[name]}/${n} wins`});
  });
  row.innerHTML = kpis.map(k=>`
    <div class="kpi${k.hi?' hi':''}">
      <div class="kpi-label">${k.label}</div>
      <div class="kpi-val ${k.cls}">${k.fmt(k.val)}</div>
      <div class="kpi-sub">${k.sub}</div>
    </div>`).join('');
}

// OVERVIEW TABLE
function buildOverview() {
  if (!DATA.length) return '';
  const strats = DATA[0].strategies.map(s=>s.name);
  const hdrs = strats.map(n=>`<th colspan="3">${n}</th>`).join('');
  const sub  = strats.map(()=>'<th>RTN</th><th>MDD</th><th>SHARPE</th>').join('');
  const rows = DATA.map(ev=>{
    const m={};
    ev.strategies.forEach(s=>m[s.name]=s);
    return `<tr>
      <td><span class="sw" style="background:${ev.color}"></span>${ev.name}</td>
      <td class="neu-val">${m[strats[0]]?.metrics?.days||0}d</td>
      ${strats.map(n=>{const r=m[n]?.metrics||{};return`
        <td class="${sgn(r.return||0)}-val">${pct(r.return||0)}</td>
        <td class="neg-val">${pct(r.max_dd||0)}</td>
        <td class="neu-val">${(r.sharpe||0).toFixed(2)}</td>`;}).join('')}
    </tr>`;
  }).join('');
  return `<table class="ov-table">
    <thead><tr><th rowspan="2">EVENT</th><th rowspan="2">DAYS</th>${hdrs}</tr>
    <tr>${sub}</tr></thead>
    <tbody>${rows}</tbody></table>`;
}

// EVENT PANEL
function buildEventPanel(ev) {
  const key = ev.name.replace(/\W/g,'_');
  const byName={};
  ev.strategies.forEach(s=>byName[s.name]=s);
  const bo  = byName[BO_NAME];
  const bom = bo?.metrics||{};

  const unrealBox = bo ? `
    <div class="unreal-box">
      <div class="unreal-title">🔒 ${BO_NAME} — Unrealized Position (sell anytime to realise)</div>
      <div class="unreal-row">
        <div class="us"><div class="us-label">FINAL UNREAL. VALUE</div>
          <div class="us-val">${usd(bom.final||0)}</div></div>
        <div class="us"><div class="us-label">UNREAL. RETURN</div>
          <div class="us-val ${sgn(bom.return||0)}">${pct(bom.return||0)}</div></div>
        <div class="us"><div class="us-label">MAX VALUE DRAWDOWN</div>
          <div class="us-val neg">${pct(bom.max_dd||0)}</div></div>
        <div class="us"><div class="us-label">SHARPE (UNREAL.)</div>
          <div class="us-val">${(bom.sharpe||0).toFixed(2)}</div></div>
        <div class="us"><div class="us-label">WHAT THIS MEANS</div>
          <div class="us-note">Shares accumulate at each buy signal and are never sold.
          The value shown is mark-to-market. You could sell at any time to realise this gain or loss.</div></div>
      </div>
    </div>` : '';

  const tRows = ev.strategies.map(s=>{
    const r=s.metrics||{};
    return `<tr>
      <td><span class="sw" style="background:${s.color}"></span>${s.name}</td>
      <td class="${sgn(r.return||0)}-val">${pct(r.return||0)}</td>
      <td class="neg-val">${pct(r.max_dd||0)}</td>
      <td class="neu-val">${(r.sharpe||0).toFixed(2)}</td>
      <td class="neu-val">${usd(r.final||0)}</td>
    </tr>`;
  }).join('');

  return `
    ${unrealBox}
    <table class="strat-table">
      <thead><tr>
        <th>STRATEGY</th><th>RETURN</th><th>MAX DRAWDOWN</th>
        <th>SHARPE</th><th>FINAL VALUE</th>
      </tr></thead>
      <tbody>${tRows}</tbody>
    </table>
    <div class="chart-grid">
      <div class="chart-box" style="height:240px">
        <div class="chart-title">Equity Curves — All Strategies</div>
        <canvas id="ceq_${key}"></canvas>
      </div>
      <div class="chart-box" style="height:240px">
        <div class="chart-title">OilWar Active — Daily Allocation %</div>
        <canvas id="cal_${key}"></canvas>
      </div>
      <div class="chart-box" style="height:240px">
        <div class="chart-title">Buy-Only — Cumulative Cash Deployed %</div>
        <canvas id="cdp_${key}"></canvas>
      </div>
    </div>`;
}

function initEventCharts(ev) {
  const key = ev.name.replace(/\W/g,'_');
  const byName={};
  ev.strategies.forEach(s=>byName[s.name]=s);

  // All-strategy equity chart
  const eqEl = document.getElementById('ceq_'+key);
  if (eqEl) {
    new Chart(eqEl, {type:'line', data:{datasets:ev.strategies.map(s=>({
      label:s.name, data:s.equity,
      borderColor:s.color,
      backgroundColor: s.name==='Buy & Hold' ? 'transparent' : s.color+'14',
      fill: s.name!=='Buy & Hold',
      borderWidth: s.name==='Buy & Hold' ? 1.5 : 2,
      borderDash: s.name==='Buy & Hold' ? [4,4] : [],
      pointRadius:0, tension:0.3
    }))}, options:baseChart()});
  }

  // OilWar Active — allocation bars
  const alEl = document.getElementById('cal_'+key);
  const act = byName['OilWar Active'];
  if (alEl && act) {
    new Chart(alEl, {type:'bar', data:{datasets:[{label:'Allocation',data:act.allocation,
      backgroundColor:act.color+'99',borderWidth:0,barPercentage:1,categoryPercentage:1}]},
      options:pctChart()});
  }

  // Buy-Only — cumulative deployment line
  const dpEl = document.getElementById('cdp_'+key);
  const bo = byName[BO_NAME];
  if (dpEl && bo) {
    new Chart(dpEl, {type:'line', data:{datasets:[{label:'Deployed %',data:bo.allocation,
      borderColor:bo.color, backgroundColor:bo.color+'22',
      fill:true, borderWidth:2, pointRadius:0, tension:0.4}]},
      options:pctChart()});
  }
}

function buildTabs() {
  const tabRow=document.getElementById('tabRow');
  const panelArea=document.getElementById('panelArea');

  const ovTab=document.createElement('div');
  ovTab.className='tab active'; ovTab.innerHTML='◈ Overview';
  ovTab.dataset.target='panel_overview'; tabRow.appendChild(ovTab);

  const ovPanel=document.createElement('div');
  ovPanel.className='panel active'; ovPanel.id='panel_overview';
  ovPanel.innerHTML=buildOverview(); panelArea.appendChild(ovPanel);

  DATA.forEach(ev=>{
    const id='panel_'+ev.name.replace(/\W/g,'_');
    const tab=document.createElement('div');
    tab.className='tab';
    tab.innerHTML=`<span class="tab-dot" style="background:${ev.color}"></span>${ev.name}`;
    tab.dataset.target=id; tabRow.appendChild(tab);

    const panel=document.createElement('div');
    panel.className='panel'; panel.id=id;
    panel.innerHTML=buildEventPanel(ev); panelArea.appendChild(panel);
  });

  tabRow.addEventListener('click',e=>{
    const tab=e.target.closest('.tab'); if(!tab) return;
    document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
    document.querySelectorAll('.panel').forEach(p=>p.classList.remove('active'));
    tab.classList.add('active');
    const panel=document.getElementById(tab.dataset.target);
    panel.classList.add('active');
    if(!panel.dataset.chartsReady){
      panel.dataset.chartsReady='1';
      const ev=DATA.find(x=>'panel_'+x.name.replace(/\W/g,'_')===tab.dataset.target);
      if(ev) initEventCharts(ev);
    }
  });
}

buildLegend();
buildSummary();
buildTabs();
</script>
</body>
</html>
"""

json_payload = json.dumps(payload, separators=(",", ":"))
html_out = (HTML
    .replace('"<<EQUITY>>"', json.dumps(EQUITY_TICKER))
    .replace('"<<OIL>>"',    json.dumps(OIL_TICKER))
    .replace("<<DATA>>",     json_payload)
)

with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
    f.write(html_out)

print(f"\n  Dashboard written → {OUTPUT_FILE}")
print(f"  Open in any browser — no server required.\n")
