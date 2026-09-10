import json
import logging
from typing import Dict

log = logging.getLogger("xauusd_bot.backtest.report")


def print_report(results: dict):
    print("=" * 60)
    print("  QUANT PROFIT DIGGER BOT — INSTITUTIONAL QUANT REPORT")
    print("=" * 60)
    print(f"  Initial balance:    ${results.get('initial_balance', 0):.2f}")
    print(f"  Final balance:      ${results.get('final_balance', 0):.2f}")
    print(f"  Total PnL:          ${results.get('total_pnl', 0):.2f} ({results.get('return_pct', 0):+.2f}%)")
    print(f"  Total trades:       {results.get('total_trades', 0)}")
    print(f"  Wins / Losses:      {results.get('wins', 0)} / {results.get('losses', 0)}")
    print(f"  Win rate:           {results.get('win_rate', 0):.1f}%")
    print(f"  Profit factor:      {results.get('profit_factor', 0):.2f}")
    print(f"  Max drawdown:       {results.get('max_drawdown_pct', 0):.2f}%")
    print(f"  Trade clusters:     {results.get('clusters', 0)}")
    print("-" * 60)
    print("  INSTITUTIONAL RISK & ASYMMETRY METRICS")
    print("-" * 60)
    print(f"  Sharpe Ratio:       {results.get('sharpe_ratio', 0.0):.2f}")
    print(f"  Sortino Ratio:      {results.get('sortino_ratio', 0.0):.2f}")
    print(f"  Calmar Ratio:       {results.get('calmar_ratio', 0.0):.2f}")
    print(f"  Payoff Ratio (W/L): {results.get('payoff_ratio', 0.0):.2f}")
    print(f"  Average Win:        ${results.get('avg_win', 0.0):.2f}")
    print(f"  Average Loss:       ${results.get('avg_loss', 0.0):.2f}")
    print(f"  Trade Expectancy:   ${results.get('expectancy', 0.0):.2f}")
    print(f"  Recovery Factor:    {results.get('recovery_factor', 0.0):.2f}")
    print(f"  Kelly Fraction:     {results.get('kelly_fraction_pct', 0.0):.2f}%")
    print("=" * 60)


def export_report(results: dict, path: str = "backtest_result.json"):
    with open(path, "w") as f:
        json.dump(results, f, indent=2)
    log.info("Backtest report exported to %s", path)
