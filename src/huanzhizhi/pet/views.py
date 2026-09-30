"""The task bubble, foot strip, and task-owned parameter form."""
from PySide6 import QtCore, QtGui, QtWidgets
from huanzhizhi.platform.window_policy import WindowPolicy


STYLE = """QWidget { font-family: 'Microsoft YaHei UI'; font-size: 13px; color: #303541; }
QPushButton { background: #f1f3f6; border: 0; border-radius: 7px; padding: 7px 11px; }
QPushButton:hover { background: #e4e9f0; }
QPushButton:pressed { background: #d8e0ea; }
QPushButton:disabled { color: #969ba3; }
QPushButton[primary="true"] { background: #405d52; color: white; }
QPushButton[danger="true"] { color: #a64646; background: #f9eeee; }
QPushButton[danger="true"]:hover { background: #f2dada; }
QPushButton[danger="true"]:pressed { background: #e9bcbc; }
QPushButton[danger="true"]:disabled { color: #969ba3; background: #f1f3f6; }
QLabel#heading { font-size: 15px; font-weight: 600; }
QLabel#status { color: #63716b; }
QPlainTextEdit { background: transparent; border: none; padding: 0; }
QSpinBox { padding: 6px; }
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

    def __init__(self):
        super().__init__()
        self.policy = setup_surface(self, '幻之之 · 运行状态')
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(7, 6, 7, 6)
        layout.setSpacing(6)
        self.task = QtWidgets.QPushButton()
        self.task.setFixedWidth(148)
        self.task.clicked.connect(self.details_requested)
        self.status = QtWidgets.QLabel()
        self.status.setObjectName('status')
        self.status.setTextFormat(QtCore.Qt.TextFormat.PlainText)
        self.status.setFixedWidth(88)
        self.stop = QtWidgets.QPushButton('停止')
        self.stop.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.stop.setProperty('danger', True)
        self.stop.clicked.connect(self.stop_requested)
        layout.addWidget(self.task)
        layout.addWidget(self.status)
        layout.addWidget(self.stop)
        self.adjustSize()

    def set_status(self, task, status, can_stop):
        self.task.setText(self.task.fontMetrics().elidedText(task, QtCore.Qt.TextElideMode.ElideRight, 125))
        self.task.setToolTip(task)
        self.status.setText(self.status.fontMetrics().elidedText(status, QtCore.Qt.TextElideMode.ElideRight, 88))
        self.status.setToolTip(status)
        self.stop.setEnabled(can_stop)
        self.stop.setText('停止中…' if status == '停止中' else '停止')
        self.stop.setVisible(can_stop or status == '停止中')

    def showEvent(self, event):
        super().showEvent(event)
        self.policy.sync_after_show()

    def paintEvent(self, event):
        paint_surface(self)


class ParameterDialog(QtWidgets.QDialog):
    def __init__(self, descriptor, values, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f'{descriptor.name} · 参数设置')
        self.setStyleSheet(STYLE)
        self.setMinimumWidth(420)
        layout = QtWidgets.QVBoxLayout(self)
        self.editors = {}
        form = QtWidgets.QFormLayout()
        for parameter in descriptor.parameters:
            field = QtWidgets.QWidget()
            rows = QtWidgets.QVBoxLayout(field)
            rows.setContentsMargins(0, 0, 0, 8)
            spin = QtWidgets.QSpinBox()
            spin.setRange(parameter.minimum, parameter.maximum)
            spin.setValue(values[parameter.key])
            spin.setSuffix(parameter.suffix)
            spin.setObjectName(parameter.key)
            rows.addWidget(spin)
            if parameter.help:
                label = QtWidgets.QLabel(parameter.help)
                label.setWordWrap(True)
                label.setTextFormat(QtCore.Qt.TextFormat.PlainText)
                rows.addWidget(label)
            self.editors[parameter.key] = spin
            form.addRow(parameter.label, field)
        layout.addLayout(form)
        if not self.editors:
            layout.addWidget(QtWidgets.QLabel('此任务无需设置参数。'))
        self.error_label = QtWidgets.QLabel()
        self.error_label.setWordWrap(True)
        self.error_label.setTextFormat(QtCore.Qt.TextFormat.PlainText)
        layout.addWidget(self.error_label)
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Save
                                            | QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Save).setText('保存')
        buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Cancel).setText('取消')
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self):
        return {key: editor.value() for key, editor in self.editors.items()}
