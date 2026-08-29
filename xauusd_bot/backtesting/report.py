import json
import logging
from typing import Dict

log = logging.getLogger("xauusd_bot.backtest.report")


def print_report(results: dict):
    print("=" * 60)
    print("  QUANT PROFIT DIGGER BOT — BACKTEST REPORT")
    print("=" * 60)
    print(f"  Initial balance: ${results.get('initial_balance', 0):.2f}")
    print(f"  Final balance:   ${results.get('final_balance', 0):.2f}")
    print(f"  Total PnL:       ${results.get('total_pnl', 0):.2f} ({results.get('return_pct', 0):+.2f}%)")
    print(f"  Total trades:    {results.get('total_trades', 0)}")
    print(f"  Wins:            {results.get('wins', 0)}")
    print(f"  Losses:          {results.get('losses', 0)}")
    print(f"  Win rate:        {results.get('win_rate', 0):.1f}%")
    print(f"  Profit factor:   {results.get('profit_factor', 0):.2f}")
    print(f"  Max drawdown:    {results.get('max_drawdown_pct', 0):.2f}%")
    print(f"  Trade clusters:  {results.get('clusters', 0)}")
    print("=" * 60)


def export_report(results: dict, path: str = "backtest_result.json"):
    with open(path, "w") as f:
        json.dump(results, f, indent=2)
    log.info("Backtest report exported to %s", path)
