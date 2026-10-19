"""Reuse these controls and style fragments across desktop pages.

Page styles should describe their layout-specific surfaces and labels; common
controls, menus and interaction states are maintained here.
"""
from PySide6 import QtCore, QtGui, QtWidgets


BUTTON_STYLE = """
QPushButton { background: #f1f3f6; border: 0; border-radius: 7px; padding: 7px 11px; }
QPushButton:hover { background: #e4e9f0; }
QPushButton:pressed { background: #d8e0ea; }
QPushButton:disabled { color: #969ba3; }
QPushButton:focus { border: 1px solid #405d52; }
QPushButton[primary="true"] { background: #405d52; color: white; }
QPushButton[primary="true"]:hover { background: #344f44; }
QPushButton[primary="true"]:pressed { background: #293f36; }
QPushButton[danger="true"] { color: #a64646; background: #f9eeee; }
QPushButton[danger="true"]:hover { background: #f2dada; }
QPushButton[danger="true"]:pressed { background: #e9bcbc; }
QPushButton[danger="true"]:disabled { color: #969ba3; background: #f1f3f6; }
"""

TOOL_BUTTON_STYLE = """
QToolButton { border: none; border-radius: 8px; padding: 5px 7px; color: #857b92; font-size: 12px; }
QToolButton:hover { background: #f5effc; color: #8057b3; }
QToolButton:disabled { color: #aaa3b1; }
"""

SCROLLBAR_STYLE = """
QScrollBar:vertical { background: transparent; width: 6px; margin: 0; }
QScrollBar::handle:vertical { background: #d6cedf; border-radius: 3px; min-height: 24px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
"""

MENU_STYLE = """
QMenu { background: #ffffff; border: 1px solid #d0d7e2; border-radius: 8px; font-size: 14px; padding: 5px 0; }
QMenu::item { color: #1f2937; min-width: 168px; min-height: 30px; padding: 6px 30px 6px 14px; border-radius: 6px; }
QMenu::item:selected { background: #eef4ff; color: #111827; }
QMenu::indicator { width: 16px; height: 16px; padding-left: 2px; }
QMenu::right-arrow { width: 10px; height: 10px; padding-right: 10px; }
QMenu::separator { height: 1px; background: #e5e7eb; margin: 5px 0; }
"""


def apply_menu_style(menu: QtWidgets.QMenu) -> None:
    font = menu.font()
    font.setPixelSize(14)
    menu.setFont(font)
    menu.setStyleSheet(MENU_STYLE)


class ComboBox(QtWidgets.QComboBox):
    """Keep native selection behavior with the project's settings appearance."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("""
QComboBox { border: 1px solid #e2e2e2; border-radius: 10px; padding: 6px 34px 6px 12px; min-width: 126px; background: #ffffff; }
QComboBox:hover { background: #f8f8f8; border-color: #d3d3d3; }
QComboBox:focus { border-color: #8ab4f8; }
QComboBox::drop-down { subcontrol-origin: padding; subcontrol-position: top right; width: 28px; border: none; background: transparent; }
QComboBox::down-arrow { image: none; }
""")

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        super().paintEvent(event)
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        center_x = self.width() - 18
        center_y = self.height() // 2
        painter.setPen(QtGui.QPen(QtGui.QColor("#737373"), 1.5))
        painter.drawLine(center_x - 5, center_y - 2, center_x, center_y + 3)
        painter.drawLine(center_x, center_y + 3, center_x + 5, center_y - 2)
        painter.end()


class SpinBox(QtWidgets.QSpinBox):
    """Keep native stepping and hit targets, with small painted chevrons."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("""
QSpinBox { background: #f8f9fb; border: 1px solid #dce2e8; border-radius: 6px; padding: 6px 28px 6px 8px; }
QSpinBox:hover { border-color: #aebbc7; }
QSpinBox:focus { background: #ffffff; border-color: #405d52; }
QSpinBox::up-button, QSpinBox::down-button { subcontrol-origin: border; width: 24px; border: none; background: transparent; }
QSpinBox::up-button { subcontrol-position: top right; border-top-right-radius: 6px; }
QSpinBox::down-button { subcontrol-position: bottom right; border-bottom-right-radius: 6px; }
QSpinBox::up-button:hover, QSpinBox::down-button:hover { background: #e4e9f0; }
QSpinBox::up-button:pressed, QSpinBox::down-button:pressed { background: #d8e0ea; }
QSpinBox::up-arrow, QSpinBox::down-arrow { image: none; }
""")

    def paintEvent(self, event):
        super().paintEvent(event)
        option = QtWidgets.QStyleOptionSpinBox()
        self.initStyleOption(option)
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        for control, step, direction in (
            (QtWidgets.QStyle.SubControl.SC_SpinBoxUp, self.StepEnabledFlag.StepUpEnabled, -1),
            (QtWidgets.QStyle.SubControl.SC_SpinBoxDown, self.StepEnabledFlag.StepDownEnabled, 1),
        ):
            rect = self.style().subControlRect(QtWidgets.QStyle.ComplexControl.CC_SpinBox, option, control, self)
            center = QtCore.QRectF(rect).center()
            enabled = self.isEnabled() and bool(option.stepEnabled & step)
            pen = QtGui.QPen(QtGui.QColor('#63716b' if enabled else '#b9c1c8'), 1.4)
            pen.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(QtCore.Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.drawPolyline(QtGui.QPolygonF([
                center + QtCore.QPointF(-3, -direction * 1.5),
                center + QtCore.QPointF(0, direction * 1.5),
                center + QtCore.QPointF(3, -direction * 1.5),
            ]))
        painter.end()


class ToggleSwitch(QtWidgets.QCheckBox):
    """A compact switch with an explicit full-control click target."""

    def __init__(self, accessible_name: str) -> None:
        super().__init__()
        self.setObjectName("desktopPetSettingsSwitch")
        self.setAccessibleName(accessible_name)
        self.setFixedSize(52, 30)
        self._thumb_position = 0.0
        self._thumb_animation = QtCore.QPropertyAnimation(self, b"thumbPosition", self)
        self._thumb_animation.setDuration(140)
        self._thumb_animation.setEasingCurve(QtCore.QEasingCurve.Type.OutCubic)
        self.toggled.connect(self._animate_thumb)

    @QtCore.Property(float)
    def thumbPosition(self) -> float:
        return self._thumb_position

    @thumbPosition.setter
    def thumbPosition(self, position: float) -> None:
        self._thumb_position = min(1.0, max(0.0, float(position)))
        self.update()

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.MouseButton.LeftButton and self.rect().contains(
            event.position().toPoint()
        ):
            self.toggle()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    @QtCore.Slot(bool)
    def _animate_thumb(self, checked: bool) -> None:
        self._thumb_animation.stop()
        self._thumb_animation.setStartValue(self._thumb_position)
        self._thumb_animation.setEndValue(1.0 if checked else 0.0)
        self._thumb_animation.start()

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        del event
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        track = self.rect().adjusted(1, 3, -1, -3)
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.setBrush(QtGui.QColor("#3b82f6") if self.isChecked() and self.isEnabled() else QtGui.QColor("#d5d5d5"))
        painter.drawRoundedRect(track, track.height() / 2, track.height() / 2)
        diameter = track.height() - 4
        minimum_x = track.left() + 2
        maximum_x = track.right() - diameter - 2
        x = round(minimum_x + (maximum_x - minimum_x) * self._thumb_position)
        painter.setBrush(QtGui.QColor("#ffffff"))
        painter.drawEllipse(x, track.top() + 2, diameter, diameter)
        painter.end()
