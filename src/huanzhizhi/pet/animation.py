"""One-window stop-motion poses and contact-timed desktop actions.

This module never sends native input. The owner performs input in ``on_step``
after the corresponding pose has been rendered, and releases held input when
``on_done`` receives an error.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
import json
import math
from pathlib import Path
import time

from PySide6 import QtCore, QtGui

from huanzhizhi.pet.constants import DEFAULT_DISPLAY_SCALE, MIN_DISPLAY_SCALE, ULTRA_DISPLAY_MAX_SCALE
from huanzhizhi.pet.window import PetWindow


class AnimationCancelled(RuntimeError):
    """The visual/input request was cancelled before its final release."""


@dataclass
class _Segment:
    duration: float
    frame: int
    start: QtCore.QPointF
    end: QtCore.QPointF
    mirrored: bool = False
    phase: str = ""
    contact: QtCore.QPointF | None = None
    final: bool = False
    straight: bool = False
    progress_range: tuple[float, float] | None = None


class PetAnimationController(QtCore.QObject):
    """Animate the supplied pet only; all coordinates are Qt global pixels."""

    motion_changed = QtCore.Signal(bool)
    position_changed = QtCore.Signal(QtCore.QPoint)

    def __init__(
        self, window: PetWindow, *, theme: str = "ufo",
        display_scale: float = DEFAULT_DISPLAY_SCALE, assets_root: Path | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__(window)
        self.window = window
        self._clock = clock
        self._assets = Path(assets_root) if assets_root is not None else Path(__file__).parents[1] / "assets" / "themes"
        self._metadata = json.loads((self._assets / "metadata.json").read_text(encoding="utf-8"))["themes"]
        self._sheets: dict[str, QtGui.QPixmap] = {}
        self._frames: dict[tuple, tuple[QtGui.QPixmap, QtGui.QRegion, QtCore.QPointF, QtCore.QPointF]] = {}
        self._scale = self._validated_scale(display_scale)
        self._theme = "ufo"
        self._frame = 0
        self._mirrored = False
        self._home_mirrored = False
        self._pivot = QtCore.QPointF()
        self._home = QtCore.QPoint(window.pos())
        self._segments: list[_Segment] = []
        self._index = 0
        self._entered = False
        self._started = self._clock()
        self._generation = 0
        self._on_step: Callable[[str, QtCore.QPointF, float], None] | None = None
        self._on_done: Callable[[Exception | None], None] | None = None
        self._state = "idle"
        self._state_started = self._started
        self._held_feedback = False
        self._hint_points: tuple[QtCore.QPointF, ...] = ()
        self._hint_kind = "click"
        self._hint_index = 0
        self._hint_duration = .5
        self._closed = False
        self._paused = False
        self._controlled = False
        self._visual_active = False
        self._runtime_opacity: float | None = None
        self._last_render: tuple | None = None
        self._timer = QtCore.QTimer(self)
        self._timer.setTimerType(QtCore.Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self.advance)
        self.set_theme(theme)
        self.window.installEventFilter(self)
        if self.window.isVisible():
            self._timer.start(80)

    @property
    def busy(self) -> bool:
        """Only pending input is busy; returning home and idle poses are not."""
        return self._on_done is not None

    @property
    def visual_active(self) -> bool:
        return self._visual_active

    @property
    def theme(self) -> str:
        return self._theme

    @property
    def current_frame(self) -> int:
        return self._frame

    @property
    def fingertip(self) -> QtCore.QPointF:
        return QtCore.QPointF(self.window.pos()) + self._prepared(self._frame, self._mirrored)[3]

    @property
    def mirrored(self) -> bool:
        return self._mirrored

    @property
    def home_geometry(self) -> QtCore.QRect:
        size = self._prepared(0, self._home_mirrored)[0].deviceIndependentSize().toSize()
        return QtCore.QRect(self._home, size)

    @property
    def home_anchor_geometry(self) -> QtCore.QRect:
        """Keep runtime controls centered on the resting body during travel."""
        _, mask, anchor, _ = self._prepared(0, self._home_mirrored)
        rect = mask.boundingRect().translated(self._home)
        rect.moveCenter(QtCore.QPoint(round(self._home.x()+anchor.x()), rect.center().y()))
        return rect

    @property
    def anchored_geometry(self) -> QtCore.QRect:
        """Center attached UI on the body anchor, with stable resting bounds."""
        _, mask, anchor, _ = self._prepared(0, False)
        pivot = (QtCore.QPointF(self.window.pos()) + self._prepared(self._frame, self._mirrored)[2]
                 if self._paused else self._pivot)
        rect = mask.boundingRect().translated((pivot-anchor).toPoint())
        rect.moveCenter(QtCore.QPoint(round(pivot.x()), rect.center().y()))
        return rect

    @property
    def task_menu_geometry(self) -> QtCore.QRect:
        pivot = (QtCore.QPointF(self.window.pos()) + self._prepared(self._frame, self._mirrored)[2]
                 if self._paused else self._pivot)
        if self._theme != "broom":
            # Keep both columns outside the resting silhouette for this facing.
            _, mask, anchor, _ = self._prepared(0, self._mirrored)
            return mask.boundingRect().translated((pivot-anchor).toPoint())
        rect = self.anchored_geometry
        # Narrow the horizontal anchor to the character, keeping the full
        # pose's vertical center. Task bubbles may cover part of the broom.
        scale = self._scale / .32
        return QtCore.QRect(round(pivot.x()-55*scale), rect.top(),
                            round(110*scale), rect.height())

    def set_home(self, position: QtCore.QPoint) -> None:
        if not isinstance(position, QtCore.QPoint):
            raise TypeError("桌宠待机位置必须是 QPoint")
        self._home = QtCore.QPoint(position)
        previous = self._home_mirrored
        if not self._paused:
            self._update_home_mirror()
        if not self._segments and not self._held_feedback:
            if not self._paused and previous != self._home_mirrored:
                self._render(0, self._home_pivot(), self._home_mirrored)
            else:
                self._sync_pivot()

    def set_theme(self, theme: str) -> None:
        if theme not in self._metadata:
            raise ValueError(f"未知桌宠主题：{theme}")
        self.cancel(return_home=False)
        self._theme = theme
        self._state_started = self._clock()
        self._update_home_mirror()
        self._render(0, self._home_pivot(), self._home_mirrored)

    def set_display_scale(self, scale: float) -> None:
        scale = self._validated_scale(scale)
        if scale == self._scale:
            return
        self._scale = scale
        self.restore_display()

    def restore_display(self) -> None:
        """Discard rendered frames after a display/DPI change, even at the same scale."""
        self.cancel(return_home=False)
        self._frames.clear()
        self._last_render = None
        self._update_home_mirror()
        self._render(0, self._home_pivot(), self._home_mirrored)

    def set_runtime_transparency(self, active: bool, transparency: int) -> None:
        if type(transparency) is not int or not 0 <= transparency <= 100:
            raise ValueError("桌宠透明度必须是 0 到 100 的整数")
        self._runtime_opacity = 1. - transparency / 100 if active else None
        self._set_opacity(1.)

    def _set_opacity(self, opacity: float) -> None:
        self.window.setWindowOpacity(self._runtime_opacity if self._runtime_opacity is not None else opacity)

    def set_task_state(self, state: str) -> None:
        if state not in {"idle", "thinking", "working", "complete"}:
            raise ValueError(f"不支持的桌宠任务状态：{state}")
        if self._state != state:
            self._state = state
            self._state_started = self._clock()
        if not self._segments and not self._held_feedback:
            self.advance()

    def begin_control(self) -> dict:
        """Suspend autonomous animation and describe existing poses at this DPI."""
        self.cancel(return_home=False)
        self._controlled = True
        self._timer.stop()
        poses = {}
        for facing in ('left', 'right'):
            poses[facing] = {}
            for pose, frame in (('idle', 0), ('move', 7), ('reach', 12)):
                _, mask, anchor, contact = self._prepared(frame, facing == 'left')
                rect = QtCore.QRectF(mask.boundingRect()).translated(-anchor)
                point = contact-anchor
                poses[facing][pose] = dict(bounds=[rect.x(), rect.y(), rect.width(), rect.height()],
                                          contact=[point.x(), point.y()])
                metadata = self._metadata[self._theme]['frames'][str(frame)]
                if 'head' in metadata:
                    hx, hy, radius = metadata['head']
                    ax, ay = metadata['anchor']
                    unit = (768*self._scale/metadata['rect'][3])*metadata.get('scale', 1.)*self._metadata[self._theme]['size_factor']
                    poses[facing][pose]['head'] = [(hx-ax)*unit*(-1 if facing == 'left' else 1), (hy-ay)*unit]
                    poses[facing][pose]['head_radius'] = radius*unit
                    poses[facing][pose]['head_radius_x'] = metadata.get('head_radius_x', radius)*unit
        return dict(coordinate_space='qt_global_logical', theme=self._theme,
                    pivot=[self._pivot.x(), self._pivot.y()], poses=poses)

    def render_control(self, x: float, y: float, facing: str, pose: str) -> None:
        if not self._controlled:
            raise RuntimeError('桌宠未被接管')
        self._render({'idle': 0, 'move': 7, 'reach': 12}[pose],
                     QtCore.QPointF(x, y), facing == 'left')

    def end_control(self) -> None:
        if not self._controlled:
            return
        self._controlled = False
        self.cancel(return_home=False)
        self._render(0, self._home_pivot(), self._home_mirrored)
        self._state_started = self._clock()
        if self.window.isVisible() and not self._closed:
            self._timer.start(80)

    def start_action(
        self, kind: str, target: QtCore.QPoint | QtCore.QPointF,
        end: QtCore.QPoint | QtCore.QPointF | None = None, duration: float = .5, *,
        on_step: Callable[[str, QtCore.QPointF, float], None],
        on_done: Callable[[Exception | None], None],
    ) -> None:
        """Move, contact and release; ``duration`` also controls a drag's travel.

        ``press``/``release`` are emitted once per click, after repaint. ``move``
        and ``drag`` are emitted on animation ticks with continuous eased travel
        progress (across all approach poses for ``move``). Final release invokes done
        immediately; the owner may start another action while return continues.
        """
        if kind not in {"click", "right_click", "double_click", "drag", "move"}:
            raise ValueError(f"不支持的桌宠动作：{kind}")
        target = self._point(target)
        destination = self._point(end) if end is not None else None
        if kind == "drag" and destination is None:
            raise ValueError("拖拽必须提供终点")
        if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not math.isfinite(duration) or not .01 <= duration <= 60:
            raise ValueError("动作时长必须在 0.01 到 60 秒之间")
        if not callable(on_step) or not callable(on_done):
            raise TypeError("动作回调必须可调用")
        if self._closed:
            raise RuntimeError("桌宠动画控制器已关闭")
        self.cancel(return_home=False)
        self._sync_pivot()
        self._on_step, self._on_done = on_step, on_done
        self._set_opacity(.6)
        segments = self._action_segments(kind, target, destination, duration)
        last = segments[-1]
        segments.extend(self._return_segments(last.end, last.mirrored, wait=8.))
        self._begin(segments)

    def _action_segments(self, kind, target, destination, duration, *, approach=True) -> list[_Segment]:
        """The same movement and contact poses for input and visual-only actions."""
        mirror = self._choose_mirror((target,) if destination is None else (target, destination), 12)
        segments = self._approach(target, mirror, max(.12, duration)) if approach else []
        contact_pivot = self._contact_pivot(12, target, mirror)
        if kind == "move":
            segments.append(_Segment(.001, 11, contact_pivot, contact_pivot, mirror, final=True))
        elif kind == "drag":
            end_pivot = self._contact_pivot(12, destination, mirror)
            segments.extend([
                _Segment(.08, 12, contact_pivot, contact_pivot, mirror, "press", target),
                _Segment(duration, 12, contact_pivot, end_pivot, mirror, "drag", straight=True),
                _Segment(.12, 11, end_pivot, end_pivot, mirror, "release", destination, True),
            ])
        else:
            for index in range(2 if kind == "double_click" else 1):
                segments.extend([
                    _Segment(.15, 12, contact_pivot, contact_pivot, mirror, "press", target),
                    _Segment(.08, 11, contact_pivot, contact_pivot, mirror, "release", target,
                             final=kind != "double_click" or index == 1),
                ])
        return segments

    def show_feedback(self, point: QtCore.QPoint | QtCore.QPointF, hold: bool = False, *, smooth: bool = False) -> None:
        """Background/manual feedback; never emits input callbacks."""
        target = self._point(point)
        self.cancel(return_home=False)
        self._sync_pivot()
        self._held_feedback = bool(hold)
        self._set_opacity(.6)
        self._set_visual_active(True)
        if not hold:
            # Immediate background feedback skips travel, but shares the same
            # contact/release poses; smooth feedback also shares the approach.
            segments = self._action_segments('click', target, None, .5, approach=smooth)
            last = segments[-1]
            self._begin([*segments, *self._return_segments(last.end, last.mirrored, wait=8.)])
            return
        mirror = self._choose_mirror((target,), 12)
        pivot = self._contact_pivot(12, target, mirror)
        self._render(12, pivot, mirror)

    def move_feedback(self, point: QtCore.QPoint | QtCore.QPointF) -> None:
        target = self._point(point)
        if self._held_feedback:
            self._render(12, self._contact_pivot(12, target, self._mirrored), self._mirrored)

    def release_feedback(self, point: QtCore.QPoint | QtCore.QPointF) -> None:
        target = self._point(point)
        if not self._held_feedback:
            return
        self.move_feedback(target)
        self._held_feedback = False
        self._begin(self._return_segments(self._pivot, self._mirrored, wait=.6))

    def show_hint(
        self, points: Sequence[QtCore.QPoint | QtCore.QPointF], kind: str = "click", *,
        duration: float = .5,
    ) -> None:
        if kind not in {"click", "right_click", "double_click", "drag", "line"} or not 1 <= len(points) <= 4:
            raise ValueError("提示必须包含 1 到 4 个点，类型为点击、拖动或辅助线")
        if (kind == "drag" and len(points) < 2) or (kind == "line" and len(points) != 2):
            raise ValueError("拖动提示至少需要起终点，直线提示必须包含两个点")
        targets = tuple(self._point(point) for point in points)
        if type(duration) not in (int, float) or not math.isfinite(duration) or not .01 <= duration <= 60:
            raise ValueError("动作时长必须在 0.01 到 60 秒之间")
        if kind == "line":
            self.clear_hint()
            return
        # Tasks refresh an unchanged hint while verifying the board. Keep
        # the current approach/pointing sequence instead of restarting it.
        if targets == self._hint_points and kind == self._hint_kind and duration == self._hint_duration:
            return
        self.cancel(return_home=False)
        self._sync_pivot()
        self._hint_points, self._hint_kind = targets, kind
        self._hint_index, self._hint_duration = 0, duration
        self._set_opacity(.6)
        self._advance_hint()

    def clear_hint(self) -> None:
        if self._hint_points:
            self.cancel(return_home=False)
            self._sync_pivot()
            self._begin(self._return_segments(self._pivot, self._mirrored, wait=8.))

    def cancel(self, error: Exception | None = None, *, return_home: bool = True) -> None:
        """Invalidate every old tick, then notify a pending input request once."""
        self._generation += 1
        self._segments = []
        self._hint_points = ()
        self._held_feedback = False
        self._set_opacity(1.)
        callback, self._on_done = self._on_done, None
        self._on_step = None
        generation = self._generation
        if callback is not None:
            callback(error if error is not None else AnimationCancelled("桌宠操作已取消"))
        if return_home and not self._closed and generation == self._generation and self._frames:
            self._sync_pivot()
            # Resting and returning poses have already retracted. Repeated
            # stop/cleanup calls must not restart the contact recovery poses.
            self._begin(self._return_segments(self._pivot, self._mirrored,
                                              retract=self._frame not in (0, 1, 22)))
        elif generation == self._generation:
            self._set_visual_active(False)

    def set_visible(self, visible: bool) -> None:
        if visible:
            self.window.show()
            if not self._paused and not self._closed:
                self._timer.start(80)
            self._state_started = self._clock()
            self.advance()
        else:
            self.cancel(return_home=False)
            self._timer.stop()
            self.window.hide()
            self._render(0, self._home_pivot(), self._home_mirrored)

    def set_paused(self, paused: bool) -> None:
        """Let a manual drag own the visible window until its new home is saved."""
        self._paused = bool(paused)
        if paused:
            self.cancel(return_home=False)
            self._timer.stop()
        else:
            self._state_started = self._clock()
            self._update_home_mirror()
            if (self._mirrored != self._home_mirrored
                    and self.window.isVisible() and not self._closed):
                self._begin(self._home_turn_segments(self._mirrored))
                return
            self._render(0, self._home_pivot(), self._home_mirrored)
            if self.window.isVisible() and not self._closed:
                self._timer.start(80)
                self.advance()

    def close(self) -> None:
        self._closed = True
        self.cancel(return_home=False)
        self._timer.stop()

    def advance(self, now: float | None = None) -> None:
        """Advance against the injected monotonic clock (seconds)."""
        if self._closed or self._paused or self._controlled:
            return
        now = self._clock() if now is None else now
        generation = self._generation
        if not self._segments:
            if self._held_feedback:
                return
            if self._hint_points:
                self._advance_hint()
                return
            self._advance_idle(now)
            return
        while self._index < len(self._segments):
            segment = self._segments[self._index]
            if not self._entered:
                self._entered = True
                if segment.phase in {"press", "release"}:
                    # A delayed GUI tick may catch up travel, but must not
                    # squeeze several physical input edges into one paint.
                    self._started = now
                self._render(segment.frame, self._path(segment, 0.), segment.mirrored)
                if segment.phase in {"press", "release"}:
                    self.window.repaint()
                    if not self._step(segment.phase, segment.contact, 1., generation):
                        return
                if segment.final:
                    callback, self._on_done = self._on_done, None
                    self._on_step = None
                    if callback is not None:
                        callback(None)
                    if generation != self._generation:
                        return
            elapsed = max(0., now - self._started)
            progress = min(1., elapsed / segment.duration)
            pivot = self._path(segment, progress)
            self._render(segment.frame, pivot, segment.mirrored)
            if segment.phase in {"move", "drag"}:
                # Physical input follows the entire travel, not a pose's local
                # progress or changing fingertip offset.
                spatial_progress = progress
                if segment.progress_range is not None:
                    a, b = segment.progress_range
                    spatial_progress = a + (b-a)*progress
                if segment.straight or segment.progress_range is not None:
                    spatial_progress = spatial_progress**2*(3-2*spatial_progress)
                if not self._step(segment.phase, self.fingertip, spatial_progress, generation):
                    return
            if progress < 1.:
                return
            self._started += segment.duration
            self._index += 1
            self._entered = False
        self._segments = []
        self._state_started = now
        if self._hint_points:
            self._advance_hint()
        else:
            self._timer.setInterval(80)
            self._set_visual_active(False)

    def _step(self, phase: str, point: QtCore.QPointF | None, progress: float, generation: int) -> bool:
        if self._on_step is not None:
            try:
                if phase == "move":
                    # Near an edge, a raised fingertip may extend a few pixels
                    # past the desktop. Keep the visible cursor at that edge;
                    # do not alter the exact press/drag/release coordinates.
                    areas = [screen.geometry() for screen in QtGui.QGuiApplication.screens()]
                    if areas and not any(area.contains(point.toPoint()) for area in areas):
                        candidates = [QtCore.QPointF(min(max(point.x(), a.left()), a.right()),
                                                    min(max(point.y(), a.top()), a.bottom())) for a in areas]
                        point = min(candidates, key=lambda p: math.hypot(p.x()-point.x(), p.y()-point.y()))
                self._on_step(phase, QtCore.QPointF(point), progress)
            except Exception as error:
                self.cancel(error)
                return False
        return generation == self._generation

    def _begin(self, segments: list[_Segment]) -> None:
        self._segments, self._index, self._entered = segments, 0, False
        self._started = self._clock()
        self._set_visual_active(True)
        if self.window.isVisible() and not self._paused:
            self._timer.start(16)
        self.advance(self._started)

    def _set_visual_active(self, active: bool) -> None:
        if not active:
            self._set_opacity(1.)
        if self._visual_active != active:
            self._visual_active = active
            self.motion_changed.emit(active)

    def eventFilter(self, watched: QtCore.QObject, event: QtCore.QEvent) -> bool:
        if watched is self.window:
            if event.type() == QtCore.QEvent.Type.Hide:
                self._timer.stop()
            elif event.type() == QtCore.QEvent.Type.Show and not self._closed and not self._paused:
                self._timer.start(16 if self._visual_active else 80)
        return super().eventFilter(watched, event)

    def _approach(self, target: QtCore.QPointF, mirrored: bool, duration: float) -> list[_Segment]:
        end = self._contact_pivot(12, target, mirrored)
        start = QtCore.QPointF(self._pivot)
        travel_mirror = mirrored
        if abs(end.x()-start.x()) > 1:
            travel_mirror = end.x() < start.x()
        segments = []
        # Poses keep their original timing, but share one continuous flight.
        for a, b, frame in ((0., .55, 7), (.55, .76, 9), (.76, .9, 10), (.9, 1., 11)):
            facing = travel_mirror if frame in (7, 9) else mirrored
            segments.append(_Segment(duration*(b-a), frame, start, end, facing,
                                     "move", progress_range=(a, b)))
        return segments

    def _return_segments(self, position: QtCore.QPointF, mirrored: bool, *, wait: float = 0.,
                         retract: bool = True) -> list[_Segment]:
        position = QtCore.QPointF(position)
        segments = [
            _Segment(.06, 13, position, position, mirrored),
            _Segment(.06, 10, position, position, mirrored),
            _Segment(.1, 9, position, position, mirrored),
        ] if retract else []
        if wait:
            nearby = position + QtCore.QPointF(20 if mirrored else -20, -5)
            segments.extend([_Segment(.18, 0, position, nearby, mirrored),
                             _Segment(wait, 0, nearby, nearby, mirrored)])
            position = nearby
        home = self._home_pivot()
        duration = min(.7, max(.2, math.hypot(home.x()-position.x(), home.y()-position.y()) / 900))
        travel_mirror = mirrored
        if abs(home.x()-position.x()) > 1:
            travel_mirror = home.x() < position.x()
        return [*segments,
            _Segment(duration, 22, position, home, travel_mirror),
            *self._home_turn_segments(travel_mirror),
        ]

    def _home_turn_segments(self, previous_mirror: bool) -> list[_Segment]:
        home = self._home_pivot()
        segments = []
        if previous_mirror != self._home_mirrored:
            # Flip while the eyes are closed, with only a 120 ms total pause.
            segments.extend([_Segment(.06, 1, home, home, previous_mirror),
                             _Segment(.06, 1, home, home, self._home_mirrored)])
        segments.append(_Segment(.001, 0, home, home, self._home_mirrored))
        return segments

    def _advance_hint(self) -> None:
        target = self._hint_points[self._hint_index]
        destination = self._hint_points[1] if self._hint_kind == 'drag' else None
        segments = self._action_segments(self._hint_kind, target, destination, self._hint_duration)
        last = segments[-1]
        # No input callbacks are installed. Hold briefly, then demonstrate the
        # next point while fresh observations keep the existing hint alive.
        segments.append(_Segment(.6, last.frame, last.end, last.end, last.mirrored))
        self._hint_index = 0 if destination is not None else (self._hint_index+1) % len(self._hint_points)
        self._begin(segments)

    def _advance_idle(self, now: float) -> None:
        interval = 80
        elapsed = max(0., now-self._state_started)
        if self._state == "complete":
            frame = 18 if elapsed < .65 else 19 if elapsed < 1.1 else 0
            if elapsed >= 1.1:
                self._state, self._state_started = "idle", now
        else:
            # Startup and work status alone do not describe an action.
            # Stay at rest; input and hint requests drive actual motion.
            phase = elapsed % 5.4
            # Frame 2 lowers the whole head; replaying it on either side
            # of closed eyes looks like two nods instead of one blink.
            frame = 1 if 3.7 <= phase < 3.82 else 0
            interval = 16 if 3.6 <= phase < 3.84 else 80
        if self._timer.interval() != interval:
            self._timer.setInterval(interval)
        self._render(frame, self._home_pivot(), self._home_mirrored)

    def _sync_pivot(self) -> None:
        self._pivot = QtCore.QPointF(self.window.pos()) + self._prepared(self._frame, self._mirrored)[2]

    def _home_pivot(self) -> QtCore.QPointF:
        return QtCore.QPointF(self._home) + self._prepared(0, self._home_mirrored)[2]

    def _update_home_mirror(self) -> None:
        center = self.home_geometry.center()
        screen = QtGui.QGuiApplication.screenAt(center) or self.window.screen()
        if screen is not None:
            area = screen.availableGeometry()
            offset = center.x()-area.center().x()
            # A central 10% dead zone retains the last resting direction.
            if abs(offset) > area.width()*.05:
                self._home_mirrored = offset > 0

    def _contact_pivot(self, frame: int, target: QtCore.QPointF, mirrored: bool) -> QtCore.QPointF:
        _, _, anchor, point = self._prepared(frame, mirrored)
        return target - point + anchor

    def _choose_mirror(self, targets: tuple[QtCore.QPointF, ...], frame: int) -> bool:
        def overflow(mirrored: bool) -> float:
            pixmap, _, _, point = self._prepared(frame, mirrored)
            size = pixmap.deviceIndependentSize()
            score = 0.
            for target in targets:
                screen = QtGui.QGuiApplication.screenAt(target.toPoint()) or self.window.screen()
                if screen is None:
                    continue
                area = QtCore.QRectF(screen.availableGeometry())
                left, top = target.x()-point.x(), target.y()-point.y()
                score += max(0., area.left()-left) + max(0., left+size.width()-area.right())
                score += max(0., area.top()-top) + max(0., top+size.height()-area.bottom())
            return score
        flipped, normal = overflow(True), overflow(False)
        if not math.isclose(flipped, normal, abs_tol=1e-6):
            return flipped < normal
        origin = targets[0] if len(targets) > 1 else self._pivot
        delta_x = targets[-1].x()-origin.x()
        return self._mirrored if abs(delta_x) <= 1 else delta_x < 0

    def _render(self, frame: int, pivot: QtCore.QPointF, mirrored: bool) -> None:
        pixmap, mask, anchor, _ = self._prepared(frame, mirrored)
        position = (pivot-anchor).toPoint()
        key = (self._theme, frame, mirrored, self._scale, position.x(), position.y(), pixmap.cacheKey())
        direction_changed = mirrored != self._mirrored
        self._frame, self._mirrored, self._pivot = frame, mirrored, QtCore.QPointF(pivot)
        if key == self._last_render:
            return
        self._last_render = key
        self.window.set_frame(pixmap, mask)
        moved = self.window.pos() != position
        if moved:
            self.window.move(position)
        if moved or direction_changed:
            self.position_changed.emit(QtCore.QPoint(position))

    def _prepared(self, index: int, mirrored: bool) -> tuple[QtGui.QPixmap, QtGui.QRegion, QtCore.QPointF, QtCore.QPointF]:
        ratio = max(1., self.window.devicePixelRatioF())
        frames = self._metadata[self._theme]["frames"]
        # Seven-pose themes reuse artwork without changing the action timeline.
        source_index = index if str(index) in frames else {9: 0, 13: 10, 19: 0, 22: 7}.get(index, index)
        key = (self._theme, source_index, mirrored, self._scale, ratio)
        if key in self._frames:
            return self._frames[key]
        frame = frames[str(source_index)]
        source = self._sheet(self._assets / frame["sheet"]).copy(*frame["rect"])
        anchor = frame["anchor"]
        point = frame.get("point", anchor)
        # Keep the established visible area, including the vehicle.
        unit = (768*self._scale/frame["rect"][3])*frame.get("scale", 1.)*self._metadata[self._theme]["size_factor"]
        if source.isNull():
            raise RuntimeError(f"桌宠动作帧为空：{self._theme}/{index}")
        # A dedicated return pose faces left; a reused move pose faces right.
        source_mirrored = not mirrored if source_index == 22 else mirrored
        if source_mirrored:
            source = source.transformed(QtGui.QTransform().scale(-1, 1))
            anchor = [source.width()-anchor[0], anchor[1]]
            point = [source.width()-point[0], point[1]]
        # Integer local hotspots let a Qt integer global target coincide exactly
        # with the painted fingertip; only subpixel canvas padding is adjusted.
        dx = 2 + round(point[0]*unit)-point[0]*unit
        dy = 2 + round(point[1]*unit)-point[1]*unit
        width, height = math.ceil(source.width()*unit+4), math.ceil(source.height()*unit+4)
        pixmap = QtGui.QPixmap(math.ceil(width*ratio), math.ceil(height*ratio))
        pixmap.setDevicePixelRatio(ratio)
        pixmap.fill(QtCore.Qt.GlobalColor.transparent)
        painter = QtGui.QPainter(pixmap)
        painter.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform)
        painter.translate(dx, dy)
        painter.scale(unit, unit)
        painter.drawPixmap(0, 0, source)
        painter.end()
        mask_image = pixmap.mask().scaled(width, height, QtCore.Qt.AspectRatioMode.IgnoreAspectRatio,
                                          QtCore.Qt.TransformationMode.FastTransformation).toImage()
        mask = QtGui.QRegion(QtGui.QBitmap.fromImage(mask_image))
        if mask.isEmpty():
            raise RuntimeError(f"桌宠动作没有可见区域：{self._theme}/{index}")
        result = (pixmap, mask, QtCore.QPointF(anchor[0]*unit+dx, anchor[1]*unit+dy),
                  QtCore.QPointF(point[0]*unit+dx, point[1]*unit+dy))
        self._frames[key] = result
        return result

    def _sheet(self, path: Path) -> QtGui.QPixmap:
        key = str(path)
        if key not in self._sheets:
            pixmap = QtGui.QPixmap(key)
            if pixmap.isNull():
                raise RuntimeError(f"无法加载桌宠主题素材：{path}")
            self._sheets[key] = pixmap
        return self._sheets[key]

    @staticmethod
    def _point(value: QtCore.QPoint | QtCore.QPointF) -> QtCore.QPointF:
        if not isinstance(value, (QtCore.QPoint, QtCore.QPointF)):
            raise TypeError("桌宠目标必须是 QPoint 或 QPointF")
        point = QtCore.QPointF(value)
        if not math.isfinite(point.x()) or not math.isfinite(point.y()):
            raise ValueError("桌宠目标坐标必须有限")
        return point

    @staticmethod
    def _validated_scale(value: float) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not MIN_DISPLAY_SCALE <= value <= ULTRA_DISPLAY_MAX_SCALE:
            raise ValueError("桌宠显示比例必须在 0.19 到 1.25 之间")
        return float(value)

    @staticmethod
    def _bezier(start: QtCore.QPointF, end: QtCore.QPointF, progress: float) -> QtCore.QPointF:
        distance = math.hypot(end.x()-start.x(), end.y()-start.y())
        control = (start+end)*.5 + QtCore.QPointF(0, -min(36., distance*.12))
        return start*(1-progress)**2 + control*(2*(1-progress)*progress) + end*progress**2

    def _path(self, segment: _Segment, progress: float) -> QtCore.QPointF:
        if segment.progress_range is not None:
            start, end = segment.progress_range
            progress = start + (end-start)*progress
        t = progress*progress*(3-2*progress)
        if segment.straight:
            return segment.start*(1-t)+segment.end*t
        return self._bezier(segment.start, segment.end, t)


__all__ = ("AnimationCancelled", "PetAnimationController")
