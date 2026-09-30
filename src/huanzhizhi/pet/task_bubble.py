"""Interactive task bubble displayed around the desktop pet."""

from __future__ import annotations

from collections.abc import Sequence

from PySide6 import QtCore, QtGui, QtWidgets

from huanzhizhi.pet.bubble_layout import bubble_scale_for_display
from huanzhizhi.platform.window_policy import WindowPolicy


_MAX_WIDTH = 280
_DETAIL_SCALE_FACTOR = 0.8
_MAX_DETAIL_SCALE = 1.35
_POP_DURATION_MS = 160
_LAYOUT_MODES = {"stack", "compact"}
_BUTTON_KINDS = {"secondary", "primary", "danger"}


class TaskBubbleWindow(QtWidgets.QWidget):
    clicked = QtCore.Signal(str)

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
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.NoFocus)

        self._display_scale = self._validate_scale(display_scale)
        self.bubble_scale = bubble_scale_for_display(self._display_scale)
        self.click_key = ""
        self.layout_mode = "stack"
        self.accent_index = 0
        self.content_signature: tuple | None = None
        self._pop_animation: QtCore.QParallelAnimationGroup | None = None
        self._pop_target_geometry = QtCore.QRect()
        self._pop_delay_timer = QtCore.QTimer(self)
        self._pop_delay_timer.setSingleShot(True)
        self._pop_delay_timer.timeout.connect(self._start_pop_in_animation)

        self.title_label = QtWidgets.QLabel(self)
        self.title_label.setObjectName("petTaskBubbleTitle")
        self.title_label.setWordWrap(True)
        self.detail_label = QtWidgets.QLabel(self)
        self.detail_label.setObjectName("petTaskBubbleDetail")
        self.detail_label.setWordWrap(True)
        self.button_row = QtWidgets.QHBoxLayout()

        self.bubble_layout = QtWidgets.QVBoxLayout(self)
        self.bubble_layout.addWidget(self.title_label)
        self.bubble_layout.addWidget(self.detail_label)
        self.bubble_layout.addLayout(self.button_row)

        self._apply_scaled_style()
        self.ensurePolished()

    def set_content(
        self,
        title: str,
        detail: str = "",
        buttons: Sequence[dict] | None = None,
        click_key: str = "",
        layout_mode: str = "stack",
        accent_index: int = 0,
    ) -> None:
        if not isinstance(title, str) or not title.strip():
            raise ValueError("任务气泡标题不能为空")
        if not isinstance(detail, str):
            raise TypeError("任务气泡详情必须是字符串")
        if not isinstance(click_key, str):
            raise TypeError("任务气泡点击键必须是字符串")
        if layout_mode not in _LAYOUT_MODES:
            raise ValueError(f"不支持的任务气泡布局模式：{layout_mode}")
        if isinstance(accent_index, bool) or not isinstance(accent_index, int):
            raise TypeError("强调色索引必须是整数")
        if buttons is not None and (
            not isinstance(buttons, Sequence) or isinstance(buttons, (str, bytes))
        ):
            raise TypeError("按钮集合必须是序列")
        normalized = tuple(self._validate_button(item) for item in buttons or ())
        signature = (title, detail, normalized, click_key, layout_mode, accent_index)
        if signature == self.content_signature:
            return
        self.content_signature = signature
        self.click_key = click_key
        self.layout_mode = layout_mode
        self.accent_index = accent_index
        self.setCursor(
            QtCore.Qt.CursorShape.PointingHandCursor
            if click_key
            else QtCore.Qt.CursorShape.ArrowCursor
        )
        self.title_label.setText(title)
        self.detail_label.setText(detail)
        self.detail_label.setVisible(bool(detail.strip()))
        self._clear_buttons()
        for key, label, kind, enabled in normalized:
            button = QtWidgets.QPushButton(label)
            button.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
            button.setProperty("kind", kind)
            button.setEnabled(enabled)
            button.clicked.connect(
                lambda _checked=False, action_key=key: self.clicked.emit(action_key)
            )
            self.button_row.addWidget(button)
            if self.isVisible():
                button.show()
        self._apply_scaled_style()
        self._fit_content_width()
        self.adjustSize()

    def set_display_scale(self, display_scale: float) -> None:
        scale = self._validate_scale(display_scale)
        if abs(scale - self._display_scale) < 0.001:
            return
        self._display_scale = scale
        self.bubble_scale = bubble_scale_for_display(scale)
        self._apply_scaled_style()
        if self.content_signature is not None:
            self._fit_content_width()
        self.adjustSize()
        self.update()

    def set_pet_display_scale(self, display_scale: float) -> None:
        self.set_display_scale(display_scale)

    def show_pop_in(self, delay_ms: int = 0) -> None:
        if self.content_signature is None:
            raise RuntimeError("显示任务气泡前必须先设置内容")
        if isinstance(delay_ms, bool) or not isinstance(delay_ms, int):
            raise TypeError("弹出延迟必须是整数毫秒")
        if delay_ms < 0:
            raise ValueError("弹出延迟不能小于零")
        self._stop_pop_animation()
        self._pop_target_geometry = QtCore.QRect(self.geometry())
        self.setWindowOpacity(0.0)
        if delay_ms:
            self._pop_delay_timer.start(delay_ms)
        else:
            self._start_pop_in_animation()

    def hide(self) -> None:
        self._stop_pop_animation()
        self.setWindowOpacity(1.0)
        super().hide()

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        del event
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        shadow_offset = self._shadow_offset()
        rect = QtCore.QRectF(self.rect()).adjusted(1, 1, -1, -1 - shadow_offset)
        fill, border = self._bubble_colors()
        radius = self._scaled_int(18 if self.layout_mode == "compact" else 12)
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        for offset in range(shadow_offset, 0, -1):
            alpha = max(4, round(34 * offset / shadow_offset))
            painter.setBrush(QtGui.QColor(68, 53, 36, alpha))
            painter.drawRoundedRect(rect.translated(0, offset), radius, radius)
        painter.setPen(QtGui.QPen(QtGui.QColor(border), self._scaled_int(1)))
        painter.setBrush(QtGui.QBrush(QtGui.QColor(fill)))
        painter.drawRoundedRect(rect, radius, radius)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        if self.click_key and event.button() == QtCore.Qt.MouseButton.LeftButton:
            self.clicked.emit(self.click_key)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    @staticmethod
    def _validate_scale(scale: float) -> float:
        if isinstance(scale, bool) or not isinstance(scale, (int, float)):
            raise TypeError("桌宠显示倍率必须是数字")
        scale = float(scale)
        if scale <= 0:
            raise ValueError("桌宠显示倍率必须大于零")
        return scale

    @staticmethod
    def _validate_button(item: dict) -> tuple[str, str, str, bool]:
        if not isinstance(item, dict):
            raise TypeError("按钮定义必须是字典")
        key = item.get("key")
        label = item.get("label")
        kind = item.get("kind", "secondary")
        enabled = item.get("enabled", True)
        if not isinstance(key, str) or not key:
            raise ValueError("按钮 key 不能为空")
        if not isinstance(label, str) or not label:
            raise ValueError("按钮 label 不能为空")
        if kind not in _BUTTON_KINDS:
            raise ValueError(f"不支持的按钮类型：{kind}")
        if not isinstance(enabled, bool):
            raise TypeError("按钮 enabled 必须是布尔值")
        return key, label, kind, enabled

    def _start_pop_in_animation(self) -> None:
        target = QtCore.QRect(self._pop_target_geometry)
        if not target.isValid():
            raise RuntimeError("任务气泡弹出目标位置无效")
        self.setGeometry(target)
        self.setWindowOpacity(0.0)
        super().show()
        self.raise_()

        group = QtCore.QParallelAnimationGroup(self)
        opacity_animation = QtCore.QPropertyAnimation(self, b"windowOpacity", group)
        opacity_animation.setStartValue(0.0)
        opacity_animation.setEndValue(1.0)
        opacity_animation.setDuration(max(80, _POP_DURATION_MS - 30))
        opacity_animation.setEasingCurve(QtCore.QEasingCurve.Type.OutCubic)
        group.finished.connect(
            lambda target=QtCore.QRect(target): self._finish_pop_in_animation(target)
        )
        self._pop_animation = group
        group.start()

    def _finish_pop_in_animation(self, target: QtCore.QRect) -> None:
        self._pop_animation = None
        self.setGeometry(target)
        self.setWindowOpacity(1.0)

    def _stop_pop_animation(self) -> None:
        self._pop_delay_timer.stop()
        if self._pop_animation is not None:
            animation = self._pop_animation
            self._pop_animation = None
            animation.stop()

    def _clear_buttons(self) -> None:
        while self.button_row.count():
            item = self.button_row.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.deleteLater()

    def _fit_content_width(self) -> None:
        self.ensurePolished()
        width = 1
        for label in (self.title_label, self.detail_label):
            label.ensurePolished()
            metrics = label.fontMetrics()
            for line in label.text().splitlines():
                width = max(width, metrics.horizontalAdvance(line) + self._scaled_int(4))
        width = min(self._scaled_int(_MAX_WIDTH), width)
        buttons = [self.button_row.itemAt(i).widget() for i in range(self.button_row.count())]
        for button in buttons:
            button.ensurePolished()
        if buttons:
            button_width = sum(button.sizeHint().width() for button in buttons)
            button_width += self.button_row.spacing() * (len(buttons) - 1)
            width = max(width, button_width)
        self.title_label.setFixedWidth(width)
        self.detail_label.setFixedWidth(width)

    def _apply_scaled_style(self) -> None:
        if self.layout_mode == "compact":
            shadow_offset = self._shadow_offset()
            self.bubble_layout.setContentsMargins(
                self._scaled_int(16),
                self._scaled_int(8),
                self._scaled_int(16),
                self._scaled_int(8) + shadow_offset,
            )
            self.bubble_layout.setSpacing(0)
            self.button_row.setSpacing(0)
            title_size = self._scaled_int(14)
            self.setStyleSheet(
                f'QLabel#petTaskBubbleTitle {{ color: #27345f; font-family: "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI"; font-size: {title_size}px; font-weight: 800; }}'
                f'QLabel#petTaskBubbleDetail {{ color: #695f52; font-family: "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI"; font-size: {title_size}px; }}'
                "QPushButton { border: 0; }"
            )
            return
        shadow_offset = self._shadow_offset()
        self.bubble_layout.setContentsMargins(
            self._scaled_int(14),
            self._scaled_int(10),
            self._scaled_int(14),
            self._scaled_int(10) + shadow_offset,
        )
        self.bubble_layout.setSpacing(self._scaled_int(6))
        self.button_row.setSpacing(self._scaled_int(6))
        title_size, detail_size, button_size = (
            self._scaled_int(16), self._scaled_int(13), self._scaled_int(13)
        )
        button_height, radius = self._scaled_int(28), self._scaled_int(7)
        self.setStyleSheet(
            f'QLabel#petTaskBubbleTitle {{ color: #2f2a24; font-family: "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI"; font-size: {title_size}px; font-weight: 800; }}'
            f'QLabel#petTaskBubbleDetail {{ color: #695f52; font-family: "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI"; font-size: {detail_size}px; }}'
            f'QPushButton {{ background: #f3eadc; border: 1px solid #d8c9b5; border-radius: {radius}px; color: #3b3128; font-family: "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI"; font-size: {button_size}px; font-weight: 800; min-height: {button_height}px; padding: 2px {self._scaled_int(10)}px; }}'
            "QPushButton:hover { background: #eadcca; }"
            "QPushButton[kind=\"primary\"] { background: #2563eb; border-color: #1d4ed8; color: #ffffff; }"
            "QPushButton[kind=\"primary\"]:hover { background: #1d4ed8; }"
            "QPushButton[kind=\"danger\"] { background: #dc2626; border-color: #b91c1c; color: #ffffff; }"
            "QPushButton[kind=\"danger\"]:hover { background: #b91c1c; }"
            "QPushButton[kind=\"danger\"]:pressed { background: #991b1b; }"
            "QPushButton:disabled { background: #f3eadc; border-color: #d8c9b5; color: #969ba3; }"
        )

    def _shadow_offset(self) -> int:
        return self._scaled_int(3 if self.layout_mode == "compact" else 4)

    def _bubble_colors(self) -> tuple[str, str]:
        if self.layout_mode != "compact":
            return "#fffdf7", "#d6c8b5"
        palette = (
            ("#eef5ff", "#b9d4ff"),
            ("#fff0f4", "#ffd0dd"),
            ("#eefcf9", "#bcece4"),
            ("#f2edff", "#d8c7ff"),
            ("#fff4ec", "#ffd3bd"),
        )
        return palette[self.accent_index % len(palette)]

    def _scaled_int(self, value: float) -> int:
        scale = self.bubble_scale
        if self.layout_mode == "stack":
            scale = min(scale * _DETAIL_SCALE_FACTOR, _MAX_DETAIL_SCALE)
        return max(1, round(value * scale))


__all__ = ("TaskBubbleWindow",)
