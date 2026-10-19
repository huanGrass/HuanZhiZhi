from __future__ import annotations

from collections.abc import Callable

from PySide6 import QtCore, QtGui, QtWidgets

from huanzhizhi.pet.constants import DEFAULT_DISPLAY_SCALE, MIN_DISPLAY_SCALE, INITIAL_POSITION_MARGIN, SPEECH_DURATION_MS, ULTRA_DISPLAY_MAX_SCALE
from huanzhizhi.pet.speech_bubble import SpeechBubbleWindow
from huanzhizhi.pet.point_overlay import PointOverlayWindow, _validate_click_coordinates
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
    theme_changed = QtCore.Signal(str)
    foreground_changed = QtCore.Signal(bool)
    control_changed = QtCore.Signal(bool)
    control_revoked = QtCore.Signal(str)

    def __init__(
        self, window: PetWindow, speech_bubble: SpeechBubbleWindow,
        point_overlay: PointOverlayWindow, context_menu_factory: ContextMenuFactory,
        *, display_scale: float = DEFAULT_DISPLAY_SCALE,
        initial_position: QtCore.QPoint | None = None, mouse_point_visible: bool = False,
        theme: str = 'ufo',
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
        self._theme = 'ufo'
        self._animation = None
        self._foreground_mouse = None
        self._foreground_request_id = None
        self._control_lease = None
        self._control_pending = None
        self._control_timer = QtCore.QTimer(self)
        self._control_timer.setTimerType(QtCore.Qt.TimerType.PreciseTimer)
        self._control_timer.setInterval(16)
        self._control_timer.timeout.connect(self._flush_control)
        self._context_menu_factory = context_menu_factory
        self._window.position_changing.connect(self._handle_window_position_changing)
        self._window.moved.connect(self._handle_window_moved)
        self._window.clicked.connect(self.clicked)
        self._window.context_menu_requested.connect(self._show_context_menu)
        self._window.interaction_started.connect(self._begin_pet_drag)
        self._window.interaction_finished.connect(self._finish_pet_drag)
        self._point_overlay.finished.connect(self._finish_hint_animation)
        self.set_theme(theme)
        self._ensure_animation()


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

    @property
    def theme(self) -> str:
        return self._theme

    @property
    def foreground_active(self) -> bool:
        return self._foreground_mouse is not None

    @property
    def control_active(self) -> bool:
        return self._control_lease is not None

    def acquire_control(self, lease: str) -> dict:
        if self.control_active or self.foreground_active:
            raise RuntimeError('桌宠正在执行其他动作')
        if not self._window.isVisible():
            raise RuntimeError('请先显示桌宠再开始互动')
        self._window.end_drag()
        self.hide_hint()
        self._hide_point_overlays()
        self._speech_bubble.hide_bubble()
        self._control_lease = lease
        try:
            geometry = self._ensure_animation().begin_control()
            self._set_animation_passthrough(True)
            self._control_timer.start()
            self.control_changed.emit(True)
            return geometry
        except Exception:
            self.release_control()
            raise

    def update_control(self, lease: str, x: float, y: float, facing: str, pose: str) -> None:
        from huanzhizhi.tasks.protocol import validate_pet_state
        validate_pet_state(x, y, facing, pose)
        if lease != self._control_lease:
            raise RuntimeError('桌宠接管已失效')
        if QtGui.QGuiApplication.screenAt(QtCore.QPoint(round(x), round(y))) is None:
            raise ValueError('桌宠目标位置不在可用屏幕内')
        # One pending state, not an animation queue. Old frames are discarded.
        self._control_pending = (x, y, facing, pose)

    def _flush_control(self) -> None:
        state, self._control_pending = self._control_pending, None
        if self.control_active and state is not None:
            try:
                self._animation.render_control(*state)
                self._window.raise_()
            except Exception as error:
                self._revoke_control(str(error))

    def release_control(self, lease: str | None = None) -> None:
        if not self.control_active or (lease is not None and lease != self._control_lease):
            return
        self._control_lease = None
        self._control_pending = None
        self._control_timer.stop()
        try:
            self._animation.end_control()
        finally:
            self._set_animation_passthrough(False)
            self.control_changed.emit(False)

    def _revoke_control(self, reason: str) -> None:
        if self.control_active:
            self.release_control()
            self.control_revoked.emit(reason)

    def bubble_geometry(self) -> QtCore.QRect:
        """Anchor attached UI to the pet's position, independent of its pose."""
        if self._animation is not None:
            return self._animation.anchored_geometry
        return self._window.visible_geometry()

    def task_menu_geometry(self) -> QtCore.QRect:
        if self._animation is not None:
            return self._animation.task_menu_geometry
        return self.bubble_geometry()

    def runtime_geometry(self) -> QtCore.QRect:
        if self._animation is not None:
            return self._animation.home_anchor_geometry
        return self._window.visible_geometry()

    def _ensure_animation(self):
        if self._animation is None:
            from huanzhizhi.pet.animation import PetAnimationController
            self._animation = PetAnimationController(
                self._window, theme=self._theme, display_scale=self._display_scale)
            self._animation.position_changed.connect(self._handle_animation_position)
            self._animation.motion_changed.connect(self._set_animation_passthrough)
            self._animation.set_home(self._window.pos())
            self._animation.set_task_state(self._window.task_state)
            self._animation.set_visible(self._window.isVisible())
        return self._animation

    def set_theme(self, theme: str) -> None:
        if self.control_active:
            raise RuntimeError('请先结束桌宠互动再切换主题')
        if theme not in ('ufo', 'broom'):
            raise ValueError('未知桌宠主题')
        if self.foreground_active:
            raise RuntimeError('前台操作正在进行，请先停止再切换主题')
        if theme == self._theme:
            return
        self._hide_point_overlays()
        animation = self._ensure_animation()
        animation.set_theme(theme)
        self._theme = theme
        self._reposition_speech_bubble()
        self.theme_changed.emit(theme)

    def start_foreground_input(self, request, on_done: Callable) -> None:
        """Execute one validated task request on the Qt animation timeline."""
        from huanzhizhi.platform.foreground_input import ForegroundMouse
        from huanzhizhi.platform.target_window import WindowInfo
        from huanzhizhi.platform.screen_coordinates import physical_to_qt_global
        if self.control_active:
            raise RuntimeError('桌宠互动期间不能执行前台输入')
        if self.foreground_active:
            raise RuntimeError('已有前台鼠标操作正在进行')
        if not self._window.isVisible():
            raise RuntimeError('桌宠已隐藏，不能开始前台动画操作')
        self.hide_hint()
        self._hide_point_overlays()
        animation = self._ensure_animation()
        info = WindowInfo(**request.window)
        mouse = ForegroundMouse(info, request.kind)
        mouse.begin()
        start = (info.left+request.x, info.top+request.y)
        end = None if request.kind != 'drag' else (info.left+request.end_x, info.top+request.end_y)

        def step(phase, point, progress):
            if self._foreground_mouse is not mouse:
                raise InterruptedError('前台操作已取消')
            # Press/release use the exact requested physical hotspot, avoiding
            # a Qt round-trip error on fractional DPI screens.
            if phase == 'press':
                position = start
            elif phase == 'release':
                position = end or start
            elif phase == 'drag':
                position = (round(start[0]+(end[0]-start[0])*progress),
                            round(start[1]+(end[1]-start[1])*progress))
            else:
                # Approach from the actual cursor, independent of pose offsets
                # and of where the pet retreated after the previous action.
                position = (round(origin[0]+(start[0]-origin[0])*progress),
                            round(origin[1]+(start[1]-origin[1])*progress))
            mouse.step(phase, position)

        def finished(error):
            if self._foreground_mouse is not mouse:
                return
            try:
                mouse.close()
            except Exception as release_error:
                error = release_error if error is None else RuntimeError(f'{error}；释放鼠标失败：{release_error}')
            self._foreground_mouse = None
            self._foreground_request_id = None
            self.foreground_changed.emit(False)
            try:
                self._set_animation_passthrough(animation.visual_active)
            except Exception as policy_error:
                if error is None:
                    error = policy_error
            on_done(error)

        try:
            self._foreground_mouse = mouse
            self._foreground_request_id = request.request_id
            self.foreground_changed.emit(True)
            self._set_animation_passthrough(True)
            origin = mouse.cursor_position()
            animation.start_action(request.kind, physical_to_qt_global(*start),
                                   None if end is None else physical_to_qt_global(*end),
                                   request.duration if end is not None else .5,
                                   on_step=step, on_done=finished)
        except Exception as error:
            finished(error)

    def cancel_foreground_input(self, request_id: str | None = None, reason: str = '前台操作已停止') -> None:
        if self.control_active:
            return
        if self.foreground_active and (request_id is None or request_id == self._foreground_request_id):
            self._animation.cancel(InterruptedError(reason))
        elif request_id is None and self._animation is not None and self._animation.visual_active:
            # A global stop also ends the nearby wait after release. Late
            # cancellations for a completed request must not interrupt new work.
            self._animation.cancel()

    @QtCore.Slot(bool)
    def _set_animation_passthrough(self, enabled: bool) -> None:
        self._window.set_input_passthrough(enabled or self.control_active)

    @QtCore.Slot(QtCore.QPoint)
    def _handle_animation_position(self, position: QtCore.QPoint) -> None:
        self._reposition_speech_bubble()
        self.position_changing.emit(position)

    def _begin_pet_drag(self) -> None:
        if self._animation is not None:
            self._animation.set_paused(True)

    def _finish_pet_drag(self) -> None:
        if self._animation is not None:
            self._animation.set_home(self._window.pos())
            self._animation.set_paused(False)

    def is_pet_hit_at(self, global_point: QtCore.QPoint) -> bool:
        if not isinstance(global_point, QtCore.QPoint):
            raise TypeError("桌宠命中位置必须是 QPoint")
        if not self._window.isVisible() or not self._window.geometry().contains(global_point):
            return False
        local_point = self._window.mapFromGlobal(global_point)
        return self._window.hit_test(local_point)

    def set_runtime_transparency(self, active: bool, transparent: bool) -> None:
        if active or self._animation is not None:
            self._ensure_animation().set_runtime_transparency(active, transparent)

    def set_task_state(self, state: str) -> None:
        self._window.set_task_state(state)
        if self._animation is not None:
            self._animation.set_task_state(state)

    def show(self) -> None:
        if self.control_active:
            self._window.show()
            return
        if not self._position_initialized:
            self._move_to_initial_position()
            self._position_initialized = True
        self._window.show()
        self._window.raise_()
        if self._animation is not None:
            self._animation.set_home(self._window.pos())
            self._animation.set_visible(True)
        self.visibility_changed.emit(True)


    def hide(self) -> None:
        self._revoke_control('桌宠已隐藏，互动已停止')
        self.cancel_foreground_input(reason='桌宠已隐藏，前台操作已取消')
        self._speech_bubble.hide_bubble()
        self._hide_point_overlays()
        if self._animation is not None:
            self._animation.set_visible(False)
        self._window.hide()
        self.visibility_changed.emit(False)


    def close(self) -> None:
        self._revoke_control('程序正在关闭')
        self.cancel_foreground_input(reason='程序正在关闭')
        if self._animation is not None:
            self._animation.close()
        self._speech_bubble.close()
        self._hide_point_overlays()
        self._point_overlay.close()
        self._window.close()


    def show_message(self, text: str, duration_ms: int | None = None, *, action: str = "auto") -> None:
        if not self._window.isVisible():
            raise RuntimeError("桌宠隐藏时不能显示对话")
        self.show_notification(text, duration_ms)


    def show_notification(self, text: str, duration_ms: int | None = None, *,
                          on_repair: Callable[[], None] | None = None,
                          action_text: str = '让 AI 检查并修复') -> None:
        text = str(text).strip()
        if text:
            duration = max(SPEECH_DURATION_MS, len(text) * 180) if duration_ms is None else int(duration_ms)
            self._speech_bubble.show_text(text, duration, self.bubble_geometry(),
                                          on_repair=on_repair, action_text=action_text)





    def set_display_scale(self, scale: float) -> None:
        if self.control_active:
            raise RuntimeError('请先结束桌宠互动再调整大小')
        if isinstance(scale, bool) or not isinstance(scale, (int, float)):
            raise TypeError("桌宠显示比例必须是数字")
        scale = float(scale)
        if not MIN_DISPLAY_SCALE <= scale <= ULTRA_DISPLAY_MAX_SCALE:
            raise ValueError("桌宠显示比例必须在 0.19 到 1.25 之间")
        if abs(scale - self._display_scale) < 0.001:
            return
        if self.foreground_active:
            raise RuntimeError('前台操作正在进行，请先停止再调整大小')

        old_center = (self._animation.home_geometry if self._animation is not None
                      else self._window.geometry()).center()
        self._display_scale = scale
        self._speech_bubble.set_display_scale(scale)
        self._point_overlay.set_display_scale(scale)
        self._ensure_animation().set_display_scale(scale)
        new_position = old_center - QtCore.QPoint(
            self._window.width() // 2,
            self._window.height() // 2,
        )
        new_position = self._window.clamp_position(new_position)
        self._window.move(new_position)
        if self._animation is not None:
            self._animation.set_home(new_position)
        self._reposition_speech_bubble()
        self.scale_changed.emit(scale, QtCore.QPoint(new_position))

    def restore_display(self) -> None:
        """Rebuild the pet at the restored DPI and update its attached windows."""
        self._revoke_control('显示器设置已变化，互动已停止')
        self.cancel_foreground_input(reason='显示器设置已变化，前台操作已取消')
        self._ensure_animation().restore_display()
        self._window.move(self._window.clamp_position(self._window.pos()))
        center = self._window.visible_geometry().center()
        if QtGui.QGuiApplication.screenAt(center) is None:
            # Dragging permits half the pet offscreen. Ensure the bubble anchor
            # is on a screen even at that boundary after display recovery.
            screen = self._window.screen()
            if screen is None:
                raise RuntimeError('无法确定桌宠所在显示器')
            area = screen.availableGeometry()
            anchor = QtCore.QPoint(
                min(max(center.x(), area.left()), area.right()),
                min(max(center.y(), area.top()), area.bottom()),
            )
            self._window.move(self._window.pos() + anchor - center)
        self._reposition_speech_bubble()
        if self._animation is not None:
            self._animation.set_home(self._window.pos())
        self.position_changed.emit(QtCore.QPoint(self._window.pos()))

    def set_mouse_point_visible(self, visible: bool) -> None:
        if not isinstance(visible, bool):
            raise TypeError("鼠标指向设置必须是 bool")
        if visible == self._mouse_point_visible:
            return
        self._mouse_point_visible = visible
        if not visible:
            self._hide_point_overlays()
        self.mouse_point_visible_changed.emit(visible)

    def show_point_at_click(self, x: int, y: int) -> None:
        if self.control_active:
            return
        _validate_click_coordinates(x, y)
        if not self._point_overlay.hint_points and not self.foreground_active and self._window.isVisible():
            self._ensure_animation().show_feedback(QtCore.QPoint(x, y))

    def begin_manual_point_at_click(self, x: int, y: int) -> None:
        if self.control_active:
            return
        _validate_click_coordinates(x, y)
        if not self._point_overlay.hint_points and not self.foreground_active:
            self._ensure_animation().show_feedback(QtCore.QPoint(x, y), hold=True)

    def move_manual_point_at_click(self, x: int, y: int) -> None:
        if self.control_active:
            return
        _validate_click_coordinates(x, y)
        if self._animation is not None and not self.foreground_active:
            self._animation.move_feedback(QtCore.QPoint(x, y))

    def release_manual_point_at_click(self, x: int, y: int) -> None:
        if self.control_active:
            return
        _validate_click_coordinates(x, y)
        if self._animation is not None and not self.foreground_active:
            self._animation.release_feedback(QtCore.QPoint(x, y))

    def _hide_point_overlays(self) -> None:
        self._point_overlay.hide_overlay()
        if self._animation is not None and self._animation.visual_active and not self.foreground_active:
            self._animation.cancel()

    def show_hint(self, points: tuple[QtCore.QPoint, ...], kind: str, label: str) -> None:
        if self.control_active:
            return
        self._point_overlay.show_hint(points, kind, label)
        if not self.foreground_active:
            if kind == 'line':
                self._finish_hint_animation()
            else:
                self._ensure_animation().show_hint(points, kind, elapsed_ms=self._point_overlay.hint_elapsed_ms)

    def hide_hint(self, *, return_home: bool = False) -> None:
        """Clear transient guidance; terminal task events also end nearby waiting."""
        self._point_overlay.hide_hint()
        if return_home and self._animation is not None and not self.foreground_active:
            if self._animation.visual_active:
                self._animation.cancel()
        else:
            self._finish_hint_animation()

    def _finish_hint_animation(self) -> None:
        if self._animation is not None and not self.foreground_active:
            self._animation.clear_hint()

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
        if self._animation is not None:
            self._animation.set_home(position)
        self._reposition_speech_bubble()
        self.position_changed.emit(QtCore.QPoint(position))

    @QtCore.Slot(QtCore.QPoint)
    def _handle_window_position_changing(self, position: QtCore.QPoint) -> None:
        self._reposition_speech_bubble()
        self.position_changing.emit(QtCore.QPoint(position))







    @QtCore.Slot()
    def _reposition_speech_bubble(self) -> None:
        if self._speech_bubble.isVisible():
            self._speech_bubble.position_around(self.bubble_geometry())


    @QtCore.Slot(QtCore.QPoint)
    def _show_context_menu(self, global_position: QtCore.QPoint) -> None:
        screen = QtGui.QGuiApplication.screenAt(global_position)
        if screen is None:
            raise RuntimeError("无法确定桌宠菜单所在显示器")
        menu = self._context_menu_factory(
            self._display_scale,
            1536,  # Reference height for the existing menu size percentages.
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
        menu.exec_at(global_position)


