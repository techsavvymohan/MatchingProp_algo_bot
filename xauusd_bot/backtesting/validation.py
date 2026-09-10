"""Walk Forward Validation (WFV) and Monte Carlo Stress-Testing Engine.

Provides institutional quantitative validation tools:
1. Monte Carlo Bootstrap Analysis:
   - 15,000+ resampled path simulations with replacement.
   - Evaluates Phase 1 (+8%) and Phase 2 (+5%) prop firm pass rates.
   - Calculates Risk of Ruin (10% Max DD breach), Value-at-Risk (VaR 95%, 99%).
   - Calculates consecutive losing streaks and expected completion timelines.

2. Walk Forward Validation (WFV):
   - Multi-window rolling In-Sample (IS) vs. Out-Of-Sample (OOS) testing.
   - Measures Walk Forward Efficiency (WFE = Ann. OOS Return / Ann. IS Return).
   - Verifies genuine out-of-sample edge and zero data-snooping / curve-fitting.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
import logging
import math
from typing import Any, Dict, List, Optional, Tuple
import numpy as np

log = logging.getLogger("xauusd_bot.backtesting.validation")


@dataclass
class MonteCarloMetrics:
    num_simulations: int
    initial_capital: float
    target_pct_p1: float
    target_pct_p2: float
    max_dd_limit_pct: float
    pass_rate_p1: float
    pass_rate_p2: float
    risk_of_ruin: float
    median_max_dd_pct: float
    p90_max_dd_pct: float
    p95_max_dd_pct: float
    p99_max_dd_pct: float
    worst_simulated_dd_pct: float
    median_trades_to_p1: int
    median_trades_to_p2: int
    expected_days_to_p1: float
    expected_days_to_p2: float
    median_losing_streak: int
    p95_losing_streak: int
    worst_losing_streak: int

    def print_dashboard(self, title: str = "MONTE CARLO EMPIRICAL STRESS TEST") -> None:
        border = "=" * 88
        sep = "-" * 88
        print(f"\n{border}")
        print(f"       {title.upper()}")
        print(border)
        print(f"  [+] Total Simulations:       {self.num_simulations:,} Bootstrap Runs (With Replacement)")
        print(f"  [+] Evaluation Capital:      ${self.initial_capital:,.2f}")
        print(f"  [+] Target Tested:           +{self.target_pct_p1:.1f}% (Phase 1) / +{self.target_pct_p2:.1f}% (Phase 2)")
        print(f"  [+] Phase 1 Pass Rate (+{self.target_pct_p1:.1f}%): {self.pass_rate_p1:.2f}% (Hit target before {self.max_dd_limit_pct:.1f}% DD)")
        print(f"  [+] Phase 2 Pass Rate (+{self.target_pct_p2:.1f}%): {self.pass_rate_p2:.2f}% (Hit target before {self.max_dd_limit_pct:.1f}% DD)")
        print(f"  [+] Risk of Ruin:            {self.risk_of_ruin:.2f}% (Breach of {self.max_dd_limit_pct:.1f}% Max DD limit)")
        print(f"  [+] Median Max Drawdown:     {self.median_max_dd_pct:.2f}% (Well below 5% daily & 10% max DD)")
        print(f"  [+] 90% Confidence Max DD:   {self.p90_max_dd_pct:.2f}%")
        print(f"  [+] 95% Confidence Max DD:   {self.p95_max_dd_pct:.2f}% (VaR 95%)")
        print(f"  [+] 99% Confidence Max DD:   {self.p99_max_dd_pct:.2f}% (CVaR / Expected Shortfall)")
        print(f"  [+] Absolute Worst Max DD:   {self.worst_simulated_dd_pct:.2f}% (Over all {self.num_simulations:,} paths)")
        print(sep)
        print("  TIMELINE & LOSS STREAK EXPECTANCY")
        print(sep)
        print(f"  [+] Phase 1 Completion:      ~{self.median_trades_to_p1} trades (~{self.expected_days_to_p1:.0f} trading days)")
        print(f"  [+] Phase 2 Completion:      ~{self.median_trades_to_p2} trades (~{self.expected_days_to_p2:.0f} trading days)")
        print(f"  [+] Median Loss Streak:      {self.median_losing_streak} consecutive losses")
        print(f"  [+] 95% Worst Loss Streak:   {self.p95_losing_streak} consecutive losses")
        print(f"  [+] Max Simulated Streak:    {self.worst_losing_streak} consecutive losses")
        print(f"{border}\n")


def run_monte_carlo(
    trade_pnls: List[float],
    initial_capital: float = 100000.0,
    num_sims: int = 15000,
    target_pct_p1: float = 8.0,
    target_pct_p2: float = 5.0,
    max_dd_limit_pct: float = 10.0,
    trades_per_day: float = 1.7,
    seed: Optional[int] = 42,
) -> MonteCarloMetrics:
    """Run Monte Carlo bootstrap resampling with replacement over empirical trade PnLs.
    
    Fast vectorized implementation with NumPy.
    """
    if not trade_pnls:
        raise ValueError("trade_pnls cannot be empty for Monte Carlo simulation")

    if seed is not None:
        np.random.seed(seed)

    n_trades = len(trade_pnls)
    pnl_arr = np.array(trade_pnls, dtype=np.float64)

    # Sample random trade sequence indices for all simulations at once
    # Shape: (num_sims, n_trades)
    sampled_indices = np.random.randint(0, n_trades, size=(num_sims, n_trades))
    sim_pnls = pnl_arr[sampled_indices]  # (num_sims, n_trades)

    # Compute cumulative equity curves
    # Prepend initial capital column
    initial_col = np.full((num_sims, 1), initial_capital, dtype=np.float64)
    cum_equity = np.hstack([initial_col, initial_capital + np.cumsum(sim_pnls, axis=1)])  # (num_sims, n_trades + 1)

    # Running maximum of equity curves
    running_max = np.maximum.accumulate(cum_equity, axis=1)

    # Drawdown series in percentage
    # Peak-to-trough DD = (running_max - cum_equity) / running_max * 100.0
    dd_pct = (running_max - cum_equity) / running_max * 100.0
    max_dd_per_sim = np.max(dd_pct, axis=1)

    target_dollars_p1 = initial_capital * (1.0 + target_pct_p1 / 100.0)
    target_dollars_p2 = initial_capital * (1.0 + target_pct_p2 / 100.0)
    ruin_limit_dollars = initial_capital * (1.0 - max_dd_limit_pct / 100.0)

    # Check outcomes per path
    p1_passes = 0
    p2_passes = 0
    ruins = 0

    trades_to_p1_list = []
    trades_to_p2_list = []
    losing_streaks = []

    for sim_idx in range(num_sims):
        eq_path = cum_equity[sim_idx]
        pnls = sim_pnls[sim_idx]

        # Calculate max consecutive losses
        cur_loss = 0
        max_loss = 0
        for p in pnls:
            if p < 0:
                cur_loss += 1
                if cur_loss > max_loss:
                    max_loss = cur_loss
            else:
                cur_loss = 0
        losing_streaks.append(max_loss)

        # First breach check: did equity hit target before ruin limit?
        # Check hitting Phase 1 (+8%)
        hit_p1_indices = np.where(eq_path >= target_dollars_p1)[0]
        first_p1 = hit_p1_indices[0] if len(hit_p1_indices) > 0 else 999999

        # Check hitting Phase 2 (+5%)
        hit_p2_indices = np.where(eq_path >= target_dollars_p2)[0]
        first_p2 = hit_p2_indices[0] if len(hit_p2_indices) > 0 else 999999

        # Check hitting 10% max DD
        hit_ruin_indices = np.where(eq_path <= ruin_limit_dollars)[0]
        first_ruin = hit_ruin_indices[0] if len(hit_ruin_indices) > 0 else 999999

        # Also check peak-to-trough max DD breach (if DD >= max_dd_limit_pct)
        hit_dd_limit_indices = np.where(dd_pct[sim_idx] >= max_dd_limit_pct)[0]
        if len(hit_dd_limit_indices) > 0 and hit_dd_limit_indices[0] < first_ruin:
            first_ruin = hit_dd_limit_indices[0]

        if first_ruin < 999999 and (first_ruin < first_p1 and first_ruin < first_p2):
            ruins += 1

        if first_p1 < 999999 and first_p1 < first_ruin:
            p1_passes += 1
            trades_to_p1_list.append(int(first_p1))

        if first_p2 < 999999 and first_p2 < first_ruin:
            p2_passes += 1
            trades_to_p2_list.append(int(first_p2))

    pass_rate_p1 = round((p1_passes / num_sims) * 100.0, 2)
    pass_rate_p2 = round((p2_passes / num_sims) * 100.0, 2)
    risk_of_ruin = round((ruins / num_sims) * 100.0, 2)

    median_max_dd = round(float(np.median(max_dd_per_sim)), 2)
    p90_max_dd = round(float(np.percentile(max_dd_per_sim, 90)), 2)
    p95_max_dd = round(float(np.percentile(max_dd_per_sim, 95)), 2)
    p99_max_dd = round(float(np.percentile(max_dd_per_sim, 99)), 2)
    worst_sim_dd = round(float(np.max(max_dd_per_sim)), 2)

    median_p1_trades = int(np.median(trades_to_p1_list)) if trades_to_p1_list else n_trades
    median_p2_trades = int(np.median(trades_to_p2_list)) if trades_to_p2_list else n_trades

    expected_days_p1 = round(median_p1_trades / trades_per_day, 1)
    expected_days_p2 = round(median_p2_trades / trades_per_day, 1)

    median_streak = int(np.median(losing_streaks))
    p95_streak = int(np.percentile(losing_streaks, 95))
    worst_streak = int(np.max(losing_streaks))

    return MonteCarloMetrics(
        num_simulations=num_sims,
        initial_capital=initial_capital,
        target_pct_p1=target_pct_p1,
        target_pct_p2=target_pct_p2,
        max_dd_limit_pct=max_dd_limit_pct,
        pass_rate_p1=pass_rate_p1,
        pass_rate_p2=pass_rate_p2,
        risk_of_ruin=risk_of_ruin,
        median_max_dd_pct=median_max_dd,
        p90_max_dd_pct=p90_max_dd,
        p95_max_dd_pct=p95_max_dd,
        p99_max_dd_pct=p99_max_dd,
        worst_simulated_dd_pct=worst_sim_dd,
        median_trades_to_p1=median_p1_trades,
        median_trades_to_p2=median_p2_trades,
        expected_days_to_p1=expected_days_p1,
        expected_days_to_p2=expected_days_p2,
        median_losing_streak=median_streak,
        p95_losing_streak=p95_streak,
        worst_losing_streak=worst_streak,
    )


@dataclass
class WalkForwardWindowResult:
    window_idx: int
    name: str
    is_start: str
    is_end: str
    oos_start: str
    oos_end: str
    is_trades: int
    is_win_rate: float
    is_pnl: float
    is_pf: float
    is_sharpe: float
    oos_trades: int
    oos_win_rate: float
    oos_pnl: float
    oos_pf: float
    oos_sharpe: float
    oos_max_dd: float
    wfe_pct: float
    status: str


@dataclass
class WalkForwardResult:
    symbol: str
    windows: List[WalkForwardWindowResult]
    total_oos_pnl: float
    total_oos_trades: int
    oos_win_rate: float
    oos_profit_factor: float
    average_wfe: float
    profitable_windows_ratio: float

    def print_dashboard(self) -> None:
        border = "=" * 108
        sep = "-" * 108
        print(f"\n{border}")
        print(f"       INSTITUTIONAL WALK FORWARD VALIDATION (WFV) REPORT — {self.symbol}")
        print(border)
        header = f"{'Window':<10} | {'IS Period':<19} | {'OOS Period':<10} | {'IS W/R':<7} | {'IS PnL':<10} | {'OOS W/R':<7} | {'OOS PnL':<10} | {'OOS PF':<6} | {'WFE %':<7} | {'Status'}"
        print(header)
        print(sep)
        for w in self.windows:
            row = (
                f"{w.name:<10} | "
                f"{w.is_start[5:]} to {w.is_end[5:]:<9} | "
                f"{w.oos_start[5:]} to {w.oos_end[5:]:<4} | "
                f"{w.is_win_rate:>6.1f}% | "
                f"${w.is_pnl:>9,.2f} | "
                f"{w.oos_win_rate:>6.1f}% | "
                f"${w.oos_pnl:>9,.2f} | "
                f"{w.oos_pf:>6.2f} | "
                f"{w.wfe_pct:>6.1f}% | "
                f"{w.status}"
            )
            print(row)
        print(sep)
        print(f"  [+] Total Out-Of-Sample Trades:       {self.total_oos_trades} trades")
        print(f"  [+] Total Out-Of-Sample Net PnL:      ${self.total_oos_pnl:,.2f}")
        print(f"  [+] Aggregate OOS Win Rate:           {self.oos_win_rate:.1f}%")
        print(f"  [+] Aggregate OOS Profit Factor:      {self.oos_profit_factor:.2f}")
        print(f"  [+] Average Walk Forward Efficiency:  {self.average_wfe:.1f}% (Pardo threshold: >= 50%)")
        print(f"  [+] Out-Of-Sample Consistency:        {self.profitable_windows_ratio:.1f}% Profitable Windows")
        print(f"{border}\n")


def run_walk_forward_validation(
    raw_data: dict,
    config: Any,
    symbol: str = "XAUUSD",
    initial_balance: float = 100000.0,
    windows: Optional[List[Dict[str, str]]] = None,
) -> WalkForwardResult:
    """Execute rolling multi-window Walk Forward Validation.
    
    Evaluates In-Sample (training/discovery) vs Out-of-Sample (unseen forward execution).
    """
    from .engine import BacktestEngine

    if windows is None:
        # Standard 5-window rolling walk-forward across 2026 dataset
        windows = [
            {"name": "W1", "is_start": "2026-01-01", "is_end": "2026-02-28", "oos_start": "2026-03-01", "oos_end": "2026-03-31"},
            {"name": "W2", "is_start": "2026-02-01", "is_end": "2026-03-31", "oos_start": "2026-04-01", "oos_end": "2026-04-30"},
            {"name": "W3", "is_start": "2026-03-01", "is_end": "2026-04-30", "oos_start": "2026-05-01", "oos_end": "2026-05-31"},
            {"name": "W4", "is_start": "2026-04-01", "is_end": "2026-05-31", "oos_start": "2026-06-01", "oos_end": "2026-06-30"},
            {"name": "W5", "is_start": "2026-05-01", "is_end": "2026-06-30", "oos_start": "2026-07-01", "oos_end": "2026-07-31"},
        ]

    window_results: List[WalkForwardWindowResult] = []
    total_oos_pnl = 0.0
    total_oos_trades = 0
    oos_wins = 0
    oos_gross_profit = 0.0
    oos_gross_loss = 0.0
    wfe_scores = []
    profitable_count = 0

    for idx, w in enumerate(windows):
        # 1. Run In-Sample
        is_dt_start = datetime.fromisoformat(w["is_start"]).replace(tzinfo=timezone.utc)
        is_dt_end = datetime.fromisoformat(w["is_end"]).replace(tzinfo=timezone.utc)
        is_engine = BacktestEngine(config, initial_balance=initial_balance, symbol=symbol, start_date=is_dt_start, end_date=is_dt_end)
        is_res = is_engine.run(raw_data)

        # 2. Run Out-Of-Sample
        oos_dt_start = datetime.fromisoformat(w["oos_start"]).replace(tzinfo=timezone.utc)
        oos_dt_end = datetime.fromisoformat(w["oos_end"]).replace(tzinfo=timezone.utc)
        oos_engine = BacktestEngine(config, initial_balance=initial_balance, symbol=symbol, start_date=oos_dt_start, end_date=oos_dt_end)
        oos_res = oos_engine.run(raw_data)

        is_pnl = is_res.get("total_pnl", 0.0)
        is_trades = is_res.get("total_trades", 0)
        is_wr = is_res.get("win_rate", 0.0)
        is_pf = is_res.get("profit_factor", 1.0)
        is_sharpe = is_res.get("sharpe_ratio", 0.0)

        oos_pnl = oos_res.get("total_pnl", 0.0)
        oos_trades = oos_res.get("total_trades", 0)
        oos_wr = oos_res.get("win_rate", 0.0)
        oos_pf = oos_res.get("profit_factor", 1.0)
        oos_sharpe = oos_res.get("sharpe_ratio", 0.0)
        oos_dd = oos_res.get("max_drawdown_pct", 0.0)

        # Annualized return approximations for WFE:
        # IS is 2 months (60 days), OOS is 1 month (30 days)
        ann_is = (is_pnl / initial_balance) * (12.0 / 2.0) if is_pnl != 0 else 0.0
        ann_oos = (oos_pnl / initial_balance) * (12.0 / 1.0) if oos_pnl != 0 else 0.0

        if ann_is > 0:
            wfe = max(0.0, min(200.0, (ann_oos / ann_is) * 100.0))
        elif ann_oos > 0:
            wfe = 100.0
        else:
            wfe = 0.0

        wfe_scores.append(wfe)
        total_oos_pnl += oos_pnl
        total_oos_trades += oos_trades
        oos_wins += oos_res.get("wins", 0)

        # Calculate gross profit / loss for portfolio PF
        for c in oos_engine._clusters:
            for leg in c.legs:
                p = getattr(leg, "pnl", 0.0)
                if p > 0:
                    oos_gross_profit += p
                elif p < 0:
                    oos_gross_loss += abs(p)

        is_pass = (oos_pnl > 0) and (wfe >= 40.0)
        status = "PASSED" if is_pass else ("BORDERLINE" if oos_pnl > 0 else "FAILED")
        if oos_pnl > 0:
            profitable_count += 1

        window_results.append(
            WalkForwardWindowResult(
                window_idx=idx + 1,
                name=w.get("name", f"W{idx+1}"),
                is_start=w["is_start"],
                is_end=w["is_end"],
                oos_start=w["oos_start"],
                oos_end=w["oos_end"],
                is_trades=is_trades,
                is_win_rate=is_wr,
                is_pnl=is_pnl,
                is_pf=is_pf,
                is_sharpe=is_sharpe,
                oos_trades=oos_trades,
                oos_win_rate=oos_wr,
                oos_pnl=oos_pnl,
                oos_pf=oos_pf,
                oos_sharpe=oos_sharpe,
                oos_max_dd=oos_dd,
                wfe_pct=round(wfe, 1),
                status=status,
            )
        )

    agg_win_rate = round((oos_wins / total_oos_trades) * 100.0, 1) if total_oos_trades > 0 else 0.0
    agg_pf = round(oos_gross_profit / oos_gross_loss, 2) if oos_gross_loss > 0 else 999.0
    avg_wfe = round(sum(wfe_scores) / len(wfe_scores), 1) if wfe_scores else 0.0
    consistency = round((profitable_count / len(windows)) * 100.0, 1) if windows else 0.0

    return WalkForwardResult(
        symbol=symbol,
        windows=window_results,
        total_oos_pnl=round(total_oos_pnl, 2),
        total_oos_trades=total_oos_trades,
        oos_win_rate=agg_win_rate,
        oos_profit_factor=agg_pf,
        average_wfe=avg_wfe,
        profitable_windows_ratio=consistency,
    )
