"""Samsung EHS: ring buffer for the log messages of the EHS Sentinel integration."""
from __future__ import annotations

import logging
import threading
import time
import traceback
from collections import deque

EHS_LOGGER = "custom_components.ehs_sentinel"


class EhsLogBuffer(logging.Handler):
    """Hält die letzten Meldungen von EHS Sentinel im Speicher (wie der EMS-ESP Manager)."""

    def __init__(self, size: int = 2000) -> None:
        super().__init__(level=logging.NOTSET)
        self._buf: deque[dict] = deque(maxlen=size)
        self._next_id = 1
        self._lock_buf = threading.Lock()
        self._orig_level: int | None = None

    # ---- logging.Handler ----
    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = record.getMessage()
            if record.exc_info:
                msg += "\n" + "".join(traceback.format_exception(*record.exc_info)).rstrip()
            name = record.name.replace(EHS_LOGGER, "ehs_sentinel")
            with self._lock_buf:
                self._buf.append(
                    {"id": self._next_id, "ts": record.created, "level": record.levelname, "name": name, "msg": msg[:4000]}
                )
                self._next_id += 1
        except Exception:  # pylint: disable=broad-except
            self.handleError(record)

    # ---- Abfrage ----
    def entries(self, since: int = 0, min_level: int = 0, limit: int = 500) -> list[dict]:
        with self._lock_buf:
            items = [e for e in self._buf if e["id"] > since and logging.getLevelName(e["level"]) >= min_level]
        return items[-limit:]

    def clear(self) -> None:
        with self._lock_buf:
            self._buf.clear()

    # ---- Ein-/Aushängen, Protokollstufe ----
    def attach(self) -> None:
        logger = logging.getLogger(EHS_LOGGER)
        if self not in logger.handlers:
            logger.addHandler(self)
        self.add_marker("Protokoll-Aufzeichnung gestartet")

    def detach(self) -> None:
        self.set_level("default")
        logging.getLogger(EHS_LOGGER).removeHandler(self)

    def set_level(self, level: str) -> str:
        logger = logging.getLogger(EHS_LOGGER)
        if self._orig_level is None:
            self._orig_level = logger.level
        if level == "debug":
            logger.setLevel(logging.DEBUG)
        elif level == "info":
            logger.setLevel(logging.INFO)
        else:
            logger.setLevel(self._orig_level)
            self._orig_level = None
            level = "default"
        self.add_marker(f"Protokollstufe: {level}")
        return level

    def current_level(self) -> str:
        if self._orig_level is None:
            return "default"
        lvl = logging.getLogger(EHS_LOGGER).level
        return "debug" if lvl <= logging.DEBUG else "info"

    def add_marker(self, text: str) -> None:
        with self._lock_buf:
            self._buf.append({"id": self._next_id, "ts": time.time(), "level": "INFO", "name": "panel", "msg": f"— {text} —"})
            self._next_id += 1
