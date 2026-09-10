import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, List, Optional, Tuple


class Bias(Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


class Regime(Enum):
    TRENDING_BULL = "trending_bull"
    TRENDING_BEAR = "trending_bear"
    RANGING = "ranging"


class Session(Enum):
    ASIAN = "asian"
    LONDON = "london"
    NY = "ny"
    LONDON_NY_OVERLAP = "london_ny_overlap"
    CLOSED = "closed"


class SignalGrade(Enum):
    A = "A"
    B = "B"
    C = "C"


class TradeDirection(Enum):
    BUY = "buy"
    SELL = "sell"


class TradeStatus(Enum):
    PENDING = "pending"
    OPEN = "open"
    PARTIAL_CLOSED = "partial_closed"
    CLOSED = "closed"
    REJECTED = "rejected"


class ExitReason(Enum):
    TAKE_PROFIT = "take_profit"
    STOP_LOSS = "stop_loss"
    TIME_BASED = "time_based"
    CHANDELIER_TRAIL = "chandelier_trail"
    MANUAL = "manual"
    EQUITY_KILL = "equity_kill"
    SIGNAL_REVERSAL = "signal_reversal"
    STRUCTURAL_INVALIDATION = "structural_invalidation"
    BREAKEVEN = "breakeven"
    STAGNATION = "stagnation"


@dataclass
class TimeframeData:
    tf: str
    time: List[datetime]
    open: List[float]
    high: List[float]
    low: List[float]
    close: List[float]
    tick_volume: List[int]
    spread: List[int]

    @property
    def current(self) -> dict:
        i = -1
        return {
            "time": self.time[i],
            "open": self.open[i],
            "high": self.high[i],
            "low": self.low[i],
            "close": self.close[i],
            "volume": self.tick_volume[i],
            "spread": self.spread[i],
        }

    def len(self) -> int:
        return len(self.close)


@dataclass
class Signal:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    symbol: str = "XAUUSD"

    direction: TradeDirection = TradeDirection.BUY
    grade: SignalGrade = SignalGrade.C
    score: int = 0
    entry_tf: str = ""

    h4_bias: Bias = Bias.NEUTRAL
    h1_bias: Bias = Bias.NEUTRAL
    m15_bias: Bias = Bias.NEUTRAL
    m5_bias: Bias = Bias.NEUTRAL
    m1_bias: Bias = Bias.NEUTRAL

    regime: Regime = Regime.RANGING
    session: Session = Session.CLOSED

    entry_price: float = 0.0
    sl_price: float = 0.0
    tp_price: float = 0.0
    atr_value: float = 0.0
    lot_size: float = 0.0

    zone_high: float = 0.0
    zone_low: float = 0.0
    fvg_low: float = 0.0
    fvg_high: float = 0.0

    news_blocked: bool = False
    spread_blocked: bool = False
    session_blocked: bool = False
    equity_blocked: bool = False
    sideways_blocked: bool = False
    m15_zone: Optional[tuple] = None

    ao_saucer: bool = False
    ha_trend: str = ""
    tradingview_recommendation: str = ""
    setup_type: str = "SWEEP_FVG"

    is_pyramid_add: bool = False
    parent_signal_id: Optional[str] = None
    risk_verdict: Optional["RiskVerdict"] = None
    research_plan: Optional["ResearchPlan"] = None

    def blocked(self) -> bool:
        return any([self.news_blocked, self.spread_blocked, self.session_blocked, self.equity_blocked, self.sideways_blocked])

    def is_tradeable(self) -> bool:
        return not self.blocked() and self.grade in (SignalGrade.A, SignalGrade.B)


@dataclass
class TradeLeg:
    leg_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    position_ticket: int = 0
    symbol: str = "XAUUSD"
    direction: TradeDirection = TradeDirection.BUY
    entry_price: float = 0.0
    lot_size: float = 0.0
    sl_price: float = 0.0
    tp_price: float = 0.0
    open_time: Optional[datetime] = None
    close_time: Optional[datetime] = None
    exit_price: float = 0.0
    exit_reason: Optional[ExitReason] = None
    pnl: float = 0.0
    status: TradeStatus = TradeStatus.PENDING


@dataclass
class PyraCluster:
    cluster_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    signal_id: str = ""
    symbol: str = "XAUUSD"
    direction: TradeDirection = TradeDirection.BUY
    legs: List[TradeLeg] = field(default_factory=list)
    collective_sl: float = 0.0
    initial_r_dist: float = 0.0
    breakeven_activated: bool = False
    highest_price: float = 0.0
    lowest_price: float = 0.0
    entry_tf: str = ""
    fvg_low: float = 0.0
    fvg_high: float = 0.0
    open_time: Optional[datetime] = None
    status: TradeStatus = TradeStatus.OPEN

    def r_distance(self) -> float:
        """Return the initial risk distance (1R).
        
        Even after breakeven stop trailing where collective_sl == avg_entry,
        this preserves the true 1R scale for pyramiding and partial close calculations.
        """
        if self.collective_sl <= 0 and not self.breakeven_activated:
            return 0.0
        if self.initial_r_dist > 0:
            return self.initial_r_dist
        if self.legs:
            leg1 = self.legs[0]
            if leg1.entry_price > 0 and leg1.sl_price > 0:
                dist = abs(leg1.entry_price - leg1.sl_price)
                if dist > 0:
                    self.initial_r_dist = dist
                    return dist
        avg_entry = self.avg_entry_price()
        if avg_entry > 0 and self.collective_sl > 0 and abs(avg_entry - self.collective_sl) > 0:
            return abs(avg_entry - self.collective_sl)
        return 0.0

    def total_lot_size(self) -> float:
        return sum(leg.lot_size for leg in self.legs if leg.status == TradeStatus.OPEN)

    def leg_count(self) -> int:
        return sum(1 for leg in self.legs if leg.status == TradeStatus.OPEN)

    def avg_entry_price(self) -> float:
        open_legs = [leg for leg in self.legs if leg.status == TradeStatus.OPEN]
        if not open_legs:
            return 0.0
        total_notional = sum(leg.lot_size * leg.entry_price for leg in open_legs)
        total_lots = sum(leg.lot_size for leg in open_legs)
        if total_lots > 0:
            return total_notional / total_lots
        return sum(leg.entry_price for leg in open_legs) / len(open_legs)

    def unrealized_pnl(self, current_price: float, point_value: float, contract_size: int = 100, tick_size: float = 0.0) -> float:
        total = 0.0
        for leg in self.legs:
            if leg.status != TradeStatus.OPEN:
                continue
            diff = (current_price - leg.entry_price) if leg.direction == TradeDirection.BUY else (leg.entry_price - current_price)
            if tick_size > 0:
                total += (diff / tick_size) * point_value * leg.lot_size
            else:
                total += diff * point_value * contract_size * leg.lot_size
        return total

    def total_risk_amount(self, point_value: float, contract_size: int = 100, tick_size: float = 0.0) -> float:
        total = 0.0
        for leg in self.legs:
            if leg.status != TradeStatus.OPEN:
                continue
            risk_pts = abs(leg.entry_price - leg.sl_price)
            if tick_size > 0:
                total += (risk_pts / tick_size) * point_value * leg.lot_size
            else:
                total += risk_pts * point_value * contract_size * leg.lot_size
        return total


@dataclass
class DailyState:
    date: str = ""
    start_equity: float = 0.0
    current_equity: float = 0.0
    daily_pnl: float = 0.0
    peak_equity: float = 0.0
    trades_today: int = 0
    kill_switch_active: bool = False


@dataclass
class AccountInfo:
    balance: float = 0.0
    equity: float = 0.0
    margin: float = 0.0
    margin_free: float = 0.0
    margin_level: float = 0.0
    leverage: int = 0
    currency: str = "USD"
    server_time: Optional[datetime] = None


# =====================================================================
# 7-Agent Institutional Research Team Data Models
# =====================================================================

@dataclass
class ScoutCandidate:
    """A candidate shortlisted by Agent 01: Market Scout."""
    symbol: str
    session_range: float = 0.0
    atr_value: float = 0.0
    relative_spread: float = 1.0
    volatility_score: float = 0.0
    priority_rank: int = 1
    catalyst_hint: str = ""

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "session_range": self.session_range,
            "atr_value": self.atr_value,
            "relative_spread": self.relative_spread,
            "volatility_score": self.volatility_score,
            "priority_rank": self.priority_rank,
            "catalyst_hint": self.catalyst_hint,
        }


@dataclass
class ScoutResult:
    """Output of Agent 01: Market Scout."""
    session: str
    candidates: List[ScoutCandidate] = field(default_factory=list)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    summary: str = ""

    def to_dict(self) -> dict:
        return {
            "session": self.session,
            "candidates": [c.to_dict() for c in self.candidates],
            "timestamp": self.timestamp.isoformat(),
            "summary": self.summary,
        }


@dataclass
class TechnicalStructure:
    """Output of Agent 02: Technical Analyst."""
    symbol: str
    current_price: float
    h4_bias: str = "neutral"
    h1_bias: str = "neutral"
    m15_bias: str = "neutral"
    m5_bias: str = "neutral"
    m15_zone: Optional[Tuple[float, float]] = None
    swing_high: float = 0.0
    swing_low: float = 0.0
    key_support: float = 0.0
    key_resistance: float = 0.0
    trigger_formed: bool = False
    direction: str = "NEUTRAL"  # BUY, SELL, NEUTRAL
    alignment_score: int = 0
    summary: str = ""

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "current_price": self.current_price,
            "h4_bias": self.h4_bias,
            "h1_bias": self.h1_bias,
            "m15_bias": self.m15_bias,
            "m5_bias": self.m5_bias,
            "m15_zone": list(self.m15_zone) if self.m15_zone else None,
            "swing_high": self.swing_high,
            "swing_low": self.swing_low,
            "key_support": self.key_support,
            "key_resistance": self.key_resistance,
            "trigger_formed": self.trigger_formed,
            "direction": self.direction,
            "alignment_score": self.alignment_score,
            "summary": self.summary,
        }


@dataclass
class FundamentalDossier:
    """Output of Agent 03: Fundamental Analyst."""
    symbol: str
    macro_regime: str = "Neutral / Balanced"
    dxy_trend: str = "Consolidating"
    yield_trend: str = "Stable"
    macro_alignment: str = "Neutral"  # Favorable, Neutral, Adverse
    score: float = 0.0  # -1.0 to +1.0
    tailwinds: List[str] = field(default_factory=list)
    headwinds: List[str] = field(default_factory=list)
    summary: str = ""

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "macro_regime": self.macro_regime,
            "dxy_trend": self.dxy_trend,
            "yield_trend": self.yield_trend,
            "macro_alignment": self.macro_alignment,
            "score": self.score,
            "tailwinds": self.tailwinds,
            "headwinds": self.headwinds,
            "summary": self.summary,
        }


@dataclass
class NewsCatalystBrief:
    """Output of Agent 04: News Analyst."""
    symbol: str
    has_high_impact_near: bool = False
    minutes_to_next_event: Optional[int] = None
    next_event_title: str = "None scheduled"
    event_currency: str = "USD"
    event_risk_level: str = "LOW"  # LOW, MEDIUM, HIGH, EXTREME
    is_blocked: bool = False
    upcoming_events: List[str] = field(default_factory=list)
    summary: str = ""

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "has_high_impact_near": self.has_high_impact_near,
            "minutes_to_next_event": self.minutes_to_next_event,
            "next_event_title": self.next_event_title,
            "event_currency": self.event_currency,
            "event_risk_level": self.event_risk_level,
            "is_blocked": self.is_blocked,
            "upcoming_events": self.upcoming_events,
            "summary": self.summary,
        }


@dataclass
class QuantHypothesis:
    """Output of Agent 05: Quant Analyst."""
    symbol: str
    is_sideways: bool = False
    sideways_reasons: List[str] = field(default_factory=list)
    signal_score: int = 0
    signal_grade: str = "C"  # A, B, C
    atr_regime: str = "Normal"  # Squeeze, Normal, Expansion
    expected_rr: float = 0.0
    statistical_edge: str = "Neutral"  # Strong, Moderate, Weak
    summary: str = ""

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "is_sideways": self.is_sideways,
            "sideways_reasons": self.sideways_reasons,
            "signal_score": self.signal_score,
            "signal_grade": self.signal_grade,
            "atr_regime": self.atr_regime,
            "expected_rr": self.expected_rr,
            "statistical_edge": self.statistical_edge,
            "summary": self.summary,
        }


@dataclass
class RiskVerdict:
    """Output of Agent 06: Risk Manager ('The Trade Killer')."""
    status: str = "APPROVED"  # APPROVED or KILLED
    veto_reasons: List[str] = field(default_factory=list)
    thesis_invalidation_level: float = 0.0
    risk_per_trade_pct: float = 0.25
    max_loss_usd: float = 0.0
    daily_loss_headroom_pct: float = 3.0
    max_dd_headroom_pct: float = 10.0
    kill_switches_active: bool = False
    adversarial_assessment: str = ""

    @property
    def is_approved(self) -> bool:
        return self.status == "APPROVED" and not self.veto_reasons

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "is_approved": self.is_approved,
            "veto_reasons": self.veto_reasons,
            "thesis_invalidation_level": self.thesis_invalidation_level,
            "risk_per_trade_pct": self.risk_per_trade_pct,
            "max_loss_usd": self.max_loss_usd,
            "daily_loss_headroom_pct": self.daily_loss_headroom_pct,
            "max_dd_headroom_pct": self.max_dd_headroom_pct,
            "kill_switches_active": self.kill_switches_active,
            "adversarial_assessment": self.adversarial_assessment,
        }


@dataclass
class ResearchPlan:
    """Output of Agent 07: Portfolio Manager (6 Inputs -> 1 Structured Plan)."""
    symbol: str
    date: str
    action: str  # EXECUTE_BUY, EXECUTE_SELL, STAND_ASIDE, KILLED
    inputs: Dict[str, str] = field(default_factory=dict)
    thesis: str = ""
    evidence: List[str] = field(default_factory=list)
    risks: List[str] = field(default_factory=list)
    next_steps: List[str] = field(default_factory=list)
    entry_price: float = 0.0
    sl_price: float = 0.0
    tp_price: float = 0.0
    lot_size: float = 0.0
    markdown_dossier: str = ""

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "date": self.date,
            "action": self.action,
            "inputs": self.inputs,
            "thesis": self.thesis,
            "evidence": self.evidence,
            "risks": self.risks,
            "next_steps": self.next_steps,
            "entry_price": self.entry_price,
            "sl_price": self.sl_price,
            "tp_price": self.tp_price,
            "lot_size": self.lot_size,
            "markdown_dossier": self.markdown_dossier,
        }

