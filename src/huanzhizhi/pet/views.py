"""The task bubble, foot strip, and task-owned parameter form."""
import ctypes
from ctypes import wintypes
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets
from huanzhizhi.parameters import ChoiceParameter
from huanzhizhi.widgets import BUTTON_STYLE, SCROLLBAR_STYLE, TOOL_BUTTON_STYLE, ComboBox, SpinBox
from huanzhizhi.platform.window_policy import WindowPolicy


STYLE = BUTTON_STYLE + """QWidget { font-family: 'Microsoft YaHei UI'; font-size: 13px; color: #303541; }
QLabel#heading { font-size: 15px; font-weight: 600; }
QLabel#status { color: #63716b; }
QPlainTextEdit { background: transparent; border: none; padding: 0; }
"""


def setup_surface(widget, title):
    widget.setWindowTitle(title)
    widget.setWindowFlags(QtCore.Qt.WindowType.Tool | QtCore.Qt.WindowType.FramelessWindowHint
                          | QtCore.Qt.WindowType.WindowStaysOnTopHint)
    widget.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)
    widget.setAttribute(QtCore.Qt.WidgetAttribute.WA_ShowWithoutActivating)
    widget.setStyleSheet(STYLE)
    policy = WindowPolicy(widget)
    policy.set_input_passthrough(False, layered=True, no_activate=True)
    return policy


def paint_surface(widget, tail=False):
    painter = QtGui.QPainter(widget)
    painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
    painter.setPen(QtGui.QPen(QtGui.QColor('#dce2e5'), 1))
    painter.setBrush(QtGui.QColor('#ffffff'))
    rect = QtCore.QRectF(widget.rect()).adjusted(1, 1, -1, -1)
    if tail:
        rect.adjust(0, 0, 0, -9)
    painter.drawRoundedRect(rect, 13, 13)
    if tail:
        x = widget.tail_x
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.drawPolygon(QtGui.QPolygonF([QtCore.QPointF(x-8, rect.bottom()-1),
                                            QtCore.QPointF(x, rect.bottom()+9),
                                            QtCore.QPointF(x+8, rect.bottom()-1)]))




class StatusStrip(QtWidgets.QWidget):
    details_requested = QtCore.Signal()
    stop_requested = QtCore.Signal()
    pause_requested = QtCore.Signal()
    parameters_changed = QtCore.Signal(dict)

    def __init__(self):
        super().__init__()
        self.runtime_popup = None
        self._hotkey_registered = False
        self._hotkey_attempted = False
        self._stop_hotkey_registered = False
        self._stop_hotkey_attempted = False
        self._hotkey_user32 = None
        if QtGui.QGuiApplication.platformName() == 'windows':
            self._hotkey_user32 = ctypes.WinDLL('user32', use_last_error=True)
            self._hotkey_user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
            self._hotkey_user32.RegisterHotKey.restype = wintypes.BOOL
            self._hotkey_user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
            self._hotkey_user32.UnregisterHotKey.restype = wintypes.BOOL
        self.policy = setup_surface(self, '幻之之 · 运行状态')
        self.setObjectName('runtimeStrip')
        self.setStyleSheet(STYLE + """
            QPushButton#runtimeTask { color: #f0f1f6; background: transparent;
                border: none; padding: 4px 0; text-align: left; font-weight: 600; }
            QPushButton#runtimeTask:hover { color: #c7beff; }
            QLabel#runtimeStatus { color: #b9c1ce; }
            QLabel#runtimeDot { color: #54d493; }
            QPushButton#runtimeControlsButton, QPushButton#runtimePause, QPushButton#runtimeStop {
                border: 1px solid #505763; border-radius: 9px; padding: 6px 10px;
                color: #c9ced8; background: #303640; }
            QPushButton#runtimePause { background: #b9adf3; border-color: #c9bff9; color: #252039; }
            QPushButton#runtimeStop { color: #f19a9f; border-color: #785259; background: #373137; }
            QPushButton#runtimeControlsButton:hover { background: #3c4350; }
            QPushButton#runtimePause:hover { background: #cdc2ff; }
            QPushButton#runtimeStop:hover { background: #50383e; }
            QPushButton#runtimeControlsButton:disabled, QPushButton#runtimePause:disabled,
            QPushButton#runtimeStop:disabled { color: #777e8b; background: #2c3039; border-color: #414650; }
        """)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 12)
        layout.setSpacing(8)
        heading = QtWidgets.QHBoxLayout()
        heading.setSpacing(6)
        actions = QtWidgets.QHBoxLayout()
        actions.setSpacing(8)
        self.controls = QtWidgets.QPushButton('参数')
        self.controls.setObjectName('runtimeControlsButton')
        self.controls.setAccessibleName('运行控制')
        self.controls.setToolTip('显示或隐藏参数')
        self.controls.clicked.connect(self._toggle_controls)
        self.controls.hide()
        self.task = QtWidgets.QPushButton()
        self.task.setObjectName('runtimeTask')
        self.task.clicked.connect(self.details_requested)
        self.status = QtWidgets.QLabel()
        self.status.setObjectName('runtimeStatus')
        self.status.setTextFormat(QtCore.Qt.TextFormat.PlainText)
        self.status.setFixedWidth(88)
        self.dot = QtWidgets.QLabel('●')
        self.dot.setObjectName('runtimeDot')
        self.pause = QtWidgets.QPushButton('暂停')
        self.pause.setObjectName('runtimePause')
        self.pause.setAccessibleName('暂停任务')
        self.pause.clicked.connect(self.pause_requested)
        self.pause.setEnabled(False)
        self.stop = QtWidgets.QPushButton('停止')
        self.stop.setObjectName('runtimeStop')
        self.stop.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.stop.setProperty('danger', True)
        self.stop.clicked.connect(self.stop_requested)
        def tinted_icon(icon, color):
            pixmap = icon.pixmap(32, 32)
            painter = QtGui.QPainter(pixmap)
            painter.setCompositionMode(QtGui.QPainter.CompositionMode.CompositionMode_SourceIn)
            painter.fillRect(pixmap.rect(), QtGui.QColor(color))
            painter.end()
            pixmap.setDevicePixelRatio(2)
            return QtGui.QIcon(pixmap)
        self.controls.setIcon(tinted_icon(QtGui.QIcon(str(
            Path(__file__).parents[1] / 'assets/dock/sliders-horizontal.svg')), '#c9ced8'))
        self._pause_icon = tinted_icon(self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_MediaPause), '#252039')
        self._resume_icon = tinted_icon(self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_MediaPlay), '#252039')
        self.stop.setIcon(tinted_icon(self.style().standardIcon(QtWidgets.QStyle.StandardPixmap.SP_MediaStop), '#f19a9f'))
        heading.addWidget(self.task)
        heading.addStretch()
        heading.addWidget(self.dot)
        heading.addWidget(self.status)
        layout.addLayout(heading)
        divider = QtWidgets.QFrame()
        divider.setFixedHeight(1)
        divider.setStyleSheet('background: #424853;')
        layout.addWidget(divider)
        self.transparency = QtWidgets.QCheckBox('桌宠透明')
        self.transparency.setChecked(True)
        self.transparency.setToolTip('勾选后，桌宠在整个任务运行期间保持半透明')
        self.transparency.setStyleSheet('QCheckBox { color: #c9ced8; }')
        layout.addWidget(self.transparency)
        for button in (self.controls, self.pause, self.stop):
            button.setIconSize(QtCore.QSize(14, 14))
            button.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
            actions.addWidget(button, 1)
        layout.addLayout(actions)
        self.hotkey_hint = QtWidgets.QLabel('Esc 停止 · F8 暂停 / 继续')
        self.hotkey_hint.setStyleSheet('color: #b9c1ce; font-size: 11px;')
        layout.addWidget(self.hotkey_hint)
        self.setFixedWidth(292)
        self.adjustSize()

    def set_status(self, task, status, can_stop):
        self.task.ensurePolished()
        self.task.setFixedWidth(min(112, max(76, self.task.fontMetrics().horizontalAdvance(task) + 24)))
        self.task.setText(self.task.fontMetrics().elidedText(task, QtCore.Qt.TextElideMode.ElideRight, self.task.width() - 24))
        self.task.setToolTip(task)
        self.status.setText(self.status.fontMetrics().elidedText(status, QtCore.Qt.TextElideMode.ElideRight, 88))
        self.status.setToolTip(status)
        self.stop.setEnabled(can_stop)
        self.stop.setText('停止中…' if status == '停止中' else '停止')
        self.stop.setVisible(can_stop or status == '停止中')
        self.pause.setVisible(can_stop or status == '停止中')
        if not can_stop:
            self._release_stop_hotkey()
        elif self._hotkey_user32 is not None and not self._stop_hotkey_attempted:
            self._stop_hotkey_attempted = True
            self._stop_hotkey_registered = bool(self._hotkey_user32.RegisterHotKey(int(self.winId()), 2, 0x4000, 0x1B))
        self.stop.setToolTip('Esc：停止任务' if self._stop_hotkey_registered
                            else '停止任务' + ('；Esc 不可用，可能已被占用，请使用按钮' if self._stop_hotkey_attempted else ''))
        self._update_hotkey_hint()
        self.adjustSize()

    def set_pause_state(self, state):
        # Foreground input deliberately stops on manual mouse intervention.
        # F8 reaches pause without moving the cursor or weakening that guard.
        if state == 'unavailable':
            self._release_pause_hotkey()
        elif self._hotkey_user32 is not None and not self._hotkey_attempted:
            self._hotkey_attempted = True
            self._hotkey_registered = bool(self._hotkey_user32.RegisterHotKey(int(self.winId()), 1, 0x4000, 0x77))
        self.pause.setToolTip('F8：暂停 / 继续（当前操作核对完成后暂停）' if self._hotkey_registered
                             else '当前操作核对完成后暂停' + ('；F8 不可用，可能已被占用，请使用按钮' if self._hotkey_attempted else ''))
        paused = state == 'paused'
        self.pause.setText({'pausing': '暂停中…', 'resuming': '继续中…'}.get(
            state, '继续' if paused else '暂停'))
        self.pause.setIcon(QtGui.QIcon() if state in {'pausing', 'resuming'}
                           else self._resume_icon if paused else self._pause_icon)
        self.pause.setAccessibleName('继续任务' if paused else '暂停任务')
        self.pause.setEnabled(state in {'running', 'paused'})
        color = '#d9b576' if paused or state == 'pausing' else '#8f98a6' if state == 'unavailable' else '#54d493'
        self.dot.setStyleSheet(f'color: {color};')
        self._update_hotkey_hint()

    def _update_hotkey_hint(self):
        stop = 'Esc 不可用' if self._stop_hotkey_attempted and not self._stop_hotkey_registered else 'Esc 停止'
        pause = 'F8 不可用' if self._hotkey_attempted and not self._hotkey_registered else 'F8 暂停 / 继续'
        self.hotkey_hint.setText(f'{stop} · {pause}')

    def _release_stop_hotkey(self):
        if self._stop_hotkey_registered:
            self._hotkey_user32.UnregisterHotKey(int(self.winId()), 2)
        self._stop_hotkey_registered = False
        self._stop_hotkey_attempted = False

    def _release_pause_hotkey(self):
        if self._hotkey_registered:
            self._hotkey_user32.UnregisterHotKey(int(self.winId()), 1)
        self._hotkey_registered = False
        self._hotkey_attempted = False

    def nativeEvent(self, event_type, message):
        if self._hotkey_registered or self._stop_hotkey_registered:
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == 0x0312 and msg.wParam == 1 and self._hotkey_registered:  # WM_HOTKEY
                if self.pause.isEnabled():
                    self.pause_requested.emit()
                return True, 0
            if msg.message == 0x0312 and msg.wParam == 2 and self._stop_hotkey_registered:
                if self.stop.isEnabled():
                    self.stop_requested.emit()
                return True, 0
        return super().nativeEvent(event_type, message)

    def closeEvent(self, event):
        self._release_pause_hotkey()
        self._release_stop_hotkey()
        super().closeEvent(event)

    def set_runtime_parameters(self, parameters, values):
        if self.runtime_popup is not None:
            self.runtime_popup.close()
            self.runtime_popup.deleteLater()
        self.runtime_popup = RuntimeControlPopup(parameters, values, self) if parameters else None
        if self.runtime_popup is not None:
            self.runtime_popup.parameters_changed.connect(self.parameters_changed)
        self.controls.setVisible(bool(parameters))
        self.controls.setEnabled(False)
        self.adjustSize()

    def set_runtime_enabled(self, enabled):
        self.controls.setEnabled(enabled)
        if not enabled and self.runtime_popup is not None:
            self.runtime_popup.hide()

    def _toggle_controls(self):
        if self.runtime_popup is None:
            return
        if self.runtime_popup.isVisible():
            self.runtime_popup.hide()
        else:
            self.runtime_popup.adjustSize()
            self._position_controls()
            self.runtime_popup.show()

    def _position_controls(self):
        popup = self.runtime_popup
        if popup is None:
            return
        area = self.screen().availableGeometry()
        x = min(max(self.x(), area.left()), area.right() - popup.width() + 1)
        y = self.y() - popup.height() - 6
        if y < area.top():
            y = self.geometry().bottom() + 7
        y = max(area.top(), min(y, area.bottom() - popup.height() + 1))
        popup.move(x, y)

    def runtime_contains(self, point):
        popup = self.runtime_popup
        if popup is None or not popup.isVisible():
            return False
        active = QtWidgets.QApplication.activePopupWidget()
        return popup.geometry().contains(point) or (
            active is not None and popup.isAncestorOf(active) and active.geometry().contains(point))

    def moveEvent(self, event):
        super().moveEvent(event)
        if self.runtime_popup is not None and self.runtime_popup.isVisible():
            self._position_controls()

    def hideEvent(self, event):
        if self.runtime_popup is not None:
            self.runtime_popup.hide()
        super().hideEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        self.policy.sync_after_show()

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        rect = QtCore.QRectF(self.rect()).adjusted(1, 1, -1, -1)
        painter.setPen(QtGui.QPen(QtGui.QColor('#505763'), 1))
        painter.setBrush(QtGui.QColor(36, 41, 50, 248))
        painter.drawRoundedRect(rect, 14, 14)


def _parameter_editor(parameter, value):
    if isinstance(parameter, ChoiceParameter):
        editor = ComboBox()
        for option, label in parameter.choices:
            editor.addItem(label, option)
        editor.setCurrentIndex(editor.findData(value))
    else:
        editor = SpinBox()
        editor.setRange(parameter.minimum, parameter.maximum)
        editor.setValue(value)
        editor.setSuffix(parameter.suffix)
    editor.setObjectName(parameter.key)
    editor.setAccessibleName(parameter.label)
    editor.setAccessibleDescription(parameter.help)
    return editor


class RuntimeControlPopup(QtWidgets.QWidget):
    parameters_changed = QtCore.Signal(dict)

    def __init__(self, parameters, values, parent):
        super().__init__(parent, QtCore.Qt.WindowType.Tool | QtCore.Qt.WindowType.FramelessWindowHint
                         | QtCore.Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setWindowTitle('运行控制')
        self.setFixedWidth(280)
        self.setStyleSheet(STYLE + SCROLLBAR_STYLE + """
QScrollArea, QWidget#runtimeFields { background: transparent; border: none; }
""")
        self.editors = {}
        self._values = dict(values)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)
        scroll = QtWidgets.QScrollArea()
        scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        fields = QtWidgets.QWidget()
        fields.setObjectName('runtimeFields')
        rows = QtWidgets.QVBoxLayout(fields)
        rows.setContentsMargins(0, 0, 8, 0)
        rows.setSpacing(10)
        for parameter in parameters:
            row = QtWidgets.QHBoxLayout()
            row.setSpacing(10)
            label = QtWidgets.QLabel(parameter.label)
            label.setTextFormat(QtCore.Qt.TextFormat.PlainText)
            label.setWordWrap(True)
            label.setToolTip(parameter.help)
            editor = _parameter_editor(parameter, values[parameter.key])
            if isinstance(editor, QtWidgets.QComboBox):
                editor.setStyleSheet(editor.styleSheet() + 'QComboBox { min-width: 0; padding: 6px 28px 6px 8px; border-radius: 6px; }')
                editor.activated.connect(lambda _index, key=parameter.key: self._request_change(key))
            else:
                editor.setKeyboardTracking(False)
                editor.valueChanged.connect(lambda _value, key=parameter.key: self._request_change(key))
            label.setBuddy(editor)
            self.editors[parameter.key] = editor
            row.addWidget(label, 1)
            row.addWidget(editor)
            rows.addLayout(row)
        scroll.setWidget(fields)
        layout.addWidget(scroll)
        self.ensurePolished()
        for editor in self.editors.values():
            editor.ensurePolished()
            editor.setFixedWidth(136)
        scroll.setFixedHeight(min(236, max(rows.sizeHint().height(), rows.heightForWidth(self.width() - 28))))

    def _request_change(self, key):
        editor = self.editors[key]
        value = editor.currentData() if isinstance(editor, QtWidgets.QComboBox) else editor.value()
        if value != self._values[key]:
            self.parameters_changed.emit({key: value})

    def set_pending(self):
        for editor in self.editors.values():
            editor.setEnabled(False)

    def set_result(self, values):
        self._values.update(values)
        for key, editor in self.editors.items():
            with QtCore.QSignalBlocker(editor):
                if isinstance(editor, QtWidgets.QComboBox):
                    editor.setCurrentIndex(editor.findData(self._values[key]))
                else:
                    editor.setValue(self._values[key])
            editor.setEnabled(True)

    def keyPressEvent(self, event):
        if event.key() == QtCore.Qt.Key.Key_Escape:
            self.hide()
            event.accept()
        else:
            super().keyPressEvent(event)

    def paintEvent(self, event):
        paint_surface(self)


class ParameterDialog(QtWidgets.QDialog):
    def __init__(self, descriptor, values, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f'{descriptor.name} · 参数设置')
        self.setObjectName('parameterDialog')
        self.setStyleSheet(STYLE + """
QDialog#parameterDialog { background: #f6f7f9; }
QFrame#parameterCard { background: #ffffff; border: 1px solid #e2e6eb; border-radius: 12px; }
QLabel#parameterLabel { font-weight: 600; }
QLabel#parameterHelp { color: #747d89; font-size: 12px; }
QLabel#parameterError { color: #a64646; font-size: 12px; }
QFrame#parameterDivider { background: #edf0f3; border: none; }
""")
        self.setMinimumWidth(480)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 16)
        layout.setSpacing(16)
        self.editors = {}
        card = QtWidgets.QFrame()
        card.setObjectName('parameterCard')
        form = QtWidgets.QVBoxLayout(card)
        form.setContentsMargins(16, 4, 16, 4)
        form.setSpacing(0)
        for parameter in descriptor.parameters:
            if self.editors:
                divider = QtWidgets.QFrame()
                divider.setObjectName('parameterDivider')
                divider.setFixedHeight(1)
                form.addWidget(divider)
            row = QtWidgets.QHBoxLayout()
            row.setContentsMargins(0, 14, 0, 14)
            row.setSpacing(20)
            labels = QtWidgets.QVBoxLayout()
            labels.setSpacing(5)
            title = QtWidgets.QLabel(parameter.label)
            title.setObjectName('parameterLabel')
            title.setTextFormat(QtCore.Qt.TextFormat.PlainText)
            title.setWordWrap(True)
            labels.addWidget(title)
            spin = _parameter_editor(parameter, values[parameter.key])
            spin.setFixedWidth(max(132, spin.sizeHint().width()))
            title.setBuddy(spin)
            if parameter.help:
                label = QtWidgets.QLabel(parameter.help)
                label.setObjectName('parameterHelp')
                label.setWordWrap(True)
                label.setTextFormat(QtCore.Qt.TextFormat.PlainText)
                labels.addWidget(label)
            row.addLayout(labels, 1)
            row.addWidget(spin, 0, QtCore.Qt.AlignmentFlag.AlignVCenter)
            self.editors[parameter.key] = spin
            form.addLayout(row)
        layout.addWidget(card)
        if not self.editors:
            empty = QtWidgets.QLabel('此任务无需设置参数。')
            empty.setObjectName('parameterHelp')
            empty.setContentsMargins(0, 16, 0, 16)
            form.addWidget(empty)
        footer = QtWidgets.QHBoxLayout()
        footer.setSpacing(16)
        self.error_label = QtWidgets.QLabel()
        self.error_label.setObjectName('parameterError')
        self.error_label.setWordWrap(True)
        self.error_label.setTextFormat(QtCore.Qt.TextFormat.PlainText)
        footer.addWidget(self.error_label, 1)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Save
                                            | QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        save = buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Save)
        save.setText('保存')
        save.setProperty('primary', True)
        save.setDefault(True)
        buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Cancel).setText('取消')
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        footer.addWidget(buttons, 0, QtCore.Qt.AlignmentFlag.AlignBottom)
        layout.addLayout(footer)

    def values(self):
        return {key: editor.currentData() if isinstance(editor, QtWidgets.QComboBox) else editor.value()
                for key, editor in self.editors.items()}
