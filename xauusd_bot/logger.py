import logging
import sys
from pathlib import Path


def setup_logging(
    level: str = "INFO",
    log_file: str = "logs/xauusd_bot.log",
    telegram_token: str = "",
    telegram_chat_id: str = "",
) -> logging.Logger:
    root = logging.getLogger("xauusd_bot")
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(formatter)
    root.addHandler(stream)

    if log_file:
        p = Path(log_file)
        p.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setFormatter(formatter)
        root.addHandler(fh)

    if telegram_token and telegram_chat_id:
        root.addHandler(TelegramHandler(telegram_token, telegram_chat_id))

    return root


class TelegramHandler(logging.Handler):
    def __init__(self, token: str, chat_id: str, level: int = logging.WARNING):
        super().__init__(level)
        self.token = token
        self.chat_id = chat_id

    def emit(self, record):
        try:
            import requests
            msg = self.format(record)
            if len(msg) > 4000:
                msg = msg[:4000] + "..."
            requests.post(
                f"https://api.telegram.org/bot{self.token}/sendMessage",
                json={"chat_id": self.chat_id, "text": f"`{msg}`", "parse_mode": "Markdown"},
                timeout=10,
            )
        except Exception:
            self.handleError(record)
