"""
War Trade Backtest  ·  backtest.py
────────────────────────────────────────────────────────────────────────────
Runs every strategy in strategy.REGISTRY across all configured conflict
windows, then writes a self-contained HTML dashboard to backtest_report.html.

HTML/CSS/JS lives in report_template.html — edit that file to change the
dashboard appearance without touching any Python logic.

Usage:
    python3 backtest.py

Dependencies:
    pip install yfinance pandas numpy
"""

import json
import math
import os
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

# ═══════════════════════════════════════════════════════════════════════════
# WAR NARRATIVES
# Each entry: summary paragraph + list of market trigger events
# "date" is approximate ISO date for chart annotation reference
# ═══════════════════════════════════════════════════════════════════════════
EVENT_NARRATIVES = {
    "Gulf War 1990": {
        "summary": (
            "Iraq invaded Kuwait on 2 Aug 1990, triggering an immediate oil shock as markets priced in "
            "the loss of Kuwaiti and potential Saudi supply. The UN authorised force in Nov 1990; "
            "Operation Desert Storm launched 17 Jan 1991 and achieved a rapid ceasefire by 28 Feb 1991. "
            "Equities initially sold off sharply on invasion uncertainty, then reversed strongly once "
            "the air campaign's swift progress became clear — one of the sharpest V-recoveries of the era."
        ),
        "triggers": [
            {"date": "1990-08-02", "label": "Iraq invades Kuwait",
             "detail": "Oil spikes ~40% in weeks. Equity drawdown begins — strategy starts accumulating at dip thresholds."},
            {"date": "1990-10-11", "label": "Market bottom",
             "detail": "S&P 500 hits trough (~−20% from pre-war high). Large drawdown signals max allocation for OilWar Active."},
            {"date": "1991-01-17", "label": "Desert Storm begins",
             "detail": "Rapid air campaign success — oil collapses back, equity surges. OilWar Active reduces allocation as drawdown heals."},
            {"date": "1991-02-28", "label": "Ceasefire declared",
             "detail": "Oil normalises. Equity in full recovery. Buy-Only holders sitting on unrealised gains from dip purchases."},
        ],
    },
    "Iraq War 2003": {
        "summary": (
            "Diplomatic pressure and troop build-up dominated late 2002, keeping oil elevated and "
            "equity markets depressed — still nursing the dot-com bust. The US-led invasion launched "
            "20 Mar 2003; Baghdad fell 9 Apr 2003. Equity markets bottomed in mid-March just before "
            "the invasion and then rallied sharply as the regime collapsed faster than feared. "
            "Oil spiked pre-war then sold off hard on swift victory, following the classic 'buy the rumour, "
            "sell the news' pattern."
        ),
        "triggers": [
            {"date": "2002-11-01", "label": "Window opens — war premium builds",
             "detail": "Oil drifts up on war rhetoric; equity still in post-dot-com malaise. Shallow drawdowns keep allocation modest."},
            {"date": "2003-02-05", "label": "Powell's UN presentation",
             "detail": "War certainty rises. Oil spikes toward $38. Equity sells off — drawdown signals increase allocation."},
            {"date": "2003-03-12", "label": "Equity trough",
             "detail": "Markets hit multi-year low just before invasion. Maximum drawdown triggers full OilWar allocation."},
            {"date": "2003-03-20", "label": "Invasion begins",
             "detail": "Oil briefly spikes then collapses as rapid advance unfolds. Equity begins strong multi-month rally."},
            {"date": "2003-04-09", "label": "Baghdad falls",
             "detail": "War premium fully unwound. Oil back below $28. Equity up sharply — allocated positions now profitable."},
        ],
    },
    "Libya 2011": {
        "summary": (
            "The Arab Spring reached Libya in Feb 2011. As civil war erupted, Libya's 1.6 mbpd output "
            "fell to near zero, sending Brent above $120. NATO began air strikes in Mar 2011. "
            "Equity markets were broadly resilient — global growth remained intact — making this a "
            "case where oil spiked but stocks did not fall commensurately, challenging pure oil-signal strategies. "
            "Gaddafi was killed in Oct 2011 and production began recovering."
        ),
        "triggers": [
            {"date": "2011-02-17", "label": "Libya uprising begins",
             "detail": "Oil jumps immediately on supply outage fears. Equity dips briefly but recovers — drawdown signals stay shallow."},
            {"date": "2011-03-19", "label": "NATO air strikes begin",
             "detail": "Oil sustains elevated levels above $110. Moderate equity drawdown generates partial allocation signals."},
            {"date": "2011-06-23", "label": "IEA releases strategic reserves",
             "detail": "Oil drops ~5% in one day — largest single-day fall since 2008. De-escalation signal; OilWar Active reduces exposure."},
            {"date": "2011-08-08", "label": "Global equity sell-off (US debt downgrade)",
             "detail": "Unrelated shock — S&amp;P downgrades US debt. Deep drawdown triggers maximum allocation signals."},
            {"date": "2011-10-20", "label": "Gaddafi killed, war ends",
             "detail": "Libya production begins recovery. Oil drifts lower. Equity stabilises after volatile summer."},
        ],
    },
    "ISIS Surge 2014": {
        "summary": (
            "ISIS swept across northern Iraq in Jun 2014, briefly threatening Erbil and Baghdad. "
            "Paradoxically, oil fell sharply through this window as surging US shale production "
            "overwhelmed any supply-risk premium. OPEC's Nov 2014 decision to maintain output "
            "accelerated the collapse — WTI fell from ~$105 in Jun to below $60 by Dec 2014. "
            "Equities remained broadly positive through mid-year before a brief correction, "
            "creating an unusual environment where oil-spike logic worked in reverse."
        ),
        "triggers": [
            {"date": "2014-06-10", "label": "ISIS captures Mosul",
             "detail": "Brief oil spike to $107 on supply-disruption fears. Equity dips — moderate allocation signal triggered."},
            {"date": "2014-06-19", "label": "Oil spike fades",
             "detail": "Markets conclude southern Iraqi fields are safe. Oil reverses. OilWar Active reduces allocation as oil spike vanishes."},
            {"date": "2014-09-18", "label": "US airstrikes begin",
             "detail": "Military escalation fails to lift oil — shale supply narrative dominates. Equity at highs, drawdown near zero."},
            {"date": "2014-11-27", "label": "OPEC holds output — oil collapses",
             "detail": "WTI drops through $70. Equity volatility picks up. Buy-Only strategy has limited deployed capital in this window."},
            {"date": "2014-12-31", "label": "Window closes",
             "detail": "Oil ended near $55, down ~45% from June peak. Unusual window where oil-war signal was persistently misleading."},
        ],
    },
    "Tanker Attacks 2019": {
        "summary": (
            "Escalating US–Iran tensions through 2019 produced several oil-market shocks. "
            "Iranian-linked attacks on Saudi Aramco facilities on 14 Sep 2019 briefly knocked out "
            "~5% of global oil supply — the largest single supply disruption since the Gulf War — "
            "sending oil up ~15% overnight. Equities were largely unaffected as the disruption "
            "proved temporary, but the short, sharp spikes created clear strategy trigger windows."
        ),
        "triggers": [
            {"date": "2019-05-12", "label": "Tanker sabotage near UAE",
             "detail": "First attack on oil infrastructure. Modest oil spike; equity unaffected. Allocation signal remains low."},
            {"date": "2019-06-13", "label": "Gulf of Oman tanker attacks",
             "detail": "Oil jumps ~4%. Risk-off briefly weighs on equity. Minor drawdown signal for OilWar Active."},
            {"date": "2019-07-18", "label": "Iran seizes UK tanker",
             "detail": "Geopolitical escalation. Oil spikes, equity pulls back. Moderate allocation signal generated."},
            {"date": "2019-09-14", "label": "Aramco attack — massive oil spike",
             "detail": "Oil gaps up ~15% at open — largest spike in the dataset. OilWar Active hits extreme-spike dampening (×0.70 modifier). Buy-Only accumulates heavily."},
            {"date": "2019-09-17", "label": "Oil reverses as supply restored",
             "detail": "Saudi assurances of rapid restoration. Oil gives back half the spike within days. De-escalation reduces allocation."},
        ],
    },
    "Ukraine 2022": {
        "summary": (
            "Russia invaded Ukraine on 24 Feb 2022 after months of build-up visible in satellite imagery "
            "and intelligence assessments. Markets had been selling off since Jan 2022 on Fed rate-hike fears, "
            "so the invasion compounded an existing drawdown. Oil rocketed to $130 in early March — "
            "a 14-year high — as Europe scrambled to source alternatives to Russian supply. "
            "Equity continued falling through June as inflation surged. This window combines a deep, "
            "sustained equity drawdown with persistent oil elevation — the ideal environment for "
            "the OilWar strategies."
        ),
        "triggers": [
            {"date": "2022-01-24", "label": "Fed hawkish pivot — equity sell-off begins",
             "detail": "Rate-hike fears drive initial drawdown before the war. OilWar Active begins accumulating on equity weakness alone."},
            {"date": "2022-02-24", "label": "Russia invades Ukraine",
             "detail": "Oil gaps up. Equity drops sharply. Drawdown + oil spike combine to trigger maximum allocation signals across both strategies."},
            {"date": "2022-03-07", "label": "Oil peaks near $130",
             "detail": "Extreme oil spike modifier (×0.70) dampens OilWar Active. Buy-Only continues accumulating at depressed equity prices."},
            {"date": "2022-03-16", "label": "Fed raises rates 25bp — equity relief rally",
             "detail": "Stocks bounce briefly. Drawdown eases. OilWar Active partially reduces allocation."},
            {"date": "2022-06-16", "label": "Equity trough for the window",
             "detail": "Cumulative drawdown reaches ~−23% from pre-war high. Strategies remain heavily allocated throughout the decline."},
            {"date": "2022-09-01", "label": "Window closes",
             "detail": "Oil still elevated, equity still below pre-war levels. Buy-Only unrealised position reflects full drawdown on accumulated shares."},
        ],
    },
    "Gaza Oct 2023": {
        "summary": (
            "Hamas launched a large-scale attack on Israel on 7 Oct 2023. Israel declared war and began "
            "a ground offensive into Gaza. Regional escalation fears briefly lifted oil above $95, "
            "but supply was never physically threatened — Iran and Houthi actions in the Red Sea "
            "created shipping disruption rather than oil outage. Equity markets dipped briefly then "
            "resumed a strong rally into year-end 2023 driven by AI enthusiasm and Fed pivot hopes, "
            "making this a case where the geopolitical signal faded quickly against a powerful macro tailwind."
        ),
        "triggers": [
            {"date": "2023-10-07", "label": "Hamas attack on Israel",
             "detail": "Oil spikes ~4%, equity sells off sharply. Drawdown signals activate OilWar strategies immediately."},
            {"date": "2023-10-19", "label": "Hospital explosion — escalation fears peak",
             "detail": "Regional war premium highest. Oil near $95. Maximum drawdown signal in the window — full allocation triggered."},
            {"date": "2023-11-01", "label": "Equity recovery begins",
             "detail": "Fed signals rate-hike pause. Equity rallies hard. Drawdown heals quickly — OilWar Active reduces allocation."},
            {"date": "2023-12-19", "label": "Fed pivot — equity surge",
             "detail": "Dovish Fed projections drive strong year-end rally. Oil falls below $75. Low drawdown + falling oil = minimal allocation signal."},
            {"date": "2024-01-12", "label": "US/UK strike Houthi targets",
             "detail": "Red Sea shipping disruption escalates. Brief oil bounce. Equity unaffected — allocation signals remain subdued."},
            {"date": "2024-04-01", "label": "Window closes",
             "detail": "Equity significantly above Oct 2023 levels. Oil ~$85. Strategies that bought the Oct dip captured a strong recovery."},
        ],
    },
}

OUTPUT_FILE   = "backtest_report.html"
TEMPLATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "report_template.html")

# Local cache directory.  Raw Close-price CSVs are saved here on first run
# and read directly on subsequent runs — no network call needed.
# Pass --refresh on the CLI to force a fresh download regardless.
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

# ═══════════════════════════════════════════════════════════════════════════
# CLI FLAGS
# ═══════════════════════════════════════════════════════════════════════════

FORCE_REFRESH = "--refresh" in sys.argv

# ═══════════════════════════════════════════════════════════════════════════
# DATA FETCHING  (cache-aware)
# ═══════════════════════════════════════════════════════════════════════════

def _cache_path(ticker: str, start: str, end: str) -> str:
    """Return the CSV path for this ticker + date range."""
    safe = ticker.replace("=", "_").replace("/", "_")
    return os.path.join(DATA_DIR, f"{safe}__{start}__{end}.csv")


def fetch(ticker: str, start: str, end: str) -> pd.Series:
    """
    Return Close prices for ticker over [start, end].

    On first call the data is downloaded from yfinance and saved to
    DATA_DIR/<ticker>__<start>__<end>.csv.  Subsequent calls read the
    local file directly, skipping the network entirely.

    Pass --refresh on the command line to force a fresh download.
    """
    os.makedirs(DATA_DIR, exist_ok=True)
    cache = _cache_path(ticker, start, end)

    if os.path.exists(cache) and not FORCE_REFRESH:
        series = pd.read_csv(cache, index_col=0, parse_dates=True).squeeze("columns")
        series.name = ticker
        return series.dropna()

    # Cache miss (or --refresh) — download and save
    raw = yf.download(ticker, start=start, end=end, progress=False, auto_adjust=True)
    if raw.empty:
        raise ValueError(f"No data for {ticker} [{start} → {end}]")
    if isinstance(raw.columns, pd.MultiIndex):
        series = raw[("Close", ticker)].dropna()
    else:
        series = raw["Close"].dropna()

    series.to_csv(cache)
    return series


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

    Returns a dict with:
      "strategies"  — keyed by strategy.name, each containing pd.Series for:
                        equity, allocation, unrealized, cash_deployed
      "stock_price" — raw equity Close prices over [bt_start, bt_end]
      "oil_price"   — raw oil Close prices over [bt_start, bt_end]
    """
    eq  = fetch(EQUITY_TICKER, fetch_start, bt_end)
    oil = fetch(OIL_TICKER,    fetch_start, bt_end)

    df = pd.DataFrame({"eq": eq, "oil": oil}).dropna()
    df["eq_ret"] = df["eq"].pct_change().fillna(0)

    bt_start_dt = pd.Timestamp(bt_start)
    n = len(df)

    ec:  dict[str, list] = {}
    ac:  dict[str, list] = {}
    uc:  dict[str, list] = {}
    cdc: dict[str, list] = {}

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
                alloc   = strat.next_allocation(sigs)
                new_val = ec[strat.name][-1] * (1.0 + alloc * eq_ret)

                ac[strat.name].append(alloc)
                ec[strat.name].append(new_val)
                uc[strat.name].append(new_val)
                cdc[strat.name].append(float("nan"))

            strat.on_bar(sigs)

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

    # Raw price series sliced to the backtest window (for the price chart)
    stock_window = eq[df.index >= bt_start_dt]
    oil_window   = oil[df.index >= bt_start_dt]

    return {
        "strategies":  results,
        "stock_price": stock_window,
        "oil_price":   oil_window,
    }


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

os.makedirs(DATA_DIR, exist_ok=True)

print("=" * 64)
print(f"  WAR TRADE BACKTEST  ·  {datetime.today().strftime('%Y-%m-%d')}")
print(f"  Equity: {EQUITY_TICKER}   Oil: {OIL_TICKER}")
print(f"  Strategies: {', '.join(s.name for s in REGISTRY)}")
print(f"  Data cache: {DATA_DIR}")
if FORCE_REFRESH:
    print("  Mode: FORCE REFRESH — all data will be re-downloaded")
else:
    print("  Mode: cache-first (pass --refresh to force re-download)")
print("=" * 64)

payload = []

for event_name, (fetch_start, bt_start, bt_end, color) in EVENTS.items():
    # Check cache status for this window before running
    eq_cached  = os.path.exists(_cache_path(EQUITY_TICKER, fetch_start, bt_end))
    oil_cached = os.path.exists(_cache_path(OIL_TICKER,    fetch_start, bt_end))
    cache_tag  = "cached" if (eq_cached and oil_cached and not FORCE_REFRESH) else "downloading"
    print(f"\n  ▶ {event_name}  [{bt_start} → {bt_end}]  [{cache_tag}]", flush=True)
    try:
        run = run_backtest(fetch_start, bt_start, bt_end)

        strats_out = []
        for strat in REGISTRY:
            r    = run["strategies"][strat.name]
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

        narrative = EVENT_NARRATIVES.get(event_name, {})
        payload.append({
            "name":             event_name,
            "color":            color,
            "strategies":       strats_out,
            # Raw price series for the Oil & Stock price chart
            "stock_price":      _to_pts(run["stock_price"]),
            "oil_price_series": _to_pts(run["oil_price"]),
            # War narrative and market trigger annotations
            "summary":          narrative.get("summary", ""),
            "triggers":         narrative.get("triggers", []),
        })

    except Exception as exc:
        print(f"     ✗ SKIPPED — {exc}")

print(f"\n  {len(payload)} events completed.")

# Cache inventory summary
cached_files = sorted(os.listdir(DATA_DIR)) if os.path.isdir(DATA_DIR) else []
print(f"  Cache directory: {DATA_DIR}  ({len(cached_files)} file(s))")
for f in cached_files:
    fpath = os.path.join(DATA_DIR, f)
    kb = os.path.getsize(fpath) / 1024
    print(f"    {f}  ({kb:.0f} KB)")


# ═══════════════════════════════════════════════════════════════════════════
# HTML DASHBOARD  (self-contained, no server required)
# ═══════════════════════════════════════════════════════════════════════════

with open(TEMPLATE_FILE, "r", encoding="utf-8") as f:
    HTML = f.read()

json_payload = json.dumps(payload, separators=(",", ":"))
html_out = (HTML
    .replace('"<<EQUITY>>"', json.dumps(EQUITY_TICKER))
    .replace('"<<OIL>>"',    json.dumps(OIL_TICKER))
    .replace("<<DATA>>",     json_payload)
)

with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
    f.write(html_out)

print(f"\n  Dashboard written → {OUTPUT_FILE}")
print(f"  Template loaded from → {TEMPLATE_FILE}")
print(f"  Open in any browser — no server required.\n")