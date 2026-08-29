from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from .conftest import make_tfdata, make_cluster, make_signal, make_account, ranging
from xauusd_bot.trade.cluster import ClusterManager
from xauusd_bot.trade.trade_manager import TradeManager
from xauusd_bot.models import (
    TradeDirection, TradeStatus, TradeLeg, ExitReason, PyraCluster, SignalGrade,
)
from xauusd_bot.order.exit import ExitManager
from xauusd_bot.order.partial_close import PartialCloseManager
from xauusd_bot.risk.pyramid_manager import PyramidManager
from xauusd_bot.risk.position_sizer import PositionSizer
from xauusd_bot.risk.daily_loss import DailyLossTracker
from xauusd_bot.risk.max_dd import MaxDDTracker


def _make_trade_manager():
    cfg = MagicMock()
    cfg.time_based_exit_minutes = 120
    cfg.atr_period = 14
    cfg.max_pyramid_entries = 4
    cfg.pyramid_add_trigger_r = 0.5
    cfg.pyramid_initial_risk_pct = 0.25
    cfg.atr_multiplier_m1 = 1.2
    cfg.atr_multiplier_m5 = 1.5
    cfg.atr_multiplier_m15 = 2.0
    cfg.atr_multiplier_for_tf.return_value = 2.0
    order_entry = MagicMock()
    order_entry.place_market_order.return_value = TradeLeg(
        position_ticket=1001, direction=TradeDirection.BUY,
        entry_price=2000, lot_size=0.1, sl_price=1980, tp_price=2040,
        open_time=datetime(2025, 1, 1), status=TradeStatus.OPEN,
    )
    order_entry.close_position.return_value = True
    exit_mgr = ExitManager(cfg)
    partial_close = PartialCloseManager(1.0, 50.0)
    pyramid_mgr = PyramidManager(4, 0.5)
    sizer = PositionSizer(0.25, 4)
    daily_loss = DailyLossTracker(3.0, 1.0)
    max_dd = MaxDDTracker(10.0, 2.0)
    return TradeManager(order_entry, exit_mgr, partial_close, pyramid_mgr, sizer, daily_loss, max_dd), order_entry


def _initialize_daily_loss(tm, acc, equity: float):
    acc.equity = equity
    tm.daily_loss.update(acc)


# ── ClusterManager ──

def test_cluster_active_empty():
    cm = ClusterManager()
    assert cm.active == []


def test_cluster_add_and_get():
    cm = ClusterManager()
    c = PyraCluster(direction=TradeDirection.BUY)
    cm.add(c)
    assert cm.get(c.cluster_id) is c
    assert len(cm.active) == 1


def test_cluster_has_active_for_direction():
    cm = ClusterManager()
    c = PyraCluster(direction=TradeDirection.BUY)
    cm.add(c)
    assert cm.has_active_for_direction(TradeDirection.BUY)
    assert not cm.has_active_for_direction(TradeDirection.SELL)


def test_cluster_closed_not_active():
    cm = ClusterManager()
    c = PyraCluster(direction=TradeDirection.BUY, status=TradeStatus.CLOSED)
    cm.add(c)
    assert cm.active == []


def test_cluster_total_open_lots():
    cm = ClusterManager()
    c = make_cluster(TradeDirection.BUY, 2)
    cm.add(c)
    assert abs(cm.total_open_lots() - 0.2) < 0.01


def test_cluster_clear():
    cm = ClusterManager()
    cm.add(PyraCluster(direction=TradeDirection.BUY))
    cm.clear()
    assert cm.active == []


def test_cluster_all_clusters():
    cm = ClusterManager()
    c1 = PyraCluster(direction=TradeDirection.BUY)
    c2 = PyraCluster(direction=TradeDirection.SELL)
    cm.add(c1)
    cm.add(c2)
    assert len(cm.all_clusters) == 2


# ── TradeManager.execute_signal ──

def test_execute_signal_success():
    tm, oe = _make_trade_manager()
    signal = make_signal(TradeDirection.BUY)
    acc = make_account(100000)
    _initialize_daily_loss(tm, acc, 100000)
    data_all = {"M1": make_tfdata("M1", ranging(30))}
    cluster = tm.execute_signal(signal, acc, data_all, 1.0, 100, 0.01, 0.01, 100.0)
    assert cluster is not None
    assert cluster.direction == TradeDirection.BUY
    assert len(cluster.legs) == 1


def test_execute_signal_grade_c():
    tm, oe = _make_trade_manager()
    signal = make_signal(TradeDirection.BUY)
    signal.grade = SignalGrade.C
    acc = make_account(100000)
    cluster = tm.execute_signal(signal, acc, {}, 1.0, 100, 0.01, 0.01)
    assert cluster is None


def test_execute_signal_daily_loss_kill():
    tm, oe = _make_trade_manager()
    signal = make_signal(TradeDirection.BUY)
    acc = make_account(100000)
    _initialize_daily_loss(tm, acc, 100000)
    tm.daily_loss.update(acc)
    acc.equity = 97000
    tm.daily_loss.update(acc)
    assert tm.daily_loss.kill_switch_engaged()
    cluster = tm.execute_signal(signal, acc, {}, 1.0, 100, 0.01, 0.01)
    assert cluster is None


def test_execute_signal_zero_budget():
    tm, oe = _make_trade_manager()
    signal = make_signal(TradeDirection.BUY)
    acc = make_account(1000)
    _initialize_daily_loss(tm, acc, 1000)
    acc.equity = 970
    tm.daily_loss.update(acc)
    # Used 3% of 1000 = 30, remaining_budget = 0
    cluster = tm.execute_signal(signal, acc, {}, 1.0, 100, 0.01, 0.01)
    assert cluster is None


def test_execute_signal_order_fails():
    tm, oe = _make_trade_manager()
    oe.place_market_order.return_value = None
    signal = make_signal(TradeDirection.BUY)
    acc = make_account(100000)
    _initialize_daily_loss(tm, acc, 100000)
    cluster = tm.execute_signal(signal, acc, {}, 1.0, 100, 0.01, 0.01)
    assert cluster is None


# ── TradeManager.manage_pyramid_add ──

def test_manage_pyramid_add():
    tm, oe = _make_trade_manager()
    signal = make_signal(TradeDirection.BUY)
    signal.entry_tf = "M1"
    acc = make_account(100000)
    _initialize_daily_loss(tm, acc, 100000)
    _utc = lambda: datetime.now(timezone.utc).replace(tzinfo=None)
    data_all = {"M1": make_tfdata("M1", [2010] * 50)}
    cluster = tm.pyramid_mgr.create_cluster("sig1", TradeDirection.BUY, "M1")
    leg = TradeLeg(direction=TradeDirection.BUY, entry_price=2000, lot_size=0.1,
                   sl_price=1980, open_time=_utc(), status=TradeStatus.OPEN)
    cluster.legs.append(leg)
    cluster.collective_sl = 1980
    cluster.open_time = _utc()
    result = tm.manage_pyramid_add(signal, cluster, acc, data_all, 1.0, 100, 0.01, 0.01)
    assert result is not None


def test_manage_pyramid_add_insufficient_move():
    _utc = lambda: datetime.now(timezone.utc).replace(tzinfo=None)
    tm, oe = _make_trade_manager()
    signal = make_signal(TradeDirection.BUY)
    signal.entry_tf = "M1"
    acc = make_account(100000)
    _initialize_daily_loss(tm, acc, 100000)
    data_all = {"M1": make_tfdata("M1", [2000] * 50)}  # price hasn't moved
    cluster = tm.pyramid_mgr.create_cluster("sig1", TradeDirection.BUY, "M1")
    leg = TradeLeg(direction=TradeDirection.BUY, entry_price=2000, lot_size=0.1,
                   sl_price=1980, open_time=_utc(), status=TradeStatus.OPEN)
    cluster.legs.append(leg)
    cluster.collective_sl = 1980
    cluster.open_time = _utc()
    result = tm.manage_pyramid_add(signal, cluster, acc, data_all, 1.0, 100, 0.01, 0.01)
    assert result is None


# ── TradeManager.manage_exits ──

def test_manage_exits_partial_tp():
    tm, oe = _make_trade_manager()
    cluster = make_cluster(TradeDirection.BUY, 1)
    cluster.legs[0].entry_price = 2000
    cluster.collective_sl = 1980
    _utc = lambda: datetime.now(timezone.utc).replace(tzinfo=None)
    cluster.open_time = _utc() - timedelta(minutes=10)
    data_all = {"M1": make_tfdata("M1", [2030] * 30), "M5": make_tfdata("M5", [2030] * 30)}
    actions = tm.manage_exits(cluster, data_all)
    tp_actions = [a for a in actions if a["action"] == "partial_tp"]
    assert len(tp_actions) > 0


def test_manage_exits_chandelier():
    _utc = lambda: datetime.now(timezone.utc).replace(tzinfo=None)
    tm, oe = _make_trade_manager()
    cluster = make_cluster(TradeDirection.BUY, 1)
    cluster.highest_price = 2050
    cluster.legs[0].entry_price = 2000
    cluster.open_time = _utc() - timedelta(minutes=10)
    data_all = {"M1": make_tfdata("M1", [1950] * 30), "M5": make_tfdata("M5", [1950] * 30)}
    actions = tm.manage_exits(cluster, data_all)
    chandelier = [a for a in actions if a["action"] == "chandelier_exit"]
    assert len(chandelier) > 0


def test_manage_exits_time_based():
    tm, oe = _make_trade_manager()
    cluster = make_cluster(TradeDirection.BUY, 1)
    cluster.open_time = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=3)
    data_all = {"M1": make_tfdata("M1", [2010] * 30), "M5": make_tfdata("M5", [2010] * 30)}
    actions = tm.manage_exits(cluster, data_all)
    time_exits = [a for a in actions if a["action"] == "time_exit"]
    assert len(time_exits) > 0


def test_manage_exits_no_price_data():
    tm, oe = _make_trade_manager()
    cluster = make_cluster(TradeDirection.BUY, 1)
    actions = tm.manage_exits(cluster, {})
    assert actions == []


def test_close_cluster_positions():
    tm, oe = _make_trade_manager()
    cluster = make_cluster(TradeDirection.BUY, 2)
    tm._close_cluster_positions(cluster, 2010, ExitReason.TAKE_PROFIT)
    for leg in cluster.legs:
        assert leg.status == TradeStatus.CLOSED
        assert leg.exit_price == 2010
        assert leg.exit_reason == ExitReason.TAKE_PROFIT
    assert cluster.status == TradeStatus.CLOSED