"""Coordinate conversion shared by Win32-backed desktop tasks."""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import sys

from PySide6 import QtCore, QtGui


def _native_monitor(x: int, y: int) -> tuple[str, int, int]:
    class MonitorInfo(ctypes.Structure):
        _fields_ = [('size', ctypes.wintypes.DWORD), ('monitor', ctypes.wintypes.RECT),
                    ('work', ctypes.wintypes.RECT), ('flags', ctypes.wintypes.DWORD),
                    ('device', ctypes.wintypes.WCHAR * 32)]

    user32 = ctypes.WinDLL('user32', use_last_error=True)
    user32.MonitorFromPoint.argtypes = [ctypes.wintypes.POINT, ctypes.wintypes.DWORD]
    user32.MonitorFromPoint.restype = ctypes.wintypes.HANDLE
    user32.GetMonitorInfoW.argtypes = [ctypes.wintypes.HANDLE, ctypes.POINTER(MonitorInfo)]
    user32.GetMonitorInfoW.restype = ctypes.wintypes.BOOL
    handle = user32.MonitorFromPoint(ctypes.wintypes.POINT(x, y), 2)
    info = MonitorInfo()
    info.size = ctypes.sizeof(info)
    if not handle or not user32.GetMonitorInfoW(handle, ctypes.byref(info)):
        raise OSError(ctypes.get_last_error(), '无法读取鼠标所在显示器')
    return info.device, info.monitor.left, info.monitor.top


def _uses_native_screens() -> bool:
    return (sys.platform == 'win32' and QtGui.QGuiApplication.instance() is not None
            and QtGui.QGuiApplication.platformName() != 'offscreen')


def physical_to_qt_global(x: int, y: int) -> QtCore.QPoint:
    """Convert a task's physical screen pixels to Qt global logical pixels."""
    if _uses_native_screens():
        name, left, top = _native_monitor(x, y)
        # Qt can expose an EDID product name while Win32 uses \\.\DISPLAYn.
        # Windows preserves monitor origins in Qt's logical screen geometry.
        screen = next((s for s in QtGui.QGuiApplication.screens()
                       if s.geometry().topLeft() == QtCore.QPoint(left, top)), None)
        if screen is None:
            raise RuntimeError(f'无法匹配显示器：{name}')
        origin, ratio = screen.geometry().topLeft(), screen.devicePixelRatio()
        return origin + QtCore.QPoint(round((x-left)/ratio), round((y-top)/ratio))
    screen = QtGui.QGuiApplication.primaryScreen()
    ratio = 1.0 if screen is None else max(1.0, float(screen.devicePixelRatio()))
    return QtCore.QPoint(round(x / ratio), round(y / ratio))


def qt_to_physical_global(point: QtCore.QPointF | QtCore.QPoint) -> QtCore.QPoint:
    """Convert an animated Qt hotspot back to physical input coordinates."""
    if _uses_native_screens():
        screen = QtGui.QGuiApplication.screenAt(point.toPoint() if isinstance(point, QtCore.QPointF) else point)
        if screen is None:
            screen = min(QtGui.QGuiApplication.screens(),
                         key=lambda s: (QtCore.QPointF(s.geometry().center())-point).manhattanLength())
        origin, ratio = screen.geometry().topLeft(), screen.devicePixelRatio()
        # On Windows Qt retains the native monitor origin and scales its size.
        name, left, top = _native_monitor(origin.x()+1, origin.y()+1)
        if (left, top) != (origin.x(), origin.y()):
            raise RuntimeError('显示器布局已变化，请重试操作')
        return QtCore.QPoint(round(left+(point.x()-origin.x())*ratio),
                            round(top+(point.y()-origin.y())*ratio))
    screen = QtGui.QGuiApplication.primaryScreen()
    ratio = 1.0 if screen is None else max(1.0, float(screen.devicePixelRatio()))
    return QtCore.QPoint(round(point.x()*ratio), round(point.y()*ratio))


__all__ = ("physical_to_qt_global", "qt_to_physical_global")
