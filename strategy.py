"""
strategy.py  ·  War Trade Strategy Library
────────────────────────────────────────────────────────────────────────────
Defines the Strategy base class, all concrete implementations, and the
history/forecast engine used by dashboard.py.

Adding a new strategy:
  1. Subclass Strategy
  2. Implement next_allocation(signals) → float in [0, 1]
  3. Add an instance to REGISTRY
"""

from __future__ import annotations
from dataclasses import dataclass


# ═══════════════════════════════════════════════════════════════════════════
# SIGNALS
# ═══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class Signals:
    """Read-only market context passed to every strategy on each bar."""
    price:      float   # equity close
    high_20d:   float   # 20-day rolling high
    drawdown:   float   # (high_20d - price) / high_20d
    return_3d:  float   # (price / price_3d_ago) - 1
    oil_price:  float   # oil close
    oil_5d_avg: float   # 5-day rolling oil mean
    oil_spike:  float   # (oil_price - oil_5d_avg) / oil_5d_avg


# ═══════════════════════════════════════════════════════════════════════════
# BASE STRATEGY
# ═══════════════════════════════════════════════════════════════════════════

class Strategy:
    """Abstract base. Subclasses must implement next_allocation(signals)."""
    name:  str = "Unnamed Strategy"
    color: str = "#888888"

    def next_allocation(self, signals: Signals) -> float:
        raise NotImplementedError

    def reset(self) -> None:
        pass

    def on_bar(self, signals: Signals) -> None:
        pass


# ═══════════════════════════════════════════════════════════════════════════
# SHARED RULE PRIMITIVES
# ═══════════════════════════════════════════════════════════════════════════

def _base_allocation(drawdown: float) -> float:
    if   drawdown < 0.03: return 0.00
    elif drawdown < 0.05: return 0.25
    elif drawdown < 0.07: return 0.50
    elif drawdown < 0.10: return 0.75
    else:                 return 1.00


def _oil_modifier(alloc: float, oil_spike: float) -> float:
    if   oil_spike < 0.05: alloc *= 0.50
    elif oil_spike < 0.10: pass
    elif oil_spike < 0.20: alloc = min(alloc * 1.20, 1.0)
    else:                  alloc *= 0.70
    return min(alloc, 1.0)


def _momentum_boost(alloc: float, return_3d: float, oil_spike: float) -> float:
    if return_3d > 0.015 and oil_spike < 0.10:
        alloc = min(alloc + 0.25, 1.0)
    return alloc


def compute_target_allocation(signals: Signals) -> float:
    alloc = _base_allocation(signals.drawdown)
    alloc = _oil_modifier(alloc, signals.oil_spike)
    alloc = _momentum_boost(alloc, signals.return_3d, signals.oil_spike)
    return alloc


def signal_label(alloc: float) -> str:
    if   alloc == 0.00: return "HOLD / CASH"
    elif alloc <= 0.25: return "SMALL BUY"
    elif alloc <= 0.50: return "MODERATE BUY"
    elif alloc <= 0.75: return "LARGE BUY"
    else:               return "FULL POSITION"


def momentum_warnings(signals: Signals) -> list:
    out = []
    if signals.return_3d > 0.015 and signals.oil_spike < 0.10:
        out.append("📈 Positive momentum — allocation boosted +25%")
    if signals.return_3d < -0.015 and signals.oil_spike > 0.10:
        out.append("⚠️  Deterioration phase — avoid adding new positions")
    return out


# ═══════════════════════════════════════════════════════════════════════════
# OILWAR ACTIVE
# ═══════════════════════════════════════════════════════════════════════════

class OilWarStrategy(Strategy):
    """Rebalances daily to the drawdown/oil target allocation."""
    name  = "OilWar Active"
    color = "#e8a020"

    def next_allocation(self, signals: Signals) -> float:
        return compute_target_allocation(signals)


# ═══════════════════════════════════════════════════════════════════════════
# OILWAR BUY-ONLY
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class _BuyOnlyState:
    shares_held:   float = 0.0
    cash_deployed: float = 0.0


class BuyOnlyOilWarStrategy(Strategy):
    """Accumulates shares at buy signals; never sells."""
    name  = "OilWar Buy-Only"
    color = "#3ecf6a"

    def __init__(self) -> None:
        self._state = _BuyOnlyState()
        self._initial_capital: float = 0.0

    def reset(self) -> None:
        self._state = _BuyOnlyState()

    def set_initial_capital(self, capital: float) -> None:
        self._initial_capital = capital

    def next_allocation(self, signals: Signals) -> float:
        target = compute_target_allocation(signals)
        current_value = self._state.shares_held * signals.price
        current_alloc = (current_value / self._initial_capital
                         if self._initial_capital > 0 else 0.0)
        if target > current_alloc:
            return min(target - current_alloc, 1.0 - current_alloc)
        return 0.0

    def record_purchase(self, shares: float, cost: float) -> None:
        self._state.shares_held   += shares
        self._state.cash_deployed += cost

    @property
    def shares_held(self) -> float:
        return self._state.shares_held

    @property
    def cash_deployed(self) -> float:
        return self._state.cash_deployed

    def unrealized_value(self, price: float) -> float:
        return self._state.shares_held * price

    def unrealized_pnl(self, price: float) -> float:
        return self.unrealized_value(price) - self._state.cash_deployed

    def unrealized_pct(self, price: float) -> float:
        if self._state.cash_deployed == 0:
            return 0.0
        return self.unrealized_pnl(price) / self._state.cash_deployed


# ═══════════════════════════════════════════════════════════════════════════
# BUY AND HOLD
# ═══════════════════════════════════════════════════════════════════════════

class BuyAndHoldStrategy(Strategy):
    """100% invested from day one. Passive benchmark."""
    name  = "Buy & Hold"
    color = "#5a6370"

    def next_allocation(self, signals: Signals) -> float:
        return 1.0


# ═══════════════════════════════════════════════════════════════════════════
# REGISTRY
# ═══════════════════════════════════════════════════════════════════════════

REGISTRY: list = [
    OilWarStrategy(),
    BuyOnlyOilWarStrategy(),
    BuyAndHoldStrategy(),
]


# ═══════════════════════════════════════════════════════════════════════════
# RESULT DATACLASSES
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class HistoricalBar:
    """One realised trading day — full portfolio accounting."""
    date:           str
    price:          float
    oil_price:      float
    drawdown:       float
    oil_spike:      float
    return_3d:      float
    allocation:     float    # fraction of portfolio currently invested (or deployed for BuyOnly)
    shares_value:   float    # mark-to-market value of equity holdings
    cash_remaining: float    # uninvested cash still sitting in the portfolio
    portfolio_value: float   # shares_value + cash_remaining  ← plot this
    signal:         str

    # Back-compat alias so existing code using b.equity still works
    @property
    def equity(self) -> float:
        return self.portfolio_value


@dataclass
class ForecastPath:
    """Percentile bands from Monte Carlo projection."""
    dates:        list   # ISO date strings
    p10:          list
    p25:          list
    p50:          list   # median
    p75:          list
    p90:          list
    alloc_median: list   # median allocation at each future bar


# ═══════════════════════════════════════════════════════════════════════════
# run_history
# ═══════════════════════════════════════════════════════════════════════════

def run_history(eq_series, oil_series, strategy: Strategy,
                initial_capital: float = 100_000,
                history_days: int = 60) -> list:
    """
    Replay the last `history_days` trading bars through `strategy`.

    Requires ≥20 warm-up bars before the history window in eq_series/oil_series.

    Portfolio accounting per strategy type
    ───────────────────────────────────────
    OilWar Active / Buy & Hold (daily rebalancing):
      The model rebalances to `alloc` at each close, so at any point:
        shares_value   = alloc × portfolio_value
        cash_remaining = (1 − alloc) × portfolio_value
      portfolio_value evolves as: prev × (1 + alloc × daily_equity_return)

    OilWar Buy-Only (accumulate; never sell):
      Purchases are explicit: cash is spent once and shares accumulate.
        shares_value   = shares_held × current_price
        cash_remaining = initial_capital − total_cash_deployed
        portfolio_value = shares_value + cash_remaining

    Both start with portfolio_value = initial_capital at bar 0 of history,
    because warm-up bars are processed but their state is discarded — the
    portfolio clock starts fresh at the first bar of the history window.
    """
    import pandas as pd

    eq  = eq_series.dropna()
    oil = oil_series.dropna()
    combined = pd.DataFrame({"eq": eq, "oil": oil}).dropna()
    if len(combined) < 22:
        return []

    combined["eq_ret"] = combined["eq"].pct_change().fillna(0)

    # Trim to warm-up + history window
    total = 20 + history_days
    if len(combined) < total:
        history_days = max(1, len(combined) - 20)
        total = 20 + history_days
    df = combined.iloc[-total:]

    strategy.reset()
    if isinstance(strategy, BuyOnlyOilWarStrategy):
        strategy.set_initial_capital(initial_capital)

    # portfolio_value tracks the total (cash + shares) for rebalancing strategies.
    # It is reset to initial_capital at the first history bar (i == 20) so warm-up
    # returns don't bleed into the displayed history.
    portfolio_value = initial_capital
    bars = []

    for i in range(len(df)):
        if i < 20:
            continue

        # Reset portfolio to initial_capital at the very first history bar
        if i == 20:
            portfolio_value = initial_capital

        P   = float(df["eq"].iat[i])
        H   = float(df["eq"].iloc[i-20:i].max())
        D   = (H - P) / H if H > 0 else 0.0
        O   = float(df["oil"].iat[i])
        O5  = float(df["oil"].iloc[i-5:i].mean())
        spk = (O - O5) / O5 if O5 > 0 else 0.0
        P3  = float(df["eq"].iat[i-3]) if i >= 3 else P
        r3  = (P / P3) - 1 if P3 > 0 else 0.0

        sigs = Signals(price=P, high_20d=H, drawdown=D,
                       return_3d=r3, oil_price=O, oil_5d_avg=O5, oil_spike=spk)
        er   = float(df["eq_ret"].iat[i])

        if isinstance(strategy, BuyOnlyOilWarStrategy):
            # Explicit purchase: spend cash, accumulate shares
            inc   = strategy.next_allocation(sigs)
            spend = min(inc * initial_capital,
                        max(initial_capital - strategy.cash_deployed, 0.0))
            if spend > 0 and P > 0:
                strategy.record_purchase(spend / P, spend)

            shares_val    = strategy.unrealized_value(P)
            cash_rem      = initial_capital - strategy.cash_deployed
            portfolio_value = shares_val + cash_rem
            alloc_disp    = strategy.cash_deployed / initial_capital

        else:
            # Rebalancing model: allocate fraction to equity each bar
            alloc_disp    = strategy.next_allocation(sigs)
            portfolio_value = portfolio_value * (1.0 + alloc_disp * er)
            shares_val    = alloc_disp * portfolio_value
            cash_rem      = (1.0 - alloc_disp) * portfolio_value

        bars.append(HistoricalBar(
            date            = df.index[i].strftime("%Y-%m-%d"),
            price           = round(P, 2),
            oil_price       = round(O, 2),
            drawdown        = round(D, 4),
            oil_spike       = round(spk, 4),
            return_3d       = round(r3, 4),
            allocation      = round(alloc_disp, 4),
            shares_value    = round(shares_val, 2),
            cash_remaining  = round(cash_rem, 2),
            portfolio_value = round(portfolio_value, 2),
            signal          = signal_label(alloc_disp),
        ))

    return bars


# ═══════════════════════════════════════════════════════════════════════════
# simulate_future
# ═══════════════════════════════════════════════════════════════════════════

def simulate_future(eq_series, oil_series, strategy: Strategy,
                    last_bar: HistoricalBar,
                    forecast_days: int = 30,
                    n_paths: int = 500,
                    seed: int = 42,
                    initial_capital: float = 100_000) -> ForecastPath:
    """
    Monte Carlo projection of the next `forecast_days` trading days.

    Calibrates daily return distribution from the most recent 20 bars,
    runs `n_paths` independent simulations through `strategy`, and returns
    percentile bands (p10/p25/p50/p75/p90) of total portfolio value
    (shares mark-to-market + remaining cash).
    """
    import numpy as np
    import pandas as pd

    rng = np.random.default_rng(seed)

    eq  = eq_series.dropna()
    oil = oil_series.dropna()
    combined = pd.DataFrame({"eq": eq, "oil": oil}).dropna()
    recent   = combined.iloc[-20:]

    eq_rets  = recent["eq"].pct_change().dropna().values
    oil_rets = recent["oil"].pct_change().dropna().values

    eq_mu,  eq_sig  = float(np.mean(eq_rets)),  max(float(np.std(eq_rets)),  0.001)
    oil_mu, oil_sig = float(np.mean(oil_rets)), max(float(np.std(oil_rets)), 0.001)

    last_date    = pd.Timestamp(last_bar.date)
    future_dates = pd.bdate_range(last_date + pd.Timedelta(days=1), periods=forecast_days)

    # Seed windows from real data
    seed_eq_win  = list(eq.iloc[-20:].values.astype(float))
    seed_oil_win = list(oil.iloc[-5:].values.astype(float))

    all_portfolio = [[0.0] * forecast_days for _ in range(n_paths)]
    all_alloc     = [[0.0] * forecast_days for _ in range(n_paths)]

    for p in range(n_paths):
        eq_win   = list(seed_eq_win)
        oil_win  = list(seed_oil_win)
        curr_eq  = last_bar.price
        curr_oil = last_bar.oil_price

        strategy.reset()

        if isinstance(strategy, BuyOnlyOilWarStrategy):
            strategy.set_initial_capital(initial_capital)
            # Re-seed the buy-only state to match where history ended:
            # cash_deployed = allocation * initial_capital (approximate)
            deployed = last_bar.allocation * initial_capital
            if curr_eq > 0 and deployed > 0:
                strategy.record_purchase(deployed / curr_eq, deployed)
            # Portfolio starts from where history left off
            curr_portfolio = last_bar.portfolio_value
        else:
            curr_portfolio = last_bar.portfolio_value

        prev3 = [float(eq.iloc[-3]), float(eq.iloc[-2]), float(eq.iloc[-1])]

        for d in range(forecast_days):
            er   = float(rng.normal(eq_mu,  eq_sig))
            oilr = float(rng.normal(oil_mu, oil_sig))
            new_eq  = curr_eq  * (1.0 + er)
            new_oil = curr_oil * (1.0 + oilr)

            eq_win.append(new_eq)
            if len(eq_win) > 20: eq_win.pop(0)
            oil_win.append(new_oil)
            if len(oil_win) > 5:  oil_win.pop(0)

            H   = max(eq_win)
            D   = (H - new_eq) / H if H > 0 else 0.0
            O5  = sum(oil_win) / len(oil_win)
            spk = (new_oil - O5) / O5 if O5 > 0 else 0.0
            P3  = prev3[0] if len(prev3) >= 3 else curr_eq
            r3  = (new_eq / P3) - 1 if P3 > 0 else 0.0

            sigs = Signals(price=new_eq, high_20d=H, drawdown=D,
                           return_3d=r3, oil_price=new_oil, oil_5d_avg=O5, oil_spike=spk)

            if isinstance(strategy, BuyOnlyOilWarStrategy):
                inc   = strategy.next_allocation(sigs)
                spend = min(inc * initial_capital,
                            max(initial_capital - strategy.cash_deployed, 0.0))
                if spend > 0 and new_eq > 0:
                    strategy.record_purchase(spend / new_eq, spend)
                shares_val     = strategy.unrealized_value(new_eq)
                cash_rem       = initial_capital - strategy.cash_deployed
                curr_portfolio = shares_val + cash_rem
                alloc_val      = strategy.cash_deployed / initial_capital
            else:
                alloc_val      = strategy.next_allocation(sigs)
                curr_portfolio = curr_portfolio * (1.0 + alloc_val * er)

            all_portfolio[p][d] = curr_portfolio
            all_alloc[p][d]     = alloc_val

            prev3.append(new_eq)
            if len(prev3) > 3: prev3.pop(0)
            curr_eq  = new_eq
            curr_oil = new_oil

    import numpy as np

    pf_arr = np.array(all_portfolio)
    al_arr = np.array(all_alloc)

    return ForecastPath(
        dates        = [d.strftime("%Y-%m-%d") for d in future_dates],
        p10          = [round(float(v), 2) for v in np.percentile(pf_arr, 10, axis=0)],
        p25          = [round(float(v), 2) for v in np.percentile(pf_arr, 25, axis=0)],
        p50          = [round(float(v), 2) for v in np.percentile(pf_arr, 50, axis=0)],
        p75          = [round(float(v), 2) for v in np.percentile(pf_arr, 75, axis=0)],
        p90          = [round(float(v), 2) for v in np.percentile(pf_arr, 90, axis=0)],
        alloc_median = [round(float(v), 4) for v in np.percentile(al_arr, 50, axis=0)],
    )
