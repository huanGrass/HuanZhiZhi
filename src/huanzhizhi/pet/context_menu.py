"""Independent context-menu view for the desktop pet."""

from __future__ import annotations

import math

from PySide6 import QtCore, QtGui, QtWidgets


DEFAULT_DISPLAY_SCALE = 0.32
MIN_DISPLAY_SCALE = 0.19
MAX_DISPLAY_SCALE = 0.56
ULTRA_MAX_DISPLAY_SCALE = 1.25
DISPLAY_SCALE_STEP = 0.04
ULTRA_HEIGHT_FRACTION = 2.0 / 3.0


def display_scale_options(
    source_image_height: int, screen_available_height: int
) -> tuple[float, ...]:
    """Return the original fixed size steps plus its screen-relative ultra size."""

    source_height = _positive_integer(source_image_height, "source_image_height")
    screen_height = _positive_integer(screen_available_height, "screen_available_height")
    count = round((MAX_DISPLAY_SCALE - 0.20) / DISPLAY_SCALE_STEP)
    options = [
        round(0.20 + index * DISPLAY_SCALE_STEP, 2)
        for index in range(count + 1)
    ]
    ultra_scale = round(
        _clamp_scale(
            screen_height * ULTRA_HEIGHT_FRACTION / float(source_height)
        ),
        2,
    )
    if ultra_scale > options[-1] + 0.01:
        options.append(ultra_scale)
    return tuple(dict.fromkeys(options))


def display_scale_label(display_scale: float) -> str:
    scale = _validated_scale(display_scale)
    percent = int((scale / DEFAULT_DISPLAY_SCALE) * 100 + 0.5)
    if scale > MAX_DISPLAY_SCALE + 0.001:
        return f"超大（约{percent}%）"
    if abs(scale - DEFAULT_DISPLAY_SCALE) < 0.001:
        return f"{percent}%（默认）"
    return f"{percent}%"


def pet_context_menu_style() -> str:
    """Return the logical-pixel menu style managed by Qt."""
    def px(value: int) -> int:
        return value

    return f"""
QMenu#desktopPetContextMenu {{
    background: #ffffff;
    border: {px(1)}px solid #d0d7e2;
    border-radius: {px(8)}px;
    font-size: {px(14)}px;
    padding: {px(5)}px 0;
}}
QMenu#desktopPetContextMenu::item {{
    color: #1f2937;
    min-width: {px(168)}px;
    min-height: {px(30)}px;
    padding: {px(6)}px {px(30)}px {px(6)}px {px(14)}px;
    border-radius: {px(6)}px;
}}
QMenu#desktopPetContextMenu::item:selected {{
    background: #eef4ff;
    color: #111827;
}}
QMenu#desktopPetContextMenu::indicator {{
    width: {px(16)}px;
    height: {px(16)}px;
    padding-left: {px(2)}px;
}}
QMenu#desktopPetContextMenu::right-arrow {{
    width: {px(10)}px;
    height: {px(10)}px;
    padding-right: {px(10)}px;
}}
QMenu#desktopPetContextMenu::separator {{
    height: {px(1)}px;
    background: #e5e7eb;
    margin: {px(5)}px 0;
}}
"""


class PetContextMenu(QtWidgets.QMenu):
    """Desktop-pet menu that reports intent without operating application state."""

    show_settings_requested = QtCore.Signal()
    hide_requested = QtCore.Signal()
    quit_requested = QtCore.Signal()
    restart_requested = QtCore.Signal()
    scale_requested = QtCore.Signal(float)
    open_changed = QtCore.Signal(bool)

    def __init__(
        self,
        current_scale: float,
        source_image_height: int,
        screen_available_height: int,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        current_scale = _validated_scale(current_scale)
        self._scale_options = display_scale_options(
            source_image_height, screen_available_height
        )
        if current_scale not in self._scale_options:
            self._scale_options = tuple(sorted((*self._scale_options, current_scale)))
        self._open = False
        self.setObjectName("desktopPetContextMenu")
        self._apply_menu_style(self)

        self.settings_action = self.addAction("设置")
        self.hide_action = self.addAction("收起之之")
        self.addSeparator()
        self.size_menu = self.addMenu("大小")
        self.size_menu.setObjectName("desktopPetContextMenu")
        self._apply_menu_style(self.size_menu)

        self.scale_actions: dict[QtGui.QAction, float] = {}
        self._scale_action_group = QtGui.QActionGroup(self)
        self._scale_action_group.setExclusive(True)
        nearest_scale = min(
            self._scale_options, key=lambda value: abs(value - current_scale)
        )
        for scale in self._scale_options:
            action = self.size_menu.addAction(display_scale_label(scale))
            action.setCheckable(True)
            action.setChecked(scale == nearest_scale)
            self._scale_action_group.addAction(action)
            action.triggered.connect(
                lambda _checked=False, value=scale: self.scale_requested.emit(value)
            )
            self.scale_actions[action] = scale

        self.addSeparator()
        self.restart_action = self.addAction("重启")
        self.quit_action = self.addAction("退出")

        self.settings_action.triggered.connect(
            lambda _checked=False: self.show_settings_requested.emit()
        )
        self.hide_action.triggered.connect(
            lambda _checked=False: self.hide_requested.emit()
        )
        self.restart_action.triggered.connect(
            lambda _checked=False: self.restart_requested.emit()
        )
        self.quit_action.triggered.connect(
            lambda _checked=False: self.quit_requested.emit()
        )
        self.aboutToShow.connect(lambda: self._set_open(True))
        self.aboutToHide.connect(lambda: self._set_open(False))

    @property
    def scale_options(self) -> tuple[float, ...]:
        return self._scale_options

    def exec_at(self, global_position: QtCore.QPoint) -> QtGui.QAction | None:
        if not isinstance(global_position, QtCore.QPoint):
            raise TypeError("global_position must be a QPoint")
        screen = QtGui.QGuiApplication.screenAt(global_position)
        if screen is None:
            raise RuntimeError("cannot determine the context-menu screen")
        self._apply_menu_style(self)
        self._apply_menu_style(self.size_menu)
        try:
            return self.exec(global_position)
        finally:
            self._set_open(False)

    def close_view(self) -> None:
        self.close()
        self._set_open(False)

    @staticmethod
    def _apply_menu_style(menu: QtWidgets.QMenu) -> None:
        font = menu.font()
        font.setPixelSize(14)
        menu.setFont(font)
        menu.setStyleSheet(pet_context_menu_style())

    def _set_open(self, open_: bool) -> None:
        open_ = bool(open_)
        if self._open == open_:
            return
        self._open = open_
        self.open_changed.emit(open_)

    @staticmethod
    def _primary_screen() -> QtGui.QScreen:
        screen = QtGui.QGuiApplication.primaryScreen()
        if screen is None:
            raise RuntimeError("no screen is available for the context menu")
        return screen


def _validated_scale(value: float) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
    ):
        raise TypeError("display scale must be a finite number")
    scale = float(value)
    if not MIN_DISPLAY_SCALE <= scale <= ULTRA_MAX_DISPLAY_SCALE:
        raise ValueError(
            f"display scale must be between {MIN_DISPLAY_SCALE:.2f} "
            f"and {ULTRA_MAX_DISPLAY_SCALE:.2f}"
        )
    return scale


def _clamp_scale(value: float) -> float:
    return max(MIN_DISPLAY_SCALE, min(ULTRA_MAX_DISPLAY_SCALE, value))


def _positive_integer(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


__all__ = [
    "PetContextMenu",
    "display_scale_label",
    "display_scale_options",
    "pet_context_menu_style",
]
