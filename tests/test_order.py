from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from .conftest import make_signal, make_account
from xauusd_bot.order.exit import ExitManager
from xauusd_bot.order.partial_close import PartialCloseManager
from xauusd_bot.models import (
    TradeDirection, TradeStatus, TradeLeg, PyraCluster, TimeframeData, ExitReason,
)


# ── ExitManager ──

def _make_exit_mgr():
    cfg = MagicMock()
    cfg.atr_period = 14
    cfg.atr_multiplier_m1 = 1.2
    cfg.atr_multiplier_m5 = 1.5
    cfg.atr_multiplier_m15 = 2.0
    cfg.atr_multiplier_for_tf.return_value = 2.0
    cfg.time_based_exit_minutes = 120
    cfg.max_r_multiple = 2.5
    return ExitManager(cfg)


def _make_data():
    from datetime import datetime, timedelta, timezone
    base = datetime(2025, 1, 1)
    n = 50
    return TimeframeData(
        tf="M15",
        time=[base + timedelta(minutes=15 * i) for i in range(n)],
        open=[2000 + i * 0.5 for i in range(n)],
        high=[2001 + i * 0.5 + 0.5 for i in range(n)],
        low=[1999 + i * 0.5 - 0.5 for i in range(n)],
        close=[2000 + i * 0.5 for i in range(n)],
        tick_volume=[100] * n,
        spread=[10] * n,
    )


def test_calc_atr_sl_buy():
    em = _make_exit_mgr()
    data = _make_data()
    sl = em.calc_atr_sl(data, TradeDirection.BUY, "M15")
    assert sl < data.close[-1]


def test_calc_atr_sl_sell():
    em = _make_exit_mgr()
    data = _make_data()
    sl = em.calc_atr_sl(data, TradeDirection.SELL, "M15")
    assert sl > data.close[-1]


def test_calc_structure_tp_buy():
    em = _make_exit_mgr()
    data = _make_data()
    tp = em.calc_structure_tp(data, TradeDirection.BUY, data.close[-1], 5.0)
    assert tp > data.close[-1]


def test_calc_structure_tp_sell():
    em = _make_exit_mgr()
    data = _make_data()
    tp = em.calc_structure_tp(data, TradeDirection.SELL, data.close[-1], 5.0)
    assert tp < data.close[-1]


def test_check_time_exit_not_triggered():
    em = _make_exit_mgr()
    cluster = PyraCluster(open_time=datetime.now(timezone.utc).replace(tzinfo=None), direction=TradeDirection.BUY)
    assert not em.check_time_exit(cluster)


def test_check_time_exit_triggered():
    em = _make_exit_mgr()
    cluster = PyraCluster(
        open_time=datetime(2024, 1, 1),  # far in the past
        direction=TradeDirection.BUY,
    )
    assert em.check_time_exit(cluster)


def test_check_chandelier_exit_buy():
    em = _make_exit_mgr()
    data = _make_data()
    cluster = PyraCluster(direction=TradeDirection.BUY, highest_price=2100)
    stop = em.check_chandelier_exit(data, cluster)
    assert stop is not None
    assert stop < cluster.highest_price


def test_check_chandelier_exit_sell():
    em = _make_exit_mgr()
    data = _make_data()
    cluster = PyraCluster(direction=TradeDirection.SELL, lowest_price=1900)
    stop = em.check_chandelier_exit(data, cluster)
    assert stop is not None
    assert stop > cluster.lowest_price


# ── PartialCloseManager ──

def make_cluster_for_partial():
    cluster = PyraCluster(direction=TradeDirection.BUY)
    leg = TradeLeg(direction=TradeDirection.BUY, entry_price=2000, lot_size=0.1,
                   sl_price=1980, open_time=datetime(2025, 1, 1), status=TradeStatus.OPEN)
    cluster.legs.append(leg)
    cluster.collective_sl = 1980
    return cluster


def test_partial_tp_triggered():
    pcm = PartialCloseManager(1.0, 50.0)
    cluster = make_cluster_for_partial()
    assert pcm.check_partial_tp(cluster, 2025)  # 25/20 = 1.25R >= 1.0R


def test_partial_tp_not_triggered():
    pcm = PartialCloseManager(1.0, 50.0)
    cluster = make_cluster_for_partial()
    assert not pcm.check_partial_tp(cluster, 2005)  # 5/20 = 0.25R < 1.0R


def test_partial_tp_only_once():
    pcm = PartialCloseManager(1.0, 50.0)
    cluster = make_cluster_for_partial()
    assert pcm.check_partial_tp(cluster, 2025)
    assert not pcm.check_partial_tp(cluster, 2030)  # already hit


def test_partial_tp_reset():
    pcm = PartialCloseManager(1.0, 50.0)
    cluster = make_cluster_for_partial()
    assert pcm.check_partial_tp(cluster, 2025)
    pcm.reset()
    assert pcm.check_partial_tp(cluster, 2025)  # can hit again after reset


def test_partial_tp_no_collective_sl():
    pcm = PartialCloseManager(1.0, 50.0)
    cluster = make_cluster_for_partial()
    cluster.collective_sl = 0
    assert not pcm.check_partial_tp(cluster, 2025)  # no sl means no risk measure