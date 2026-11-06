"""
Enterprise-grade logging framework for LAM.

Every operation is logged with structured metadata including:
timestamp, severity, subsystem, operation, object IDs, offsets,
file paths, and detailed technical messages.

Logs appear live in the GUI, save to disk, and support filtering.
"""

import os
import time
import threading
from enum import IntEnum
from typing import List, Callable, Optional, TextIO
from dataclasses import dataclass, field


class LogLevel(IntEnum):
    DEBUG = 0
    INFO = 1
    WARN = 2
    ERROR = 3
    FATAL = 4

    def label(self) -> str:
        return self.name

    def color_tag(self) -> str:
        return {
            LogLevel.DEBUG: "log_debug",
            LogLevel.INFO: "log_info",
            LogLevel.WARN: "log_warn",
            LogLevel.ERROR: "log_error",
            LogLevel.FATAL: "log_fatal",
        }[self]


@dataclass
class LogEntry:
    timestamp: float
    level: LogLevel
    subsystem: str
    operation: str
    message: str
    object_id: Optional[int] = None
    wem_id: Optional[int] = None
    offset: Optional[int] = None
    size: Optional[int] = None
    file_path: Optional[str] = None

    def format_time(self) -> str:
        t = time.localtime(self.timestamp)
        ms = int((self.timestamp % 1) * 1000)
        return f"{t.tm_hour:02d}:{t.tm_min:02d}:{t.tm_sec:02d}.{ms:03d}"

    def format_short(self) -> str:
        parts = [
            f"[{self.format_time()}]",
            f"[{self.level.label():5s}]",
            f"[{self.subsystem}]",
            self.message,
        ]
        return " ".join(parts)

    def format_detailed(self) -> str:
        parts = [self.format_short()]
        extras = []
        if self.operation:
            extras.append(f"op={self.operation}")
        if self.object_id is not None:
            extras.append(f"oid={self.object_id}")
        if self.wem_id is not None:
            extras.append(f"wem={self.wem_id}")
        if self.offset is not None:
            extras.append(f"off=0x{self.offset:X}")
        if self.size is not None:
            extras.append(f"size={self.size}")
        if self.file_path:
            extras.append(f"file={os.path.basename(self.file_path)}")
        if extras:
            parts.append(f"  ({', '.join(extras)})")
        return "".join(parts)


LogCallback = Callable[[LogEntry], None]


class LamLogger:
    """
    Thread-safe, observable logger.
    GUI panels subscribe to receive log entries in real-time.
    """

    def __init__(self, log_file_path: Optional[str] = None):
        self._entries: List[LogEntry] = []
        self._callbacks: List[LogCallback] = []
        self._lock = threading.Lock()
        self._min_level = LogLevel.DEBUG
        self._file_handle: Optional[TextIO] = None

        if log_file_path:
            try:
                # Open in append mode ('a') so logs are persistent across launches
                self._file_handle = open(log_file_path, 'a', encoding='utf-8')
                # Write a divider header marking the start of a new application session
                session_start_time = time.strftime('%Y-%m-%d %H:%M:%S')
                self._file_handle.write(f"\n======================================================================\n")
                self._file_handle.write(f"=== NEW SESSION STARTED AT {session_start_time} ===\n")
                self._file_handle.write(f"======================================================================\n")
                self._file_handle.flush()
            except OSError:
                pass

    def set_level(self, level: LogLevel) -> None:
        self._min_level = level

    def subscribe(self, callback: LogCallback) -> None:
        with self._lock:
            self._callbacks.append(callback)

    def unsubscribe(self, callback: LogCallback) -> None:
        with self._lock:
            self._callbacks = [cb for cb in self._callbacks if cb is not callback]

    def log(self, level: LogLevel, subsystem: str, message: str,
            operation: str = "", object_id: Optional[int] = None,
            wem_id: Optional[int] = None, offset: Optional[int] = None,
            size: Optional[int] = None, file_path: Optional[str] = None) -> None:

        if level < self._min_level:
            return

        entry = LogEntry(
            timestamp=time.time(),
            level=level,
            subsystem=subsystem,
            operation=operation,
            message=message,
            object_id=object_id,
            wem_id=wem_id,
            offset=offset,
            size=size,
            file_path=file_path,
        )

        with self._lock:
            self._entries.append(entry)

            if self._file_handle:
                try:
                    self._file_handle.write(entry.format_detailed() + "\n")
                    self._file_handle.flush()
                except OSError:
                    pass

            for cb in self._callbacks:
                try:
                    cb(entry)
                except Exception:
                    pass

    def debug(self, subsystem: str, message: str, **kwargs) -> None:
        self.log(LogLevel.DEBUG, subsystem, message, **kwargs)

    def info(self, subsystem: str, message: str, **kwargs) -> None:
        self.log(LogLevel.INFO, subsystem, message, **kwargs)

    def warn(self, subsystem: str, message: str, **kwargs) -> None:
        self.log(LogLevel.WARN, subsystem, message, **kwargs)

    def error(self, subsystem: str, message: str, **kwargs) -> None:
        self.log(LogLevel.ERROR, subsystem, message, **kwargs)

    def fatal(self, subsystem: str, message: str, **kwargs) -> None:
        self.log(LogLevel.FATAL, subsystem, message, **kwargs)

    def get_entries(self, level: Optional[LogLevel] = None,
                    subsystem: Optional[str] = None) -> List[LogEntry]:
        with self._lock:
            entries = list(self._entries)
        if level is not None:
            entries = [e for e in entries if e.level >= level]
        if subsystem is not None:
            entries = [e for e in entries if e.subsystem == subsystem]
        return entries

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def export(self, path: str, detailed: bool = True) -> None:
        entries = self.get_entries()
        with open(path, 'w', encoding='utf-8') as f:
            for entry in entries:
                line = entry.format_detailed() if detailed else entry.format_short()
                f.write(line + "\n")

    def close(self) -> None:
        if self._file_handle:
            try:
                self._file_handle.close()
            except OSError:
                pass

    @property
    def entry_count(self) -> int:
        return len(self._entries)


# Global logger instance
_global_logger: Optional[LamLogger] = None


def get_logger() -> LamLogger:
    global _global_logger
    if _global_logger is None:
        _global_logger = LamLogger(log_file_path="lam_session.log")
    return _global_logger


def set_logger(logger: LamLogger) -> None:
    global _global_logger
    _global_logger = logger
