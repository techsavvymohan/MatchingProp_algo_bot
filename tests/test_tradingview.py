from unittest.mock import MagicMock, patch
import pytest

from xauusd_bot.data.tradingview_feed import TradingViewFeed
from xauusd_bot.models import Bias


def test_tv_feed_init():
    feed = TradingViewFeed(cache_ttl_seconds=30.0, enabled=True)
    assert feed.cache_ttl == 30.0


def test_tv_map_tf():
    feed = TradingViewFeed()
    assert feed.map_tf_to_interval("M1") is not None
    assert feed.map_tf_to_interval("M15") is not None
    assert feed.map_tf_to_interval("H1") is not None


def test_tv_get_bias():
    feed = TradingViewFeed(enabled=True)

    with patch.object(feed, "get_recommendation", return_value="STRONG_BUY"):
        assert feed.get_bias("XAUUSD", "M15") == Bias.BULLISH

    with patch.object(feed, "get_recommendation", return_value="SELL"):
        assert feed.get_bias("EURUSD", "M15") == Bias.BEARISH

    with patch.object(feed, "get_recommendation", return_value="NEUTRAL"):
        assert feed.get_bias("XAUUSD", "M15") == Bias.NEUTRAL


def test_tv_is_sideways():
    feed = TradingViewFeed(enabled=True)

    # 1. Neutral consensus -> sideways
    mock_analysis = MagicMock()
    mock_analysis.summary = {"RECOMMENDATION": "NEUTRAL", "BUY": 2, "SELL": 2, "NEUTRAL": 12}
    with patch.object(feed, "get_analysis", return_value=mock_analysis):
        is_sw, reason = feed.is_sideways("XAUUSD", "M15")
        assert is_sw
        assert "Neutral" in reason

    # 2. Strong trend consensus -> not sideways
    mock_trend = MagicMock()
    mock_trend.summary = {"RECOMMENDATION": "STRONG_BUY", "BUY": 16, "SELL": 1, "NEUTRAL": 3}
    with patch.object(feed, "get_analysis", return_value=mock_trend):
        is_sw, reason = feed.is_sideways("XAUUSD", "M15")
        assert not is_sw


def test_tv_cache():
    feed = TradingViewFeed(cache_ttl_seconds=60.0, enabled=True)

    mock_analysis = MagicMock()
    mock_analysis.summary = {"RECOMMENDATION": "BUY"}

    with patch("xauusd_bot.data.tradingview_feed.TA_Handler") as mock_ta:
        mock_instance = MagicMock()
        mock_instance.get_analysis.return_value = mock_analysis
        mock_ta.return_value = mock_instance

        # First call fetches from TA_Handler
        res1 = feed.get_analysis("XAUUSD", "M15")
        assert res1 == mock_analysis
        assert mock_instance.get_analysis.call_count == 1

        # Second call within TTL hits cache
        res2 = feed.get_analysis("XAUUSD", "M15")
        assert res2 == mock_analysis
        assert mock_instance.get_analysis.call_count == 1


def test_tv_disabled():
    feed = TradingViewFeed(enabled=False)
    assert feed.get_analysis("XAUUSD", "M15") is None
    assert feed.get_recommendation("XAUUSD", "M15") == ""
    assert feed.get_indicators("XAUUSD", "M15") == {}


# ── LSEFeed Tests ──

from xauusd_bot.data.tradingview_feed import LSEFeed


def test_lse_feed_normalize_symbol():
    assert LSEFeed.normalize_symbol("XAUUSD") == "XAU/USD"
    assert LSEFeed.normalize_symbol("GOLD") == "XAU/USD"
    assert LSEFeed.normalize_symbol("EURUSD") == "EUR/USD"
    assert LSEFeed.normalize_symbol("GBPUSD") == "GBP/USD"
    assert LSEFeed.normalize_symbol("BTCUSD") == "BTC/USD"
    assert LSEFeed.normalize_symbol("EUR/USD") == "EUR/USD"


def test_lse_feed_get_tick_and_spread():
    feed = LSEFeed(enabled=True)
    assert feed.get_tick("XAUUSD") is None
    assert feed.get_spread("XAUUSD") is None

    feed._latest_ticks["XAU/USD"] = {
        "symbol": "XAU/USD",
        "price": 2005.5,
        "bid": 2005.0,
        "ask": 2005.8,
        "volume": 10.0,
        "timestamp": "2026-09-08 20:00:00",
    }
    tick = feed.get_tick("XAUUSD")
    assert tick is not None
    assert tick["price"] == 2005.5
    assert feed.get_spread("XAUUSD") == 0.8


def test_lse_feed_fetch_candles_and_series():
    feed = LSEFeed(enabled=True)

    mock_resp_candles = MagicMock()
    mock_resp_candles.status_code = 200
    mock_resp_candles.json.return_value = [
        {"ts": "2026-09-08 18:00:00.000000", "symbol": "XAU/USD", "open": 2000, "high": 2010, "low": 1995, "close": 2005, "volume": 100}
    ]

    mock_resp_series = MagicMock()
    mock_resp_series.status_code = 200
    mock_resp_series.json.return_value = [
        {"symbol": "US10Y", "date": "2026-07-01", "value": 4.47}
    ]

    with patch("requests.get", side_effect=[mock_resp_candles, mock_resp_series]):
        candles = feed.fetch_candles("XAUUSD", "1m")
        assert len(candles) == 1
        assert candles[0]["open"] == 2000

        us10y = feed.get_us10y_yield()
        assert us10y == 4.47


def test_lse_feed_cot_bias():
    feed = LSEFeed(enabled=True)

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = [
        {"symbol": "GC", "pct_noncomm_long": 55.0, "pct_noncomm_short": 15.0}
    ]

    with patch("requests.get", return_value=mock_resp):
        bias = feed.get_cot_bias("GC")
        assert bias == "BULLISH"


# ── TradingView MCP & CDP Bridge Tests ──

from xauusd_bot.data.tv_importer import check_tv_cdp_status, export_pine_strategy


def test_check_tv_cdp_status_connected():
    mock_resp = MagicMock()
    mock_resp.read.return_value = b'{"Browser": "TradingView/2.6.0", "Protocol-Version": "1.3", "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/browser"}'
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp):
        status = check_tv_cdp_status("127.0.0.1", 9222)
        assert status["cdp_connected"] is True
        assert "TradingView" in status["browser"]
        assert status["error"] is None


def test_check_tv_cdp_status_disconnected():
    with patch("urllib.request.urlopen", side_effect=OSError("Connection refused")):
        status = check_tv_cdp_status("127.0.0.1", 9222)
        assert status["cdp_connected"] is False
        assert "Connection refused" in status["error"]


def test_export_pine_strategy(tmp_path):
    params = {
        "tbh_lookback": 5,
        "tbh_fib_0": 0.236,
        "tbh_fib_1": 0.786,
        "tbh_rsi_length": 7,
        "tbh_rsi_oversold": 25.0,
        "tbh_rsi_overbought": 75.0,
        "tbh_atr_sl_mult": 2.5,
        "tbh_rr_ratio": 2.0,
    }
    out_file = str(tmp_path / "test_strategy.pine")
    code = export_pine_strategy(output_path=out_file, params=params)

    assert "//@version=5" in code
    assert "strategy(\"Top and Bottom Hunter Strategy" in code
    assert "input.int(5" in code
    assert "input.float(0.236" in code
    assert "input.int(7" in code
    assert "barstate.isconfirmed" in code
    with open(out_file, "r", encoding="utf-8") as f:
        content = f.read()
    assert content == code



