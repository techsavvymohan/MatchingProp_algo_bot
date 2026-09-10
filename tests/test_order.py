from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
import MetaTrader5 as mt5

from .conftest import make_signal, make_account
from xauusd_bot.order.exit import ExitManager
from xauusd_bot.order.partial_close import PartialCloseManager
from xauusd_bot.order.entry import OrderEntry
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


# ── OrderEntry ──

def test_order_entry_get_filling_mode():
    connector = MagicMock()
    cfg = MagicMock()
    entry = OrderEntry(connector, cfg)

    # When symbol info has filling_mode with IOC bit (2)
    info_ioc = MagicMock()
    info_ioc.filling_mode = 2  # bit 2 allows IOC
    connector.symbol_info.return_value = info_ioc
    assert entry.get_filling_mode("XAUUSD") == mt5.ORDER_FILLING_IOC

    # When symbol info only has FOK bit (1)
    info_fok = MagicMock()
    info_fok.filling_mode = 1  # bit 1 allows FOK
    connector.symbol_info.return_value = info_fok
    assert entry.get_filling_mode("XAUUSD") == mt5.ORDER_FILLING_FOK

    # When symbol info has filling_mode = 0 (Return)
    info_return = MagicMock()
    info_return.filling_mode = 0
    connector.symbol_info.return_value = info_return
    assert entry.get_filling_mode("XAUUSD") == mt5.ORDER_FILLING_RETURN


def test_order_entry_place_market_order_with_fallback():
    connector = MagicMock()
    connector.ensure_connected.return_value = True
    tick = MagicMock()
    tick.ask = 2005.0
    tick.bid = 2004.5
    connector.symbol_info_tick.return_value = tick

    sym_info = MagicMock()
    sym_info.filling_mode = 2
    sym_info.point = 0.01
    connector.symbol_info.return_value = sym_info

    # First attempt fails (returns None), fallback attempt succeeds
    success_res = MagicMock()
    success_res.order = 12345
    success_res.price = 2005.0
    connector.order_send.side_effect = [None, success_res]

    cfg = MagicMock()
    cfg.symbol = "XAUUSD"
    cfg.deviation_points = 10
    cfg.magic_number = 999
    cfg.comment = "test"

    entry = OrderEntry(connector, cfg)
    sig = make_signal(TradeDirection.BUY)
    sig.entry_price = 2005.0
    sig.sl_price = 1995.0
    sig.tp_price = 2025.0
    sig.lot_size = 0.1
    acc = make_account()

    leg = entry.place_market_order(sig, acc, 1.0, 100)
    assert leg is not None
    assert leg.position_ticket == 12345
    assert leg.entry_price == 2005.0
    assert connector.order_send.call_count == 2


def test_order_entry_close_position_with_fallback():
    connector = MagicMock()
    tick = MagicMock()
    tick.ask = 2010.0
    tick.bid = 2009.5
    connector.symbol_info_tick.return_value = tick

    sym_info = MagicMock()
    sym_info.filling_mode = 1
    connector.symbol_info.return_value = sym_info

    success_res = MagicMock()
    connector.order_send.side_effect = [None, success_res]

    cfg = MagicMock()
    cfg.symbol = "XAUUSD"
    cfg.deviation_points = 10
    cfg.magic_number = 999

    entry = OrderEntry(connector, cfg)
    result = entry.close_position(12345, 0.1, TradeDirection.BUY, "XAUUSD")
    assert result is True
    assert connector.order_send.call_count == 2


def test_signal_reservation():
    from xauusd_bot.order.entry import SignalReservation
    res = SignalReservation()
    # First reservation succeeds
    assert res.reserve("sig_001", "XAUUSD") is True
    # Duplicate signal reservation fails
    assert res.reserve("sig_001", "XAUUSD") is False
    # Concurrent in-flight order for same symbol fails
    assert res.reserve("sig_002", "XAUUSD") is False
    # Another symbol succeeds
    assert res.reserve("sig_003", "EURUSD") is True

    # Releasing unlocks
    res.release("sig_001", "XAUUSD")
    assert res.reserve("sig_004", "XAUUSD") is True


def test_validate_broker_spec():
    connector = MagicMock()
    info = MagicMock()
    info.point = 0.01
    info.trade_stops_level = 50  # 50 points = 0.50
    info.trade_freeze_level = 0
    connector.symbol_info.return_value = info

    cfg = MagicMock()
    entry = OrderEntry(connector, cfg)

    # Valid stop distance: entry=2000, SL=1998 (dist=2.0 > 0.50)
    ok, msg = entry.validate_broker_spec("XAUUSD", 2000.0, 1998.0)
    assert ok is True

    # Invalid stop distance: entry=2000, SL=1999.7 (dist=0.30 < 0.50)
    ok_inv, msg_inv = entry.validate_broker_spec("XAUUSD", 2000.0, 1999.7)
    assert ok_inv is False
    assert "below broker minimum" in msg_inv


def test_check_breakeven_ratchet_buy():
    em = _make_exit_mgr()
    cluster = PyraCluster(direction=TradeDirection.BUY, collective_sl=1995.0)
    cluster.legs.append(TradeLeg(entry_price=2000.0, sl_price=1995.0, status=TradeStatus.OPEN))
    # Risk distance = 5.0. At +1.0R (price = 2005.0), breakeven target should be 2000.0 + 0.05*5.0 = 2000.25
    new_sl = em.check_breakeven_ratchet(cluster, current_price=2005.5, trigger_r=1.0, buffer_r=0.05)
    assert new_sl is not None
    assert new_sl == 2000.25
    assert new_sl > cluster.collective_sl


def test_check_stagnation_exit():
    em = _make_exit_mgr()
    cluster = PyraCluster(direction=TradeDirection.BUY, collective_sl=1995.0)
    cluster.legs.append(TradeLeg(entry_price=2000.0, sl_price=1995.0, status=TradeStatus.OPEN))
    # After 8 bars, price only reached 2001.0 (+0.2R < 0.40R) -> Stagnant
    assert em.check_stagnation_exit(cluster, current_price=2001.0, bars_held=8, max_bars=8, min_r=0.40) is True
    # After 8 bars, price reached 2003.0 (+0.6R >= 0.40R) -> Not stagnant
    assert em.check_stagnation_exit(cluster, current_price=2003.0, bars_held=8, max_bars=8, min_r=0.40) is False