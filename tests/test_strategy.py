import sys
from pathlib import Path
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent))

from xauusd_bot.models import (
    Bias, Regime, TimeframeData, Signal, SignalGrade,
    TradeDirection, Session,
)
from xauusd_bot.strategy.bias_detector import BiasDetector
from xauusd_bot.strategy.zone_detector import ZoneDetector
from xauusd_bot.strategy.signal_scorer import SignalScorer
from xauusd_bot.strategy.trigger import TriggerDetector
from xauusd_bot.strategy.timeframe_hierarchy import TimeframeHierarchy


def _make_tfdata(tf: str, close: list[float], high: list[float] | None = None,
                 low: list[float] | None = None, vol: list[int] | None = None,
                 spread: list[int] | None = None):
    n = len(close)
    base = datetime(2025, 1, 1)
    if high is None:
        high = [c + 0.5 for c in close]
    if low is None:
        low = [c - 0.5 for c in close]
    if vol is None:
        vol = [100] * n
    if spread is None:
        spread = [10] * n
    minutes = {"M1": 1, "M5": 5, "M15": 15, "M30": 30, "H1": 60, "H4": 240}
    step = minutes.get(tf, 1)
    times = [base + timedelta(minutes=i * step) for i in range(n)]
    return TimeframeData(tf=tf, time=times, open=close, high=high,
                         low=low, close=close, tick_volume=vol, spread=spread)


def _trending_bull(len=100):
    return [100 + i * 0.5 + (i % 5) * 0.1 for i in range(len)]


def _trending_bear(len=100):
    return [200 - i * 0.5 - (i % 5) * 0.1 for i in range(len)]


def _ranging(n=120):
    import math as _m
    return [150 + _m.sin(i * 0.3) * 2 + _m.cos(i * 0.7) * 2 for i in range(n)]


def _trending_bull_noise(n=100):
    p = 100
    out = [p]
    trend_end = min(15, n - 1)
    for i in range(1, min(15, n)):
        p += 0.6
        out.append(p)
    slow_end = min(25, n)
    for i in range(15, slow_end):
        p += 0.2
        out.append(p)
    consol_end = min(38, n)
    for i in range(25, consol_end):
        p += 0.2 if i % 2 == 0 else -0.2
        out.append(p)
    break_end = min(42, n)
    for i in range(38, break_end):
        p += 0.15
        out.append(p)
    for i in range(42, n):
        step = 0.15 if i % 2 == 1 else -0.15
        p += step
        out.append(p)
    return out[:n]


def _trending_bear_noise(n=100):
    p = 200
    out = [p]
    for i in range(1, min(15, n)):
        p -= 0.6
        out.append(p)
    for i in range(15, min(25, n)):
        p -= 0.2
        out.append(p)
    for i in range(25, min(38, n)):
        p -= 0.2 if i % 2 == 0 else -0.2
        out.append(p)
    for i in range(38, min(42, n)):
        p -= 0.15
        out.append(p)
    for i in range(42, n):
        step = 0.15 if i % 2 == 1 else -0.15
        p -= step
        out.append(p)
    return out[:n]


# ── BiasDetector ──

def test_bias_bullish():
    bd = BiasDetector()
    data = _make_tfdata("H1", _trending_bull(100))
    bias = bd.detect_bias(data)
    assert bias == Bias.BULLISH


def test_bias_bearish():
    bd = BiasDetector()
    data = _make_tfdata("H1", _trending_bear(100))
    bias = bd.detect_bias(data)
    assert bias == Bias.BEARISH


def test_bias_neutral():
    bd = BiasDetector()
    data = _make_tfdata("H1", _ranging(120))
    bias = bd.detect_bias(data)
    assert bias == Bias.NEUTRAL


def test_bias_insufficient_data():
    bd = BiasDetector()
    data = _make_tfdata("H1", [100, 101])
    assert bd.detect_bias(data) == Bias.NEUTRAL


def test_regime_trending_bull():
    bd = BiasDetector()
    data = _make_tfdata("H4", _trending_bull(120))
    regime = bd.detect_regime(data)
    assert regime == Regime.TRENDING_BULL


def test_regime_ranging():
    bd = BiasDetector()
    data = _make_tfdata("H4", _ranging(60))
    regime = bd.detect_regime(data)
    assert regime == Regime.RANGING


# ── ZoneDetector ──

def test_pullback_zone_bullish():
    zd = ZoneDetector()
    data = _make_tfdata("M15", _trending_bull(50))
    zone = zd.detect_pullback_zone(data, Bias.BULLISH)
    assert zone is not None
    assert zone[0] < zone[1]


def test_pullback_zone_bearish():
    zd = ZoneDetector()
    data = _make_tfdata("M15", _trending_bear(50))
    zone = zd.detect_pullback_zone(data, Bias.BEARISH)
    assert zone is not None
    assert zone[0] < zone[1]


def test_pullback_zone_neutral():
    zd = ZoneDetector()
    data = _make_tfdata("M15", _ranging(50))
    assert zd.detect_pullback_zone(data, Bias.NEUTRAL) is None


def test_price_in_zone():
    zd = ZoneDetector()
    assert zd.price_in_zone(105, (100, 110))
    assert not zd.price_in_zone(95, (100, 110))
    assert not zd.price_in_zone(115, (100, 110))
    assert not zd.price_in_zone(105, None)


# ── SignalScorer ──

def test_signal_grade_a():
    sc = SignalScorer(8, 5)
    sig = Signal(
        direction=TradeDirection.BUY, score=8,
        regime=Regime.TRENDING_BULL,
        m15_zone=(100, 110),
    )
    assert sc.grade(sig) == SignalGrade.A


def test_signal_grade_b():
    sc = SignalScorer(8, 5)
    sig = Signal(direction=TradeDirection.BUY, score=5, regime=Regime.RANGING)
    assert sc.grade(sig) == SignalGrade.B


def test_signal_grade_c():
    sc = SignalScorer(8, 5)
    sig = Signal(direction=TradeDirection.BUY, score=2, regime=Regime.RANGING)
    assert sc.grade(sig) == SignalGrade.C


def test_direction_from_bias():
    sc = SignalScorer()
    assert sc.direction_from_bias({"allowed_direction": "bullish"}) == TradeDirection.BUY
    assert sc.direction_from_bias({"allowed_direction": "bearish"}) == TradeDirection.SELL


# ── TriggerDetector ──

def test_momentum_bullish():
    td = TriggerDetector()
    data = _make_tfdata("M5", _trending_bull_noise(90))
    ok, msg = td.check_momentum_continuation(data, TradeDirection.BUY)
    assert ok, msg


def test_momentum_bearish():
    td = TriggerDetector()
    data = _make_tfdata("M5", _trending_bear_noise(90))
    ok, msg = td.check_momentum_continuation(data, TradeDirection.SELL)
    assert ok, msg


def test_micro_structure_break_buy():
    td = TriggerDetector()
    data = _make_tfdata("M1",
        close=[100, 101, 100, 102, 101, 103],
        high=[101, 102, 101, 103, 102, 104],
    )
    ok, price = td.check_micro_structure_break(data, TradeDirection.BUY, 3)
    assert ok


def test_micro_structure_break_sell():
    td = TriggerDetector()
    data = _make_tfdata("M1",
        close=[104, 103, 104, 102, 103, 101],
        low=[103, 102, 103, 101, 102, 100],
    )
    ok, price = td.check_micro_structure_break(data, TradeDirection.SELL, 3)
    assert ok


def test_zone_entry():
    td = TriggerDetector()
    ok, _ = td.check_zone_entry(105, (100, 110), TradeDirection.BUY)
    assert ok
    ok, _ = td.check_zone_entry(95, (100, 110), TradeDirection.BUY)
    assert not ok


# ── TimeframeHierarchy ──

def test_hierarchy_bullish_alignment():
    bd = BiasDetector()
    zd = ZoneDetector()
    th = TimeframeHierarchy(bd, zd)
    data_all = {
        "H4": _make_tfdata("H4", _trending_bull(120)),
        "H1": _make_tfdata("H1", _trending_bull(100)),
        "M15": _make_tfdata("M15", _trending_bull(60)),
        "M5": _make_tfdata("M5", _trending_bull(40)),
        "M1": _make_tfdata("M1", _trending_bull(20)),
    }
    result = th.evaluate(data_all, Session.LONDON)
    assert result["allowed_direction"] == "bullish"
    assert result["alignment_count"] >= 3


def test_hierarchy_bearish_alignment():
    bd = BiasDetector()
    zd = ZoneDetector()
    th = TimeframeHierarchy(bd, zd)
    data_all = {
        "H4": _make_tfdata("H4", _trending_bear(120)),
        "H1": _make_tfdata("H1", _trending_bear(100)),
        "M15": _make_tfdata("M15", _trending_bear(60)),
        "M5": _make_tfdata("M5", _trending_bear(40)),
        "M1": _make_tfdata("M1", _trending_bear(20)),
    }
    result = th.evaluate(data_all, Session.LONDON)
    assert result["allowed_direction"] == "bearish"
    assert result["alignment_count"] >= 3


def test_hierarchy_no_direction():
    bd = BiasDetector()
    zd = ZoneDetector()
    th = TimeframeHierarchy(bd, zd)
    data_all = {
        "H4": _make_tfdata("H4", _ranging(60)),
        "H1": _make_tfdata("H1", _ranging(60)),
        "M15": _make_tfdata("M15", _ranging(60)),
        "M5": _make_tfdata("M5", _ranging(40)),
        "M1": _make_tfdata("M1", _ranging(30)),
    }
    result = th.evaluate(data_all, Session.LONDON)
    assert result["allowed_direction"] is not None


def test_hierarchy_entry_tier():
    bd = BiasDetector()
    zd = ZoneDetector()
    th = TimeframeHierarchy(bd, zd)
    data_all = {"H4": _make_tfdata("H4", _trending_bull(120)),
                 "H1": _make_tfdata("H1", _trending_bull(100))}
    ny = th._select_entry_tier(Session.NY, Regime.TRENDING_BULL)
    assert ny == "M1"
    asian = th._select_entry_tier(Session.ASIAN, Regime.RANGING)
    assert asian == "M15"


# =====================================================================
# 7-Agent Institutional Research Team Tests
# =====================================================================

from xauusd_bot.config import Config
from xauusd_bot.main import InstitutionalResearchTeam
from xauusd_bot.models import (
    AccountInfo, DailyState, NewsCatalystBrief, QuantHypothesis,
    RiskVerdict, TechnicalStructure, FundamentalDossier, ResearchPlan,
)


def _make_dummy_feed(bullish: bool = True):
    trend = _trending_bull(100) if bullish else _trending_bear(100)
    return {
        "H4": _make_tfdata("H4", trend),
        "H1": _make_tfdata("H1", trend),
        "M15": _make_tfdata("M15", trend),
        "M5": _make_tfdata("M5", trend),
        "M1": _make_tfdata("M1", trend),
    }


def test_agent_01_market_scout():
    cfg = Config()
    team = InstitutionalResearchTeam(cfg)
    feeds = {
        "XAUUSD": _make_dummy_feed(True),
        "EURUSD": _make_dummy_feed(False),
    }
    res = team.scout(["XAUUSD", "EURUSD"], feeds, session_name="LONDON")
    assert len(res.candidates) == 2
    assert res.session == "LONDON"
    assert res.candidates[0].priority_rank == 1
    assert res.candidates[1].priority_rank == 2
    assert "Scouted 2 symbols" in res.summary


def test_agent_02_technical_analyst():
    cfg = Config()
    team = InstitutionalResearchTeam(cfg)
    feed = _make_dummy_feed(True)
    hierarchy_res = {
        "h4_bias": Bias.BULLISH,
        "h1_bias": Bias.BULLISH,
        "m15_bias": Bias.BULLISH,
        "m5_bias": Bias.BULLISH,
        "allowed_direction": "bullish",
        "m15_zone": (2640.0, 2660.0),
        "alignment_score": 9,
    }
    tech = team.technical("XAUUSD", feed, hierarchy_res, current_price=2650.0)
    assert tech.symbol == "XAUUSD"
    assert tech.direction == "BUY"
    assert tech.h4_bias == "bullish"
    assert tech.key_support == 2640.0
    assert tech.key_resistance == 2660.0
    assert tech.trigger_formed is True


def test_agent_03_fundamental_analyst():
    cfg = Config()
    team = InstitutionalResearchTeam(cfg)
    feed = _make_dummy_feed(True)
    fund = team.fundamental("XAUUSD", feed, tech_dir="BUY")
    assert fund.symbol == "XAUUSD"
    assert fund.macro_alignment == "Favorable"
    assert fund.score > 0
    assert len(fund.tailwinds) > 0


def test_agent_04_news_analyst():
    cfg = Config()
    team = InstitutionalResearchTeam(cfg)
    class DummyNewsFilter:
        def check(self, sym):
            return True, "No news"
        calendar = None
    news_brief = team.news("XAUUSD", DummyNewsFilter())
    assert news_brief.symbol == "XAUUSD"
    assert news_brief.is_blocked is False
    assert news_brief.event_risk_level == "LOW"


def test_agent_05_quant_analyst():
    cfg = Config()
    team = InstitutionalResearchTeam(cfg)
    feed = _make_dummy_feed(True)
    hierarchy_res = {
        "is_sideways": False,
        "sideways_reason": "",
        "alignment_score": 9,
    }
    quant = team.quant("XAUUSD", hierarchy_res, feed["M15"], atr_val=2.5,
                       entry_price=2650.0, sl_price=2645.0, tp_price=2665.0)
    assert quant.is_sideways is False
    assert quant.signal_grade == "A"
    assert quant.expected_rr == 3.0
    assert "Alpha" in quant.statistical_edge


def test_agent_06_risk_manager_kills_invalid_sl():
    cfg = Config()
    team = InstitutionalResearchTeam(cfg)
    news_brief = NewsCatalystBrief(symbol="XAUUSD", is_blocked=False)
    quant_hyp = QuantHypothesis(symbol="XAUUSD", is_sideways=False, signal_grade="A", expected_rr=2.5)

    # Missing / inverted SL for BUY
    verdict = team.risk_manager(
        symbol="XAUUSD",
        direction=TradeDirection.BUY,
        entry_price=2650.0,
        sl_price=2655.0,  # SL above entry for BUY -> FLAWED!
        tp_price=2670.0,
        atr_val=2.5,
        account_info=AccountInfo(equity=100000.0),
        daily_loss=None,
        max_dd=None,
        news_brief=news_brief,
        quant_hyp=quant_hyp,
    )
    assert verdict.is_approved is False
    assert verdict.status == "KILLED"
    assert any("Thesis Invalidation Flawed" in r for r in verdict.veto_reasons)


def test_agent_06_risk_manager_kills_daily_loss_breach():
    cfg = Config()
    team = InstitutionalResearchTeam(cfg)
    news_brief = NewsCatalystBrief(symbol="XAUUSD", is_blocked=False)
    quant_hyp = QuantHypothesis(symbol="XAUUSD", is_sideways=False, signal_grade="A", expected_rr=2.5)

    class DummyDailyLoss:
        def current_daily_loss_pct(self): return 2.9
        def kill_switch_engaged(self): return True

    verdict = team.risk_manager(
        symbol="XAUUSD",
        direction=TradeDirection.BUY,
        entry_price=2650.0,
        sl_price=2645.0,
        tp_price=2665.0,
        atr_val=2.5,
        account_info=AccountInfo(equity=100000.0),
        daily_loss=DummyDailyLoss(),
        max_dd=None,
        news_brief=news_brief,
        quant_hyp=quant_hyp,
    )
    assert verdict.is_approved is False
    assert verdict.status == "KILLED"
    assert any("Daily loss killswitch active" in r for r in verdict.veto_reasons)


def test_agent_06_risk_manager_kills_sideways_regime():
    cfg = Config()
    team = InstitutionalResearchTeam(cfg)
    news_brief = NewsCatalystBrief(symbol="XAUUSD", is_blocked=False)
    quant_hyp = QuantHypothesis(symbol="XAUUSD", is_sideways=True, signal_grade="C", expected_rr=1.0)

    verdict = team.risk_manager(
        symbol="XAUUSD",
        direction=TradeDirection.BUY,
        entry_price=2650.0,
        sl_price=2645.0,
        tp_price=2665.0,
        atr_val=2.5,
        account_info=AccountInfo(equity=100000.0),
        daily_loss=None,
        max_dd=None,
        news_brief=news_brief,
        quant_hyp=quant_hyp,
    )
    assert verdict.is_approved is False
    assert any("Regime Invalidation" in r for r in verdict.veto_reasons)


def test_agent_06_risk_manager_approves_valid_trade():
    cfg = Config()
    team = InstitutionalResearchTeam(cfg)
    news_brief = NewsCatalystBrief(symbol="XAUUSD", is_blocked=False)
    quant_hyp = QuantHypothesis(symbol="XAUUSD", is_sideways=False, signal_grade="A", expected_rr=2.5)

    verdict = team.risk_manager(
        symbol="XAUUSD",
        direction=TradeDirection.BUY,
        entry_price=2650.0,
        sl_price=2645.0,  # 5.0 distance = 2.0x ATR (optimal)
        tp_price=2665.0,  # 15.0 distance = 3.0R
        atr_val=2.5,
        account_info=AccountInfo(equity=100000.0),
        daily_loss=None,
        max_dd=None,
        news_brief=news_brief,
        quant_hyp=quant_hyp,
    )
    assert verdict.is_approved is True
    assert verdict.status == "APPROVED"
    assert len(verdict.veto_reasons) == 0
    assert "THESIS APPROVED" in verdict.adversarial_assessment


def test_agent_07_portfolio_manager_structured_plan(tmp_path):
    cfg = Config()
    cfg.trading.research_report_dir = str(tmp_path)
    team = InstitutionalResearchTeam(cfg)

    tech = TechnicalStructure(symbol="XAUUSD", current_price=2650.0, h4_bias="bullish", h1_bias="bullish",
                              key_support=2640.0, key_resistance=2660.0, direction="BUY")
    fund = FundamentalDossier(symbol="XAUUSD", macro_regime="DXY Softening / Bullion Inflow",
                              macro_alignment="Favorable", score=0.6)
    news = NewsCatalystBrief(symbol="XAUUSD", is_blocked=False, event_risk_level="LOW", minutes_to_next_event=120)
    quant = QuantHypothesis(symbol="XAUUSD", is_sideways=False, signal_grade="A", signal_score=9,
                            atr_regime="Volatility Expansion", expected_rr=2.5)
    risk = RiskVerdict(status="APPROVED", veto_reasons=[], thesis_invalidation_level=2645.0,
                       max_loss_usd=250.0, adversarial_assessment="THESIS APPROVED")

    plan = team.portfolio_manager(
        symbol="XAUUSD",
        direction=TradeDirection.BUY,
        entry_price=2650.0,
        sl_price=2645.0,
        tp_price=2665.0,
        lot_size=0.5,
        tech=tech,
        fund=fund,
        news=news,
        quant=quant,
        risk=risk,
        session_name="LONDON",
    )

    assert plan.symbol == "XAUUSD"
    assert plan.action == "EXECUTE_BUY"
    assert len(plan.inputs) == 6
    assert "Macro" in plan.inputs
    assert "HTF Direction" in plan.inputs
    assert "Trigger" in plan.inputs
    assert "Risk" in plan.inputs
    assert "Confluence" in plan.inputs
    assert "Timing" in plan.inputs
    assert len(plan.evidence) == 4
    assert len(plan.risks) == 3
    assert len(plan.next_steps) == 4
    assert "# INSTITUTIONAL TRADE & RESEARCH PLAN — AGENT 07" in plan.markdown_dossier

    # Test saving
    saved_file = team.save_research_plan(plan)
    assert saved_file.exists()
    assert "XAUUSD_EXECUTE_BUY.md" in saved_file.name

