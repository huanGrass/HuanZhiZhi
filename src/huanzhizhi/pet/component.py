from __future__ import annotations

from collections.abc import Callable

from PySide6 import QtCore, QtGui, QtWidgets

from huanzhizhi.pet.constants import DEFAULT_DISPLAY_SCALE, MIN_DISPLAY_SCALE, INITIAL_POSITION_MARGIN, SPEECH_DURATION_MS, ULTRA_DISPLAY_MAX_SCALE
from huanzhizhi.pet.speech_bubble import SpeechBubbleWindow
from huanzhizhi.pet.point_overlay import PointOverlayWindow
from huanzhizhi.pet.window import PetWindow


ContextMenuFactory = Callable[[float, int, int, QtWidgets.QWidget | None], object]


class PetComponent(QtCore.QObject):
    visibility_changed = QtCore.Signal(bool)
    position_changed = QtCore.Signal(QtCore.QPoint)
    position_changing = QtCore.Signal(QtCore.QPoint)
    clicked = QtCore.Signal()
    fatal_error = QtCore.Signal(str)
    runtime_error = QtCore.Signal(str)
    scale_changed = QtCore.Signal(float, QtCore.QPoint)
    mouse_point_visible_changed = QtCore.Signal(bool)
    show_settings_requested = QtCore.Signal()
    quit_requested = QtCore.Signal()
    restart_requested = QtCore.Signal()
    context_menu_open_changed = QtCore.Signal(bool)

    def __init__(
        self, window: PetWindow, speech_bubble: SpeechBubbleWindow,
        point_overlay: PointOverlayWindow, context_menu_factory: ContextMenuFactory,
        *, display_scale: float = DEFAULT_DISPLAY_SCALE,
        initial_position: QtCore.QPoint | None = None, mouse_point_visible: bool = False,
    ) -> None:
        super().__init__()
        if initial_position is not None and not isinstance(initial_position, QtCore.QPoint):
            raise TypeError("initial_position 必须是 QPoint 或 None")
        if not isinstance(mouse_point_visible, bool):
            raise TypeError("mouse_point_visible 必须是 bool")
        if not callable(context_menu_factory):
            raise TypeError("context_menu_factory 必须可调用")
        for name, value, kind in (("window", window, PetWindow), ("speech_bubble", speech_bubble, SpeechBubbleWindow), ("point_overlay", point_overlay, PointOverlayWindow)):
            if not isinstance(value, kind):
                raise TypeError(f"{name} 必须是 {kind.__name__}")
        self._display_scale = float(display_scale)
        if not MIN_DISPLAY_SCALE <= self._display_scale <= ULTRA_DISPLAY_MAX_SCALE:
            raise ValueError("display_scale 必须在 0.19 到 1.25 之间")
        self._initial_position = QtCore.QPoint(initial_position) if initial_position is not None else None
        self._position_initialized = False
        self._mouse_point_visible = mouse_point_visible
        self._window, self._speech_bubble, self._point_overlay = window, speech_bubble, point_overlay
        self._context_menu_factory = context_menu_factory
        self._window.position_changing.connect(self._handle_window_position_changing)
        self._window.moved.connect(self._handle_window_moved)
        self._window.clicked.connect(self.clicked)
        self._window.context_menu_requested.connect(self._show_context_menu)


    @property
    def window(self) -> PetWindow:
        return self._window


    @property
    def speech_bubble(self) -> SpeechBubbleWindow:
        return self._speech_bubble


    @property
    def point_overlay(self) -> PointOverlayWindow:
        return self._point_overlay

    @property
    def display_scale(self) -> float:
        return self._display_scale

    @property
    def mouse_point_visible(self) -> bool:
        return self._mouse_point_visible

    def is_pet_hit_at(self, global_point: QtCore.QPoint) -> bool:
        if not isinstance(global_point, QtCore.QPoint):
            raise TypeError("桌宠命中位置必须是 QPoint")
        if not self._window.isVisible() or not self._window.geometry().contains(global_point):
            return False
        local_point = self._window.mapFromGlobal(global_point)
        return self._window.hit_test(local_point)

    def set_task_state(self, state: str) -> None:
        self._window.set_task_state(state)

    def show(self) -> None:
        if not self._position_initialized:
            self._move_to_initial_position()
            self._position_initialized = True
        self._window.show()
        self._window.raise_()
        self.visibility_changed.emit(True)


    def hide(self) -> None:
        self._speech_bubble.hide_bubble()
        self._point_overlay.hide_overlay()
        self._window.hide()
        self.visibility_changed.emit(False)


    def close(self) -> None:
        self._speech_bubble.close()
        self._point_overlay.close()
        self._window.close()


    def show_message(self, text: str, duration_ms: int | None = None, *, action: str = "auto") -> None:
        if not self._window.isVisible():
            raise RuntimeError("桌宠隐藏时不能显示对话")
        self.show_notification(text, duration_ms)


    def show_notification(self, text: str, duration_ms: int | None = None) -> None:
        text = str(text).strip()
        if text:
            duration = max(SPEECH_DURATION_MS, len(text) * 180) if duration_ms is None else int(duration_ms)
            self._speech_bubble.show_text(text, duration, self._window.visible_geometry())





    def set_display_scale(self, scale: float) -> None:
        if isinstance(scale, bool) or not isinstance(scale, (int, float)):
            raise TypeError("桌宠显示比例必须是数字")
        scale = float(scale)
        if not MIN_DISPLAY_SCALE <= scale <= ULTRA_DISPLAY_MAX_SCALE:
            raise ValueError("桌宠显示比例必须在 0.19 到 1.25 之间")
        if abs(scale - self._display_scale) < 0.001:
            return

        old_center = self._window.geometry().center()
        self._display_scale = scale
        self._speech_bubble.set_display_scale(scale)
        self._point_overlay.set_display_scale(scale)
        self._window.set_display_scale(scale)
        new_position = old_center - QtCore.QPoint(
            self._window.width() // 2,
            self._window.height() // 2,
        )
        new_position = self._window.clamp_position(new_position)
        self._window.move(new_position)
        self._reposition_speech_bubble()
        self.scale_changed.emit(scale, QtCore.QPoint(new_position))

    def set_mouse_point_visible(self, visible: bool) -> None:
        if not isinstance(visible, bool):
            raise TypeError("鼠标指向设置必须是 bool")
        if visible == self._mouse_point_visible:
            return
        self._mouse_point_visible = visible
        if not visible:
            self._point_overlay.hide_overlay()
        self.mouse_point_visible_changed.emit(visible)

    def show_point_at_click(self, x: int, y: int) -> None:
        self._point_overlay.show_at_click(x, y)

    def begin_manual_point_at_click(self, x: int, y: int) -> None:
        self._point_overlay.show_at_click(x, y, hold=True)

    def move_manual_point_at_click(self, x: int, y: int) -> None:
        if self._point_overlay.is_holding():
            self._point_overlay.move_to_click(x, y)

    def release_manual_point_at_click(self, x: int, y: int) -> None:
        if self._point_overlay.is_holding():
            self._point_overlay.release_hold(x, y)

    def _move_to_initial_position(self) -> None:
        if self._initial_position is not None:
            self._window.move(self._window.clamp_position(self._initial_position))
            return
        screen = QtGui.QGuiApplication.primaryScreen()
        if screen is None:
            raise RuntimeError("未检测到可用显示器")
        area = screen.availableGeometry()
        margin = INITIAL_POSITION_MARGIN
        self._window.move(
            area.right() - self._window.width() - margin + 1,
            area.bottom() - self._window.height() - margin + 1,
        )


    @QtCore.Slot(QtCore.QPoint)
    def _handle_window_moved(self, position: QtCore.QPoint) -> None:
        self._reposition_speech_bubble()
        self.position_changed.emit(QtCore.QPoint(position))

    @QtCore.Slot(QtCore.QPoint)
    def _handle_window_position_changing(self, position: QtCore.QPoint) -> None:
        self._reposition_speech_bubble()
        self.position_changing.emit(QtCore.QPoint(position))







    @QtCore.Slot()
    def _reposition_speech_bubble(self) -> None:
        if self._speech_bubble.isVisible():
            self._speech_bubble.position_around(self._window.visible_geometry())


    @QtCore.Slot(QtCore.QPoint)
    def _show_context_menu(self, global_position: QtCore.QPoint) -> None:
        screen = QtGui.QGuiApplication.screenAt(global_position)
        if screen is None:
            raise RuntimeError("无法确定桌宠菜单所在显示器")
        menu = self._context_menu_factory(
            self._display_scale,
            self._window.source_size.height(),
            screen.availableGeometry().height(),
            self._window,
        )
        required_members = (
            "show_settings_requested",
            "hide_requested",
            "quit_requested",
            "restart_requested",
            "scale_requested",
            "open_changed",
            "exec_at",
        )
        missing = tuple(name for name in required_members if not hasattr(menu, name))
        if missing:
            raise TypeError(f"桌宠菜单缺少公开成员：{', '.join(missing)}")
        menu.show_settings_requested.connect(self.show_settings_requested)
        menu.hide_requested.connect(self.hide)
        menu.quit_requested.connect(self.quit_requested)
        menu.restart_requested.connect(self.restart_requested)
        menu.scale_requested.connect(self.set_display_scale)
        menu.open_changed.connect(self.context_menu_open_changed)
        menu.open_changed.connect(self._window.set_motion_paused)
        menu.exec_at(global_position)


