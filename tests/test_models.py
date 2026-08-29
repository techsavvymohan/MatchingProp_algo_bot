import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from xauusd_bot.models import (
    Signal, TradeDirection, SignalGrade, Bias, Regime, Session,
    TradeLeg, TradeStatus, ExitReason, PyraCluster, DailyState, AccountInfo,
    TimeframeData,
)
from datetime import datetime, timezone


def test_signal_defaults():
    s = Signal()
    assert s.grade == SignalGrade.C
    assert s.direction == TradeDirection.BUY
    assert s.id is not None
    assert len(s.id) == 12


def test_signal_blocked():
    s = Signal()
    assert not s.blocked()
    s.news_blocked = True
    assert s.blocked()


def test_signal_is_tradeable():
    s = Signal(grade=SignalGrade.A)
    assert s.is_tradeable()
    s.equity_blocked = True
    assert not s.is_tradeable()


def test_signal_grade_c_not_tradeable():
    s = Signal(grade=SignalGrade.C)
    assert not s.is_tradeable()


def test_trade_leg_defaults():
    leg = TradeLeg()
    assert leg.status == TradeStatus.PENDING
    assert leg.exit_reason is None


def test_pyra_cluster_total_lot():
    c = PyraCluster()
    c.legs.append(TradeLeg(lot_size=0.1, status=TradeStatus.OPEN))
    c.legs.append(TradeLeg(lot_size=0.2, status=TradeStatus.OPEN))
    assert abs(c.total_lot_size() - 0.3) < 0.01


def test_pyra_cluster_leg_count():
    c = PyraCluster()
    c.legs.append(TradeLeg(lot_size=0.1, status=TradeStatus.OPEN))
    c.legs.append(TradeLeg(status=TradeStatus.CLOSED))
    assert c.leg_count() == 1


def test_pyra_cluster_avg_entry():
    c = PyraCluster(direction=TradeDirection.BUY)
    c.legs.append(TradeLeg(entry_price=2000, lot_size=0.1, status=TradeStatus.OPEN))
    c.legs.append(TradeLeg(entry_price=2010, lot_size=0.1, status=TradeStatus.OPEN))
    assert abs(c.avg_entry_price() - 2005) < 0.01


def test_pyra_cluster_unrealized_pnl_buy():
    c = PyraCluster(direction=TradeDirection.BUY)
    c.legs.append(TradeLeg(entry_price=2000, lot_size=0.1, status=TradeStatus.OPEN, direction=TradeDirection.BUY))
    pnl = c.unrealized_pnl(2010, 1.0, 100)
    assert abs(pnl - 100) < 0.01


def test_pyra_cluster_unrealized_pnl_sell():
    c = PyraCluster(direction=TradeDirection.SELL)
    c.legs.append(TradeLeg(entry_price=2000, lot_size=0.1, status=TradeStatus.OPEN, direction=TradeDirection.SELL))
    pnl = c.unrealized_pnl(1990, 1.0, 100)
    assert abs(pnl - 100) < 0.01


def test_pyra_cluster_total_risk():
    c = PyraCluster(direction=TradeDirection.BUY)
    c.legs.append(TradeLeg(entry_price=2000, lot_size=0.1, sl_price=1990, status=TradeStatus.OPEN))
    risk = c.total_risk_amount(1.0, 100)
    assert abs(risk - 100) < 0.01


def test_daily_state_defaults():
    ds = DailyState()
    assert not ds.kill_switch_active
    assert ds.trades_today == 0


def test_account_info_defaults():
    ai = AccountInfo()
    assert ai.currency == "USD"
    assert ai.leverage == 0


def test_timeframe_data_current():
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    td = TimeframeData(
        tf="M1", time=[now], open=[100], high=[101],
        low=[99], close=[100.5], tick_volume=[100], spread=[10],
    )
    c = td.current
    assert c["close"] == 100.5
    assert c["spread"] == 10


def test_timeframe_data_len():
    td = TimeframeData(tf="M1", time=[], open=[], high=[], low=[], close=[], tick_volume=[], spread=[])
    assert td.len() == 0
    td = TimeframeData(tf="M1", time=[datetime.now(timezone.utc).replace(tzinfo=None)], open=[100], high=[101], low=[99], close=[100.5], tick_volume=[100], spread=[10])
    assert td.len() == 1
