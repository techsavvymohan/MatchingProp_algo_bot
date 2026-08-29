import logging
from typing import Optional

from ..models import AccountInfo, TradeDirection

log = logging.getLogger("xauusd_bot.risk.sizer")


class PositionSizer:
    def __init__(self, initial_risk_pct: float = 0.25, max_pyramid_entries: int = 4):
        self.initial_risk_pct = initial_risk_pct
        self.max_pyramid_entries = max_pyramid_entries

    def calculate_lot_size(
        self,
        account: AccountInfo,
        entry_price: float,
        sl_price: float,
        direction: TradeDirection,
        point_value: float,
        contract_size: int = 100,
        min_lot: float = 0.01,
        max_lot: float = 100.0,
        lot_step: float = 0.01,
        remaining_budget: float = 0.0,
        max_risk_amount: Optional[float] = None,
        tick_size: float = 0.0,
    ) -> float:
        if entry_price <= 0 or sl_price <= 0:
            log.warning("Invalid prices: entry=%.2f sl=%.2f", entry_price, sl_price)
            return min_lot
        if direction == TradeDirection.BUY:
            risk_points = entry_price - sl_price
        else:
            risk_points = sl_price - entry_price
        if risk_points <= 0:
            log.warning("SL must be beyond entry for risk to exist")
            return min_lot

        if tick_size > 0:
            risk_per_unit = (risk_points / tick_size) * point_value
        else:
            risk_per_unit = risk_points * point_value * contract_size

        if risk_per_unit <= 0:
            return min_lot

        account_risk_amount = account.equity * (self.initial_risk_pct / 100.0)

        if max_risk_amount is not None and max_risk_amount > 0:
            account_risk_amount = min(account_risk_amount, max_risk_amount)
        if remaining_budget > 0:
            per_entry_budget = remaining_budget / max(self.max_pyramid_entries, 1)
            account_risk_amount = min(account_risk_amount, per_entry_budget)

        raw_lots = account_risk_amount / risk_per_unit
        raw_lots = max(raw_lots, min_lot)
        raw_lots = min(raw_lots, max_lot)
        if lot_step > 0:
            raw_lots = round(raw_lots / lot_step) * lot_step
        return round(raw_lots, 2)

    def calc_risk_amount(self, lot_size: float, entry: float, sl: float,
                          direction: TradeDirection, point_value: float,
                          contract_size: int = 100, tick_size: float = 0.0) -> float:
        if direction == TradeDirection.BUY:
            risk_pts = entry - sl
        else:
            risk_pts = sl - entry
        if tick_size > 0:
            return (risk_pts / tick_size) * point_value * lot_size
        return risk_pts * point_value * contract_size * lot_size


