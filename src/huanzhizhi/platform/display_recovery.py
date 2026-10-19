"""Resynchronize Qt's Windows display state after sleep."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from collections.abc import Callable
import math

from PySide6 import QtCore, QtGui, QtWidgets


class DisplayRecovery(QtCore.QAbstractNativeEventFilter):
    """Refresh screen data on resume, then repair geometry after Qt processes it."""

    def __init__(
        self,
        window: QtWidgets.QWidget,
        recovered: Callable[[], None],
        log_message: Callable[[str], None],
    ) -> None:
        super().__init__()
        self._window = window
        self._recovered = recovered
        self._log_message = log_message
        self._app = QtWidgets.QApplication.instance()
        self._closed = False
        self._attempts = 0
        self._verifying = False
        self._timer = QtCore.QTimer(window)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._recover)
        self._user32 = None
        if QtGui.QGuiApplication.platformName() == 'windows':
            self._user32 = ctypes.WinDLL('user32', use_last_error=True)
            self._user32.GetDpiForWindow.argtypes = [wintypes.HWND]
            self._user32.GetDpiForWindow.restype = wintypes.UINT
            self._user32.SendMessageTimeoutW.argtypes = [
                wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
                wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t),
            ]
            self._user32.SendMessageTimeoutW.restype = wintypes.LPARAM
            self._app.installNativeEventFilter(self)

    def nativeEventFilter(self, event_type, message):
        if self._closed or self._user32 is None:
            return False, 0
        msg = wintypes.MSG.from_address(int(message))
        if msg.message == 0x0218:  # WM_POWERBROADCAST
            if msg.wParam == 4:  # PBT_APMSUSPEND: cancel a pending recovery.
                self._timer.stop()
                self._verifying = False
            elif msg.wParam in (7, 18):  # RESUMESUSPEND / RESUMEAUTOMATIC
                self.schedule()
        return False, 0

    def schedule(self) -> None:
        if self._closed:
            return
        # Windows sends resume to multiple windows and may send both resume events.
        self._attempts = 0
        self._verifying = False
        self._timer.start(1000)

    def close(self) -> None:
        self._closed = True
        self._timer.stop()
        if self._user32 is not None:
            self._app.removeNativeEventFilter(self)

    def _refresh_screens(self) -> None:
        # Qt's Windows SettingChangedEvent calls handleScreenChanges(). Use a
        # synchronous message to our own window; PostMessage cannot carry this
        # pointer-bearing system message. Never broadcast to other applications.
        result = ctypes.c_size_t()
        if not self._user32.SendMessageTimeoutW(
            int(self._window.winId()), 0x001A, 0, 0, 0x0002, 1000,
            ctypes.byref(result),
        ):
            raise OSError(ctypes.get_last_error(), '无法刷新 Qt 显示器信息')

    def _verify_dpi(self) -> None:
        if not self._app.screens():
            raise RuntimeError('显示器尚未就绪')
        for widget in self._app.topLevelWidgets():
            handle = widget.windowHandle()
            if handle is None:
                continue
            dpi = self._user32.GetDpiForWindow(int(widget.winId()))
            if not dpi:
                raise RuntimeError('无法读取窗口的系统 DPI')
            ratio = handle.devicePixelRatio()
            if not math.isclose(ratio, dpi / 96.0, abs_tol=0.001):
                raise RuntimeError(f'显示缩放尚未同步：Windows={dpi / 96.0:g}，Qt={ratio:g}')

    def _restore_windows(self) -> None:
        for widget in self._app.topLevelWidgets():
            handle = widget.windowHandle()
            if handle is None or widget.isMinimized() or widget.isMaximized() or widget.isFullScreen():
                continue
            # QWindow reads the native geometry using the refreshed scale.
            # QWidget may still cache coordinates from the temporary 100% scale.
            rect = QtCore.QRect(handle.position(), widget.size())
            handle.setGeometry(rect)
            widget.setGeometry(rect)
            mask = widget.mask()
            if not mask.isEmpty():
                widget.clearMask()
                widget.setMask(mask)
            widget.update()

    def _recover(self) -> None:
        if self._closed:
            return
        try:
            if not self._verifying:
                self._attempts += 1
                self._refresh_screens()
                self._verifying = True
                # Screen/DPR notifications are queued by Qt. Let them finish
                # before checking DPR or applying any window geometry.
                self._timer.start(0)
                return
            self._verifying = False
            self._verify_dpi()
            self._restore_windows()
            self._recovered()
        except (OSError, RuntimeError) as error:
            self._verifying = False
            if self._attempts < 10:
                self._timer.start(1000)
            else:
                self._log_message(f'睡眠唤醒后的显示恢复失败：{error}')
            return
        self._log_message('睡眠唤醒后的显示缩放和窗口布局已恢复')
