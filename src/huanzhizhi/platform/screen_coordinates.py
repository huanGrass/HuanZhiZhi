"""Coordinate conversion shared by Win32-backed desktop tasks."""

from __future__ import annotations

from PySide6 import QtCore, QtGui


def physical_to_qt_global(x: int, y: int) -> QtCore.QPoint:
    """Convert a task's physical screen pixels to Qt global logical pixels."""
    screen = QtGui.QGuiApplication.primaryScreen()
    ratio = 1.0 if screen is None else max(1.0, float(screen.devicePixelRatio()))
    return QtCore.QPoint(round(x / ratio), round(y / ratio))


__all__ = ("physical_to_qt_global",)
