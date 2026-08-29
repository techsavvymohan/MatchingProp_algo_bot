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
