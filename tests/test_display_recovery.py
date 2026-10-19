from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6 import QtCore, QtGui, QtWidgets

from huanzhizhi.platform.display_recovery import DisplayRecovery


class DisplayRecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.window = QtWidgets.QWidget()
        self.window.resize(164, 246)
        self.window.winId()
        self.recovered = Mock()
        self.log = Mock()
        self.recovery = DisplayRecovery(self.window, self.recovered, self.log)

    def tearDown(self):
        self.recovery.close()
        self.window.close()
        self.window.deleteLater()
        self.app.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)

    def power_event(self, value):
        msg = wintypes.MSG()
        msg.message = 0x0218
        msg.wParam = value
        return self.recovery.nativeEventFilter(b'windows_generic_MSG', ctypes.addressof(msg))

    def test_resume_notifications_coalesce_and_suspend_cancels(self):
        self.recovery._user32 = Mock()
        for value in (18, 18, 7, 7):
            self.assertEqual(self.power_event(value), (False, 0))
        self.assertTrue(self.recovery._timer.isActive())
        self.assertEqual(self.recovery._attempts, 0)
        self.recovered.assert_not_called()
        self.power_event(4)
        self.assertFalse(self.recovery._timer.isActive())

    def test_geometry_waits_for_queued_qt_dpi_updates(self):
        order = []
        self.recovery._recovered = lambda: order.append('recovered')
        with patch.object(self.recovery, '_refresh_screens', side_effect=lambda: order.append('refresh')), \
             patch.object(self.recovery, '_verify_dpi', side_effect=lambda: order.append('verify')), \
             patch.object(self.recovery, '_restore_windows', side_effect=lambda: order.append('geometry')):
            self.recovery._recover()
            self.assertEqual(order, ['refresh'])
            self.app.processEvents()
            self.assertEqual(order, ['refresh', 'verify', 'geometry', 'recovered'])
        self.assertFalse(self.recovery._timer.isActive())
        self.log.assert_called_once()

    def test_mismatch_retries_then_recovers_without_using_stale_geometry(self):
        with patch.object(self.recovery, '_refresh_screens') as refresh, \
             patch.object(self.recovery, '_verify_dpi', side_effect=[RuntimeError('DPI mismatch'), None]), \
             patch.object(self.recovery, '_restore_windows') as restore:
            self.recovery._recover()
            self.app.processEvents()
            restore.assert_not_called()
            self.recovered.assert_not_called()
            self.assertTrue(self.recovery._timer.isActive())
            self.recovery._timer.stop()
            self.recovery._recover()
            self.app.processEvents()
            self.assertEqual(refresh.call_count, 2)
            restore.assert_called_once()
            self.recovered.assert_called_once()

    def test_unavailable_display_has_bounded_retries_and_logs_failure(self):
        with patch.object(self.recovery, '_refresh_screens', side_effect=OSError('display unavailable')) as refresh:
            for _ in range(10):
                self.recovery._timer.stop()
                self.recovery._recover()
            self.assertEqual(refresh.call_count, 10)
        self.assertFalse(self.recovery._timer.isActive())
        self.recovered.assert_not_called()
        self.assertIn('display unavailable', self.log.call_args.args[0])

    def test_native_200_percent_is_rejected_while_qt_stays_at_100_percent(self):
        self.recovery._user32 = Mock()
        self.recovery._user32.GetDpiForWindow.return_value = 192
        with patch.object(self.app, 'topLevelWidgets', return_value=[self.window]):
            with self.assertRaisesRegex(RuntimeError, '显示缩放尚未同步'):
                self.recovery._verify_dpi()
            self.recovery._user32.GetDpiForWindow.return_value = 96
            self.recovery._verify_dpi()

    def test_recovery_preserves_hidden_state_and_mask(self):
        mask = QtGui.QRegion(QtCore.QRect(10, 12, 70, 90))
        self.window.setMask(mask)
        self.window.hide()
        with patch.object(self.app, 'topLevelWidgets', return_value=[self.window]):
            self.recovery._restore_windows()
        self.assertFalse(self.window.isVisible())
        self.assertEqual(self.window.mask(), mask)
        self.assertEqual(self.window.size(), QtCore.QSize(164, 246))

    def test_close_cancels_queued_verification(self):
        with patch.object(self.recovery, '_refresh_screens'), \
             patch.object(self.recovery, '_restore_windows') as restore:
            self.recovery._recover()
            self.recovery.close()
            self.app.processEvents()
            self.recovery.schedule()
            self.assertFalse(self.recovery._timer.isActive())
            restore.assert_not_called()
            self.recovered.assert_not_called()


if __name__ == '__main__':
    unittest.main()
