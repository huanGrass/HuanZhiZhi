"""UTF-8 run log with the original application's observable format."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import Callable, TextIO


class RunLogError(RuntimeError):
    """Raised when a run log cannot be created, written, or closed."""


class RunLogComponent:
    """Create and append to one ``run_YYYYMMDD_HHMMSS.log`` file."""

    def __init__(
        self,
        log_root: Path,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not isinstance(log_root, Path):
            raise TypeError("log_root must be a pathlib.Path")
        if clock is not None and not callable(clock):
            raise TypeError("clock must be callable")
        self._clock = clock or datetime.now
        self._lock = RLock()
        self._file: TextIO | None = None
        created_at = self._now()
        self._path = log_root / f"run_{created_at.strftime('%Y%m%d_%H%M%S')}.log"
        try:
            log_root.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise RunLogError(f"cannot create log directory {log_root}: {error}") from error
        try:
            self._file = self._path.open(
                "a", encoding="utf-8", newline="\n"
            )
        except OSError as error:
            raise RunLogError(f"cannot open run log {self._path}: {error}") from error

    @property
    def path(self) -> Path:
        return self._path

    def append(self, message: str) -> str:
        if not isinstance(message, str):
            raise TypeError("log message must be text")
        if not message.strip():
            raise ValueError("log message must not be empty")
        normalized = message.replace("\r\n", "\n").replace("\r", "\n")
        physical_lines = normalized.split("\n")
        with self._lock:
            file = self._open_file()
            timestamp = self._now().strftime("%H:%M:%S")
            entry = "\n".join(f"[{timestamp}] {line}" for line in physical_lines)
            try:
                file.write(f"{entry}\n")
                file.flush()
            except (OSError, UnicodeError) as error:
                raise RunLogError(f"cannot write run log {self._path}: {error}") from error
        return entry

    def close(self) -> None:
        with self._lock:
            file = self._file
            if file is None:
                return
            try:
                file.close()
            except OSError as error:
                raise RunLogError(f"cannot close run log {self._path}: {error}") from error
            self._file = None

    def _open_file(self) -> TextIO:
        if self._file is None:
            raise RunLogError(f"run log is closed: {self._path}")
        return self._file

    def _now(self) -> datetime:
        try:
            value = self._clock()
        except Exception as error:
            raise RunLogError(f"run log clock failed: {error}") from error
        if not isinstance(value, datetime):
            raise RunLogError("run log clock must return datetime")
        return value
