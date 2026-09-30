from __future__ import annotations

from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets


def tray_menu_style() -> str:

    def px(value: int) -> int:
        return value

    return f"""
QMenu#trayContextMenu {{
    background: #ffffff;
    border: {px(1)}px solid #d0d7e2;
    border-radius: {px(8)}px;
    font-size: {px(14)}px;
    padding: {px(5)}px 0;
}}
QMenu#trayContextMenu::item {{
    color: #1f2937;
    min-width: {px(210)}px;
    min-height: {px(32)}px;
    padding: {px(6)}px {px(30)}px {px(6)}px {px(16)}px;
    border-radius: {px(6)}px;
}}
QMenu#trayContextMenu::item:selected {{
    background: #eef4ff;
    color: #111827;
}}
QMenu#trayContextMenu::separator {{
    height: {px(1)}px;
    background: #e5e7eb;
    margin: {px(5)}px 0;
}}
"""


class TrayComponent(QtCore.QObject):
    show_settings_requested = QtCore.Signal()
    show_pet_requested = QtCore.Signal()
    hide_pet_requested = QtCore.Signal()
    quit_requested = QtCore.Signal()

    def __init__(self, icon_path: Path) -> None:
        super().__init__()
        self._tray = QtWidgets.QSystemTrayIcon(QtGui.QIcon(str(icon_path)))
        self._tray.setToolTip("幻之之")

        self._menu = QtWidgets.QMenu()
        self._menu.setObjectName("trayContextMenu")
        self._apply_menu_style()

        self._settings_action = self._menu.addAction("设置")
        self._visibility_action = self._menu.addAction("收起之之")
        self._menu.addSeparator()
        self._quit_action = self._menu.addAction("退出")

        self._pet_visible = True
        self._settings_action.triggered.connect(self.show_settings_requested)
        self._visibility_action.triggered.connect(self._toggle_pet)
        self._quit_action.triggered.connect(self.quit_requested)
        self._tray.activated.connect(self._handle_activation)

    def start(self) -> None:
        if not QtWidgets.QSystemTrayIcon.isSystemTrayAvailable():
            raise RuntimeError("当前系统没有可用的系统托盘")
        self._tray.show()

    def set_pet_visible(self, visible: bool) -> None:
        self._pet_visible = visible
        self._visibility_action.setText("收起之之" if visible else "显示之之")

    def close(self) -> None:
        self._tray.hide()

    def _toggle_pet(self) -> None:
        if self._pet_visible:
            self.hide_pet_requested.emit()
        else:
            self.show_pet_requested.emit()

    def _handle_activation(self, reason: QtWidgets.QSystemTrayIcon.ActivationReason) -> None:
        if reason == QtWidgets.QSystemTrayIcon.ActivationReason.DoubleClick:
            self.show_pet_requested.emit()
        elif reason == QtWidgets.QSystemTrayIcon.ActivationReason.Context:
            cursor_pos = QtGui.QCursor.pos()
            self._apply_menu_style()
            self._menu.close()
            self._menu.exec(cursor_pos)

    def _apply_menu_style(self) -> None:
        menu_font = self._menu.font()
        menu_font.setPixelSize(14)
        self._menu.setFont(menu_font)
        self._menu.setStyleSheet(tray_menu_style())
