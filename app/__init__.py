import json
import logging
from logging.handlers import RotatingFileHandler

__version__ = "0.1.0"


class _JSONLFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = record.args if isinstance(record.args, dict) else {}
        entry = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "event": record.getMessage() if not isinstance(record.args, dict) else record.msg,
            "payload": payload,
        }
        return json.dumps(entry, ensure_ascii=False)


def _configure_logger() -> logging.Logger:
    from app.paths import app_home
    log_dir = app_home() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "app.log"

    logger = logging.getLogger("lonta")
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)
    handler = RotatingFileHandler(log_path, maxBytes=5_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(_JSONLFormatter())
    logger.addHandler(handler)
    logger.propagate = False
    return logger


logger = _configure_logger()
