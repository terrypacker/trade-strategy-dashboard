"""
War Trade Signal Dashboard  ·  dashboard.py
────────────────────────────────────────────────────────────────────────────
Fetches price data, runs every strategy in REGISTRY over a configurable
window, and writes signal_output.json for war_trade_dashboard.html.

The chart shows three zones on one continuous timeline:
  ① Pre-strategy context  — raw market data before you activated the strategy
  ② Strategy active       — portfolio value tracked from STRATEGY_START_DATE
  ③ Forecast              — Monte Carlo fan from today forward

Set STRATEGY_START_DATE to the date you started (or plan to start) using
the strategy.  Set PRE_HISTORY_DAYS to control how much market context
appears to the left of that line.

Usage:
    python3 dashboard.py
    # open war_trade_dashboard.html and load signal_output.json

Dependencies:
    pip install yfinance pandas numpy
"""

import dataclasses
import json
import sys
from datetime import datetime, timedelta

import pandas as pd
import yfinance as yf

from strategy import (
    REGISTRY,
    BuyOnlyOilWarStrategy,
    OilWarStrategy,
    Signals,
    signal_label,
    momentum_warnings,
    get_market_context,
    run_history,
    simulate_future,
)

# ─────────────────────────────────────────────────────────────────────────
# CONFIG  ← edit these values
# ─────────────────────────────────────────────────────────────────────────
EQUITY_TICKER   = "VTI"       # equity ticker (Yahoo Finance symbol)
OIL_TICKER      = "CL=F"      # oil ticker   (WTI futures)
INITIAL_CAPITAL = 100_000     # starting portfolio value ($)

# The date you activated (or intend to activate) the strategy.
# Set to None to use the rolling "last M_HISTORY_DAYS" mode instead.
STRATEGY_START_DATE = "2026-02-28"    # e.g. "2024-10-01"  or  None

# When STRATEGY_START_DATE is None, how many recent trading days to replay.
M_HISTORY_DAYS  = 60          # trading days of active strategy history

# How many trading days of raw market data to show BEFORE the strategy start.
PRE_HISTORY_DAYS = 60         # trading days of context before strategy start

# Projected war-end date.  Must be in the future (in the forecast zone).
# Oil rises from today's level toward OIL_WAR_PEAK_MULT × current price
# by war_end_date, then reverts to pre-war baseline over OIL_REVERT_DAYS.
# Set WAR_END_DATE to None for a plain random-walk forecast.
WAR_END_DATE      = "2026-4-2"   # e.g. "2025-09-01"  or  None
OIL_WAR_PEAK_MULT = 1.30   # oil rises to 130% of current price by war end
OIL_REVERT_DAYS   = 30     # trading days for oil to return to baseline after war ends

# Monte Carlo forecast settings.
# N_FORECAST is automatically extended to reach WAR_END_DATE + OIL_REVERT_DAYS.
N_FORECAST = 30            # minimum trading days to project forward
N_PATHS    = 500           # simulation paths (higher = smoother bands)

# ─────────────────────────────────────────────────────────────────────────
# Derived: how far back to fetch data
# ─────────────────────────────────────────────────────────────────────────
if STRATEGY_START_DATE:
    _start_dt = datetime.strptime(STRATEGY_START_DATE, "%Y-%m-%d")
    _days_since_start = (datetime.today() - _start_dt).days
    _total_trading_est = int((20 + PRE_HISTORY_DAYS + _days_since_start) * 1.6) + 30
else:
    _total_trading_est = int((20 + PRE_HISTORY_DAYS + 20 + M_HISTORY_DAYS) * 1.6) + 30

_FETCH_CALENDAR_DAYS = _total_trading_est

# ─────────────────────────────────────────────────────────────────────────
# FETCH
# ─────────────────────────────────────────────────────────────────────────
end_date   = datetime.today()
start_date = end_date - timedelta(days=_FETCH_CALENDAR_DAYS)

print(f"Fetching {EQUITY_TICKER} and {OIL_TICKER} "
      f"({_FETCH_CALENDAR_DAYS} calendar days back)…", flush=True)

raw_eq  = yf.download(EQUITY_TICKER, start=start_date, end=end_date, progress=False)
raw_oil = yf.download(OIL_TICKER,    start=start_date, end=end_date, progress=False)


def _to_series(raw: pd.DataFrame, ticker: str) -> pd.Series:
    """Flatten yfinance MultiIndex columns → plain Close Series."""
    if isinstance(raw.columns, pd.MultiIndex):
        return raw[("Close", ticker)].dropna()
    return raw["Close"].dropna()


eq  = _to_series(raw_eq,  EQUITY_TICKER)
oil = _to_series(raw_oil, OIL_TICKER)

min_needed = 20 + PRE_HISTORY_DAYS + 20
if len(eq) < min_needed or len(oil) < min_needed:
    print(f"ERROR: need ≥{min_needed} bars; "
          f"got eq={len(eq)}, oil={len(oil)}.", file=sys.stderr)
    sys.exit(1)

print(f"  {len(eq)} equity bars, {len(oil)} oil bars.", flush=True)

# ─────────────────────────────────────────────────────────────────────────
# Resolve the effective strategy start date
# ─────────────────────────────────────────────────────────────────────────
if STRATEGY_START_DATE:
    effective_start = STRATEGY_START_DATE
    print(f"  Strategy start date: {effective_start} (explicit)", flush=True)
else:
    combined = pd.DataFrame({"eq": eq, "oil": oil}).dropna()
    if len(combined) >= M_HISTORY_DAYS:
        effective_start = combined.index[-M_HISTORY_DAYS].strftime("%Y-%m-%d")
    else:
        effective_start = combined.index[0].strftime("%Y-%m-%d")
    print(f"  Strategy start date: {effective_start} (last {M_HISTORY_DAYS} bars)", flush=True)

# ─────────────────────────────────────────────────────────────────────────
# Oil baseline — the "pre-war" oil price at strategy start
# This is the reversion target when WAR_END_DATE is set.
# ─────────────────────────────────────────────────────────────────────────
combined_full = pd.DataFrame({"eq": eq, "oil": oil}).dropna()
start_ts      = pd.Timestamp(effective_start)
bars_at_start = combined_full[combined_full.index <= start_ts]
oil_baseline  = float(bars_at_start["oil"].iloc[-1]) if len(bars_at_start) > 0 else float(oil.iloc[0])
print(f"  Oil baseline (pre-war): ${oil_baseline:.2f}", flush=True)

# ─────────────────────────────────────────────────────────────────────────
# Extend N_FORECAST to cover WAR_END_DATE if it falls beyond N_FORECAST days
# ─────────────────────────────────────────────────────────────────────────
effective_forecast = N_FORECAST
if WAR_END_DATE:
    war_end_dt    = datetime.strptime(WAR_END_DATE, "%Y-%m-%d")
    today_dt      = datetime.today()
    calendar_days = (war_end_dt - today_dt).days
    if calendar_days <= 0:
        print(f"  WARNING: WAR_END_DATE {WAR_END_DATE} is in the past — ignoring.", flush=True)
        effective_war_end = None
    else:
        # Trading days to reach war_end_date + full OIL_REVERT_DAYS window + 10-day tail
        trading_days_to_end = max(int(calendar_days / 1.4), 1)
        effective_forecast  = max(N_FORECAST, trading_days_to_end + OIL_REVERT_DAYS + 10)
        effective_war_end   = WAR_END_DATE
        print(f"  War end date: {WAR_END_DATE}  oil reverts over {OIL_REVERT_DAYS}d  "
              f"→ forecast extended to {effective_forecast}d", flush=True)
else:
    effective_war_end = None

# ─────────────────────────────────────────────────────────────────────────
# TODAY'S LIVE SIGNAL  (OilWar Active — the actionable number)
# ─────────────────────────────────────────────────────────────────────────
P   = float(eq.iloc[-1])
H   = float(eq.iloc[-20:].max())
D   = (H - P) / H
O   = float(oil.iloc[-1])
O5  = float(oil.iloc[-5:].mean())
spk = (O - O5) / O5
P3  = float(eq.iloc[-4]) if len(eq) >= 4 else P
R3  = (P / P3) - 1 if P3 > 0 else 0.0

live_signals  = Signals(price=P, high_20d=H, drawdown=D,
                        return_3d=R3, oil_price=O, oil_5d_avg=O5, oil_spike=spk)
live_strat    = OilWarStrategy()
live_alloc    = live_strat.next_allocation(live_signals)
live_label    = signal_label(live_alloc)
live_warnings = momentum_warnings(live_signals)

# ─────────────────────────────────────────────────────────────────────────
# PRE-STRATEGY CONTEXT  (shared across all strategies — same raw market)
# ─────────────────────────────────────────────────────────────────────────
print(f"  Context:  pre-strategy market ({PRE_HISTORY_DAYS} bars before {effective_start})…",
      flush=True)
context_bars = get_market_context(
    eq_series           = eq,
    oil_series          = oil,
    strategy_start_date = effective_start,
    pre_days            = PRE_HISTORY_DAYS,
)
print(f"    ✓  {len(context_bars)} context bars "
      f"({context_bars[0].date if context_bars else 'n/a'} → "
      f"{context_bars[-1].date if context_bars else 'n/a'})", flush=True)

# ─────────────────────────────────────────────────────────────────────────
# STRATEGY HISTORY + FORECAST (one pass per strategy)
# run_history:     (eq, oil, strategy, initial_capital, history_days, start_date) → list[HistoricalBar]
# simulate_future: (eq, oil, strategy, last_bar, forecast_days, n_paths, ...) → ForecastPath
# ─────────────────────────────────────────────────────────────────────────
strategies_output = []

for strat in REGISTRY:
    print(f"  History:  {strat.name}…", flush=True)
    bars = run_history(
        eq_series       = eq,
        oil_series      = oil,
        strategy        = strat,
        initial_capital = INITIAL_CAPITAL,
        history_days    = M_HISTORY_DAYS,   # ignored when start_date is given
        start_date      = effective_start,
    )

    if not bars:
        print(f"    WARNING: no bars returned, skipping {strat.name}.")
        continue

    print(f"    ✓  {len(bars)} bars  "
          f"({bars[0].date} → {bars[-1].date})  "
          f"pv=${bars[-1].portfolio_value:,.0f}", flush=True)

    print(f"  Forecast: {strat.name} ({N_PATHS} paths × {effective_forecast} days)…", flush=True)

    fc_strat = (BuyOnlyOilWarStrategy()
                if isinstance(strat, BuyOnlyOilWarStrategy)
                else type(strat)())

    fc = simulate_future(
        eq_series               = eq,
        oil_series              = oil,
        strategy                = fc_strat,
        last_bar                = bars[-1],
        forecast_days           = effective_forecast,
        n_paths                 = N_PATHS,
        initial_capital         = INITIAL_CAPITAL,
        war_end_date            = effective_war_end,
        oil_baseline_price      = oil_baseline,
        oil_war_peak_price      = round(bars[-1].oil_price * OIL_WAR_PEAK_MULT, 2)
                                  if effective_war_end else None,
        oil_revert_trading_days = OIL_REVERT_DAYS,
    )

    strategies_output.append({
        "name":     strat.name,
        "color":    strat.color,
        "history":  [dataclasses.asdict(b) for b in bars],
        "forecast": dataclasses.asdict(fc),
    })
    print(f"    ✓  forecast p50 end=${fc.p50[-1]:,.0f}", flush=True)

# ─────────────────────────────────────────────────────────────────────────
# TERMINAL SUMMARY
# ─────────────────────────────────────────────────────────────────────────
LINE = "─" * 46
print(f"\n{LINE}")
print(f"  WAR TRADE SIGNAL  [{live_strat.name}]")
print(f"  {datetime.today().strftime('%Y-%m-%d %H:%M')}")
print(LINE)
print(f"  {EQUITY_TICKER:<6} Price        : ${P:>9.2f}")
print(f"  20-day High       : ${H:>9.2f}")
print(f"  Drawdown          :  {D*100:>7.2f}%")
print(LINE)
print(f"  WTI Oil (CL=F)    : ${O:>9.2f}")
print(f"  Oil 5d Avg        : ${O5:>9.2f}")
print(f"  Oil Spike (5d)    :  {spk*100:>7.2f}%")
print(LINE)
print(f"  3-day Equity Rtn  :  {R3*100:>7.2f}%")
print(LINE)
print(f"  ► TODAY'S SIGNAL  :  {live_label}")
print(f"  ► ALLOCATION      :  {live_alloc*100:.0f}%")
print(LINE)
for w in live_warnings:
    print(f"  {w}")
if live_warnings:
    print(LINE)

# ─────────────────────────────────────────────────────────────────────────
# JSON EXPORT
# ─────────────────────────────────────────────────────────────────────────
output = {
    "generated":          datetime.today().strftime("%Y-%m-%d %H:%M"),
    "ticker":             EQUITY_TICKER,
    "oil_ticker":         OIL_TICKER,
    "strategy_start":     effective_start,
    "pre_history_days":   PRE_HISTORY_DAYS,
    "forecast_days":      effective_forecast,
    "initial_capital":    INITIAL_CAPITAL,
    "war_end_date":       effective_war_end,
    "oil_baseline":       round(oil_baseline, 2),
    "oil_war_peak":       round(bars[-1].oil_price * OIL_WAR_PEAK_MULT, 2)
                          if effective_war_end else None,
    "oil_revert_days":    OIL_REVERT_DAYS,
    # Live point-in-time snapshot (OilWar Active)
    "live": {
        "date":       datetime.today().strftime("%Y-%m-%d"),
        "strategy":   live_strat.name,
        "price":      round(P,   2),
        "high_20d":   round(H,   2),
        "drawdown":   round(D,   4),
        "oil_price":  round(O,   2),
        "oil_5d_avg": round(O5,  2),
        "oil_spike":  round(spk, 4),
        "return_3d":  round(R3,  4),
        "allocation": round(live_alloc, 2),
        "signal":     live_label,
        "warnings":   live_warnings,
    },
    # Pre-strategy raw market context (same for all strategies)
    "context": [dataclasses.asdict(b) for b in context_bars],
    # Per-strategy: history (portfolio) + forecast
    "strategies": strategies_output,
}

with open("signal_output.json", "w") as f:
    json.dump(output, f, indent=2)

kb = len(json.dumps(output)) // 1024
print(f"\n  Saved → signal_output.json  ({kb} KB)")
print(f"  Zones: {len(context_bars)} context + "
      f"{len(strategies_output[0]['history']) if strategies_output else 0} strategy + "
      f"{N_FORECAST} forecast days")
print(f"  Load in war_trade_dashboard.html\n")
