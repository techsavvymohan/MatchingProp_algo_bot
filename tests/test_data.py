from datetime import datetime
from unittest.mock import MagicMock

from .conftest import make_tfdata, trending_bull
from xauusd_bot.data.ohlcv import MultiTFData, TIMEFRAMES


def _mock_connector():
    m = MagicMock()
    m.ensure_connected.return_value = True
    m.tf_to_mt5.return_value = 1
    m.point_size.return_value = 0.01
    return m


def test_multi_tf_init():
    conn = _mock_connector()
    d = MultiTFData(conn)
    assert list(d._data.keys()) == TIMEFRAMES
    assert all(v is None for v in d._data.values())


def test_update_all_handles_no_data_gracefully():
    conn = _mock_connector()
    conn.copy_rates_from_pos.return_value = None
    d = MultiTFData(conn)
    result = d.update_all()
    for tf in TIMEFRAMES:
        assert d.get(tf) is None


def test_update_all_with_data():
    conn = _mock_connector()
    bars = [(datetime(2025, 1, 1).timestamp(), 100, 101, 99, 100.5, 1000, 0, 10) for _ in range(100)]
    conn.copy_rates_from_pos.return_value = bars
    d = MultiTFData(conn)
    assert d.update_all()
    for tf in TIMEFRAMES:
        assert d.get(tf) is not None


def test_latest_close():
    conn = _mock_connector()
    bars = [(datetime(2025, 1, 1).timestamp(), 100 + i, 101 + i, 99 + i, 100.5 + i, 1000, 0, 10) for i in range(10)]
    conn.copy_rates_from_pos.return_value = bars
    d = MultiTFData(conn)
    d.update_all()
    assert d.latest_close("M1") == 109.5


def test_latest_time():
    conn = _mock_connector()
    bars = [(datetime(2025, 1, 1).timestamp(), 100, 101, 99, 100.5, 1000, 0, 10) for _ in range(10)]
    conn.copy_rates_from_pos.return_value = bars
    d = MultiTFData(conn)
    d.update_all()
    t = d.latest_time("M1")
    assert t is not None
    assert isinstance(t, datetime)


def test_current_spread():
    conn = _mock_connector()
    tick = MagicMock()
    tick.ask = 2000.5
    tick.bid = 2000.0
    conn.symbol_info_tick.return_value = tick
    d = MultiTFData(conn, "XAUUSD")
    sp = d.current_spread()
    assert abs(sp - 50.0) < 0.01


def test_aggregate_to_tf_returns_exact():
    conn = _mock_connector()
    bars = [(datetime(2025, 1, 1).timestamp(), 100, 101, 99, 100.5, 1000, 0, 10) for _ in range(100)]
    conn.copy_rates_from_pos.return_value = bars
    d = MultiTFData(conn)
    d.update_all()
    result = d.aggregate_to_tf("M5", 10)
    assert result is not None
    assert result.tf == "M5"
    assert len(result.close) <= 10


def test_all_tfs_empty():
    conn = _mock_connector()
    d = MultiTFData(conn)
    assert d.all_tfs() == {}


def test_all_tfs_with_data():
    conn = _mock_connector()
    bars = [(datetime(2025, 1, 1).timestamp(), 100, 101, 99, 100.5, 1000, 0, 10) for _ in range(100)]
    conn.copy_rates_from_pos.return_value = bars
    d = MultiTFData(conn)
    d.update_all()
    all_tfs = d.all_tfs()
    assert len(all_tfs) == len(TIMEFRAMES)