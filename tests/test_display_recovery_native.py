"""Run with QT_QPA_PLATFORM=windows to exercise native sleep-resume recovery."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import sys
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6 import QtCore, QtWidgets

from huanzhizhi.platform.display_recovery import DisplayRecovery
from tests.pet_factory import create_test_pet


@unittest.skipUnless(sys.platform == 'win32', 'Windows native window test')
class NativeDisplayRecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        if cls.app.platformName() != 'windows':
            raise unittest.SkipTest('requires QT_QPA_PLATFORM=windows')
        cls.api = ctypes.WinDLL('user32', use_last_error=True)
        cls.api.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        cls.api.SetWindowPos.argtypes = [
            wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, ctypes.c_int, wintypes.UINT,
        ]
        cls.api.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        cls.api.SendMessageW.restype = wintypes.LPARAM

    def setUp(self):
        root = Path(__file__).resolve().parents[1]
        self.pet = create_test_pet(initial_position=QtCore.QPoint(400, 300))
        self.window = self.pet.window
        self.window.setWindowTitle('DPI recovery test')
        self.window.setWindowOpacity(0)
        self.pet.show()
        self.app.processEvents()
        self.logs = []
        self.recovery = DisplayRecovery(self.window, self.pet.restore_display, self.logs.append)

    def tearDown(self):
        self.recovery.close()
        self.pet.close()
        self.app.processEvents()

    def native_size(self):
        rect = wintypes.RECT()
        self.assertTrue(self.api.GetClientRect(int(self.window.winId()), ctypes.byref(rect)))
        return QtCore.QSize(rect.right, rect.bottom)

    def resume(self):
        self.api.SendMessageW(int(self.window.winId()), 0x0218, 18, 0)
        self.api.SendMessageW(int(self.window.winId()), 0x0218, 7, 0)
        loop = QtCore.QEventLoop()
        QtCore.QTimer.singleShot(1500, loop.quit)
        loop.exec()
        self.assertEqual(len(self.logs), 1, self.logs)
        self.assertIn('已恢复', self.logs[0])

    def test_resume_repairs_native_shrink_despite_unchanged_fixed_constraints(self):
        intended = QtCore.QSize(self.window.size())
        native = self.native_size()
        self.assertTrue(self.api.SetWindowPos(
            int(self.window.winId()), None, 0, 0, native.width() // 2,
            native.height() // 2, 0x0016,
        ))
        self.app.processEvents()
        self.assertNotEqual(self.window.size(), intended)
        self.assertEqual(self.window.minimumSize(), intended)
        self.window.setFixedSize(intended)
        self.assertNotEqual(self.native_size(), native)  # Reproduces the no-op.
        self.resume()
        self.assertEqual(self.window.size(), intended)
        self.assertEqual(self.native_size(), native)
        self.assertTrue(self.window.isVisible())
        self.assertEqual(self.window.mask(), self.window._hit_mask)

    def test_resume_does_not_show_hidden_pet(self):
        self.pet.hide()
        self.resume()
        self.assertFalse(self.window.isVisible())


if __name__ == '__main__':
    unittest.main()
