import os
import tempfile

from xauusd_bot.state.persistence import StatePersistence
from xauusd_bot.models import DailyState, TradeLeg, TradeDirection, TradeStatus


def _make_sp(tmp):
    sp = StatePersistence(os.path.join(tmp, "test.db"), os.path.join(tmp, "log.csv"))
    return sp


def test_state_persistence_save_and_load():
    with tempfile.TemporaryDirectory() as tmp:
        sp = _make_sp(tmp)
        state = DailyState(
            date="2025-01-15", start_equity=100000.0, current_equity=100000.0,
            peak_equity=100000.0, trades_today=3, kill_switch_active=False,
        )
        sp.save_daily_state(state)
        loaded = sp.load_daily_state()
        sp.close()
        assert loaded is not None
        assert loaded.date == "2025-01-15"
        assert loaded.start_equity == 100000.0
        assert loaded.trades_today == 3
        assert not loaded.kill_switch_active


def test_state_persistence_kill_switch():
    with tempfile.TemporaryDirectory() as tmp:
        sp = _make_sp(tmp)
        state = DailyState(
            date="2025-01-15", start_equity=100000.0, current_equity=100000.0,
            peak_equity=100000.0, trades_today=0, kill_switch_active=True,
        )
        sp.save_daily_state(state)
        loaded = sp.load_daily_state()
        sp.close()
        assert loaded is not None
        assert loaded.kill_switch_active


def test_state_persistence_empty():
    with tempfile.TemporaryDirectory() as tmp:
        sp = _make_sp(tmp)
        result = sp.load_daily_state()
        sp.close()
        assert result is None


def test_save_trade_leg():
    with tempfile.TemporaryDirectory() as tmp:
        sp = _make_sp(tmp)
        leg = TradeLeg(
            position_ticket=1001, direction=TradeDirection.BUY,
            entry_price=2000, lot_size=0.1, sl_price=1980, tp_price=2040,
            status=TradeStatus.OPEN,
        )
        sp.save_trade_leg(leg, "cluster_abc", "signal_xyz", "M15")
        sp.close()


def test_append_trade_csv():
    with tempfile.TemporaryDirectory() as tmp:
        log_path = os.path.join(tmp, "trades.csv")
        sp = StatePersistence(os.path.join(tmp, "test.db"), log_path)
        sp.append_trade_csv({"cluster": "abc", "pnl": 100.0, "reason": "tp"})
        assert os.path.exists(log_path)
        with open(log_path) as f:
            content = f.read()
        assert "abc" in content
        assert "100.0" in content


def test_close():
    with tempfile.TemporaryDirectory() as tmp:
        sp = StatePersistence(os.path.join(tmp, "test.db"), os.path.join(tmp, "log.csv"))
        sp.connect()
        sp.close()
        # Should not crash on double close
        sp.close()