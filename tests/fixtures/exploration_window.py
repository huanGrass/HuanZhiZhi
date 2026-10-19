"""Native, disposable two-page window for WGC/input integration probes."""
import json
from pathlib import Path
import sys

from PySide6 import QtCore, QtGui, QtWidgets


class ExplorationWindow(QtWidgets.QWidget):
    def __init__(self, output):
        super().__init__()
        self.output = output
        self.state = 0
        self.variant = 0
        self.setWindowTitle('ZhiZhi Exploration Test')
        self.setFixedSize(640, 400)
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.check_reset)
        self.timer.start(100)
        self.write_state()

    def write_state(self):
        self.output.write_text(json.dumps({'state': self.state, 'variant': self.variant, 'hwnd': int(self.winId())}), encoding='utf-8')

    def check_reset(self):
        marker = self.output.with_suffix('.reset')
        if marker.exists():
            marker.unlink()
            self.state = 0
            self.variant += 1
            self.write_state()
            self.update()

    def button(self):
        if self.variant:
            return QtCore.QRect(350, 185, 230, 90) if self.state == 0 else QtCore.QRect(70, 210, 230, 90)
        return QtCore.QRect(60, 220, 210, 90) if self.state == 0 else QtCore.QRect(370, 220, 210, 90)

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.fillRect(self.rect(), QtGui.QColor('#eff4ff' if self.state < 2 else '#d6ffe4'))
        painter.setPen(QtGui.QColor('#172b4d'))
        painter.setFont(QtGui.QFont('Arial', 24))
        painter.drawText(QtCore.QRect(30, 40, 580, 100), QtCore.Qt.AlignmentFlag.AlignCenter,
                         ['Step 1 of 2', 'Step 2 of 2', 'COMPLETE 73146'][self.state])
        painter.setFont(QtGui.QFont('Arial', 12))
        painter.drawText(30, 375, 'Run '+str(123+self.variant))
        painter.setFont(QtGui.QFont('Arial', 24))
        if self.state < 2:
            painter.fillRect(self.button(), QtGui.QColor('#245add'))
            painter.setPen(QtCore.Qt.GlobalColor.white)
            painter.drawText(self.button(), QtCore.Qt.AlignmentFlag.AlignCenter,
                             'Continue' if self.state == 0 else 'Finish')
        painter.end()

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.MouseButton.LeftButton and self.state < 2 and self.button().contains(event.position().toPoint()):
            self.state += 1
            self.write_state()
            self.update()


if __name__ == '__main__':
    app = QtWidgets.QApplication([])
    window = ExplorationWindow(Path(sys.argv[1]))
    window.show()
    QtCore.QTimer.singleShot(900_000, app.quit)
    app.exec()
