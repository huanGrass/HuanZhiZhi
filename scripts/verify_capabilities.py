"""Exercise native input and OCR only in a disposable, owned test window."""
import json
from pathlib import Path
import time

from PySide6 import QtCore, QtGui, QtWidgets

from huanzhizhi.platform.target_window import Win32DesktopApi
from huanzhizhi.vision import ReadText, FindText


class Probe(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.events = []
        self.setWindowTitle('ZhiZhi Capability Test')
        self.setFixedSize(600, 350)
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.fillRect(self.rect(), QtCore.Qt.GlobalColor.white)
        painter.setPen(QtCore.Qt.GlobalColor.black)
        painter.setFont(QtGui.QFont('Microsoft YaHei', 28))
        painter.drawText(30, 70, 'Hello 73146')
        painter.drawText(30, 140, '通用能力测试')

    def mousePressEvent(self, event):
        self.events.append(('down', event.button().value))

    def mouseReleaseEvent(self, event):
        self.events.append(('up', event.button().value))

    def mouseDoubleClickEvent(self, event):
        self.events.append(('double', event.button().value))

    def mouseMoveEvent(self, event):
        self.events.append(('move', event.buttons().value))

    def wheelEvent(self, event):
        self.events.append(('wheel', event.angleDelta().y()))

    def keyPressEvent(self, event):
        self.events.append(('key', event.key(), event.text()))


def main():
    app = QtWidgets.QApplication([])
    window = Probe()
    output = Path(__file__).resolve().parents[1] / 'artifacts' / ('capabilities-'+time.strftime('%Y%m%d-%H%M%S'))
    output.mkdir(parents=True)
    window.show()
    def wait(seconds):
        deadline = time.monotonic()+seconds
        while time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.005)
    api = Win32DesktopApi(configure_dpi_awareness=False)
    try:
        wait(.4)
        target = next(item for item in api.list_windows() if item.hwnd == int(window.winId()))
        x, y = target.width//4, target.height*3//4
        api.post_left_click(target, x, y)
        api.post_click(target, x, y, 'right_click', wait=wait)
        api.post_click(target, x, y, 'double_click', wait=wait)
        api.post_drag(target, x, y, target.width//2, target.height*4//5, .2, wait=wait)
        api.post_scroll(target, x, y, -120)
        api.post_key(target, 'ENTER', wait=wait)
        api.post_text(target, '测试ABC', wait=wait)
        wait(.3)
        events = window.events
        assert ('down', 1) in events and ('up', 1) in events, events
        assert ('down', 2) in events and ('up', 2) in events, events
        assert ('double', 1) in events and ('move', 1) in events, events
        assert ('wheel', -120) in events, events
        assert any(e[0] == 'key' and e[1] == QtCore.Qt.Key.Key_Return for e in events), events
        for char in '测试ABC':
            assert any(e[0] == 'key' and e[2] == char for e in events), events
        image = api.capture_client(target)
        image.save(output / 'window.png')
        english = ReadText(image, language='en-US')
        chinese = ReadText(image, language='zh-Hans-CN')
        assert '73146' in english['text'], english
        assert '通用能力测试' in chinese['text'].replace(' ', ''), chinese
        assert FindText(image, '73146', language='en-US')
        report = dict(input='passed', ocr='passed', events=events, english=english, chinese=chinese)
        (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(str(output / 'report.json'))
    finally:
        api._capture.close()
        window.close()
        app.processEvents()


if __name__ == '__main__':
    main()
