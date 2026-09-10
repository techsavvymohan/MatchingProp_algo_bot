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
    assert "sharpe_ratio" in r
    assert "sortino_ratio" in r
    assert "calmar_ratio" in r
    assert "expectancy" in r
    assert "recovery_factor" in r
    assert "kelly_fraction_pct" in r


def test_institutional_quant_metrics_multi_trade():
    _utc = lambda: datetime.now(timezone.utc).replace(tzinfo=None)
    cfg = _make_config()
    engine = BacktestEngine(cfg)

    # Add 3 winning legs and 1 losing leg
    cluster = PyraCluster(signal_id="s1", direction=TradeDirection.BUY, entry_tf="M1",
                          open_time=_utc(), status=TradeStatus.CLOSED)
    leg1 = TradeLeg(direction=TradeDirection.BUY, entry_price=2000, lot_size=0.1,
                    sl_price=1980, status=TradeStatus.CLOSED, exit_price=2030, exit_reason="tp")
    leg2 = TradeLeg(direction=TradeDirection.BUY, entry_price=2010, lot_size=0.1,
                    sl_price=1990, status=TradeStatus.CLOSED, exit_price=2040, exit_reason="tp")
    leg3 = TradeLeg(direction=TradeDirection.BUY, entry_price=2020, lot_size=0.1,
                    sl_price=2000, status=TradeStatus.CLOSED, exit_price=2010, exit_reason="sl")
    cluster.legs.extend([leg1, leg2, leg3])
    engine._clusters.append(cluster)

    r = engine._report()
    assert r["total_trades"] == 3
    assert r["wins"] == 2
    assert r["losses"] == 1
    assert r["win_rate"] == 66.67
    assert r["payoff_ratio"] > 0
    assert r["expectancy"] > 0
    assert r["sharpe_ratio"] > 0
    assert r["sortino_ratio"] > 0

    from xauusd_bot.backtesting.report import print_report
    # Ensure print_report runs smoothly with new quant metrics
    print_report(r)


def test_backtest_top_bottom_hunter_trigger():
    cfg = _make_config()
    cfg.trading.strategy_trigger_type = "top_bottom_hunter"
    cfg.trading.tbh_lookback = 3
    cfg.trading.tbh_fib_0 = 0.382
    cfg.trading.tbh_fib_1 = 0.618
    cfg.trading.tbh_rsi_oversold = 30.0
    cfg.trading.tbh_rsi_overbought = 70.0
    cfg.trading.tbh_atr_sl_mult = 2.0
    cfg.trading.tbh_rr_ratio = 2.0

    engine = BacktestEngine(cfg)
    assert engine.cfg.trading.strategy_trigger_type == "top_bottom_hunter"
    data = {"M1": _make_data(150)["M1"]}
    res = engine.run(data)
    assert isinstance(res, dict)


def test_backtest_run_sweep_schema():
    cfg = _make_config()
    data = {"M1": _make_data(150)["M1"]}
    param_grid = [
        {"config_id": "EXP-001", "tbh_rsi_length": 7, "tbh_lookback": 2},
        {"config_id": "EXP-002", "tbh_rsi_length": 14, "tbh_lookback": 2},
    ]
    results = BacktestEngine.run_sweep(data, cfg, param_grid)
    assert len(results) == 2
    for r in results:
        assert "config_id" in r
        assert "strategy_version" in r
        assert "parameters" in r
        assert "metrics" in r
        assert "status" in r
        assert "captured_at" in r
        m = r["metrics"]
        assert "total_trades" in m
        assert "net_profit" in m
        assert "profit_factor" in m
        assert "max_drawdown_pct" in m


def test_audit_neighborhood_robustness():
    # Construct synthetic results:
    # Config 1: Robust plateau (candidate and neighbor have high PF)
    # Config 2: Robust neighbor of Config 1
    # Config 3: Fragile spike (candidate high PF, neighbor low PF)
    # Config 4: Neighbor of Config 3 with poor PF
    # Config 5: Reject (losing or insufficient trades)
    results = [
        {
            "config_id": "EXP-001",
            "parameters": {"rsi_length": 7, "lookback": 2},
            "metrics": {"total_trades": 20, "net_profit": 500.0, "profit_factor": 1.6},
        },
        {
            "config_id": "EXP-002",
            "parameters": {"rsi_length": 7, "lookback": 3},  # Neighbor (differs by 1 param)
            "metrics": {"total_trades": 18, "net_profit": 420.0, "profit_factor": 1.45},
        },
        {
            "config_id": "EXP-003",
            "parameters": {"rsi_length": 21, "lookback": 2},
            "metrics": {"total_trades": 25, "net_profit": 600.0, "profit_factor": 1.8},
        },
        {
            "config_id": "EXP-004",
            "parameters": {"rsi_length": 21, "lookback": 3},  # Neighbor of 3, but collapsed
            "metrics": {"total_trades": 15, "net_profit": 50.0, "profit_factor": 0.65},
        },
        {
            "config_id": "EXP-005",
            "parameters": {"rsi_length": 50, "lookback": 2},
            "metrics": {"total_trades": 2, "net_profit": -100.0, "profit_factor": 0.3},
        },
    ]

    audited = BacktestEngine.audit_neighborhood(results, min_trades=5)
    assert len(audited) == 5

    # Check labels
    label_map = {r["config_id"]: r["neighborhood_audit"]["robustness_label"] for r in audited}
    assert label_map["EXP-001"] == "ROBUST"
    assert label_map["EXP-003"] == "FRAGILE"
    assert label_map["EXP-005"] == "REJECT"


def test_backtest_xau_liquidity_sweep_fvg_m1_execution():
    cfg = _make_config()
    cfg.trading.strategy_trigger_type = "xau_liquidity_sweep_fvg_m1"
    cfg.trading.enable_session_filter = False  # Keep test session agnostic
    cfg.trading.xau_session_start = ""
    cfg.trading.xau_displacement_atr_mult = 0.50
    cfg.trading.xau_displacement_body_ratio = 0.50
    cfg.trading.xau_fvg_expiry_bars = 5
    cfg.trading.xau_max_holding_bars = 15
    cfg.trading.xau_target_r = 2.0

    engine = BacktestEngine(cfg)
    assert engine.cfg.trading.strategy_trigger_type == "xau_liquidity_sweep_fvg_m1"

    # Create synthetic multi-timeframe data
    bars = 150
    start_t = datetime(2025, 7, 15, 14, 0, tzinfo=timezone.utc)
    times_m1 = [start_t + timedelta(minutes=j) for j in range(bars)]
    times_m15 = [start_t + timedelta(minutes=j * 15) for j in range(bars // 3)]

    # M15 data with clear highs and lows
    m15_highs = [2020.0] * len(times_m15)
    m15_lows = [1980.0] * len(times_m15)
    m15_closes = [2000.0] * len(times_m15)
    m15_data = TimeframeData(
        tf="M15", time=times_m15, open=m15_closes, high=m15_highs, low=m15_lows, close=m15_closes,
        tick_volume=[100]*len(times_m15), spread=[1]*len(times_m15)
    )

    # M1 data
    m1_opens = [2000.0] * bars
    m1_highs = [2005.0] * bars
    m1_lows = [1995.0] * bars
    m1_closes = [2000.0] * bars

    data = {
        "M1": TimeframeData(tf="M1", time=times_m1, open=m1_opens, high=m1_highs, low=m1_lows, close=m1_closes,
                            tick_volume=[100]*bars, spread=[1]*bars),
        "M5": TimeframeData(tf="M5", time=times_m1[::5], open=m1_opens[::5], high=m1_highs[::5], low=m1_lows[::5],
                            close=m1_closes[::5], tick_volume=[100]*(bars//5), spread=[1]*(bars//5)),
        "M15": m15_data,
        "H1": m15_data,
        "H4": m15_data,
    }

    res = engine.run(data)
    assert isinstance(res, dict)
    assert "ablation" in res
    assert "sweeps_detected" in res["ablation"]
    assert "orders_placed" in res["ablation"]
    assert "orders_filled" in res["ablation"]