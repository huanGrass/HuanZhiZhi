"""Input-transparent task hint markers and direction lines."""

from __future__ import annotations

import math

from PySide6 import QtCore, QtGui, QtWidgets

from huanzhizhi.platform.window_policy import WindowPolicy


DEFAULT_DISPLAY_SCALE = 0.32
MIN_DISPLAY_SCALE = 0.19
MAX_DISPLAY_SCALE = 1.25


class PointOverlayWindow(QtWidgets.QWidget):
    """Show task markers while the themed pet supplies the character pose."""

    finished = QtCore.Signal()

    def __init__(
        self,
        *,
        display_scale: float = DEFAULT_DISPLAY_SCALE,
    ) -> None:
        flags = (
            QtCore.Qt.WindowType.Tool
            | QtCore.Qt.WindowType.FramelessWindowHint
            | QtCore.Qt.WindowType.WindowStaysOnTopHint
            | QtCore.Qt.WindowType.WindowDoesNotAcceptFocus
            | QtCore.Qt.WindowType.WindowTransparentForInput
        )
        super().__init__(None, flags)
        self.setWindowTitle("桌宠点击动作")
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)

        self.display_scale = _validated_display_scale(display_scale)
        self.input_passthrough_applied = False
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)
        self._window_policy = WindowPolicy(self)

        self.return_idle_timer = QtCore.QTimer(self)
        self.return_idle_timer.setSingleShot(True)
        self.return_idle_timer.timeout.connect(self._finish)

        self.hint_points: tuple[QtCore.QPoint, ...] = ()
        self._hint_kind = "click"
        self._hint_label = ""
        self._hint_clock = QtCore.QElapsedTimer()
        self._hint_timer = QtCore.QTimer(self)
        self._hint_timer.setInterval(33)
        self._hint_timer.timeout.connect(self.update)

        self._apply_native_input_passthrough()

    @property
    def hint_elapsed_ms(self) -> int:
        """Elapsed time for the marker pulse; motion uses the action animation."""
        return self._hint_clock.elapsed() if self._hint_clock.isValid() else 0

    def hide_overlay(self) -> None:
        self.return_idle_timer.stop()
        self.hint_points = ()
        self._hint_timer.stop()
        self.hide()
        self.setWindowOpacity(1.)

    def hide_hint(self) -> None:
        if self.hint_points:
            self.hide_overlay()

    def show_hint(self, points: tuple[QtCore.QPoint, ...], kind: str, label: str) -> None:
        expected = {"click": {1, 2}, "right_click": {1}, "double_click": {1}, "drag": {2, 3}, "line": {2}}
        if kind not in expected or len(points) not in expected[kind]:
            raise ValueError("提示类型或坐标数量无效")
        if not all(isinstance(point, QtCore.QPoint) for point in points):
            raise TypeError("提示位置必须是 Qt 全局逻辑坐标")
        if not isinstance(label, str) or not label.strip():
            raise ValueError("提示文字不能为空")
        if kind == "line" and points[0] == points[1]:
            raise ValueError("直线的起点和终点不能相同")
        self.setWindowOpacity(1.)
        changed = points != self.hint_points or kind != self._hint_kind or label != self._hint_label
        self.hint_points, self._hint_kind, self._hint_label = tuple(points), kind, label
        if changed:
            self._hint_clock.start()
        screens = [QtGui.QGuiApplication.screenAt(point) or QtGui.QGuiApplication.primaryScreen() for point in points]
        geometry = screens[0].availableGeometry()
        for screen in screens[1:]:
            geometry = geometry.united(screen.availableGeometry())
        self.clearMask()
        self.setMinimumSize(0, 0)
        self.setMaximumSize(16777215, 16777215)
        self.setGeometry(geometry)
        self.show()
        self._window_policy.sync_after_show()
        self.raise_()
        if kind == "line":
            self._hint_timer.stop()
        else:
            self._hint_timer.start()
        # A task must keep confirming the screenshot; stale hints expire.
        self.return_idle_timer.start(200 if kind == "line" else 1800)
        self.update()

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        super().paintEvent(event)
        if not self.hint_points:
            return
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform)
        points = [QtCore.QPointF(point-self.pos()) for point in self.hint_points]
        if self._hint_kind == "line":
            painter.setPen(QtGui.QPen(QtGui.QColor("#20e6ff"), 1.5))
            painter.drawLine(points[0], points[1])
            return
        phase = (self.hint_elapsed_ms % 2600) / 2600
        color = QtGui.QColor("#b98bff")
        pen = QtGui.QPen(color, 2.5)
        pen.setStyle(QtCore.Qt.PenStyle.DashLine)
        painter.setPen(pen)
        if self._hint_kind == "drag":
            painter.drawLine(points[0], points[1])
        for point in points:
            pulse = 10 + 5 * (1 + math.sin(phase*math.tau*2)) / 2
            painter.setPen(QtGui.QPen(color, 2.5))
            painter.setBrush(QtGui.QColor(185,139,255,35))
            painter.drawEllipse(point, pulse, pulse)
        # Keep the action origin and direction visible even while the sprite
        # moves to the destination.
        action_color = QtGui.QColor("#ffd166")
        outline_color = QtGui.QColor("#30203f")
        if self._hint_kind == "drag":
            delta = points[1] - points[0]
            length = math.hypot(delta.x(), delta.y())
            if length > 0:
                direction = delta / length
                normal = QtCore.QPointF(-direction.y(), direction.x())
                inset = min(16.0, length / 4)
                tip = points[1] - direction * inset
                head = min(12.0, length / 4)
                painter.setPen(QtGui.QPen(outline_color, 7))
                painter.drawLine(points[0] + direction * inset, tip)
                painter.setPen(QtGui.QPen(action_color, 3))
                painter.drawLine(points[0] + direction * inset, tip)
                painter.setPen(QtGui.QPen(outline_color, 2))
                painter.setBrush(action_color)
                painter.drawPolygon(QtGui.QPolygonF([
                    tip,
                    tip - direction * head + normal * head * .6,
                    tip - direction * head - normal * head * .6,
                ]))
        painter.setBrush(QtCore.Qt.BrushStyle.NoBrush)
        painter.setPen(QtGui.QPen(outline_color, 7))
        painter.drawEllipse(points[0], 16, 16)
        painter.setPen(QtGui.QPen(action_color, 3))
        painter.drawEllipse(points[0], 16, 16)

    def set_display_scale(self, display_scale: float) -> None:
        new_scale = _validated_display_scale(display_scale)
        if abs(new_scale - self.display_scale) < 0.001:
            return
        self.display_scale = new_scale
        self.update()

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        self.return_idle_timer.stop()
        self._hint_timer.stop()
        super().closeEvent(event)

    @QtCore.Slot()
    def _finish(self) -> None:
        self.hide_overlay()
        self.finished.emit()

    def _apply_native_input_passthrough(self) -> None:
        self._window_policy.set_input_passthrough(True, layered=True, no_activate=True)
        self.input_passthrough_applied = self._window_policy.input_passthrough


def _validated_display_scale(display_scale: float) -> float:
    if isinstance(display_scale, bool) or not isinstance(display_scale, (int, float)):
        raise TypeError("虚拟手指显示倍率必须是数字")
    scale = float(display_scale)
    if not math.isfinite(scale):
        raise ValueError("虚拟手指显示倍率必须是有限数值")
    return max(MIN_DISPLAY_SCALE, min(MAX_DISPLAY_SCALE, scale))


def _validate_click_coordinates(click_screen_x: int, click_screen_y: int) -> None:
    if (
        isinstance(click_screen_x, bool)
        or not isinstance(click_screen_x, int)
        or isinstance(click_screen_y, bool)
        or not isinstance(click_screen_y, int)
    ):
        raise TypeError("点击位置必须是 Qt 全局逻辑坐标整数")


DesktopPetPointOverlayWindow = PointOverlayWindow

__all__ = (
    "DEFAULT_DISPLAY_SCALE",
    "MAX_DISPLAY_SCALE",
    "MIN_DISPLAY_SCALE",
    "DesktopPetPointOverlayWindow",
    "PointOverlayWindow",
)
