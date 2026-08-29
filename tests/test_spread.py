from xauusd_bot.data.spread import SpreadTracker


def test_spread_tracker_defaults():
    st = SpreadTracker()
    assert st.current == 0.0
    assert st.average == 0.0
    assert st.max == 0.0
    assert st.min == 0.0
    assert st.std == 0.0


def test_spread_tracker_update():
    st = SpreadTracker()
    st.update(10)
    assert st.current == 10
    assert st.average == 10
    assert st.max == 10
    assert st.min == 10


def test_spread_tracker_multiple():
    st = SpreadTracker()
    for s in [10, 20, 30]:
        st.update(s)
    assert st.current == 30
    assert st.average == 20
    assert st.max == 30
    assert st.min == 10


def test_spread_tracker_std():
    st = SpreadTracker()
    for s in [10, 10, 10, 10, 10]:
        st.update(s)
    assert st.std == 0.0


def test_spread_tracker_std_nonzero():
    st = SpreadTracker()
    for s in [10, 20, 10, 20, 10]:
        st.update(s)
    assert st.std > 0


def test_spread_spike_not_enough_data():
    st = SpreadTracker(lookback=50)
    st.update(100)
    assert not st.is_spike()
    st.update(100)
    assert not st.is_spike()
    st.update(100)
    assert not st.is_spike()


def test_spread_spike_detected():
    st = SpreadTracker()
    for _ in range(10):
        st.update(10)
    st.update(100)
    assert st.is_spike()


def test_spread_spike_not_detected():
    st = SpreadTracker()
    for _ in range(10):
        st.update(10)
    st.update(12)
    assert not st.is_spike()


def test_spread_reset():
    st = SpreadTracker()
    st.update(10)
    st.reset()
    assert st.current == 0.0
    assert st.average == 0.0
    assert st.max == 0.0
    assert st.min == 0.0


def test_spread_maxlen():
    st = SpreadTracker(lookback=3)
    for s in [1, 2, 3, 4, 5]:
        st.update(s)
    assert len(st._history) == 3
    assert st.average == (3 + 4 + 5) / 3