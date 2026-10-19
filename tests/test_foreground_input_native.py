"""Opt-in real SendInput test, confined to a window created by this fixture.

Run with HUANZHIZHI_NATIVE_INPUT_TEST=1 and QT_QPA_PLATFORM=windows.
"""
from __future__ import annotations

from dataclasses import asdict
import json
import os
from pathlib import Path
import sys
import time
import unittest
import uuid

from PySide6 import QtCore, QtGui, QtTest, QtWidgets
from huanzhizhi.platform.target_window import Win32DesktopApi
from huanzhizhi.platform.foreground_input import ForegroundMouse
from huanzhizhi.platform.screen_coordinates import qt_to_physical_global
from huanzhizhi.tasks.protocol import InputRequestEvent
from tests.pet_factory import create_test_pet


class InputTarget(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.events = []
        self.pet = None
        self.cancel_on_press = False
        self.capture_path = None
        self.setWindowTitle('幻之之：前台输入验证窗口')
        self.setWindowFlag(QtCore.Qt.WindowType.WindowStaysOnTopHint)
        self.setMouseTracking(True)
        self.setStyleSheet('background: #f4f6fc;')

    def record(self, phase, event):
        self.events.append(dict(phase=phase, point=event.position().toPoint().toTuple(),
                                time=time.monotonic(), frame=self.pet._animation.current_frame if self.pet._animation else 0,
                                button=event.button().name))

    def mousePressEvent(self, event):
        self.record('press', event)
        if self.capture_path is not None:
            pos = self.mapToGlobal(QtCore.QPoint())
            self.screen().grabWindow(0, pos.x(), pos.y(), self.width(), self.height()).save(str(self.capture_path))
            self.capture_path = None
        if self.cancel_on_press:
            QtCore.QTimer.singleShot(10, self.pet.cancel_foreground_input)

    def mouseDoubleClickEvent(self, event):
        self.record('double', event)

    def mouseReleaseEvent(self, event):
        self.record('release', event)

    def mouseMoveEvent(self, event):
        if event.buttons():
            self.record('drag', event)

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QtGui.QPainter(self)
        painter.setPen(QtGui.QColor('#26354a'))
        painter.setFont(QtGui.QFont('Microsoft YaHei', 17))
        painter.drawText(30, 45, '前台输入验证 · 动画到位后按下，沿路径拖拽并松开')
        painter.setBrush(QtGui.QColor('#dce5ff'))
        painter.drawRoundedRect(QtCore.QRect(560, 250, 210, 100), 12, 12)
        painter.drawText(580, 305, '点击区域')
        painter.drawLine(360, 460, 760, 460)
        painter.drawText(360, 505, '拖拽验证路径')


@unittest.skipUnless(sys.platform == 'win32' and os.environ.get('HUANZHIZHI_NATIVE_INPUT_TEST') == '1',
                     'opt-in test sends real mouse input to its own fixture')
class NativeForegroundTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        if cls.app.platformName() != 'windows':
            raise unittest.SkipTest('requires QT_QPA_PLATFORM=windows')
        cls.root = Path(__file__).resolve().parents[1]
        cls.output = cls.root / 'output/foreground-input-qa'
        cls.output.mkdir(parents=True, exist_ok=True)

    def setUp(self):
        self.target = InputTarget()
        self.target.setGeometry(100, 100, 1100, 700)
        self.target.show()
        self.pet = create_test_pet(display_scale=.3, initial_position=QtCore.QPoint(1000, 360))
        self.target.pet = self.pet
        self.pet.show()
        self.target.raise_()
        self.target.activateWindow()
        QtTest.QTest.qWait(200)
        self.api = Win32DesktopApi(configure_dpi_awareness=False)
        # A shell-launched test lacks foreground activation permission. Click
        # its own exposed blank area once, just as a user starts the real app.
        info = self.api._read_window(int(self.target.winId()), require_visible=True)
        bootstrap = ForegroundMouse(info, 'click')
        point = qt_to_physical_global(self.target.mapToGlobal(QtCore.QPoint(30, 90))).toTuple()
        import ctypes.wintypes as wt
        handle = bootstrap.user32.WindowFromPhysicalPoint(wt.POINT(*point))
        self.assertEqual(bootstrap.user32.GetAncestor(handle, 2), info.hwnd)
        bootstrap._send(0x0002 | 0x0004, point)
        QtTest.QTest.qWait(100)
        import ctypes
        bootstrap.user32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int,
                                                  ctypes.c_int, ctypes.c_int, wt.UINT]
        self.assertTrue(bootstrap.user32.SetWindowPos(info.hwnd, -2, 0, 0, 0, 0, 0x0013))
        self.pet.window.raise_()

    def tearDown(self):
        self.pet.close()
        self.target.close()
        self.app.processEvents()

    def operation(self, theme, kind, cancel=False):
        self.pet.set_theme(theme)
        actor = self.pet._ensure_animation()
        home = actor.home_geometry.topLeft()
        self.target.events.clear()
        self.target.cancel_on_press = cancel
        self.target.capture_path = self.output / f'native-{theme}-{kind}{"-cancel" if cancel else ""}.png'
        info = self.api._read_window(int(self.target.winId()), require_visible=True)
        ratio = self.target.devicePixelRatioF()
        start = (620, 300) if kind != 'drag' else (360, 460)
        end = (760, 460) if kind == 'drag' else None
        request = InputRequestEvent(uuid.uuid4().hex, kind, asdict(info),
                                    round(start[0]*ratio), round(start[1]*ratio),
                                    None if end is None else round(end[0]*ratio),
                                    None if end is None else round(end[1]*ratio), .4 if end else 0)
        if kind == 'click':
            self.pet.show_hint((self.target.mapToGlobal(QtCore.QPoint(*start)),), 'click', '点击这里')
            QtTest.QTest.qWait(600)
            self.assertAlmostEqual(self.pet.window.windowOpacity(), .6, places=2)
            pos = self.target.mapToGlobal(QtCore.QPoint())
            self.target.screen().grabWindow(0, pos.x(), pos.y(), self.target.width(), self.target.height()).save(
                str(self.output / f'native-{theme}-hint.png'))
        done = []
        self.pet.start_foreground_input(request, lambda error: done.append((error, time.monotonic())))
        deadline = time.monotonic()+8
        while not done and time.monotonic() < deadline:
            QtTest.QTest.qWait(10)
        QtTest.QTest.qWait(80)
        record = dict(theme=theme, kind=kind, cancel=cancel, events=self.target.events,
                      result=[str(error) if error else None for error, _ in done])
        (self.output / f'native-{theme}-{kind}{"-cancel" if cancel else ""}.json').write_text(
            json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
        self.assertTrue(done, record)
        self.assertEqual(len(done), 1)
        if cancel:
            self.assertIsInstance(done[0][0], InterruptedError, record)
        else:
            self.assertIsNone(done[0][0], record)
        presses = [e for e in self.target.events if e['phase'] in ('press', 'double')]
        releases = [e for e in self.target.events if e['phase'] == 'release']
        count = 2 if kind == 'double_click' else 1
        self.assertEqual(len(presses), count, record)
        self.assertEqual(len(releases), count, record)
        self.assertEqual(presses[0]['point'], start, record)
        self.assertEqual(presses[0]['frame'], 12, record)
        if not cancel:
            self.assertEqual(releases[-1]['point'], end or start, record)
            self.assertGreaterEqual(releases[0]['time']-presses[0]['time'], .065, record)
        if kind == 'drag' and not cancel:
            self.assertGreater(len([e for e in self.target.events if e['phase'] == 'drag']), 5, record)
        self.assertFalse(self.pet.foreground_active)
        self.assertEqual(self.pet.window.windowOpacity(), 1.)
        # Includes the two-second nearby wait before the return flight.
        deadline = time.monotonic()+4
        while actor.visual_active and time.monotonic() < deadline:
            QtTest.QTest.qWait(10)
        self.assertFalse(actor.visual_active)
        self.assertEqual(self.pet.window.pos(), home)
        self.assertTrue(actor.mirrored)

    def test_themes_click_and_drag(self):
        for theme in ('ufo', 'broom'):
            for kind in ('click', 'drag'):
                with self.subTest(theme=theme, kind=kind):
                    self.operation(theme, kind)

    def test_right_double_and_cancel_during_hold(self):
        self.operation('ufo', 'right_click')
        self.operation('ufo', 'double_click')
        self.operation('broom', 'drag', cancel=True)


if __name__ == '__main__':
    unittest.main()
