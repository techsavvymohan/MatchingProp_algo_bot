import csv
import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from ..models import DailyState, ExitReason, PyraCluster, TradeDirection, TradeLeg, TradeStatus

log = logging.getLogger("xauusd_bot.state.persistence")


class StatePersistence:
    def __init__(self, db_path: str = "data/bot_state.db", trade_log_path: str = "data/trade_log.csv"):
        self.db_path = db_path
        self.trade_log_path = trade_log_path
        self._conn: Optional[sqlite3.Connection] = None
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        Path(trade_log_path).parent.mkdir(parents=True, exist_ok=True)

    def connect(self):
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._init_schema()

    def _init_schema(self):
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS daily_state (
                date TEXT PRIMARY KEY,
                start_equity REAL,
                peak_equity REAL,
                trades_today INTEGER DEFAULT 0,
                kill_switch_active INTEGER DEFAULT 0
            )
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS trade_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cluster_id TEXT,
                leg_id TEXT,
                signal_id TEXT,
                direction TEXT,
                entry_price REAL,
                exit_price REAL,
                lot_size REAL,
                sl_price REAL,
                tp_price REAL,
                open_time TEXT,
                close_time TEXT,
                exit_reason TEXT,
                pnl REAL,
                status TEXT,
                entry_tf TEXT
            )
        """)

    def save_daily_state(self, state: DailyState):
        self.connect()
        self._conn.execute(
            """INSERT OR REPLACE INTO daily_state (date, start_equity, peak_equity, trades_today, kill_switch_active)
               VALUES (?, ?, ?, ?, ?)""",
            (state.date, state.start_equity, state.peak_equity, state.trades_today, int(state.kill_switch_active)),
        )
        self._conn.commit()

    def load_daily_state(self) -> Optional[DailyState]:
        self.connect()
        row = self._conn.execute(
            "SELECT date, start_equity, peak_equity, trades_today, kill_switch_active FROM daily_state ORDER BY date DESC LIMIT 1"
        ).fetchone()
        if row:
            return DailyState(
                date=row[0], start_equity=row[1], current_equity=row[1],
                peak_equity=row[2], trades_today=row[3],
                kill_switch_active=bool(row[4]),
            )
        return None

    def save_trade_leg(self, leg: TradeLeg, cluster_id: str, signal_id: str, entry_tf: str = ""):
        self.connect()
        self._conn.execute(
            """INSERT OR REPLACE INTO trade_log (cluster_id, leg_id, signal_id, direction, entry_price, exit_price,
               lot_size, sl_price, tp_price, open_time, close_time, exit_reason, pnl, status, entry_tf)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                cluster_id, leg.leg_id, signal_id, leg.direction.value,
                leg.entry_price, leg.exit_price or 0,
                leg.lot_size, leg.sl_price, leg.tp_price,
                leg.open_time.isoformat() if leg.open_time else "",
                leg.close_time.isoformat() if leg.close_time else "",
                leg.exit_reason.value if leg.exit_reason else "",
                leg.pnl, leg.status.value, entry_tf,
            ),
        )
        self._conn.commit()

    def append_trade_csv(self, trade_data: dict):
        path = Path(self.trade_log_path)
        write_header = not path.exists()
        with open(path, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=trade_data.keys())
            if write_header:
                writer.writeheader()
            writer.writerow(trade_data)

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None
