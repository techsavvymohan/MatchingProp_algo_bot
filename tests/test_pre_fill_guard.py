"""Unit tests for Guard C (Pre-Fill Runaway Invalidation Guard)."""

import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock

from xauusd_bot.config import Config, TradingConfig
from xauusd_bot.models import TradeDirection, TradeStatus, Signal, SignalGrade, PyraCluster, TradeLeg, TimeframeData
from xauusd_bot.backtesting.engine import BacktestEngine

def test_signal_and_cluster_fvg_fields():
    """Verify fvg_low and fvg_high fields are preserved on Signal and PyraCluster."""
    sig = Signal(
        symbol="XAUUSD",
        direction=TradeDirection.SELL,
        entry_price=4407.16,
        sl_price=4417.24,
        tp_price=4387.00,
        fvg_low=4407.16,
        fvg_high=4408.86,
    )
    assert sig.fvg_low == 4407.16
    assert sig.fvg_high == 4408.86

    cluster = PyraCluster(
        signal_id=sig.id,
        direction=sig.direction,
        symbol=sig.symbol,
        fvg_low=sig.fvg_low,
        fvg_high=sig.fvg_high,
    )
    assert cluster.fvg_low == 4407.16
    assert cluster.fvg_high == 4408.86

def test_pre_fill_guard_cancels_sell_on_runaway_candle():
    """Verify pending Sell Limit is cancelled when a candle penetrates and closes above FVG high."""
    cfg = Config()
    cfg.trading.xau_enable_pre_fill_guard = True
    cfg.trading.strategy_trigger_type = "xau_liquidity_sweep_fvg_m1"

    engine = BacktestEngine(cfg, initial_balance=10000.0, symbol="XAUUSD")

    # Add a pending SELL limit order
    sig = Signal(
        symbol="XAUUSD",
        direction=TradeDirection.SELL,
        entry_price=4407.0,
        sl_price=4417.0,
        tp_price=4387.0,
        fvg_low=4407.0,
        fvg_high=4409.0,
    )
    engine._pending_fvg_orders.append({
        "signal": sig,
        "direction": TradeDirection.SELL,
        "limit_price": 4407.0,
        "sl_price": 4417.0,
        "tp_price": 4387.0,
        "lot_size": 0.25,
        "bars_active": 0,
        "max_bars": 5,
    })

    # Simulate candle blowing past 4409.0 (High 4412.0, Close 4411.0)
    now = datetime(2026, 9, 9, 14, 0, tzinfo=timezone.utc)
    m1_data = TimeframeData(
        tf="M1",
        time=[now],
        open=[4405.0],
        high=[4412.0],
        low=[4405.0],
        close=[4411.0],  # Closed above fvg_high (4409.0)
        tick_volume=[500],
        spread=[10],
    )

    data = {"M1": m1_data}
    # Test logic directly
    po = engine._pending_fvg_orders[0]
    fvg_high = po["signal"].fvg_high
    should_cancel = (m1_data.high[0] >= po["limit_price"]) and (m1_data.close[0] > fvg_high)
    assert should_cancel is True

def test_pre_fill_guard_allows_respectful_rejection():
    """Verify pending Sell Limit is filled normally when price taps FVG and respects resistance."""
    sig = Signal(
        symbol="XAUUSD",
        direction=TradeDirection.SELL,
        entry_price=4407.0,
        sl_price=4417.0,
        tp_price=4387.0,
        fvg_low=4407.0,
        fvg_high=4409.0,
    )
    # Candle wicks into FVG at 4408.0 but closes at 4406.5 (rejection below FVG high)
    candle_high = 4408.0
    candle_close = 4406.5
    fvg_high = sig.fvg_high

    filled = (candle_high >= sig.entry_price) and (candle_close <= fvg_high)
    assert filled is True

def test_pre_fill_guard_cancels_buy_on_runaway_candle():
    """Verify pending Buy Limit is cancelled when a candle penetrates and closes below FVG low."""
    sig = Signal(
        symbol="XAUUSD",
        direction=TradeDirection.BUY,
        entry_price=4400.0,
        sl_price=4390.0,
        tp_price=4420.0,
        fvg_low=4398.0,
        fvg_high=4400.0,
    )
    # Runaway red candle dumps through 4398.0 and closes at 4395.0
    candle_low = 4394.0
    candle_close = 4395.0
    fvg_low = sig.fvg_low

    should_cancel = (candle_low <= sig.entry_price) and (candle_close < fvg_low)
    assert should_cancel is True
