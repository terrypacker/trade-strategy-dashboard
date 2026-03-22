"""
War Trade Signal Dashboard  ·  dashboard.py
────────────────────────────────────────────────────────────────────────────
Fetches recent price data, replays the last M_HISTORY trading days of
signals through every strategy in REGISTRY, projects N_FORECAST days
forward via Monte Carlo, then writes signal_output.json.

The JSON is consumed by war_trade_dashboard.html which shows:
  - One sidebar tab per strategy
  - History: equity curve + allocation timeline for past M_HISTORY bars
  - Forecast: p10/p25/p50/p75/p90 fan chart for next N_FORECAST trading days
  - Today's live signal snapshot for the active OilWar strategy

Usage:
    python3 dashboard.py
    # then open war_trade_dashboard.html and load signal_output.json

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
    run_history,
    simulate_future,
)

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────
EQUITY_TICKER   = "VTI"      # swap for SWTSX, SPY, etc.
OIL_TICKER      = "CL=F"     # WTI crude futures
INITIAL_CAPITAL = 100_000

M_HISTORY  = 60    # trading days of history to replay per strategy
N_FORECAST = 30    # trading days to project forward via Monte Carlo
N_PATHS    = 500   # Monte Carlo paths (higher = smoother percentile bands)

# Calendar days to fetch: (20 warm-up + M_HISTORY) trading days ≈ × 1.6 calendar days
_FETCH_CALENDAR_DAYS = int((20 + M_HISTORY) * 1.6) + 10

# ─────────────────────────────────────────────
# FETCH
# ─────────────────────────────────────────────
end_date   = datetime.today()
start_date = end_date - timedelta(days=_FETCH_CALENDAR_DAYS)

print(f"Fetching {EQUITY_TICKER} and {OIL_TICKER} "
      f"({_FETCH_CALENDAR_DAYS} calendar days)…", flush=True)

raw_eq  = yf.download(EQUITY_TICKER, start=start_date, end=end_date, progress=False)
raw_oil = yf.download(OIL_TICKER,    start=start_date, end=end_date, progress=False)


def _to_series(raw: pd.DataFrame, ticker: str) -> pd.Series:
    """Flatten yfinance MultiIndex columns → plain Close Series."""
    if isinstance(raw.columns, pd.MultiIndex):
        return raw[("Close", ticker)].dropna()
    return raw["Close"].dropna()


eq  = _to_series(raw_eq,  EQUITY_TICKER)
oil = _to_series(raw_oil, OIL_TICKER)

min_needed = 20 + M_HISTORY
if len(eq) < min_needed or len(oil) < min_needed:
    print(f"ERROR: need ≥{min_needed} bars; "
          f"got eq={len(eq)}, oil={len(oil)}.", file=sys.stderr)
    sys.exit(1)

print(f"  {len(eq)} equity bars, {len(oil)} oil bars.", flush=True)

# ─────────────────────────────────────────────
# TODAY'S LIVE SIGNAL  (OilWar Active — the actionable number)
# ─────────────────────────────────────────────
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

# ─────────────────────────────────────────────
# RUN HISTORY + FORECAST FOR EVERY STRATEGY
# run_history(eq, oil, strategy, initial_capital, history_days) → list[HistoricalBar]
# simulate_future(eq, oil, strategy, last_bar, forecast_days, n_paths, seed, initial_capital) → ForecastPath
# ─────────────────────────────────────────────
strategies_output = []

for strat in REGISTRY:
    print(f"  History:  {strat.name}…", flush=True)
    bars = run_history(
        eq_series       = eq,
        oil_series      = oil,
        strategy        = strat,
        initial_capital = INITIAL_CAPITAL,
        history_days    = M_HISTORY,
    )

    if not bars:
        print(f"    WARNING: no bars returned, skipping {strat.name}.")
        continue

    print(f"  Forecast: {strat.name} "
          f"({N_PATHS} paths × {N_FORECAST} days)…", flush=True)

    # simulate_future needs a fresh strategy instance (run_history mutated state)
    fc_strat = (BuyOnlyOilWarStrategy()
                if isinstance(strat, BuyOnlyOilWarStrategy)
                else type(strat)())

    fc = simulate_future(
        eq_series       = eq,
        oil_series      = oil,
        strategy        = fc_strat,
        last_bar        = bars[-1],
        forecast_days   = N_FORECAST,
        n_paths         = N_PATHS,
        initial_capital = INITIAL_CAPITAL,
    )

    strategies_output.append({
        "name":     strat.name,
        "color":    strat.color,
        # list[HistoricalBar] → list[dict] via dataclasses.asdict
        "history":  [dataclasses.asdict(b) for b in bars],
        # ForecastPath → dict
        "forecast": dataclasses.asdict(fc),
    })

    last_equity = bars[-1].equity
    print(f"    ✓  last equity=${last_equity:,.0f}  "
          f"forecast p50 end=${fc.p50[-1]:,.0f}", flush=True)

# ─────────────────────────────────────────────
# TERMINAL SUMMARY
# ─────────────────────────────────────────────
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

# ─────────────────────────────────────────────
# JSON EXPORT
# ─────────────────────────────────────────────
output = {
    "generated":       datetime.today().strftime("%Y-%m-%d %H:%M"),
    "ticker":          EQUITY_TICKER,
    "oil_ticker":      OIL_TICKER,
    "history_days":    M_HISTORY,
    "forecast_days":   N_FORECAST,
    "initial_capital": INITIAL_CAPITAL,
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
    # One entry per strategy with full history + forecast
    "strategies": strategies_output,
}

with open("signal_output.json", "w") as f:
    json.dump(output, f, indent=2)

kb = len(json.dumps(output)) // 1024
print(f"\n  Saved → signal_output.json  ({kb} KB)")
print(f"  Load it in war_trade_dashboard.html\n")
