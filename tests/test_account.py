from unittest.mock import MagicMock
from datetime import datetime

from xauusd_bot.broker.account import AccountManager


class MockTick:
    bid = 2000.0
    ask = 2000.5
    point = 0.01
    time = 1705300000


class MockInfo:
    balance = 100000.0
    equity = 100500.0
    margin = 1000.0
    margin_free = 99500.0
    margin_level = 10050.0
    leverage = 100
    currency = "USD"
    trade_tick_value = 0.01
    trade_contract_size = 100
    volume_step = 0.01
    volume_min = 0.01
    volume_max = 100.0
    point = 0.01


def _make_connector():
    c = MagicMock()
    c.ensure_connected.return_value = True
    c.account_info.return_value = MockInfo()
    c.symbol_info.return_value = MockInfo()
    c.symbol_info_tick.return_value = MockTick()
    return c


def test_account_refresh():
    conn = _make_connector()
    am = AccountManager(conn)
    info = am.refresh()
    assert info is not None
    assert info.balance == 100000.0
    assert info.equity == 100500.0
    assert info.currency == "USD"


def test_account_current_property():
    conn = _make_connector()
    am = AccountManager(conn)
    assert am.current is None
    am.refresh()
    assert am.current is not None


def test_account_refresh_fail():
    conn = _make_connector()
    conn.ensure_connected.return_value = False
    am = AccountManager(conn)
    info = am.refresh()
    assert info is None


def test_current_spread():
    conn = _make_connector()
    am = AccountManager(conn)
    spread = am.current_spread()
    assert spread == (2000.5 - 2000.0) / 0.01


def test_current_spread_no_tick():
    conn = _make_connector()
    conn.symbol_info_tick.return_value = None
    am = AccountManager(conn)
    spread = am.current_spread()
    assert spread == 0.0


def test_point_value():
    conn = _make_connector()
    am = AccountManager(conn)
    assert am.point_value() == 0.01


def test_point_value_fallback():
    conn = _make_connector()
    conn.symbol_info.return_value = None
    am = AccountManager(conn)
    assert am.point_value() == 0.01


def test_contract_size():
    conn = _make_connector()
    am = AccountManager(conn)
    assert am.contract_size() == 100


def test_lot_step():
    conn = _make_connector()
    am = AccountManager(conn)
    assert am.lot_step() == 0.01


def test_min_lot():
    conn = _make_connector()
    am = AccountManager(conn)
    assert am.min_lot() == 0.01


def test_max_lot():
    conn = _make_connector()
    am = AccountManager(conn)
    assert am.max_lot() == 100.0


def test_point_size():
    conn = _make_connector()
    am = AccountManager(conn)
    assert am.point_size() == 0.01


def test_point_size_fallback():
    conn = _make_connector()
    conn.symbol_info.return_value = None
    am = AccountManager(conn)
    assert am.point_size() == 0.0001