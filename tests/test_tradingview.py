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
