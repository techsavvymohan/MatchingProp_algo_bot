import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional


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

    news_blocked: bool = False
    spread_blocked: bool = False
    session_blocked: bool = False
    equity_blocked: bool = False
    sideways_blocked: bool = False
    m15_zone: Optional[tuple] = None

    ao_saucer: bool = False
    ha_trend: str = ""
    tradingview_recommendation: str = ""

    is_pyramid_add: bool = False
    parent_signal_id: Optional[str] = None

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
    breakeven_activated: bool = False
    highest_price: float = 0.0
    lowest_price: float = 0.0
    entry_tf: str = ""
    open_time: Optional[datetime] = None
    status: TradeStatus = TradeStatus.OPEN

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
        return total_notional / total_lots if total_lots else 0.0

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
