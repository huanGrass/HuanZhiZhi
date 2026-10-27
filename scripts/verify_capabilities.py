"""Exercise background input and OCR in an owned window via the task protocol.

Run with QT_QPA_PLATFORM=windows. A failed check keeps its evidence in the
report and exits nonzero; other input checks and OCR still run.
"""
import ctypes
from ctypes import wintypes
from datetime import datetime
import json
from pathlib import Path
import tempfile
import time

from PySide6 import QtCore, QtGui, QtWidgets

from huanzhizhi.platform.target_window import Win32DesktopApi
from huanzhizhi.tasks.process import TaskProcessManager
from huanzhizhi.tasks.protocol import FinishedEvent, PointEvent
from huanzhizhi.vision import ReadText, FindText


class Probe(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.events = []
        self.native_messages = []
        self.setWindowTitle('ZhiZhi Capability Test')
        self.setFixedSize(600, 350)
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)

    def nativeEvent(self, event_type, message):
        native = ctypes.cast(int(message), ctypes.POINTER(wintypes.MSG)).contents
        if 0x200 <= native.message <= 0x20A:
            self.native_messages.append(dict(message=hex(native.message), time=native.time,
                                             wparam=native.wParam, lparam=native.lParam))
        return False, 0

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


def run_probe(session):
    app = QtWidgets.QApplication([])
    if app.platformName() != 'windows':
        raise RuntimeError('需要 QT_QPA_PLATFORM=windows')
    window = Probe()
    output = Path(session.options['output'])
    report = dict(checks={}, input={})
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
        operations = (
            ('click', lambda: api.post_click(target, x, y, wait=wait),
             lambda events: ('down', 1) in events and ('up', 1) in events),
            ('right_click', lambda: api.post_click(target, x, y, 'right_click', wait=wait),
             lambda events: ('down', 2) in events and ('up', 2) in events),
            ('double_click', lambda: api.post_click(target, x, y, 'double_click', wait=wait),
             lambda events: events == [('down', 1), ('up', 1), ('double', 1), ('up', 1)]),
            ('drag', lambda: api.post_drag(target, x, y, target.width//2, target.height*4//5, .2, wait=wait),
             lambda events: ('down', 1) in events and ('move', 1) in events and ('up', 1) in events),
            ('scroll', lambda: api.post_scroll(target, x, y, -120),
             lambda events: ('wheel', -120) in events),
            ('key', lambda: api.post_key(target, 'ENTER', wait=wait),
             lambda events: any(e[0] == 'key' and e[1] == QtCore.Qt.Key.Key_Return for e in events)),
            ('text', lambda: api.post_text(target, '测试ABC', wait=wait),
             lambda events: all(any(e[0] == 'key' and e[2] == char for e in events) for char in '测试ABC')),
        )
        for name, operation, matches in operations:
            window.events.clear()
            window.native_messages.clear()
            try:
                operation()
                # Keep separate checks from becoming a double click together.
                wait(max(.3, app.styleHints().mouseDoubleClickInterval()/1000 + .05))
                report['checks'][name] = matches(window.events)
            except Exception as error:
                report['checks'][name] = False
                report.setdefault('errors', {})[name] = repr(error)
            report['input'][name] = dict(events=list(window.events), native_messages=list(window.native_messages))
            if name == 'double_click':
                messages = [item['message'] for item in window.native_messages
                            if item['message'] in ('0x201', '0x202', '0x203')]
                report['checks']['double_click_native'] = messages == ['0x201', '0x202', '0x203', '0x202']
        try:
            image = api.capture_client(target)
            image.save(output / 'window.png')
            english = ReadText(image, language='en-US')
            chinese = ReadText(image, language='zh-Hans-CN')
            report.update(english=english, chinese=chinese)
            report['checks']['ocr'] = ('73146' in english['text']
                                       and '通用能力测试' in chinese['text'].replace(' ', '')
                                       and bool(FindText(image, '73146', language='en-US')))
        except Exception as error:
            report['checks']['ocr'] = False
            report.setdefault('errors', {})['ocr'] = repr(error)
    except Exception as error:
        report['checks']['setup'] = False
        report.setdefault('errors', {})['setup'] = repr(error)
    finally:
        (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        api._capture.close()
        window.close()
        app.processEvents()
    failed = [name for name, passed in report['checks'].items() if not passed]
    session.emit_finished('验证失败' if failed else '已完成', '后台输入与 OCR 验证',
                          str(output / 'report.json'),
                          error='未通过：' + ', '.join(failed) if failed else None)


def main():
    app = QtCore.QCoreApplication([])
    output = Path(__file__).resolve().parents[1] / 'artifacts' / ('capabilities-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
    output.mkdir(parents=True)
    manager = TaskProcessManager()
    events, errors = [], []
    manager.event_received.connect(events.append)
    manager.error.connect(errors.append)
    with tempfile.TemporaryDirectory(prefix='huanzhizhi-capabilities-') as temporary:
        fixture = Path(temporary)
        (fixture / 'skill.json').write_text(json.dumps(dict(
            protocol_version=1, id=fixture.name, description='自建窗口能力验证',
            presentation='persistent')), encoding='utf-8')
        script = fixture / 'task.py'
        script.write_text(
            "import runpy\nfrom huanzhizhi.task_sdk import TaskSession\n"
            f"probe = runpy.run_path({str(Path(__file__).resolve())!r})\n"
            "probe['run_probe'](TaskSession(__file__))\n", encoding='utf-8')
        try:
            manager.start(fixture.name, script, {'output': str(output)}, input_mode='background')
            deadline = time.monotonic() + 90
            while manager.state != 'idle' and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(.01)
            finished = [event for event in events if isinstance(event, FinishedEvent)]
            points = [event for event in events if isinstance(event, PointEvent)]
            passed = (manager.state == 'idle' and not errors and len(finished) == 1
                      and finished[0].error is None and len(points) == 3)
            host = dict(state=manager.state, errors=errors, stderr=manager.stderr_text,
                        finished=[event.error for event in finished], feedback_points=len(points), passed=passed)
            (output / 'host.json').write_text(json.dumps(host, ensure_ascii=False, indent=2), encoding='utf-8')
            print(str(output / 'report.json'))
            print(json.dumps(host, ensure_ascii=False, indent=2))
        finally:
            manager.close()
            app.processEvents()
    if not passed:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
