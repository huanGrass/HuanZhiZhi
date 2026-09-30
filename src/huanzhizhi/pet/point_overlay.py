"""Transparent click-point overlay used by desktop-pet actions."""

from __future__ import annotations

import math
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

from huanzhizhi.platform.window_policy import WindowPolicy


DEFAULT_DISPLAY_SCALE = 0.32
MIN_DISPLAY_SCALE = 0.19
MAX_DISPLAY_SCALE = 1.25
POINT_HOTSPOT = QtCore.QPoint(283, 780)
POINT_DURATION_MS = 400
DEFAULT_POINT_ASSET = Path(__file__).parents[1] / "assets" / "point" / "point.png"

class PointOverlayWindow(QtWidgets.QWidget):
    """Show a click-through hand pose whose fingertip tracks a global Qt point."""

    finished = QtCore.Signal()

    def __init__(
        self,
        image_path: Path = DEFAULT_POINT_ASSET,
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

        self.image_path = Path(image_path)
        self.display_scale = _validated_display_scale(display_scale)
        self.input_passthrough_applied = False
        self.hold_active = False
        self.point_pixmap = QtGui.QPixmap()
        self.point_mask = QtGui.QRegion()
        self.point_source_size = QtCore.QSize()
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)
        self._window_policy = WindowPolicy(self)

        self.label = QtWidgets.QLabel(self)
        self.label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.label.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        self.return_idle_timer = QtCore.QTimer(self)
        self.return_idle_timer.setSingleShot(True)
        self.return_idle_timer.timeout.connect(self._finish)

        self.hint_points: tuple[QtCore.QPoint, ...] = ()
        self._hint_kind = "click"
        self._hint_label = ""
        self._hint_sprite = QtGui.QPixmap()
        self._hint_clock = QtCore.QElapsedTimer()
        self._hint_timer = QtCore.QTimer(self)
        self._hint_timer.setInterval(33)
        self._hint_timer.timeout.connect(self.update)

        self._reload_frame()
        self._apply_native_input_passthrough()

    def has_frames(self) -> bool:
        return not self.point_pixmap.isNull()

    def frame_size(self) -> QtCore.QSize:
        return self.point_pixmap.size()

    def scaled_hotspot(self) -> QtCore.QPoint:
        if self.point_pixmap.isNull() or self.point_source_size.isEmpty():
            raise RuntimeError("虚拟手指帧尚未加载")
        scale_x = self.point_pixmap.width() / float(self.point_source_size.width())
        scale_y = self.point_pixmap.height() / float(self.point_source_size.height())
        return QtCore.QPoint(
            round(POINT_HOTSPOT.x() * scale_x),
            round(POINT_HOTSPOT.y() * scale_y),
        )

    def is_holding(self) -> bool:
        return self.hold_active and self.isVisible()

    def show_at_click(self, click_screen_x: int, click_screen_y: int, hold: bool = False) -> None:
        _validate_click_coordinates(click_screen_x, click_screen_y)
        if not isinstance(hold, bool):
            raise TypeError("hold 必须是 bool")
        if self.hint_points:
            return
        self.return_idle_timer.stop()
        self.hold_active = hold
        self.move_to_click(click_screen_x, click_screen_y)
        self.show()
        self._window_policy.sync_after_show()
        self.raise_()
        if not self.hold_active:
            self.return_idle_timer.start(POINT_DURATION_MS)

    def move_to_click(self, click_screen_x: int, click_screen_y: int) -> None:
        _validate_click_coordinates(click_screen_x, click_screen_y)
        hotspot = self.scaled_hotspot()
        self.move(click_screen_x - hotspot.x(), click_screen_y - hotspot.y())
        if self.isVisible():
            self.raise_()

    def release_hold(self, click_screen_x: int, click_screen_y: int) -> None:
        _validate_click_coordinates(click_screen_x, click_screen_y)
        if not self.hold_active:
            return
        self.move_to_click(click_screen_x, click_screen_y)
        self.hold_active = False
        self.return_idle_timer.start(POINT_DURATION_MS)

    def hide_overlay(self) -> None:
        was_hint = bool(self.hint_points)
        self.return_idle_timer.stop()
        self.hold_active = False
        self.hint_points = ()
        self._hint_timer.stop()
        self.hide()
        if was_hint:
            self.label.show()
            self._reload_frame()

    def hide_hint(self) -> None:
        if self.hint_points:
            self.hide_overlay()

    def show_hint(self, points: tuple[QtCore.QPoint, ...], kind: str, label: str) -> None:
        expected = {"click": {1, 2}, "drag": {3}, "line": {2}}
        if kind not in expected or len(points) not in expected[kind]:
            raise ValueError("提示类型或坐标数量无效")
        if not all(isinstance(point, QtCore.QPoint) for point in points):
            raise TypeError("提示位置必须是 Qt 全局逻辑坐标")
        if not isinstance(label, str) or not label.strip():
            raise ValueError("提示文字不能为空")
        if kind == "line" and points[0] == points[1]:
            raise ValueError("直线的起点和终点不能相同")
        if kind != "line" and self._hint_sprite.isNull():
            self._hint_sprite.load(str(DEFAULT_POINT_ASSET.with_name("hint.png")))
            if self._hint_sprite.isNull():
                raise RuntimeError("无法加载桌宠提示素材")
        changed = points != self.hint_points or kind != self._hint_kind or label != self._hint_label
        self.hint_points, self._hint_kind, self._hint_label = tuple(points), kind, label
        self.hold_active = False
        if changed:
            self._hint_clock.start()
        screens = [QtGui.QGuiApplication.screenAt(point) or QtGui.QGuiApplication.primaryScreen() for point in points]
        geometry = screens[0].availableGeometry()
        for screen in screens[1:]:
            geometry = geometry.united(screen.availableGeometry())
        self.label.hide()
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
        phase = (self._hint_clock.elapsed() % 2600) / 2600
        color = QtGui.QColor("#b98bff")
        pen = QtGui.QPen(color, 2.5)
        pen.setStyle(QtCore.Qt.PenStyle.DashLine)
        painter.setPen(pen)
        if self._hint_kind == "drag":
            painter.drawLine(points[0], points[1])
            progress = min(1.0, max(0.0, (phase-.15)/.5))
            progress = progress*progress*(3-2*progress)
            anchor = points[0] + (points[1]-points[0])*progress
        else:
            anchor = points[0 if len(points) == 1 or phase < .7 else 1]
        for point in points:
            pulse = 10 + 5 * (1 + math.sin(phase*math.tau*2)) / 2
            painter.setPen(QtGui.QPen(color, 2.5))
            painter.setBrush(QtGui.QColor(185,139,255,35))
            painter.drawEllipse(point, pulse, pulse)
        height = min(300, max(160, round(220*self.display_scale/.32)))
        width = height*self._hint_sprite.width()/self._hint_sprite.height()
        hotspot = QtCore.QPointF(width*707/1024, height*64/1536)
        # Keep the fingertip on the target. The body may be clipped at screen
        # edges rather than shifting the whole pose away from the tile.
        origin = anchor-hotspot
        painter.save()
        painter.setOpacity(.6)
        painter.drawPixmap(QtCore.QRectF(origin.x(),origin.y(),width,height),self._hint_sprite,QtCore.QRectF(self._hint_sprite.rect()))
        painter.restore()

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
        if self.hint_points:
            self.display_scale = new_scale
            self.update()
            return
        click_position = self.pos() + self.scaled_hotspot() if self.isVisible() else None
        self.display_scale = new_scale
        self._reload_frame()
        if click_position is not None:
            self.move_to_click(click_position.x(), click_position.y())

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        self.return_idle_timer.stop()
        self.hold_active = False
        self._hint_timer.stop()
        super().closeEvent(event)

    @QtCore.Slot()
    def _finish(self) -> None:
        self.hide_overlay()
        self.finished.emit()

    def _reload_frame(self) -> None:
        source = QtGui.QPixmap(str(self.image_path))
        if source.isNull():
            raise RuntimeError(f"无法加载虚拟手指素材：{self.image_path}")
        width = max(1, round(source.width() * self.display_scale))
        height = max(1, round(source.height() * self.display_scale))
        pixmap = source.scaled(
            width,
            height,
            QtCore.Qt.AspectRatioMode.IgnoreAspectRatio,
            QtCore.Qt.TransformationMode.SmoothTransformation,
        )
        if pixmap.isNull():
            raise RuntimeError(f"缩放虚拟手指素材失败：{self.image_path}")
        mask = QtGui.QRegion(pixmap.mask())
        if mask.isEmpty():
            raise RuntimeError(f"虚拟手指素材没有可见区域：{self.image_path}")
        self.point_pixmap = pixmap
        self.point_mask = mask
        self.point_source_size = source.size()
        self.label.setPixmap(pixmap)
        self.label.setFixedSize(pixmap.size())
        self.setFixedSize(pixmap.size())
        self.setMask(mask)

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
    "DEFAULT_POINT_ASSET",
    "MAX_DISPLAY_SCALE",
    "MIN_DISPLAY_SCALE",
    "POINT_DURATION_MS",
    "POINT_HOTSPOT",
    "DesktopPetPointOverlayWindow",
    "PointOverlayWindow",
)
