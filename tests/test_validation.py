import pytest
from xauusd_bot.backtesting.validation import (
    run_monte_carlo,
    MonteCarloMetrics,
    WalkForwardWindowResult,
    WalkForwardResult,
)


def test_monte_carlo_resampling_profitable():
    # Sample trade PnLs: 60% win rate, 1.5 payoff
    # 6 wins of +$300, 4 losses of -$200
    trade_pnls = [300.0, -200.0, 300.0, 300.0, -200.0, 300.0, -200.0, 300.0, -200.0, 300.0] * 10
    mc = run_monte_carlo(
        trade_pnls=trade_pnls,
        initial_capital=10000.0,
        num_sims=500,
        target_pct_p1=8.0,
        target_pct_p2=5.0,
        max_dd_limit_pct=10.0,
        seed=123,
    )
    assert isinstance(mc, MonteCarloMetrics)
    assert mc.num_simulations == 500
    assert mc.pass_rate_p1 > 80.0
    assert mc.pass_rate_p2 > 85.0
    assert mc.risk_of_ruin < 10.0
    assert mc.median_max_dd_pct < 10.0
    assert mc.median_trades_to_p1 > 0
    assert mc.median_losing_streak >= 1


def test_monte_carlo_empty_error():
    with pytest.raises(ValueError, match="trade_pnls cannot be empty"):
        run_monte_carlo([])


def test_walk_forward_result_dashboard(capsys):
    w1 = WalkForwardWindowResult(
        window_idx=1,
        name="W1",
        is_start="2026-01-01",
        is_end="2026-02-28",
        oos_start="2026-03-01",
        oos_end="2026-03-31",
        is_trades=35,
        is_win_rate=58.0,
        is_pnl=4500.0,
        is_pf=1.65,
        is_sharpe=3.2,
        oos_trades=20,
        oos_win_rate=60.0,
        oos_pnl=3200.0,
        oos_pf=1.85,
        oos_sharpe=3.8,
        oos_max_dd=0.0,
        wfe_pct=85.0,
        status="PASSED",
    )
    res = WalkForwardResult(
        symbol="XAUUSD",
        windows=[w1],
        total_oos_pnl=3200.0,
        total_oos_trades=20,
        oos_win_rate=60.0,
        oos_profit_factor=1.85,
        average_wfe=85.0,
        profitable_windows_ratio=100.0,
    )
    res.print_dashboard()
    captured = capsys.readouterr().out
    assert "INSTITUTIONAL WALK FORWARD VALIDATION" in captured
    assert "W1" in captured
    assert "PASSED" in captured
