"""Global left-button polling used by desktop-pet interactions."""

from __future__ import annotations

import ctypes
import sys
from collections.abc import Callable

from PySide6 import QtCore, QtGui


VK_LBUTTON = 0x01
POLL_INTERVAL_MS = 20


def _win32_left_button_down() -> bool:
    if sys.platform != "win32":
        raise RuntimeError("全局鼠标监听仅支持 Windows")
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    get_async_key_state = user32.GetAsyncKeyState
    get_async_key_state.argtypes = [ctypes.c_int]
    get_async_key_state.restype = ctypes.c_short
    return bool(get_async_key_state(VK_LBUTTON) & 0x8000)


def _qt_cursor_position() -> QtCore.QPoint:
    return QtGui.QCursor.pos()


class GlobalMouseWatcher(QtCore.QObject):
    """Poll the Windows left button and emit one coherent drag sequence."""

    pressed = QtCore.Signal(int, int)
    moved = QtCore.Signal(int, int)
    released = QtCore.Signal(int, int)
    error = QtCore.Signal(str)
    enabled_changed = QtCore.Signal(bool)

    def __init__(
        self,
        parent: QtCore.QObject | None = None,
        *,
        button_state_provider: Callable[[], bool] | None = None,
        cursor_provider: Callable[[], QtCore.QPoint] | None = None,
    ) -> None:
        super().__init__(parent)
        self._button_state_provider = button_state_provider or _win32_left_button_down
        self._cursor_provider = cursor_provider or _qt_cursor_position
        self._left_was_down = False
        self._drag_active = False
        self._last_pos: tuple[int, int] | None = None
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(POLL_INTERVAL_MS)
        self._timer.timeout.connect(self.poll_once)

    @property
    def enabled(self) -> bool:
        return self._timer.isActive()

    def set_enabled(self, enabled: bool) -> None:
        if not isinstance(enabled, bool):
            raise TypeError("全局鼠标监听开关必须是 bool")
        if enabled:
            self.start()
        else:
            self.stop()

    def start(self) -> None:
        if self.enabled:
            return
        try:
            self._left_was_down = bool(self._button_state_provider())
            self._drag_active = self._left_was_down
            self._last_pos = self._read_position() if self._drag_active else None
        except Exception as exc:
            self._reset()
            raise RuntimeError(f"无法启动全局鼠标监听：{exc}") from exc
        self._timer.start()
        if not self.enabled:
            self._reset()
            raise RuntimeError("全局鼠标监听计时器启动失败")
        self.enabled_changed.emit(True)

    def stop(self) -> None:
        was_enabled = self.enabled
        self._timer.stop()
        self._reset()
        if was_enabled:
            self.enabled_changed.emit(False)

    @QtCore.Slot()
    def poll_once(self) -> None:
        try:
            left_down = bool(self._button_state_provider())
            x, y = self._read_position()
        except Exception as exc:
            self.stop()
            self.error.emit(f"全局鼠标监听失败：{exc}")
            return

        if not self._left_was_down and left_down:
            self._drag_active = True
            self._last_pos = (x, y)
            self.pressed.emit(x, y)
        elif self._left_was_down and left_down and self._drag_active:
            position = (x, y)
            if position != self._last_pos:
                self._last_pos = position
                self.moved.emit(x, y)
        elif self._left_was_down and not left_down and self._drag_active:
            self._drag_active = False
            self._last_pos = (x, y)
            self.released.emit(x, y)
        self._left_was_down = left_down

    def close(self) -> None:
        self.stop()

    def _read_position(self) -> tuple[int, int]:
        point = self._cursor_provider()
        if not isinstance(point, QtCore.QPoint):
            raise TypeError("鼠标位置提供器必须返回 QPoint")
        return int(point.x()), int(point.y())

    def _reset(self) -> None:
        self._left_was_down = False
        self._drag_active = False
        self._last_pos = None


__all__ = ("GlobalMouseWatcher", "POLL_INTERVAL_MS")
