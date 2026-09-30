"""Minimal desktop-pet shell window."""

from __future__ import annotations

import math
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

from huanzhizhi.pet.constants import CLICK_DRAG_THRESHOLD, MIN_VISIBLE_FRACTION
from huanzhizhi.platform import WindowPolicy


class PetWindow(QtWidgets.QWidget):
    """Own only the transparent pet surface and direct pointer interaction."""

    moved = QtCore.Signal(QtCore.QPoint)
    position_changing = QtCore.Signal(QtCore.QPoint)
    clicked = QtCore.Signal()
    interaction_started = QtCore.Signal()
    interaction_finished = QtCore.Signal()
    context_menu_requested = QtCore.Signal(QtCore.QPoint)

    def __init__(self, image_path: Path, display_scale: float, *, procedural_motion: bool = False) -> None:
        super().__init__()
        self._drag_start: tuple[QtCore.QPoint, QtCore.QPoint] | None = None
        self._drag_moved = False
        self._procedural_motion = procedural_motion
        self._task_state = "idle"
        self._motion_elapsed = 0.0
        self._motion_from = (0.0, 0.0)
        self._motion_paused = False
        self._motion_clock = QtCore.QElapsedTimer()
        self._motion_timer = QtCore.QTimer(self)
        self._motion_timer.timeout.connect(self._advance_motion)
        self._effects: _TaskEffectsWindow | None = None

        self.setWindowTitle("幻之之")
        self.setWindowFlags(
            QtCore.Qt.WindowType.FramelessWindowHint
            | QtCore.Qt.WindowType.WindowStaysOnTopHint
            | QtCore.Qt.WindowType.Tool
        )
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAutoFillBackground(False)
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self.setMouseTracking(True)
        self.setCursor(QtCore.Qt.CursorShape.OpenHandCursor)
        self._window_policy = WindowPolicy(self)
        self._window_policy.set_input_passthrough(False, layered=True)

        self._label = QtWidgets.QLabel(self)
        self._label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self._label.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._label.setAutoFillBackground(False)

        pixmap = QtGui.QPixmap(str(image_path))
        if pixmap.isNull():
            raise RuntimeError(f"无法加载桌宠图像：{image_path}")
        self._source_size = pixmap.size()
        self._source_pixmap = QtGui.QPixmap(pixmap)

        # Keep the existing menu percentages, with every character size halved.
        target_width = max(1, round(pixmap.width() * display_scale * 0.5))
        target_height = max(1, round(pixmap.height() * display_scale * 0.5))
        screen = QtGui.QGuiApplication.primaryScreen()
        device_pixel_ratio = 1.0 if screen is None else max(1.0, screen.devicePixelRatio())
        pixmap = pixmap.scaled(
            max(1, round(target_width * device_pixel_ratio)),
            max(1, round(target_height * device_pixel_ratio)),
            QtCore.Qt.AspectRatioMode.IgnoreAspectRatio,
            QtCore.Qt.TransformationMode.SmoothTransformation,
        )
        pixmap.setDevicePixelRatio(device_pixel_ratio)
        self._pixmap = pixmap
        self._hit_mask = QtGui.QRegion(QtGui.QBitmap.fromImage(pixmap.mask().scaled(
            target_width,
            target_height,
            QtCore.Qt.AspectRatioMode.IgnoreAspectRatio,
            QtCore.Qt.TransformationMode.FastTransformation,
        ).toImage()))
        self._label.setPixmap(pixmap)
        logical_size = pixmap.deviceIndependentSize().toSize()
        self._label.setFixedSize(logical_size)
        self.setFixedSize(logical_size)
        if procedural_motion:
            self._label.hide()
            # Layered-window alpha supplies native hit testing. A static window
            # region would clip the rotating character to its original outline.
            self.clearMask()
            self._effects = _TaskEffectsWindow(self)
        else:
            self.setMask(self._hit_mask)

    @property
    def source_size(self) -> QtCore.QSize:
        return QtCore.QSize(self._source_size)

    def visible_geometry(self) -> QtCore.QRect:
        """Anchor adjacent UI to the rendered silhouette, excluding padding."""
        bounds = self._hit_mask.boundingRect().intersected(self.rect())
        return bounds.translated(self.pos())

    def set_display_scale(self, scale: float) -> None:
        if isinstance(scale, bool) or not isinstance(scale, (int, float)) or not .19 <= scale <= 1.25:
            raise ValueError("桌宠显示比例必须在 0.19 到 1.25 之间")
        size = self._source_size * (scale * 0.5)
        ratio = max(1., self.devicePixelRatioF())
        pixmap = self._source_pixmap.scaled(size * ratio, QtCore.Qt.AspectRatioMode.IgnoreAspectRatio,
                                          QtCore.Qt.TransformationMode.SmoothTransformation)
        pixmap.setDevicePixelRatio(ratio)
        mask = QtGui.QRegion(QtGui.QBitmap.fromImage(pixmap.mask().scaled(size).toImage()))
        self.set_frame(pixmap, mask)

    def set_frame(
        self,
        pixmap: QtGui.QPixmap,
        mask: QtGui.QRegion,
        *,
        apply_window_mask: bool = True,
    ) -> None:
        if pixmap.isNull():
            raise RuntimeError("动画控制器提交了空桌宠帧")
        if mask.isEmpty():
            raise RuntimeError("动画控制器提交了空桌宠遮罩")
        self._pixmap = pixmap
        self._hit_mask = QtGui.QRegion(mask)
        self._label.setPixmap(pixmap)
        logical_size = pixmap.deviceIndependentSize().toSize()
        self._label.setFixedSize(logical_size)
        if self.size() != logical_size:
            self.setFixedSize(logical_size)
        if apply_window_mask and not self._procedural_motion:
            self.setMask(mask)
        else:
            self.clearMask()
        self.update()
        if self._effects is not None:
            self._effects.update()

    def hit_test(self, local_point: QtCore.QPoint) -> bool:
        if self._procedural_motion:
            inverse, invertible = self._character_transform().inverted()
            return (invertible and self.rect().contains(local_point)
                    and self._hit_mask.contains(inverse.map(QtCore.QPointF(local_point)).toPoint()))
        return self.rect().contains(local_point) and self._hit_mask.contains(local_point)

    @property
    def task_state(self) -> str:
        return self._task_state

    def set_task_state(self, state: str) -> None:
        if state not in {"idle", "thinking", "working", "complete"}:
            raise ValueError(f"不支持的桌宠任务状态：{state}")
        if state == self._task_state:
            return
        self._motion_from = self._motion_pose()
        self._task_state = state
        self._motion_elapsed = 0.0
        self._motion_clock.start()
        self._sync_motion_timer()
        self._sync_effects()
        self.update()

    def set_motion_paused(self, paused: bool) -> None:
        self._motion_paused = paused
        self._sync_motion_timer()

    def _motion_pose(self) -> tuple[float, float]:
        t = self._motion_elapsed
        if self._task_state == "idle":
            phase = t % 6
            pulse = math.sin(math.pi * phase / 2.6) ** 2 if phase < 2.6 else 0.0
            y, angle = -2.5 * pulse, .8 * math.sin(phase * 2.4) * pulse
        elif self._task_state == "thinking":
            y, angle = -2 + 1.5 * math.sin(t * 1.8), -1.2 + .4 * math.sin(t * 1.5)
        elif self._task_state == "working":
            y, angle = -1.8 * (1 - math.cos(t * 3)), .45 * math.sin(t * 2.3)
        else:
            jump = math.sin(math.pi * min(t / .85, 1)) ** 2
            y, angle = -16 * jump, -2 * math.sin(t * 8) * jump
        blend = min(t / .28, 1)
        blend = blend * blend * (3 - 2 * blend)
        return tuple(a + (b - a) * blend for a, b in zip(self._motion_from, (y, angle)))

    def _character_transform(self) -> QtGui.QTransform:
        bounds = self._hit_mask.boundingRect()
        y, angle = self._motion_pose()
        transform = QtGui.QTransform()
        transform.translate(bounds.center().x(), bounds.bottom() + y * bounds.height() / 302)
        transform.rotate(angle)
        transform.translate(-bounds.center().x(), -bounds.bottom())
        return transform

    def _sync_motion_timer(self) -> None:
        running = (self._procedural_motion and self.isVisible() and not self.isMinimized()
                   and not self._motion_paused and self._drag_start is None)
        if not running:
            self._motion_timer.stop()
            return
        interval = 33
        if self._task_state == "idle":
            phase = self._motion_elapsed % 6
            # No repaint loop during the quiet part of the idle gesture.
            interval = max(1, round((6 - phase) * 1000)) if phase >= 2.6 else 67
        self._motion_timer.setInterval(interval)
        if not self._motion_timer.isActive():
            self._motion_clock.start()
            self._motion_timer.start()

    def _advance_motion(self) -> None:
        self._motion_elapsed += self._motion_clock.restart() / 1000
        if self._task_state == "complete" and self._motion_elapsed >= 2.4:
            self.set_task_state("idle")
            return
        self.update()
        if self._effects is not None:
            self._effects.update()
        self._sync_motion_timer()

    def _sync_effects(self) -> None:
        if self._effects is None:
            return
        self._effects.setGeometry(self.geometry())
        visible = self.isVisible() and not self.isMinimized() and self._task_state != "idle"
        self._effects.setVisible(visible)
        if visible:
            self._effects.update()

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        if not self._procedural_motion:
            super().paintEvent(event)
            return
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform)
        painter.setWorldTransform(self._character_transform())
        painter.drawPixmap(QtCore.QPointF(0, 0), self._pixmap)

    def moveEvent(self, event: QtGui.QMoveEvent) -> None:
        super().moveEvent(event)
        self._sync_effects()

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event)
        self._sync_effects()

    def clamp_position(self, position: QtCore.QPoint) -> QtCore.QPoint:
        if not isinstance(position, QtCore.QPoint):
            raise TypeError("桌宠位置必须是 QPoint")
        screen = QtGui.QGuiApplication.screenAt(position)
        if screen is None:
            screen = self.screen()
        if screen is None:
            raise RuntimeError("无法确定桌宠所在显示器")
        area = screen.availableGeometry()
        return QtCore.QPoint(
            self._clamped_axis(position.x(), self.width(), area.left(), area.right()),
            self._clamped_axis(position.y(), self.height(), area.top(), area.bottom()),
        )

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if self._procedural_motion and not self.hit_test(event.position().toPoint()):
            event.ignore()
            return
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            self._drag_start = (event.globalPosition().toPoint(), self.pos())
            self._drag_moved = False
            self._sync_motion_timer()
            self.setCursor(QtCore.Qt.CursorShape.ClosedHandCursor)
            self.interaction_started.emit()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        if self._drag_start is not None and event.buttons() & QtCore.Qt.MouseButton.LeftButton:
            start_mouse, start_window = self._drag_start
            delta = event.globalPosition().toPoint() - start_mouse
            if not self._drag_moved and delta.manhattanLength() < CLICK_DRAG_THRESHOLD:
                event.accept()
                return
            self._drag_moved = True
            self.move(self.clamp_position(start_window + delta))
            self.position_changing.emit(self.pos())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.MouseButton.LeftButton and self._drag_start is not None:
            self._drag_start = None
            if self._drag_moved:
                self.moved.emit(self.pos())
            else:
                self.clicked.emit()
            self._drag_moved = False
            self.setCursor(QtCore.Qt.CursorShape.OpenHandCursor)
            self.interaction_finished.emit()
            self._sync_motion_timer()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def contextMenuEvent(self, event: QtGui.QContextMenuEvent) -> None:
        self.context_menu_requested.emit(event.globalPos())
        event.accept()

    def showEvent(self, event: QtGui.QShowEvent) -> None:
        super().showEvent(event)
        self._window_policy.sync_after_show()
        self._sync_effects()
        self._sync_motion_timer()

    def hideEvent(self, event: QtGui.QHideEvent) -> None:
        self._motion_timer.stop()
        if self._effects is not None:
            self._effects.hide()
        super().hideEvent(event)

    def changeEvent(self, event: QtCore.QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QtCore.QEvent.Type.WindowStateChange:
            self._sync_motion_timer()
            self._sync_effects()

    @staticmethod
    def _clamped_axis(value: int, size: int, start: int, end: int) -> int:
        visible = max(1, round(size * MIN_VISIBLE_FRACTION))
        hidden = max(0, size - visible)
        minimum = start - hidden
        maximum = end - visible + 1
        if maximum < minimum:
            raise RuntimeError("显示区域小于桌宠的最小可见范围")
        return min(max(value, minimum), maximum)


class _TaskEffectsWindow(QtWidgets.QWidget):
    """Small, input-transparent effects surface owned by the character window."""

    def __init__(self, pet: PetWindow) -> None:
        super().__init__(pet, QtCore.Qt.WindowType.Tool | QtCore.Qt.WindowType.FramelessWindowHint
                         | QtCore.Qt.WindowType.WindowStaysOnTopHint
                         | QtCore.Qt.WindowType.WindowTransparentForInput)
        self._pet = pet
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
        self._window_policy = WindowPolicy(self)
        self._window_policy.set_input_passthrough(True, layered=True, no_activate=True)

    def showEvent(self, event: QtGui.QShowEvent) -> None:
        super().showEvent(event)
        self._window_policy.sync_after_show()

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        state, t = self._pet.task_state, self._pet._motion_elapsed
        if state == "idle":
            return
        bounds = self._pet._hit_mask.boundingRect()
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        painter.translate(bounds.center().x(), bounds.bottom())
        unit = bounds.height() / 302
        painter.scale(unit, unit)
        purple, cyan = QtGui.QColor("#bc9eff"), QtGui.QColor("#8be0e4")

        def ink(color: QtGui.QColor | str, width: float = 1) -> None:
            painter.setPen(QtGui.QPen(QtGui.QColor(color), width, QtCore.Qt.PenStyle.SolidLine,
                                     QtCore.Qt.PenCapStyle.RoundCap, QtCore.Qt.PenJoinStyle.RoundJoin))
            painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)

        accent = cyan if state == "working" else purple
        if state == "complete":
            accent = QtGui.QColor("#b4ebc8")
        color = QtGui.QColor(accent)
        color.setAlpha(70)
        ink(color)
        ring = QtCore.QRectF(-78, -8, 156, 27)
        painter.drawEllipse(ring)
        color.setAlpha(210)
        ink(color, 2)
        angle = -t * 90 if state != "complete" else 25
        painter.drawArc(ring, round(angle * 16), 75 * 16)
        painter.drawArc(ring, round((angle + 180) * 16), 38 * 16)
        if state == "thinking":
            center = QtCore.QPointF(104, -236)
            ink("#9784bb")
            painter.drawEllipse(center, 22, 12)
            painter.setPen(QtCore.Qt.PenStyle.NoPen)
            painter.setBrush(purple)
            for i in range(3):
                phase = -t * 1.7 + i * math.tau / 3
                point = center + QtCore.QPointF(22 * math.cos(phase), 12 * math.sin(phase))
                painter.drawEllipse(point, 3.2, 3.2)
            painter.setBrush(QtGui.QColor("#e5d9ff"))
            painter.drawEllipse(center, 3, 3)
            painter.drawEllipse(QtCore.QPointF(78, -215), 2, 2)
            painter.drawEllipse(QtCore.QPointF(71, -208), 1.3, 1.3)
        elif state == "working":
            x, y = 66, -147 + math.sin(t * 1.8) * 2
            ink("#6f9ca8")
            painter.setBrush(QtGui.QColor(27, 44, 60, 235))
            painter.drawRoundedRect(QtCore.QRectF(x, y, 95, 76), 10, 10)
            ink(cyan, 1.7)
            painter.drawLine(QtCore.QPointF(x + 13, y + 16), QtCore.QPointF(x + 28, y + 16))
            for row in range(3):
                line_y = y + 32 + row * 12
                ink("#385365", 3)
                painter.drawLine(QtCore.QPointF(x + 14, line_y), QtCore.QPointF(x + 79, line_y))
                color = QtGui.QColor(cyan)
                color.setAlpha(round(70 + (.5 + .5 * math.sin(t * 3.5 - row)) * 160))
                ink(color, 3)
                painter.drawLine(QtCore.QPointF(x + 14, line_y), QtCore.QPointF(x + 32 + row * 12, line_y))
            painter.setPen(QtCore.Qt.PenStyle.NoPen)
            painter.setBrush(cyan)
            painter.drawEllipse(QtCore.QPointF(x + 78, y + 16), 2.5, 2.5)
        elif state == "complete":
            x, y = 78, -230
            ink("#91d6b0")
            painter.setBrush(QtGui.QColor("#253f3a"))
            painter.drawEllipse(QtCore.QPointF(x, y), 15, 15)
            ink("#c2f6d8", 2)
            painter.drawPolyline(QtGui.QPolygonF([
                QtCore.QPointF(x - 6, y), QtCore.QPointF(x - 1, y + 5), QtCore.QPointF(x + 7, y - 5),
            ]))
            if t < 1.6:
                for i in range(7):
                    a = i * math.tau / 7 - 1.5
                    radius = 40 + 27 * min(t, 1)
                    point = QtCore.QPointF(math.cos(a) * radius * 1.5, -207 + math.sin(a) * radius)
                    color = QtGui.QColor(purple if i % 2 else cyan)
                    color.setAlpha(round(220 * max(0, 1 - t / 1.6)))
                    ink(color, 1.8)
                    length = 3 + 2 * math.sin(i)
                    painter.drawLine(point - QtCore.QPointF(length, 0), point + QtCore.QPointF(length, 0))
                    painter.drawLine(point - QtCore.QPointF(0, length), point + QtCore.QPointF(0, length))


__all__ = ("PetWindow",)
