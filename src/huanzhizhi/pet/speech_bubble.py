"""Desktop-pet speech bubble presentation."""

from __future__ import annotations

from collections.abc import Callable

from PySide6 import QtCore, QtGui, QtWidgets

from huanzhizhi.pet.bubble_layout import BubbleLayoutManager, bubble_scale_for_display
from huanzhizhi.platform.window_policy import WindowPolicy


_MAX_TEXT_WIDTH = 240
_MIN_DURATION_MS = 600
_DEFAULT_DURATION_MS = 3200


class SpeechBubbleWindow(QtWidgets.QWidget):
    """A speech bubble with optional skill-repair actions around the desktop pet."""

    visibility_changed = QtCore.Signal(bool)

    def __init__(self, display_scale: float = 0.32) -> None:
        flags = (
            QtCore.Qt.WindowType.Tool
            | QtCore.Qt.WindowType.FramelessWindowHint
            | QtCore.Qt.WindowType.WindowStaysOnTopHint
        )
        super().__init__(None, flags)
        self._window_policy = WindowPolicy(self)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)

        self._display_scale = self._validated_display_scale(display_scale)
        self._bubble_scale = bubble_scale_for_display(self._display_scale)
        self.tail_edge = "down"

        self.label = QtWidgets.QLabel(self)
        self.label.setWordWrap(True)
        self.label.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.NoTextInteraction)

        self._layout = QtWidgets.QVBoxLayout(self)
        self._layout.addWidget(self.label)
        self._on_repair: Callable[[], None] | None = None
        self.repair_button = QtWidgets.QPushButton('让 AI 检查并修复', self)
        self.dismiss_button = QtWidgets.QPushButton('暂不处理', self)
        self.repair_button.clicked.connect(self._repair)
        self.dismiss_button.clicked.connect(self.hide_bubble)
        self._actions = QtWidgets.QHBoxLayout()
        for button in (self.repair_button, self.dismiss_button):
            button.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)
            button.hide()
            self._actions.addWidget(button)
        self._layout.addLayout(self._actions)
        self._apply_scaled_style()

        self.hide_timer = QtCore.QTimer(self)
        self.hide_timer.setSingleShot(True)
        self.hide_timer.timeout.connect(self.hide_bubble)

    def show_text(
        self,
        text: str,
        duration_ms: int = _DEFAULT_DURATION_MS,
        pet_rect: QtCore.QRect | None = None,
        *,
        on_repair: Callable[[], None] | None = None,
        action_text: str = '让 AI 检查并修复',
    ) -> None:
        """Display text; repair choices stay visible until acted on or replaced."""
        if not isinstance(text, str) or not text.strip():
            raise ValueError("气泡文本不能为空")
        if isinstance(duration_ms, bool) or not isinstance(duration_ms, int):
            raise TypeError("显示时长必须是整数毫秒")
        if duration_ms <= 0:
            raise ValueError("显示时长必须大于零")
        if on_repair is not None and not callable(on_repair):
            raise TypeError("修复操作必须可调用")

        self.hide_timer.stop()
        self._on_repair = on_repair
        self.repair_button.setText(action_text)
        for button in (self.repair_button, self.dismiss_button):
            button.setVisible(on_repair is not None)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents, on_repair is None)
        self.label.setText(text)
        self._fit_label_width(text)
        self.label.adjustSize()
        self.adjustSize()
        if pet_rect is not None:
            self.position_around(pet_rect)
        self.show()
        self._window_policy.set_input_passthrough(on_repair is None, layered=True)
        self.raise_()
        if on_repair is None:
            self.hide_timer.start(max(_MIN_DURATION_MS, duration_ms))

    def _repair(self) -> None:
        callback = self._on_repair
        self.hide_bubble()
        if callback is not None:
            callback()

    def hide_bubble(self) -> None:
        self.hide_timer.stop()
        self._on_repair = None
        self.hide()

    def showEvent(self, event: QtGui.QShowEvent) -> None:
        super().showEvent(event)
        self.visibility_changed.emit(True)

    def hideEvent(self, event: QtGui.QHideEvent) -> None:
        super().hideEvent(event)
        self.visibility_changed.emit(False)

    def position_around(self, pet_rect: QtCore.QRect) -> None:
        """Place the bubble near the pet without crossing the target screen."""
        BubbleLayoutManager.position_speech_bubble(self, pet_rect)

    def reposition(self, pet_rect: QtCore.QRect) -> None:
        if self.isVisible():
            self.position_around(pet_rect)
            self.raise_()

    def set_display_scale(self, scale: float) -> None:
        """Update the bubble size for the pet's display scale."""
        scale = self._validated_display_scale(scale)
        if abs(scale - self._display_scale) < 0.001:
            return
        self._display_scale = scale
        self._bubble_scale = bubble_scale_for_display(self._display_scale)
        self._apply_scaled_style()
        if self.label.text():
            self._fit_label_width(self.label.text())
        self.adjustSize()
        self.update()

    def set_pet_display_scale(self, scale: float) -> None:
        self.set_display_scale(scale)

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        del event
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        bubble_rect = self._bubble_rect()
        painter.setPen(QtGui.QPen(QtGui.QColor("#d6c8b5"), self._scaled_int(1)))
        painter.setBrush(QtGui.QBrush(QtGui.QColor("#fffdf7")))
        radius = self._scaled_int(10)
        painter.drawRoundedRect(bubble_rect, radius, radius)
        painter.drawPolygon(self._tail_polygon(bubble_rect))

    @staticmethod
    def _validated_display_scale(scale: float) -> float:
        if isinstance(scale, bool) or not isinstance(scale, (int, float)):
            raise TypeError("桌宠显示倍率必须是数字")
        scale = float(scale)
        if scale <= 0:
            raise ValueError("桌宠显示倍率必须大于零")
        return scale

    def _scaled_int(self, value: float) -> int:
        return max(1, round(value * self._bubble_scale))

    def _apply_scaled_style(self) -> None:
        self._actions.setSpacing(self._scaled_int(8))
        for button in (self.repair_button, self.dismiss_button):
            button.setStyleSheet(f'''
                QPushButton {{ color: #2f2a24; background: #f3ece0;
                    border: 1px solid #d6c8b5; border-radius: {self._scaled_int(6)}px;
                    padding: {self._scaled_int(6)}px {self._scaled_int(8)}px;
                    font-family: "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI";
                    font-size: {self._scaled_int(13)}px; }}
                QPushButton:hover {{ background: #e9dfce; }}
            ''')
        self.label.setMinimumWidth(1)
        self.label.setMaximumWidth(self._scaled_int(_MAX_TEXT_WIDTH))
        self.label.setStyleSheet(
            f"""
            QLabel {{
                color: #2f2a24;
                font-family: "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI";
                font-size: {self._scaled_int(16)}px;
                line-height: 1.35;
            }}
            """
        )
        self._apply_tail_margins()

    def _fit_label_width(self, text: str) -> None:
        self.ensurePolished()
        self.label.ensurePolished()
        metrics = self.label.fontMetrics()
        lines = text.splitlines()
        text_width = max(metrics.horizontalAdvance(line) for line in lines) + self._scaled_int(8)
        width = min(
            max(text_width, 1),
            self._scaled_int(_MAX_TEXT_WIDTH),
        )
        self.label.setFixedWidth(width)

    def _set_tail_edge(self, edge: str) -> None:
        if edge not in {"down", "left", "right", "up"}:
            raise ValueError(f"不支持的气泡尾巴方向：{edge}")
        if edge == self.tail_edge:
            return
        self.tail_edge = edge
        self._apply_tail_margins()

    def _apply_tail_margins(self) -> None:
        left, top, right, bottom = (
            self._scaled_int(18),
            self._scaled_int(12),
            self._scaled_int(18),
            self._scaled_int(12),
        )
        tail = self._scaled_int(10)
        self._layout.setContentsMargins(
            left + (tail if self.tail_edge == "left" else 0),
            top + (tail if self.tail_edge == "up" else 0),
            right + (tail if self.tail_edge == "right" else 0),
            bottom + (tail if self.tail_edge == "down" else 0),
        )

    def _bubble_rect(self) -> QtCore.QRectF:
        border = self._scaled_int(1)
        rect = QtCore.QRectF(self.rect()).adjusted(border, border, -border, -border)
        tail = self._scaled_int(10)
        if self.tail_edge == "down":
            rect.setBottom(rect.bottom() - tail)
        elif self.tail_edge == "up":
            rect.setTop(rect.top() + tail)
        elif self.tail_edge == "left":
            rect.setLeft(rect.left() + tail)
        else:
            rect.setRight(rect.right() - tail)
        return rect

    def _tail_polygon(self, bubble_rect: QtCore.QRectF) -> QtGui.QPolygonF:
        tail = float(self._scaled_int(10))
        half = float(self._scaled_int(8))
        inset = float(self._scaled_int(1))
        if self.tail_edge == "down":
            x, y = bubble_rect.center().x(), bubble_rect.bottom()
            points = (
                QtCore.QPointF(x - half, y - inset),
                QtCore.QPointF(x + half, y - inset),
                QtCore.QPointF(x, y + tail),
            )
        elif self.tail_edge == "up":
            x, y = bubble_rect.center().x(), bubble_rect.top()
            points = (
                QtCore.QPointF(x - half, y + inset),
                QtCore.QPointF(x + half, y + inset),
                QtCore.QPointF(x, y - tail),
            )
        elif self.tail_edge == "left":
            x, y = bubble_rect.left(), bubble_rect.center().y()
            points = (
                QtCore.QPointF(x + inset, y - half),
                QtCore.QPointF(x + inset, y + half),
                QtCore.QPointF(x - tail, y),
            )
        else:
            x, y = bubble_rect.right(), bubble_rect.center().y()
            points = (
                QtCore.QPointF(x - inset, y - half),
                QtCore.QPointF(x - inset, y + half),
                QtCore.QPointF(x + tail, y),
            )
        return QtGui.QPolygonF(points)


__all__ = ("SpeechBubbleWindow",)
