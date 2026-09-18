from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from .conftest import make_tfdata, make_cluster, make_signal, make_account
from xauusd_bot.trade.trade_manager import TradeManager
from xauusd_bot.models import (
    TradeDirection, TradeStatus, TradeLeg, ExitReason, PyraCluster, Signal,
)
from xauusd_bot.order.exit import ExitManager
from xauusd_bot.order.partial_close import PartialCloseManager
from xauusd_bot.risk.pyramid_manager import PyramidManager
from xauusd_bot.risk.position_sizer import PositionSizer
from xauusd_bot.risk.daily_loss import DailyLossTracker
from xauusd_bot.risk.max_dd import MaxDDTracker


def _make_tm():
    cfg = MagicMock()
    cfg.time_based_exit_minutes = 120
    cfg.atr_period = 14
    cfg.max_pyramid_entries = 4
    cfg.pyramid_add_trigger_r = 0.5
    cfg.pyramid_initial_risk_pct = 0.25
    cfg.strategy_trigger_type = "xau_liquidity_sweep_fvg_m1"
    cfg.enable_psar_trailing = True
    cfg.enable_split_tranche_runner = True
    cfg.xau_breakeven_ratchet_enabled = True
    cfg.xau_breakeven_trigger_r = 1.0
    cfg.xau_breakeven_buffer_r = 0.05
    cfg.xau_partial_close_enabled = False

    order_entry = MagicMock()
    order_entry.close_position.return_value = True
    order_entry.modify_sl_tp.return_value = True
    exit_mgr = ExitManager(cfg)
    partial_close = PartialCloseManager(1.0, 50.0)
    pyramid_mgr = PyramidManager(4, 0.5)
    sizer = PositionSizer(0.25, 4)
    daily_loss = DailyLossTracker(3.0, 1.0)
    max_dd = MaxDDTracker(10.0, 2.0)
    return TradeManager(order_entry, exit_mgr, partial_close, pyramid_mgr, sizer, daily_loss, max_dd), order_entry


def test_fvg_scalp_not_killed_by_opposite_psar():
    """FVG Scalp trade must NOT be exited by Parabolic SAR reversal upon entry."""
    tm, oe = _make_tm()
    _utc = lambda: datetime.now(timezone.utc).replace(tzinfo=None)

    # SELL setup on Gold/EUR: swept buy-side liquidity, so previous trend was strongly bullish
    cluster = PyraCluster(direction=TradeDirection.SELL, symbol="EURUSD")
    cluster.fvg_low = 1.14850
    cluster.fvg_high = 1.14865
    cluster.highest_price = 1.14861
    cluster.lowest_price = 1.14861
    cluster.collective_sl = 1.14895
    cluster.open_time = _utc()  # freshly opened trade (0 seconds old)

    leg = TradeLeg(
        position_ticket=5001,
        direction=TradeDirection.SELL,
        entry_price=1.14861,
        lot_size=1.0,
        sl_price=1.14895,
        tp_price=1.14808,
        open_time=_utc(),
        status=TradeStatus.OPEN,
    )
    cluster.legs.append(leg)

    # Bullish M5 & M1 bars (representing recent run-up before reversal)
    bullish_prices = [1.14700 + i * 0.00010 for i in range(30)]
    data_all = {
        "M1": make_tfdata("M1", bullish_prices),
        "M5": make_tfdata("M5", bullish_prices),
    }

    actions = tm.manage_exits(cluster, data_all)

    # Must NOT have psar_exit or premature market close!
    exit_types = [a.get("action") for a in actions]
    assert "psar_exit" not in exit_types
    assert "chandelier_exit" not in exit_types
    assert cluster.status == TradeStatus.OPEN
    assert oe.close_position.call_count == 0


def test_lowest_price_watermark_for_sell_trades():
    """Lowest price must correctly track lowest excursion on SELL trades, not stay at 0.0."""
    tm, oe = _make_tm()
    _utc = lambda: datetime.now(timezone.utc).replace(tzinfo=None)

    cluster = PyraCluster(direction=TradeDirection.SELL, symbol="XAUUSD")
    cluster.fvg_low = 4390.0
    cluster.fvg_high = 4395.0
    cluster.highest_price = 4395.37
    cluster.lowest_price = 0.0  # test recovery from zero initialization
    cluster.collective_sl = 4402.0
    cluster.open_time = _utc()

    leg = TradeLeg(
        position_ticket=5002,
        direction=TradeDirection.SELL,
        entry_price=4395.37,
        lot_size=0.12,
        sl_price=4402.0,
        tp_price=4381.0,
        open_time=_utc(),
        status=TradeStatus.OPEN,
    )
    cluster.legs.append(leg)

    # Price moves down in favor to 4392.0
    data_all = {
        "M1": make_tfdata("M1", [4395.37] * 20 + [4392.0] * 10),
        "M5": make_tfdata("M5", [4395.37] * 30),
    }

    tm.manage_exits(cluster, data_all)

    assert cluster.lowest_price == 4392.0
    assert cluster.lowest_price > 0.0


def test_young_momentum_trade_not_closed_by_psar():
    """Even a momentum trade under 180s old is protected from 0-second instant closure."""
    tm, oe = _make_tm()
    _utc = lambda: datetime.now(timezone.utc).replace(tzinfo=None)

    cluster = PyraCluster(direction=TradeDirection.BUY, symbol="XAUUSD")
    cluster.open_time = _utc()  # 0 seconds old
    cluster.highest_price = 2000.0
    cluster.lowest_price = 2000.0
    cluster.collective_sl = 1990.0

    leg = TradeLeg(
        position_ticket=5003,
        direction=TradeDirection.BUY,
        entry_price=2000.0,
        lot_size=0.1,
        sl_price=1990.0,
        tp_price=2020.0,
        open_time=_utc(),
        status=TradeStatus.OPEN,
    )
    cluster.legs.append(leg)

    # Bearish candles where PSAR would oppose BUY
    bearish_prices = [2010.0 - i * 0.5 for i in range(30)]
    data_all = {
        "M1": make_tfdata("M1", bearish_prices),
        "M5": make_tfdata("M5", bearish_prices),
    }

    actions = tm.manage_exits(cluster, data_all)

    exit_types = [a.get("action") for a in actions]
    assert "psar_exit" not in exit_types
    assert cluster.status == TradeStatus.OPEN
    assert oe.close_position.call_count == 0
