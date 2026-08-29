import logging

from ..models import Bias, Regime, Signal, SignalGrade, TradeDirection

log = logging.getLogger("xauusd_bot.strategy.scorer")


class SignalScorer:
    def __init__(self, score_a_min: int = 8, score_b_min: int = 5):
        self.score_a_min = score_a_min
        self.score_b_min = score_b_min

    def grade(self, signal: Signal) -> SignalGrade:
        base = signal.score
        if signal.regime in (Regime.TRENDING_BULL, Regime.TRENDING_BEAR):
            base += 1
        if signal.m15_zone is not None:
            base += 1
        if signal.is_pyramid_add:
            base += 1
        signal.score = base
        if base >= self.score_a_min:
            return SignalGrade.A
        if base >= self.score_b_min:
            return SignalGrade.B
        return SignalGrade.C

    def direction_from_bias(self, hierarchy_result: dict) -> TradeDirection:
        allowed = hierarchy_result.get("allowed_direction")
        if allowed == "bullish":
            return TradeDirection.BUY
        if allowed == "bearish":
            return TradeDirection.SELL
        return TradeDirection.BUY
