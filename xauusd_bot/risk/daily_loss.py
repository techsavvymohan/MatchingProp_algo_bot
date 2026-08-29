import logging
from datetime import datetime, timezone
from typing import Optional

from ..models import AccountInfo, DailyState
from ..utils.time_utils import broker_date

log = logging.getLogger("xauusd_bot.risk.daily_loss")


class DailyLossTracker:
    def __init__(self, daily_limit_pct: float = 3.0, buffer_pct: float = 1.0,
                 reset_hour: int = 0, reset_tz: str = "UTC"):
        self.limit_pct = daily_limit_pct
        self.buffer_pct = buffer_pct
        self.reset_hour = reset_hour
        self.reset_tz = reset_tz
        self.state: Optional[DailyState] = None

    def update(self, account: AccountInfo):
        today = broker_date(account.server_time or datetime.now(timezone.utc).replace(tzinfo=None),
                            self.reset_hour, self.reset_tz)
        if self.state is None or self.state.date != today:
            self.state = DailyState(
                date=today,
                start_equity=account.equity,
                current_equity=account.equity,
                peak_equity=account.equity,
                daily_pnl=0.0,
                trades_today=0,
                kill_switch_active=False,
            )
            log.info("New daily state: date=%s start_equity=%.2f", today, account.equity)
        else:
            self.state.current_equity = account.equity
            self.state.daily_pnl = account.equity - self.state.start_equity
            if account.equity > self.state.peak_equity:
                self.state.peak_equity = account.equity

    def loss_used_pct(self) -> float:
        if self.state is None or self.state.start_equity <= 0:
            return 0.0
        return max(0.0, -self.state.daily_pnl / self.state.start_equity * 100)

    def remaining_budget_pct(self) -> float:
        used = self.loss_used_pct()
        return max(0.0, self.limit_pct - used)

    def remaining_budget_amount(self) -> float:
        if self.state is None:
            return 0.0
        used_amount = max(0.0, -self.state.daily_pnl)
        max_loss = self.state.start_equity * (self.limit_pct / 100.0)
        return max(0.0, max_loss - used_amount)

    def effective_remaining_pct(self) -> float:
        return max(0.0, self.limit_pct - self.buffer_pct - self.loss_used_pct())

    def kill_switch_engaged(self) -> bool:
        if self.state is None:
            return False
        if self.state.kill_switch_active:
            return True
        if self.loss_used_pct() >= (self.limit_pct - self.buffer_pct):
            self.state.kill_switch_active = True
            log.warning("DAILY LOSS KILL SWITCH — used=%.2f%% limit=%.2f%% buffer=%.2f%%",
                        self.loss_used_pct(), self.limit_pct, self.buffer_pct)
            return True
        return False

    def register_trade(self):
        if self.state:
            self.state.trades_today += 1

    def reset(self):
        self.state = None
