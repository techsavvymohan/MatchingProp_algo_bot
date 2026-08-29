from datetime import datetime, timezone, timedelta
from unittest.mock import patch

from xauusd_bot.backtesting.engine import BacktestEngine, TypeSliceData
from xauusd_bot.config import Config
from xauusd_bot.models import TimeframeData, TradeDirection, TradeStatus, PyraCluster, TradeLeg


def _make_data(bars=500):
    times = [datetime(2025, 1, 1, 0, 0) + timedelta(minutes=i) for i in range(bars)]
    return {
        "M1": TimeframeData(
            tf="M1", time=times,
            open=[2000 + i * 0.1 for i in range(bars)],
            high=[2001 + i * 0.1 for i in range(bars)],
            low=[1999 + i * 0.1 for i in range(bars)],
            close=[2000.5 + i * 0.1 for i in range(bars)],
            tick_volume=[100] * bars,
            spread=[5] * bars,
        ),
    }


def _make_config():
    cfg = Config()
    cfg.trading.backtest_initial_balance = 100000
    cfg.trading.daily_loss_limit_pct = 5.0
    cfg.trading.pyramid_initial_risk_pct = 1.0
    cfg.trading.max_pyramid_entries = 3
    cfg.trading.time_based_exit_minutes = 120
    return cfg


def test_backtest_engine_init():
    cfg = _make_config()
    engine = BacktestEngine(cfg)
    assert engine.cfg == cfg
    assert engine.trades == []


def test_backtest_run_empty_data():
    cfg = _make_config()
    engine = BacktestEngine(cfg)
    result = engine.run({})
    assert result == {}


def test_backtest_run_no_m1():
    cfg = _make_config()
    engine = BacktestEngine(cfg)
    result = engine.run({"M5": _make_data()["M1"]})
    assert result == {}


def test_backtest_run_no_trades():
    cfg = _make_config()
    engine = BacktestEngine(cfg)
    data = {"M1": _make_data(150)["M1"]}
    result = engine.run(data)
    assert isinstance(result, dict)
    assert "total_trades" in result


def test_convert_dicts():
    cfg = _make_config()
    engine = BacktestEngine(cfg)
    raw = {
        "M1": {
            "time": ["2025-01-01T00:00:00", "2025-01-01T00:01:00"],
            "open": [2000, 2001], "high": [2001, 2002],
            "low": [1999, 2000], "close": [2000.5, 2001.5],
            "tick_volume": [100, 101], "spread": [5, 5],
        }
    }
    result = engine._convert_dicts(raw)
    assert "M1" in result
    assert result["M1"].close == [2000.5, 2001.5]


def test_convert_dicts_with_volume_alias():
    cfg = _make_config()
    engine = BacktestEngine(cfg)
    raw = {
        "M1": {
            "time": ["2025-01-01T00:00:00"],
            "open": [2000], "high": [2001],
            "low": [1999], "close": [2000.5],
            "volume": [100], "spread": [5],
        }
    }
    result = engine._convert_dicts(raw)
    assert result["M1"].tick_volume == [100]


def _make_tfdata(tf, bars=500):
    times = [datetime(2025, 1, 1, 0, 0) + timedelta(minutes=i) for i in range(bars)]
    return TimeframeData(
        tf=tf, time=times,
        open=[2000 + i * 0.1 for i in range(bars)],
        high=[2001 + i * 0.1 for i in range(bars)],
        low=[1999 + i * 0.1 for i in range(bars)],
        close=[2000.5 + i * 0.1 for i in range(bars)],
        tick_volume=[100] * bars,
        spread=[5] * bars,
    )


def _make_multi_data(bars=500):
    d = dict(_make_data(bars))
    for tf in ["M5", "M15", "M30", "H1", "H4"]:
        ratio = {"M5": 1, "M15": 3, "M30": 6, "H1": 12, "H4": 48}[tf]
        needed = bars // ratio + 200
        d[tf] = _make_tfdata(tf, needed)
    return d


def test_slice_data():
    cfg = _make_config()
    engine = BacktestEngine(cfg)
    data = _make_multi_data(500)
    sliced = engine._slice_data(data, "M5", 200)
    assert sliced is not None
    assert sliced.tf == "M5"


def test_slice_data_not_found():
    cfg = _make_config()
    engine = BacktestEngine(cfg)
    data = _make_multi_data(500)
    sliced = engine._slice_data(data, "M30", 50)
    assert sliced is not None


def test_slice_data_missing_tf():
    cfg = _make_config()
    engine = BacktestEngine(cfg)
    data = {}
    sliced = engine._slice_data(data, "M5", 100)
    assert sliced is None


def test_type_slice_data():
    src = _make_data(50)["M1"]
    sliced = TypeSliceData(src, 5, 15)
    assert sliced.tf == "M1"
    assert len(sliced.close) == 10


def test_compute_equity_no_clusters():
    cfg = _make_config()
    engine = BacktestEngine(cfg)
    eq = engine._compute_equity(100000, 2000.0, 0)
    assert eq == 100000.0


def test_close_all():
    cfg = _make_config()
    engine = BacktestEngine(cfg)
    _utc = lambda: datetime.now(timezone.utc).replace(tzinfo=None)
    cluster = PyraCluster(signal_id="s1", direction=TradeDirection.BUY, entry_tf="M1",
                          open_time=_utc(), status=TradeStatus.OPEN)
    leg = TradeLeg(direction=TradeDirection.BUY, entry_price=2000, lot_size=0.1,
                   sl_price=1980, open_time=_utc(), status=TradeStatus.OPEN)
    cluster.legs.append(leg)
    engine._clusters.append(cluster)
    engine._close_all(1990.0)
    assert leg.status == TradeStatus.CLOSED
    assert leg.exit_price == 1990.0
    assert leg.exit_reason.value == "equity_kill"


def test_report_empty():
    cfg = _make_config()
    engine = BacktestEngine(cfg)
    r = engine._report()
    assert r["total_trades"] == 0
    assert r["wins"] == 0
    assert r["losses"] == 0
    assert r["win_rate"] == 0


def test_report_with_legs():
    _utc = lambda: datetime.now(timezone.utc).replace(tzinfo=None)
    cfg = _make_config()
    engine = BacktestEngine(cfg)
    cluster = PyraCluster(signal_id="s1", direction=TradeDirection.BUY, entry_tf="M1",
                          open_time=_utc(), status=TradeStatus.CLOSED)
    leg = TradeLeg(direction=TradeDirection.BUY, entry_price=2000, lot_size=0.1,
                   sl_price=1980, status=TradeStatus.CLOSED, exit_price=2020, exit_reason="tp")
    cluster.legs.append(leg)
    engine._clusters.append(cluster)
    r = engine._report()
    assert r["total_trades"] == 1
    assert r["wins"] == 1
    assert r["total_pnl"] > 0