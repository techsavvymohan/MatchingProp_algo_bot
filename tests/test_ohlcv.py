from unittest.mock import MagicMock
from datetime import datetime

from xauusd_bot.data.ohlcv import MultiTFData


def _make_bars(length=100):
    import struct
    bars = []
    for i in range(length):
        bars.append((1705300000 + i * 60, 2000 + i, 2001 + i, 1999 + i, 2000.5 + i, 100 + i, 0, 5))
    return bars


def _make_connector():
    c = MagicMock()
    c.ensure_connected.return_value = True
    c.tf_to_mt5.return_value = 1
    c.copy_rates_from_pos.return_value = _make_bars()
    c.symbol_info_tick.return_value = None
    c.point_size.return_value = 0.01
    return c


def test_multi_tf_init():
    conn = _make_connector()
    m = MultiTFData(conn)
    assert m.symbol == "XAUUSD"
    assert all(m._data[tf] is None for tf in ["M1", "M5", "M15", "M30", "H1", "H4"])


def test_update_all():
    conn = _make_connector()
    m = MultiTFData(conn)
    ok = m.update_all()
    assert ok
    assert m.get("M1") is not None
    assert m.get("M5") is not None


def test_latest_close():
    conn = _make_connector()
    m = MultiTFData(conn)
    m.update_all()
    close = m.latest_close("M1")
    assert close > 0


def test_latest_close_no_data():
    conn = _make_connector()
    m = MultiTFData(conn)
    assert m.latest_close("M1") == 0.0


def test_latest_time():
    conn = _make_connector()
    m = MultiTFData(conn)
    m.update_all()
    t = m.latest_time("M1")
    assert t is not None
    assert isinstance(t, datetime)


def test_latest_time_no_data():
    conn = _make_connector()
    m = MultiTFData(conn)
    assert m.latest_time("M1") is None


def test_all_tfs_empty():
    conn = _make_connector()
    m = MultiTFData(conn)
    assert m.all_tfs() == {}


def test_all_tfs_with_data():
    conn = _make_connector()
    m = MultiTFData(conn)
    m.update_all()
    result = m.all_tfs()
    assert len(result) > 0
    assert "M1" in result


def test_current_spread_no_tick():
    conn = _make_connector()
    m = MultiTFData(conn)
    assert m.current_spread() == 0.0


def test_update_all_handles_failure():
    conn = _make_connector()
    conn.copy_rates_from_pos.return_value = None
    m = MultiTFData(conn)
    ok = m.update_all()
    assert ok
    assert m.get("M1") is None


def test_aggregate_to_m5():
    conn = _make_connector()
    m = MultiTFData(conn)
    m.update_all()
    m5 = m.aggregate_to_tf("M5")
    assert m5 is not None
    assert m5.tf == "M5"


def test_aggregate_to_h4():
    conn = _make_connector()
    m = MultiTFData(conn)
    m.update_all()
    h4 = m.aggregate_to_tf("H4")
    assert h4 is not None
    assert h4.tf == "H4"