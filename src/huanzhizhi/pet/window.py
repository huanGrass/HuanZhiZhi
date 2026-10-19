"""Minimal desktop-pet shell window."""

from __future__ import annotations

from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

from huanzhizhi.pet.constants import CLICK_DRAG_THRESHOLD, MIN_VISIBLE_FRACTION
from huanzhizhi.platform import WindowPolicy


class PetWindow(QtWidgets.QWidget):
    """Own only the transparent pet surface and direct pointer interaction."""

    skill_zips_dropped = QtCore.Signal(list)
    moved = QtCore.Signal(QtCore.QPoint)
    position_changing = QtCore.Signal(QtCore.QPoint)
    clicked = QtCore.Signal()
    interaction_started = QtCore.Signal()
    interaction_finished = QtCore.Signal()
    context_menu_requested = QtCore.Signal(QtCore.QPoint)

    def __init__(self) -> None:
        super().__init__()
        self._drag_start: tuple[QtCore.QPoint, QtCore.QPoint] | None = None
        self._drag_moved = False
        self._task_state = "idle"

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
        self.setAcceptDrops(True)
        self.setCursor(QtCore.Qt.CursorShape.OpenHandCursor)
        self._window_policy = WindowPolicy(self)
        self._window_policy.set_input_passthrough(False, layered=True)

        self._label = QtWidgets.QLabel(self)
        self._label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self._label.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._label.setAutoFillBackground(False)

        self._pixmap = QtGui.QPixmap()
        self._hit_mask = QtGui.QRegion()

    def visible_geometry(self) -> QtCore.QRect:
        """Anchor adjacent UI to the rendered silhouette, excluding padding."""
        bounds = self._hit_mask.boundingRect().intersected(self.rect())
        return bounds.translated(self.pos())

    def set_input_passthrough(self, enabled: bool) -> None:
        """Let real foreground mouse input pass through an animated pose."""
        self._window_policy.set_input_passthrough(enabled, layered=True, no_activate=True)

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
            # The native window can shrink during a display transition while
            # the fixed-size constraints remain unchanged (setFixedSize no-ops).
            self.resize(logical_size)
        if apply_window_mask:
            self.setMask(mask)
        else:
            self.clearMask()
        self.update()

    def hit_test(self, local_point: QtCore.QPoint) -> bool:
        return self.rect().contains(local_point) and self._hit_mask.contains(local_point)

    @property
    def task_state(self) -> str:
        return self._task_state

    def set_task_state(self, state: str) -> None:
        if state not in {"idle", "thinking", "working", "complete"}:
            raise ValueError(f"不支持的桌宠任务状态：{state}")
        if state == self._task_state:
            return
        self._task_state = state

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

    def _dropped_zips(self, event) -> list[str]:
        if not self.hit_test(event.position().toPoint()) or not event.mimeData().hasUrls():
            return []
        urls = event.mimeData().urls()
        if not urls or any(not url.isLocalFile() for url in urls):
            return []
        paths = [Path(url.toLocalFile()) for url in urls]
        if any(path.suffix.lower() != '.zip' or not path.is_file() for path in paths):
            return []
        return list(dict.fromkeys(str(path) for path in paths))

    def dragEnterEvent(self, event: QtGui.QDragEnterEvent) -> None:
        if self._dropped_zips(event):
            event.setDropAction(QtCore.Qt.DropAction.CopyAction)
            event.accept()
        else:
            event.ignore()

    def dragMoveEvent(self, event: QtGui.QDragMoveEvent) -> None:
        self.dragEnterEvent(event)

    def dropEvent(self, event: QtGui.QDropEvent) -> None:
        filenames = self._dropped_zips(event)
        if filenames:
            event.setDropAction(QtCore.Qt.DropAction.CopyAction)
            event.accept()
            self.skill_zips_dropped.emit(filenames)
        else:
            event.ignore()

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.MouseButton.LeftButton:
            self.begin_drag(event.globalPosition().toPoint())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        if self._drag_start is not None and event.buttons() & QtCore.Qt.MouseButton.LeftButton:
            self.drag_to(event.globalPosition().toPoint())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.MouseButton.LeftButton and self._drag_start is not None:
            self.end_drag(click_if_stationary=True)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def begin_drag(self, global_position: QtCore.QPoint) -> None:
        """Share the pet's drag lifecycle with attached chat surfaces."""
        self._drag_start = (QtCore.QPoint(global_position), self.pos())
        self._drag_moved = False
        self.setCursor(QtCore.Qt.CursorShape.ClosedHandCursor)
        self.interaction_started.emit()

    def drag_to(self, global_position: QtCore.QPoint) -> None:
        if self._drag_start is None:
            return
        start_mouse, start_window = self._drag_start
        delta = global_position - start_mouse
        if not self._drag_moved and delta.manhattanLength() < CLICK_DRAG_THRESHOLD:
            return
        self._drag_moved = True
        self.move(self.clamp_position(start_window + delta))
        self.position_changing.emit(self.pos())

    def end_drag(self, *, click_if_stationary: bool = False) -> None:
        if self._drag_start is None:
            return
        self._drag_start = None
        if self._drag_moved:
            self.moved.emit(self.pos())
        elif click_if_stationary:
            self.clicked.emit()
        self._drag_moved = False
        self.setCursor(QtCore.Qt.CursorShape.OpenHandCursor)
        self.interaction_finished.emit()

    def contextMenuEvent(self, event: QtGui.QContextMenuEvent) -> None:
        self.context_menu_requested.emit(event.globalPos())
        event.accept()

    def showEvent(self, event: QtGui.QShowEvent) -> None:
        super().showEvent(event)
        self._window_policy.sync_after_show()

    @staticmethod
    def _clamped_axis(value: int, size: int, start: int, end: int) -> int:
        visible = max(1, round(size * MIN_VISIBLE_FRACTION))
        hidden = max(0, size - visible)
        minimum = start - hidden
        maximum = end - visible + 1
        if maximum < minimum:
            raise RuntimeError("显示区域小于桌宠的最小可见范围")
        return min(max(value, minimum), maximum)


__all__ = ("PetWindow",)
