import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from xauusd_bot.models import AccountInfo, DailyState, PyraCluster, TradeDirection, TradeLeg, TradeStatus
from xauusd_bot.risk.daily_loss import DailyLossTracker
from xauusd_bot.risk.max_dd import MaxDDTracker
from xauusd_bot.risk.position_sizer import PositionSizer
from xauusd_bot.risk.pyramid_manager import PyramidManager


# ── DailyLossTracker ──

def test_daily_loss_update():
    dt = DailyLossTracker(3.0, 1.0)
    acc = AccountInfo(balance=100000, equity=100000)
    dt.update(acc)
    assert dt.state is not None
    assert dt.state.start_equity == 100000


def test_daily_loss_used():
    dt = DailyLossTracker(3.0, 1.0)
    acc = AccountInfo(balance=100000, equity=100000)
    dt.update(acc)
    acc.equity = 98000
    dt.update(acc)
    assert abs(dt.loss_used_pct() - 2.0) < 0.01


def test_remaining_budget():
    dt = DailyLossTracker(3.0, 1.0)
    acc = AccountInfo(balance=100000, equity=100000)
    dt.update(acc)
    acc.equity = 98500
    dt.update(acc)
    budget = dt.remaining_budget_amount()
    expected = 100000 * 0.03 - (100000 - 98500)
    assert abs(budget - expected) < 1.0


def test_kill_switch_not_engaged():
    dt = DailyLossTracker(3.0, 1.0)
    acc = AccountInfo(balance=100000, equity=100000)
    dt.update(acc)
    assert not dt.kill_switch_engaged()


def test_kill_switch_engaged():
    dt = DailyLossTracker(3.0, 1.0)
    acc = AccountInfo(balance=100000, equity=100000)
    dt.update(acc)
    acc.equity = 97000
    dt.update(acc)
    assert dt.kill_switch_engaged()
    assert dt.state.kill_switch_active


def test_kill_switch_buffer_edge():
    dt = DailyLossTracker(3.0, 1.0)
    acc = AccountInfo(balance=100000, equity=100000)
    dt.update(acc)
    acc.equity = 97500
    dt.update(acc)
    # used=2.5%, limit=3%, buffer=1% → effective limit=2% → kill switch engaged at 2.5% > 2%
    assert dt.kill_switch_engaged()


def test_new_day_reset():
    dt = DailyLossTracker(3.0, 1.0)
    acc = AccountInfo(balance=100000, equity=100000)
    dt.update(acc)
    day1 = dt.state.date
    assert dt.state.start_equity == 100000
    acc.equity = 97000
    dt.update(acc)
    assert dt.loss_used_pct() > 0
    # Simulate next day by forcing a new date via server_time
    from datetime import timedelta
    tomorrow_time = (acc.server_time or datetime.now(timezone.utc).replace(tzinfo=None)) + timedelta(days=1)
    acc.server_time = tomorrow_time
    acc.equity = 101000
    dt.update(acc)
    assert dt.state.start_equity == 101000
    assert abs(dt.loss_used_pct()) < 0.01


# ── MaxDDTracker ──

def test_max_dd_no_drawdown():
    dd = MaxDDTracker(10.0, 2.0)
    dd.update(100000)
    assert dd.current_dd_pct == 0.0
    assert not dd.is_near_limit()


def test_max_dd_tracking():
    dd = MaxDDTracker(10.0, 2.0)
    dd.update(100000)
    dd.update(95000)
    assert abs(dd.current_dd_pct - 5.0) < 0.1


def test_max_dd_peak_update():
    dd = MaxDDTracker(10.0, 2.0)
    dd.update(100000)
    dd.update(95000)
    dd.update(105000)
    dd.update(100000)
    # peak was 105000, current 100000 → dd = 5000/105000 ≈ 4.76%
    assert abs(dd.current_dd_pct - 4.76) < 0.1


def test_max_dd_kill_switch():
    dd = MaxDDTracker(10.0, 2.0)
    dd.update(100000)
    dd.update(80000)
    assert dd.kill_switch_engaged()


def test_max_dd_near_limit():
    dd = MaxDDTracker(10.0, 2.0)
    dd.update(100000)
    dd.update(88000)
    assert dd.is_near_limit()


# ── PositionSizer ──

def test_position_sizer_basic():
    ps = PositionSizer(0.25, 4)
    acc = AccountInfo(balance=100000, equity=100000)
    lot = ps.calculate_lot_size(
        account=acc, entry_price=2000, sl_price=1980,
        direction=TradeDirection.BUY, point_value=1.0, contract_size=100,
    )
    risk_per_lot = 20 * 1.0 * 100
    expected = (100000 * 0.0025) / risk_per_lot
    assert abs(lot - expected) < 0.01


def test_position_sizer_with_budget():
    ps = PositionSizer(0.25, 4)
    acc = AccountInfo(balance=100000, equity=100000)
    lot = ps.calculate_lot_size(
        account=acc, entry_price=2000, sl_price=1980,
        direction=TradeDirection.BUY, point_value=1.0, contract_size=100,
        remaining_budget=500,
    )
    assert lot > 0


def test_position_sizer_min_lot():
    ps = PositionSizer(0.01, 4)
    acc = AccountInfo(balance=1000, equity=1000)
    lot = ps.calculate_lot_size(
        account=acc, entry_price=2000, sl_price=1999,
        direction=TradeDirection.BUY, point_value=1.0, contract_size=100,
        min_lot=0.01,
    )
    assert lot >= 0.01


def test_position_sizer_invalid_prices():
    ps = PositionSizer(0.25, 4)
    acc = AccountInfo(balance=100000, equity=100000)
    lot = ps.calculate_lot_size(
        account=acc, entry_price=0, sl_price=0,
        direction=TradeDirection.BUY, point_value=1.0, contract_size=100,
    )
    assert lot >= 0.01


def test_calc_risk_amount():
    ps = PositionSizer()
    risk = ps.calc_risk_amount(1.0, 2000, 1980, TradeDirection.BUY, 1.0, 100)
    assert abs(risk - 2000) < 0.01


# ── PyramidManager ──

def test_create_cluster():
    pm = PyramidManager(4, 0.5)
    c = pm.create_cluster("sig1", TradeDirection.BUY, "M1")
    assert c is not None
    assert c.status == TradeStatus.OPEN


def test_can_add_leg_first():
    pm = PyramidManager(4, 0.5)
    c = pm.create_cluster("sig1", TradeDirection.BUY, "M1")
    leg = TradeLeg(entry_price=2000, lot_size=0.1, sl_price=1980, status=TradeStatus.OPEN)
    c.legs.append(leg)
    c.collective_sl = 1980
    assert pm.can_add_leg(c, 2010, 2000, 1980)


def test_cannot_add_beyond_max():
    pm = PyramidManager(2, 0.5)
    c = pm.create_cluster("sig1", TradeDirection.BUY, "M1")
    for _ in range(2):
        leg = TradeLeg(entry_price=2000, lot_size=0.1, sl_price=1980, status=TradeStatus.OPEN)
        c.legs.append(leg)
    assert not pm.can_add_leg(c, 2050, 2000, 1980)


def test_cannot_add_without_move():
    pm = PyramidManager(4, 0.5)
    c = pm.create_cluster("sig1", TradeDirection.BUY, "M1")
    leg = TradeLeg(entry_price=2000, lot_size=0.1, sl_price=1980, status=TradeStatus.OPEN)
    c.legs.append(leg)
    c.collective_sl = 1980
    assert not pm.can_add_leg(c, 2001, 2000, 1980)


def test_collective_sl_update():
    pm = PyramidManager()
    c = pm.create_cluster("sig1", TradeDirection.BUY, "M1")
    leg = TradeLeg(entry_price=2000, lot_size=0.1, sl_price=1980, status=TradeStatus.OPEN)
    c.legs.append(leg)
    pm.update_collective_sl(c, 1990)
    assert c.collective_sl == 1990
    assert leg.sl_price == 1990


def test_breakeven():
    pm = PyramidManager()
    c = pm.create_cluster("sig1", TradeDirection.BUY, "M1")
    leg = TradeLeg(entry_price=2000, lot_size=0.1, sl_price=1980, status=TradeStatus.OPEN)
    c.legs.append(leg)
    pm.activate_breakeven(c)
    assert c.breakeven_activated
    assert c.collective_sl == 2000


def test_total_open_risk():
    pm = PyramidManager()
    c = pm.create_cluster("sig1", TradeDirection.BUY, "M1")
    leg = TradeLeg(entry_price=2000, lot_size=1.0, sl_price=1980, status=TradeStatus.OPEN)
    c.legs.append(leg)
    total = pm.total_open_risk(1.0, 100)
    assert abs(total - 2000) < 0.01
