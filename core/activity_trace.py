"""Bounded, in-memory activity trace shared by the GUI and worker threads."""

import logging
import threading
from collections import deque
from datetime import datetime
from typing import Deque, List, Tuple


class ActivityTrace:
    """Keep the most recent activity entries without writing them to disk."""

    def __init__(self, limit: int = 50, max_chars: int = 512) -> None:
        """Create a bounded trace with a maximum entry count and length."""
        if limit < 1 or max_chars < 1:
            raise ValueError("Trace limits must be positive")
        self._limit = limit
        self._max_chars = max_chars
        self._entries: Deque[str] = deque(maxlen=limit)
        self._lock = threading.Lock()
        self._revision = 0

    def add(self, message: str) -> None:
        """Append one timestamped, single-line entry."""
        text = " ".join(str(message).splitlines())
        entry = f"{datetime.now():%H:%M:%S} | {text}"[:self._max_chars]
        with self._lock:
            self._entries.append(entry)
            self._revision += 1

    def snapshot(self) -> Tuple[int, List[str]]:
        """Return a consistent revision and copy of the retained entries."""
        with self._lock:
            return self._revision, list(self._entries)

    def clear(self) -> None:
        """Start a new application session with an empty trace."""
        with self._lock:
            self._entries.clear()
            self._revision += 1


activity_trace = ActivityTrace()


class ActivityTraceHandler(logging.Handler):
    """Copy existing application log messages to the in-memory trace."""

    def emit(self, record: logging.LogRecord) -> None:
        """Retain the formatted message without writing a new log record."""
        try:
            activity_trace.add(f"{record.levelname} | {record.name} | {record.getMessage()}")
        except (ValueError, TypeError):
            self.handleError(record)
