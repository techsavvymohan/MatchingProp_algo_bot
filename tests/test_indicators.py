import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from xauusd_bot.indicators.atr import atr, atr_series, true_range
from xauusd_bot.indicators.moving_averages import ema, ema_series, sma, vwap
from xauusd_bot.indicators.rsi import rsi
from xauusd_bot.indicators.chandelier import chandelier_exit_long, chandelier_exit_short


def test_true_range():
    high = [10, 12, 11, 13]
    low = [9, 10, 9, 11]
    close = [9.5, 11, 10, 12]
    tr = true_range(high, low, close)
    assert len(tr) == 4
    assert tr[0] == 1.0
    assert all(t > 0 for t in tr)


def test_atr_known_values():
    high = [10, 12, 11, 13, 14, 13, 15, 16, 15, 17, 18, 17, 19, 20, 19]
    low = [9, 10, 9, 11, 12, 11, 13, 14, 13, 15, 16, 15, 17, 18, 17]
    close = [9.5, 11, 10, 12, 13, 12, 14, 15, 14, 16, 17, 16, 18, 19, 18]
    a = atr(high, low, close, 5)
    assert a is not None
    assert a > 0


def test_atr_not_enough_data():
    assert atr([1, 2], [0, 1], [0.5, 1.5], 14) is None


def test_atr_series_length():
    high = [10 + i * 0.1 for i in range(30)]
    low = [9 + i * 0.1 for i in range(30)]
    close = [9.5 + i * 0.1 for i in range(30)]
    s = atr_series(high, low, close, 5)
    assert len(s) == len(close) - 5


def test_ema_basic():
    values = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    e = ema(values, 5)
    assert e is not None
    assert e > 0


def test_ema_insufficient():
    assert ema([1, 2], 5) is None


def test_ema_series_length():
    values = [float(i) for i in range(30)]
    s = ema_series(values, 5)
    assert len(s) == len(values) - 5 + 1


def test_sma():
    assert sma([1, 2, 3, 4, 5], 3) == 4.0
    assert sma([10, 20, 30], 3) == 20.0
    assert sma([1, 2], 3) is None


def test_vwap():
    high = [11, 12, 13]
    low = [9, 10, 11]
    close = [10, 11, 12]
    vol = [100, 200, 300]
    v = vwap(high, low, close, vol, 2)
    assert v is not None
    assert v > 0


def test_vwap_insufficient():
    assert vwap([1], [0], [0.5], [10], 5) is None


def test_rsi_basic():
    values = [10, 12, 11, 13, 14, 13, 15, 16, 15, 17, 18, 19, 20, 19, 21]
    r = rsi(values, 5)
    assert r is not None
    assert 0 <= r <= 100


def test_rsi_oversold():
    falling = [100 - i * 5 for i in range(20)]
    r = rsi(falling, 14)
    assert r is not None
    assert r < 30


def test_rsi_overbought():
    rising = [50 + i * 5 for i in range(20)]
    r = rsi(rising, 14)
    assert r is not None
    assert r > 70


def test_rsi_insufficient():
    assert rsi([1, 2, 3], 14) is None


def test_chandelier_long():
    high = [10 + i * 0.5 for i in range(25)]
    low = [9 + i * 0.3 for i in range(25)]
    close = [9.5 + i * 0.4 for i in range(25)]
    stop = chandelier_exit_long(high, low, close, 14, 3.0)
    assert stop is not None
    assert stop < close[-1]


def test_chandelier_short():
    high = [10 - i * 0.3 for i in range(25)]
    low = [9 - i * 0.5 for i in range(25)]
    close = [9.5 - i * 0.4 for i in range(25)]
    stop = chandelier_exit_short(high, low, close, 14, 3.0)
    assert stop is not None
    assert stop > close[-1]


def test_chandelier_insufficient():
    assert chandelier_exit_long([1, 2], [0, 1], [0.5, 1.5], 22) is None
