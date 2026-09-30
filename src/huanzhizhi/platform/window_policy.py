"""Strict Windows policies for desktop overlay windows."""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import sys
from typing import Protocol

from PySide6 import QtCore, QtGui, QtWidgets


GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
WS_EX_LAYERED = 0x00080000
WS_EX_NOACTIVATE = 0x08000000
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020
class WindowApi(Protocol):
    def get_extended_style(self, hwnd: int) -> int: ...

    def set_extended_style(self, hwnd: int, style: int) -> None: ...

    def refresh_extended_style(self, hwnd: int) -> None: ...

class Win32WindowApi:
    """Checked ctypes wrapper around the Win32 calls used by overlay windows."""

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise RuntimeError("Windows 窗口策略只能在 Windows 上初始化")
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        long_ptr = ctypes.c_longlong if sys.maxsize > 2**32 else ctypes.c_long
        try:
            get_window_long = user32.GetWindowLongPtrW
            set_window_long = user32.SetWindowLongPtrW
        except AttributeError:
            get_window_long = user32.GetWindowLongW
            set_window_long = user32.SetWindowLongW
        get_window_long.argtypes = (ctypes.wintypes.HWND, ctypes.c_int)
        get_window_long.restype = long_ptr
        set_window_long.argtypes = (ctypes.wintypes.HWND, ctypes.c_int, long_ptr)
        set_window_long.restype = long_ptr
        user32.SetWindowPos.argtypes = (
            ctypes.wintypes.HWND,
            ctypes.wintypes.HWND,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.wintypes.UINT,
        )
        user32.SetWindowPos.restype = ctypes.wintypes.BOOL
        self._get_window_long = get_window_long
        self._set_window_long = set_window_long
        self._set_window_pos = user32.SetWindowPos

    @staticmethod
    def _checked_hwnd(hwnd: int) -> ctypes.wintypes.HWND:
        if isinstance(hwnd, bool) or not isinstance(hwnd, int) or hwnd <= 0:
            raise ValueError("窗口句柄必须是正整数")
        return ctypes.wintypes.HWND(hwnd)

    @staticmethod
    def _raise_last_error(operation: str) -> None:
        error = ctypes.get_last_error()
        raise RuntimeError(f"{operation}失败，Windows 错误码：{error}")

    def get_extended_style(self, hwnd: int) -> int:
        native_hwnd = self._checked_hwnd(hwnd)
        ctypes.set_last_error(0)
        result = int(self._get_window_long(native_hwnd, GWL_EXSTYLE))
        error = ctypes.get_last_error()
        if result == 0 and error:
            self._raise_last_error("读取窗口扩展样式")
        return result

    def set_extended_style(self, hwnd: int, style: int) -> None:
        native_hwnd = self._checked_hwnd(hwnd)
        if isinstance(style, bool) or not isinstance(style, int) or style < 0:
            raise ValueError("窗口扩展样式必须是非负整数")
        ctypes.set_last_error(0)
        result = int(self._set_window_long(native_hwnd, GWL_EXSTYLE, style))
        error = ctypes.get_last_error()
        if result == 0 and error:
            self._raise_last_error("写入窗口扩展样式")

    def refresh_extended_style(self, hwnd: int) -> None:
        native_hwnd = self._checked_hwnd(hwnd)
        flags = SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED
        if not self._set_window_pos(native_hwnd, None, 0, 0, 0, 0, flags):
            self._raise_last_error("刷新窗口扩展样式")

class _OffscreenWindowApi:
    """Explicit test-platform adapter; offscreen widgets have no native window."""

    def __init__(self) -> None:
        self._styles: dict[int, int] = {}

    def get_extended_style(self, hwnd: int) -> int:
        return self._styles.get(hwnd, 0)

    def set_extended_style(self, hwnd: int, style: int) -> None:
        self._styles[hwnd] = style

    def refresh_extended_style(self, hwnd: int) -> None:
        del hwnd

class WindowPolicy:
    """Own and synchronize Win32 policy state for one Qt top-level widget."""

    def __init__(self, widget: QtWidgets.QWidget, *, api: WindowApi | None = None) -> None:
        if not isinstance(widget, QtWidgets.QWidget) or not widget.isWindow():
            raise TypeError("窗口策略需要一个 Qt 顶层 QWidget")
        self._widget = widget
        if api is None:
            api = (
                _OffscreenWindowApi()
                if QtGui.QGuiApplication.platformName() == "offscreen"
                else Win32WindowApi()
            )
        self._api = api
        self._input_passthrough = False
        self._layered: bool | None = None
        self._no_activate: bool | None = None
        self._input_policy_initialized = False

    @property
    def input_passthrough(self) -> bool:
        return self._input_passthrough

    def set_input_passthrough(
        self,
        enabled: bool,
        *,
        layered: bool | None = None,
        no_activate: bool | None = None,
    ) -> None:
        if not isinstance(enabled, bool):
            raise TypeError("鼠标穿透设置必须是布尔值")
        if layered is not None and not isinstance(layered, bool):
            raise TypeError("分层窗口设置必须是布尔值")
        if no_activate is not None and not isinstance(no_activate, bool):
            raise TypeError("禁止激活设置必须是布尔值")
        previous = (
            self._input_passthrough,
            self._layered,
            self._no_activate,
        )
        self._input_passthrough = enabled
        if layered is not None:
            self._layered = layered
        if no_activate is not None:
            self._no_activate = no_activate
        self._widget.setAttribute(
            QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents, enabled
        )
        try:
            self._apply_input_policy()
        except Exception:
            self._input_passthrough, self._layered, self._no_activate = previous
            self._widget.setAttribute(
                QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents,
                previous[0],
            )
            raise

    def sync_after_show(self) -> None:
        """Reapply desired state after Qt creates or recreates the native window."""
        if self._input_policy_initialized:
            self._apply_input_policy()

    def _apply_input_policy(self) -> None:
        hwnd = self._hwnd()
        current = self._api.get_extended_style(hwnd)
        target = current
        target = target | WS_EX_TRANSPARENT if self._input_passthrough else target & ~WS_EX_TRANSPARENT
        if self._layered is not None:
            target = target | WS_EX_LAYERED if self._layered else target & ~WS_EX_LAYERED
        if self._no_activate is not None:
            target = target | WS_EX_NOACTIVATE if self._no_activate else target & ~WS_EX_NOACTIVATE
        if target != current:
            self._api.set_extended_style(hwnd, target)
            self._api.refresh_extended_style(hwnd)
        applied = self._api.get_extended_style(hwnd)
        mask = WS_EX_TRANSPARENT
        if self._layered is not None:
            mask |= WS_EX_LAYERED
        if self._no_activate is not None:
            mask |= WS_EX_NOACTIVATE
        if applied & mask != target & mask:
            raise RuntimeError("窗口扩展样式写入后校验失败")
        self._input_policy_initialized = True

    def _hwnd(self) -> int:
        hwnd = int(self._widget.winId())
        if hwnd <= 0:
            raise RuntimeError("Qt 窗口没有有效的原生句柄")
        return hwnd

__all__ = (
    "GWL_EXSTYLE",
    "SWP_FRAMECHANGED",
    "SWP_NOACTIVATE",
    "SWP_NOMOVE",
    "SWP_NOSIZE",
    "SWP_NOZORDER",
    "WS_EX_LAYERED",
    "WS_EX_NOACTIVATE",
    "WS_EX_TRANSPARENT",
    "WindowPolicy",
    "Win32WindowApi",
)
