import os
import pytest
from unittest.mock import MagicMock
from datetime import datetime

from xauusd_bot.config import Config, TradingConfig
from xauusd_bot.models import (
    AccountInfo, PyraCluster, Signal, SignalGrade,
    TradeDirection, TradeLeg, TradeStatus,
)
from xauusd_bot.risk.position_sizer import PositionSizer
from xauusd_bot.trade.cluster import ClusterManager
from xauusd_bot.trade.trade_manager import TradeManager
from xauusd_bot.order.exit import ExitManager
from xauusd_bot.order.partial_close import PartialCloseManager
from xauusd_bot.risk.pyramid_manager import PyramidManager
from xauusd_bot.risk.daily_loss import DailyLossTracker
from xauusd_bot.risk.max_dd import MaxDDTracker


def test_position_sizer_compounding_in_profit():
    """When equity exceeds initial balance, position sizer scales risk upward."""
    sizer = PositionSizer(
        initial_risk_pct=0.25,
        max_pyramid_entries=4,
        enable_profit_compounding=True,
        initial_balance=10000.0,
        compounding_cap_mult=2.0,
    )
    # At initial balance ($10,000): risk is $25.00
    acc_base = AccountInfo(balance=10000.0, equity=10000.0)
    lot_base = sizer.calculate_lot_size(
        account=acc_base, entry_price=2000.0, sl_price=1995.0,
        direction=TradeDirection.BUY, point_value=1.0, contract_size=100,
        tick_size=0.01,
    )
    # SL distance = $5.00 = 500 ticks * $1 = $500/lot.
    # $25 risk / $500 = 0.05 lots
    assert lot_base == 0.05

    # In profit buffer ($12,000 equity): risk is $12,000 * 0.0025 = $30.00
    acc_profit = AccountInfo(balance=12000.0, equity=12000.0)
    lot_profit = sizer.calculate_lot_size(
        account=acc_profit, entry_price=2000.0, sl_price=1995.0,
        direction=TradeDirection.BUY, point_value=1.0, contract_size=100,
        tick_size=0.01,
    )
    # $30 risk / $500 = 0.06 lots
    assert lot_profit == 0.06
    assert lot_profit > lot_base


def test_position_sizer_compounding_protects_drawdown():
    """When in drawdown (equity < initial balance), risk is anchored to initial balance, not reduced."""
    sizer = PositionSizer(
        initial_risk_pct=0.25,
        max_pyramid_entries=4,
        enable_profit_compounding=True,
        initial_balance=10000.0,
        compounding_cap_mult=2.0,
    )
    # Drawdown ($9,700 equity): eff_equity = max(9700, 10000) = 10000
    acc_dd = AccountInfo(balance=9700.0, equity=9700.0)
    lot_dd = sizer.calculate_lot_size(
        account=acc_dd, entry_price=2000.0, sl_price=1995.0,
        direction=TradeDirection.BUY, point_value=1.0, contract_size=100,
        tick_size=0.01,
    )
    assert lot_dd == 0.05  # Preserves base risk for symmetrical recovery


def test_position_sizer_compounding_cap():
    """Risk cannot scale beyond compounding_cap_mult (e.g. 2.0x)."""
    sizer = PositionSizer(
        initial_risk_pct=0.25,
        max_pyramid_entries=4,
        enable_profit_compounding=True,
        initial_balance=10000.0,
        compounding_cap_mult=2.0,
    )
    # Massive equity ($30,000): capped at $20,000 (2.0x)
    acc_high = AccountInfo(balance=30000.0, equity=30000.0)
    lot_high = sizer.calculate_lot_size(
        account=acc_high, entry_price=2000.0, sl_price=1995.0,
        direction=TradeDirection.BUY, point_value=1.0, contract_size=100,
        tick_size=0.01,
    )
    # Max risk = 20,000 * 0.0025 = $50.00 -> 50 / 500 = 0.10 lots
    assert lot_high == 0.10


def test_position_sizer_risk_scale():
    """risk_scale parameter scales risk directly (used by Net Dollar Beta Gate)."""
    sizer = PositionSizer(
        initial_risk_pct=0.25,
        max_pyramid_entries=4,
        enable_profit_compounding=False,
    )
    acc = AccountInfo(balance=10000.0, equity=10000.0)
    # Normal risk = 0.05 lots
    lot_normal = sizer.calculate_lot_size(
        account=acc, entry_price=2000.0, sl_price=1995.0,
        direction=TradeDirection.BUY, point_value=1.0, contract_size=100,
        tick_size=0.01, risk_scale=1.0,
    )
    assert lot_normal == 0.05

    # 40% reduction (risk_scale = 0.60): risk = $25 * 0.60 = $15 -> 15 / 500 = 0.03 lots
    lot_scaled = sizer.calculate_lot_size(
        account=acc, entry_price=2000.0, sl_price=1995.0,
        direction=TradeDirection.BUY, point_value=1.0, contract_size=100,
        tick_size=0.01, risk_scale=0.60,
    )
    assert lot_scaled == 0.03


def test_cluster_manager_net_dollar_beta_gate():
    """ClusterManager.has_same_usd_exposure correctly identifies correlated USD risk."""
    cm = ClusterManager()

    # No clusters -> False
    assert not cm.has_same_usd_exposure("EURUSD", TradeDirection.BUY)

    # Open XAUUSD BUY (Short USD)
    c1 = PyraCluster(direction=TradeDirection.BUY, status=TradeStatus.OPEN)
    c1.symbol = "XAUUSD"
    cm.add(c1)

    # Proposed EURUSD BUY (Short USD) -> Same USD exposure! (Both short USD)
    assert cm.has_same_usd_exposure("EURUSD", TradeDirection.BUY) is True

    # Proposed EURUSD SELL (Long USD) -> Opposite USD exposure! (Hedged)
    assert cm.has_same_usd_exposure("EURUSD", TradeDirection.SELL) is False

    # Same symbol XAUUSD BUY -> Ignored (different symbol check)
    assert cm.has_same_usd_exposure("XAUUSD", TradeDirection.BUY) is False


def test_trade_manager_execute_with_risk_scale():
    """TradeManager correctly passes risk_scale to PositionSizer."""
    cfg = MagicMock()
    cfg.time_based_exit_minutes = 120
    cfg.atr_period = 14
    cfg.max_pyramid_entries = 4
    cfg.pyramid_add_trigger_r = 0.5
    cfg.pyramid_initial_risk_pct = 0.25
    cfg.atr_multiplier_for_tf.return_value = 2.0

    order_entry = MagicMock()
    order_entry.place_limit_order.return_value = TradeLeg(
        position_ticket=2001, direction=TradeDirection.BUY,
        entry_price=2000.0, lot_size=0.03, sl_price=1995.0, tp_price=2010.0,
        open_time=datetime(2026, 6, 1), status=TradeStatus.PENDING,
    )

    exit_mgr = ExitManager(cfg)
    partial_close = PartialCloseManager(1.0, 50.0)
    pyramid_mgr = PyramidManager(4, 0.5)
    sizer = PositionSizer(0.25, 4, enable_profit_compounding=False)
    daily_loss = DailyLossTracker(3.0, 1.0)
    max_dd = MaxDDTracker(10.0, 2.0)
    tm = TradeManager(order_entry, exit_mgr, partial_close, pyramid_mgr, sizer, daily_loss, max_dd)

    acc = AccountInfo(balance=10000.0, equity=10000.0)
    tm.daily_loss.update(acc)

    sig = Signal(
        symbol="EURUSD", direction=TradeDirection.BUY, grade=SignalGrade.A,
        entry_price=1.0850, sl_price=1.0830, tp_price=1.0882,
    )

    cluster = tm.execute_limit_signal(
        signal=sig, limit_price=1.0850, account=acc,
        point_value=1.0, contract_size=100000,
        min_lot=0.01, lot_step=0.01, max_lot=100.0,
        risk_scale=0.60,
    )
    assert cluster is not None
    # Verify that signal lot size was reduced by risk_scale (0.60)
    # EURUSD 20 pips = 200 ticks * $1 = $200/lot.
    # Normal risk = $25 / $200 = 0.12 lots.
    # With 0.60 risk_scale = $15 / $200 = 0.08 lots (or rounded).
    assert sig.lot_size < 0.12


def test_config_profit_maximization_env_bindings(monkeypatch):
    """TradingConfig correctly loads all profit maximization flags from environment."""
    monkeypatch.setenv("ENABLE_PROFIT_COMPOUNDING", "false")
    monkeypatch.setenv("INITIAL_ACCOUNT_BALANCE", "25000.0")
    monkeypatch.setenv("COMPOUNDING_CAP_MULT", "1.5")
    monkeypatch.setenv("ENABLE_NET_BETA_GATE", "false")
    monkeypatch.setenv("CORRELATED_USD_RISK_SCALE", "0.50")

    tc = TradingConfig.from_env()
    assert tc.enable_profit_compounding is False
    assert tc.initial_account_balance == 25000.0
    assert tc.compounding_cap_mult == 1.5
    assert tc.enable_net_beta_gate is False
    assert tc.correlated_usd_risk_scale == 0.50

    # Dynamic Profit Maximization bindings
    monkeypatch.setenv("DELTA_ABSORPTION_MODE", "hard")
    monkeypatch.setenv("ENABLE_SPLIT_TRANCHE_RUNNER", "true")
    monkeypatch.setenv("RUNNER_TRANCHE_PCT", "0.50")
    monkeypatch.setenv("RUNNER_TRAIL_ATR_MULT", "2.5")
    monkeypatch.setenv("FVG_ADAPTIVE_RETEST_TOLERANCE_PCT", "0.30")
    monkeypatch.setenv("ENABLE_FVG_PYRAMIDING", "true")

    tc2 = TradingConfig.from_env()
    assert tc2.delta_absorption_mode == "hard"
    assert tc2.enable_split_tranche_runner is True
    assert tc2.runner_tranche_pct == 0.50
    assert tc2.runner_trail_atr_mult == 2.5
    assert tc2.fvg_adaptive_retest_tolerance_pct == 0.30
    assert tc2.enable_fvg_pyramiding is True


def test_split_tranche_exit_banks_cash_and_spawns_runner():
    """When TP is hit, 50% of lot size is banked and a trailing runner leg is created."""
    from xauusd_bot.backtesting.engine import BacktestEngine
    from xauusd_bot.models import TimeframeData, ExitReason

    cfg = Config.load()
    cfg.trading.enable_split_tranche_runner = True
    cfg.trading.runner_trail_atr_mult = 2.0

    engine = BacktestEngine(cfg, initial_balance=10000.0, symbol="XAUUSD")

    cluster = PyraCluster(
        direction=TradeDirection.BUY, status=TradeStatus.OPEN,
        symbol="XAUUSD", collective_sl=1995.0, open_time=datetime(2026, 6, 1, 10, 0),
    )
    leg = TradeLeg(
        direction=TradeDirection.BUY, entry_price=2000.0, lot_size=0.10,
        symbol="XAUUSD", sl_price=1995.0, tp_price=2010.0,
        open_time=datetime(2026, 6, 1, 10, 0), status=TradeStatus.OPEN,
    )
    cluster.legs.append(leg)

    # Simulate price reaching TP (2010.0)
    m1_slice = TimeframeData(
        tf="M1",
        time=[datetime(2026, 6, 1, 10, 5)],
        open=[2008.0], high=[2012.0], low=[2007.0], close=[2011.0],
        tick_volume=[100], spread=[20],
    )
    data_all = {"M1": m1_slice}

    actions = engine._manage_backtest_exits(cluster, data_all, 0, 2011.0)

    # 1. Base leg should be closed for 0.05 lots at TP
    assert leg.status == TradeStatus.CLOSED
    assert leg.lot_size == 0.05
    assert leg.exit_reason == ExitReason.TAKE_PROFIT
    assert any(a.get("action") == "banker_tp_hit" for a in actions)

    # 2. Moonbag runner should be spawned for remaining 0.05 lots
    assert len(cluster.legs) == 2
    runner = cluster.legs[1]
    assert getattr(runner, "_is_runner", False) is True
    assert runner.status == TradeStatus.OPEN
    assert runner.lot_size == 0.05
    assert runner.sl_price >= 2000.0  # SL protected at or above breakeven
    assert cluster.status == TradeStatus.OPEN  # Cluster stays open because runner is active!
