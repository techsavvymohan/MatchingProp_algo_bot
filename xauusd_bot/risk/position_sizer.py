import logging
from typing import Optional

from ..models import AccountInfo, TradeDirection

log = logging.getLogger("xauusd_bot.risk.sizer")


class PositionSizer:
    def __init__(
        self,
        initial_risk_pct: float = 0.85,
        max_pyramid_entries: int = 4,
        enable_profit_compounding: bool = False,
        initial_balance: float = 10000.0,
        compounding_cap_mult: float = 2.0,
    ):
        self.initial_risk_pct = initial_risk_pct
        self.max_pyramid_entries = max_pyramid_entries
        self.enable_profit_compounding = enable_profit_compounding
        self.initial_balance = initial_balance
        self.compounding_cap_mult = compounding_cap_mult

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
        risk_scale: float = 1.0,
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

        if getattr(account, "balance", 0.0) > 0 and self.initial_balance <= 0:
            self.initial_balance = account.balance
            log.info("PositionSizer dynamically auto-calibrated baseline to account balance: $%.2f", self.initial_balance)

        if self.enable_profit_compounding and self.initial_balance > 0:
            # House Money Compounding: Anchor base risk to initial_balance in drawdown, compound when ahead
            eff_equity = max(account.equity, self.initial_balance)
            eff_equity = min(eff_equity, self.initial_balance * self.compounding_cap_mult)
            account_risk_amount = eff_equity * (self.initial_risk_pct / 100.0)
        else:
            account_risk_amount = account.equity * (self.initial_risk_pct / 100.0)

        if risk_scale > 0 and risk_scale != 1.0:
            account_risk_amount *= risk_scale

        if max_risk_amount is not None and max_risk_amount > 0:
            account_risk_amount = min(account_risk_amount, max_risk_amount)
        if remaining_budget > 0:
            account_risk_amount = min(account_risk_amount, remaining_budget)

        raw_lots = account_risk_amount / risk_per_unit
        raw_lots = max(raw_lots, min_lot)
        raw_lots = min(raw_lots, max_lot)
        if lot_step > 0:
            raw_lots = round(raw_lots / lot_step) * lot_step
        return round(raw_lots, 2)

    def calc_initial_lot(
        self,
        equity: float,
        entry_price: float,
        sl_price: float,
        point_value: float = 1.0,
        contract_size: int = 100,
        direction: Optional[TradeDirection] = None,
        min_lot: float = 0.01,
        max_lot: float = 100.0,
        lot_step: float = 0.01,
        risk_scale: float = 1.0,
    ) -> float:
        if direction is None:
            direction = TradeDirection.BUY if entry_price > sl_price else TradeDirection.SELL
        acct = AccountInfo(balance=equity, equity=equity)
        return self.calculate_lot_size(
            account=acct,
            entry_price=entry_price,
            sl_price=sl_price,
            direction=direction,
            point_value=point_value,
            contract_size=contract_size,
            min_lot=min_lot,
            max_lot=max_lot,
            lot_step=lot_step,
            risk_scale=risk_scale,
        )

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

    @staticmethod
    def calculate_kelly_fraction(
        win_rate: float,
        win_loss_ratio: float,
        half_kelly: bool = True,
        max_kelly: float = 0.02,
    ) -> float:
        """Calculate optimal fraction of capital to risk using the Kelly Criterion.
        
        Formula (from awesome-quant / kelly-criterion):
            f* = (p * b - q) / b
            where:
                p = win probability (e.g. 0.55)
                q = 1 - p (loss probability)
                b = win/loss payoff ratio (e.g. 1.5)
        
        Half-Kelly (f* / 2) is used standardly in quantitative trading to minimize
        variance of log-wealth and prevent tail-risk ruin.
        """
        if win_loss_ratio <= 0 or win_rate <= 0 or win_rate >= 1.0:
            return 0.0
        p = win_rate
        q = 1.0 - p
        b = win_loss_ratio
        kelly = (p * b - q) / b
        if kelly <= 0:
            return 0.0
        fraction = kelly / 2.0 if half_kelly else kelly
        return min(fraction, max_kelly)

    def volatility_adjusted_risk(
        self,
        current_atr: float,
        baseline_atr: float,
        min_factor: float = 0.5,
        max_factor: float = 1.5,
    ) -> float:
        """Scale risk percentage dynamically according to current volatility regime.
        
        During high volatility expansion (current_atr > baseline_atr), risk is scaled down.
        During low volatility compression, risk is scaled up up to max_factor.
        """
        if current_atr <= 0 or baseline_atr <= 0:
            return self.initial_risk_pct
        factor = baseline_atr / current_atr
        clamped_factor = max(min_factor, min(max_factor, factor))
        return self.initial_risk_pct * clamped_factor


