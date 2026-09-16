from unittest.mock import MagicMock
import os
import pytest

from xauusd_bot.config import Config, TradingConfig
from xauusd_bot.models import (
    AccountInfo, Bias, PyraCluster, Regime, Session, Signal, SignalGrade,
    TradeDirection, TradeLeg, TradeStatus,
)
from xauusd_bot.risk.position_sizer import PositionSizer
from xauusd_bot.trade.cluster import ClusterManager
from xauusd_bot.filters.session_filter import SessionFilter
from xauusd_bot.broker.account import AccountManager


def test_config_multi_symbol_parsing():
    os.environ["SYMBOLS"] = "XAUUSD, EURUSD"
    tc = TradingConfig.from_env()
    assert "XAUUSD" in tc.symbols
    assert "EURUSD" in tc.symbols
    assert tc.enable_session_filter is False
    assert tc.enable_sideways_filter is True
    del os.environ["SYMBOLS"]


def test_multi_symbol_position_sizing():
    sizer = PositionSizer(initial_risk_pct=1.0)
    account = AccountInfo(balance=10000.0, equity=10000.0)

    # 1. XAUUSD: Risk 10 dollars ($2700 - $2690)
    # tick_size = 0.01, tick_value = $1.0 (per 1.0 lot)
    # 10.0 / 0.01 = 1000 ticks * $1 = $1000 risk per 1 lot.
    # Risk budget = 1% of 10000 = $100.
    # Expected lot = 100 / 1000 = 0.10 lot
    lot_gold = sizer.calculate_lot_size(
        account=account,
        entry_price=2700.0,
        sl_price=2690.0,
        direction=TradeDirection.BUY,
        point_value=1.0,
        tick_size=0.01,
    )
    assert lot_gold == 0.10

    # 2. EURUSD: Risk 20 pips (1.0850 - 1.0830 = 0.0020)
    # tick_size = 0.00001 (1 point), tick_value = $1.0 (per 1.0 lot)
    # 0.0020 / 0.00001 = 200 ticks * $1 = $200 risk per 1 lot.
    # Risk budget = 1% of 10000 = $100.
    # Expected lot = 100 / 200 = 0.50 lot
    lot_eur = sizer.calculate_lot_size(
        account=account,
        entry_price=1.0850,
        sl_price=1.0830,
        direction=TradeDirection.BUY,
        point_value=1.0,
        tick_size=0.00001,
    )
    assert lot_eur == 0.50


def test_cluster_manager_multi_symbol():
    cm = ClusterManager()

    c_gold = PyraCluster(signal_id="sig1", symbol="XAUUSD", direction=TradeDirection.BUY)
    leg_gold = TradeLeg(position_ticket=101, symbol="XAUUSD", lot_size=0.1, status=TradeStatus.OPEN)
    c_gold.legs.append(leg_gold)

    c_eur = PyraCluster(signal_id="sig2", symbol="EURUSD", direction=TradeDirection.BUY)
    leg_eur = TradeLeg(position_ticket=102, symbol="EURUSD", lot_size=0.5, status=TradeStatus.OPEN)
    c_eur.legs.append(leg_eur)

    cm.add(c_gold)
    cm.add(c_eur)

    assert cm.has_active_for_direction(TradeDirection.BUY, symbol="XAUUSD")
    assert cm.has_active_for_direction(TradeDirection.BUY, symbol="EURUSD")
    assert not cm.has_active_for_direction(TradeDirection.SELL, symbol="XAUUSD")

    gold_clusters = cm.active_clusters_for_symbol("XAUUSD")
    assert len(gold_clusters) == 1
    assert gold_clusters[0].symbol == "XAUUSD"

    eur_clusters = cm.active_clusters_for_symbol("EURUSD")
    assert len(eur_clusters) == 1
    assert eur_clusters[0].symbol == "EURUSD"

    assert cm.total_open_lots("XAUUSD") == 0.1
    assert cm.total_open_lots("EURUSD") == 0.5
    assert cm.total_open_lots() == 0.6


def test_session_freedom_no_restrictions():
    # When enabled=False (no restrictions), all sessions and timezones are allowed
    sf = SessionFilter(enabled=False)
    ok, msg = sf.check()
    assert ok
    assert msg == "all_sessions_allowed"

    tfs = sf.allowed_entry_tfs()
    assert "M1" in tfs
    assert "M5" in tfs
    assert "M15" in tfs
    assert "M30" in tfs


def test_signal_symbol_support():
    sig = Signal(symbol="EURUSD", direction=TradeDirection.BUY, grade=SignalGrade.A)
    assert sig.symbol == "EURUSD"
    assert sig.is_tradeable()

    # Blocked by sideways
    sig.sideways_blocked = True
    assert sig.blocked()
    assert not sig.is_tradeable()


def test_eurusd_scalp_sequence_stop_calculation():
    from xauusd_bot.strategy.trigger import TriggerDetector
    from xauusd_bot.models import TimeframeData

    engine = TriggerDetector()

    # Synthetic EURUSD data (price ~ 1.155)
    n = 30
    times = [1705300000 + i * 60 for i in range(n)]
    # BSL at 1.15514, SSL at 1.15442
    highs = [1.15500] * (n - 6) + [1.15520, 1.15510, 1.15505, 1.15500, 1.15495, 1.15490]
    lows = [1.15450] * (n - 6) + [1.15480, 1.15490, 1.15480, 1.15470, 1.15460, 1.15450]
    closes = [1.15480] * (n - 6) + [1.15515, 1.15500, 1.15490, 1.15480, 1.15470, 1.15460]
    opens = [1.15470] * (n - 6) + [1.15490, 1.15515, 1.15500, 1.15490, 1.15480, 1.15470]
    volumes = [100.0] * n

    m1_data = TimeframeData("M1", times, opens, highs, lows, closes, volumes, [1.0] * n)
    m15_data = TimeframeData("M15", times, opens, highs, lows, closes, volumes, [1.0] * n)

    # Even if caller erroneously passes point_value=1.0 or tick dollar value
    seq = engine.detect_xau_scalp_sequence(
        m15_data=m15_data,
        m1_data=m1_data,
        m1_atr=0.00030,
        point_value=1.0,  # Deliberately test defensive clamping
        stops_level_points=10.0,
        target_r=1.6,
        min_sl_distance=0.0,
    )
    if seq:
        assert seq["entry_price"] < 10.0
        assert seq["sl_price"] < 10.0
        assert seq["tp_price"] > 0.0
        assert seq["sl_price"] > seq["entry_price"] if seq["direction"] == TradeDirection.SELL else seq["sl_price"] < seq["entry_price"]
        # Stops distance must be in pips (e.g. < 0.01 = 100 pips), NEVER 20.0 points!
        assert abs(seq["sl_price"] - seq["entry_price"]) < 0.01


def test_pending_cluster_immune_to_manage_exits():
    from xauusd_bot.trade.trade_manager import TradeManager
    from xauusd_bot.order.entry import OrderEntry
    from xauusd_bot.order.exit import ExitManager
    from xauusd_bot.order.partial_close import PartialCloseManager
    from xauusd_bot.risk.position_sizer import PositionSizer
    from xauusd_bot.risk.daily_loss import DailyLossTracker
    from xauusd_bot.risk.max_dd import MaxDDTracker
    from xauusd_bot.risk.pyramid_manager import PyramidManager
    from xauusd_bot.models import TimeframeData

    conn = MagicMock()
    conn.ensure_connected.return_value = True
    cfg = TradingConfig()
    order_entry = OrderEntry(conn, cfg)
    exit_mgr = ExitManager(cfg)
    trade_mgr = TradeManager(
        order_entry=order_entry,
        exit_mgr=exit_mgr,
        partial_close=PartialCloseManager(),
        pyramid_mgr=PyramidManager(cfg),
        sizer=PositionSizer(),
        daily_loss=DailyLossTracker(),
        max_dd=MaxDDTracker(5.0, 1.0),
    )

    cluster = PyraCluster(signal_id="sig_test", symbol="XAUUSD", direction=TradeDirection.SELL)
    leg = TradeLeg(position_ticket=12345, symbol="XAUUSD", lot_size=0.03, status=TradeStatus.PENDING)
    cluster.legs.append(leg)
    cluster.status = TradeStatus.PENDING

    # Data with PSAR suggesting reversal
    n = 20
    times = list(range(n))
    highs = [4330.0 + i * 0.5 for i in range(n)]
    lows = [4328.0 + i * 0.5 for i in range(n)]
    closes = [4329.0 + i * 0.5 for i in range(n)]
    data_all = {"M1": TimeframeData("M1", times, closes, highs, lows, closes, [100.0] * n, [1.0] * n)}

    actions = trade_mgr.manage_exits(cluster, data_all)
    # Pending cluster must NEVER be exited or cancelled by manage_exits
    assert actions == []
    assert cluster.status == TradeStatus.PENDING
    assert leg.status == TradeStatus.PENDING
    conn.order_send.assert_not_called()


def test_broker_spec_stops_validation():
    from xauusd_bot.order.entry import OrderEntry
    conn = MagicMock()
    conn.symbol_info.return_value = None
    entry = OrderEntry(conn, TradingConfig())

    # 1. Invalid Negative TP
    ok, msg = entry.validate_broker_spec("EURUSD", 1.16000, 1.16200, tp_price=-30.85, direction=TradeDirection.SELL)
    assert not ok
    assert "Invalid TP price" in msg

    # 2. Invalid Stop side: SELL with SL below entry
    ok, msg = entry.validate_broker_spec("EURUSD", 1.16000, 1.15500, tp_price=1.15000, direction=TradeDirection.SELL)
    assert not ok
    assert "Invalid SELL stop" in msg

    # 3. Absurd Forex stop distance (e.g. 21.16 on a 1.16 currency pair)
    ok, msg = entry.validate_broker_spec("EURUSD", 1.16000, 21.16000, tp_price=1.15000, direction=TradeDirection.SELL)
    assert not ok
    assert "abnormally large" in msg

    # 4. Valid normal EURUSD order
    ok, msg = entry.validate_broker_spec("EURUSD", 1.16000, 1.16200, tp_price=1.15600, direction=TradeDirection.SELL)
    assert ok

