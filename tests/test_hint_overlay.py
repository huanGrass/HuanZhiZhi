from __future__ import annotations
import json
import os
import unittest
from unittest import mock
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6 import QtCore, QtWidgets
from huanzhizhi.pet.point_overlay import PointOverlayWindow
from huanzhizhi.tasks.protocol import HintEvent, ProtocolError, parse_event_line
from huanzhizhi.task_sdk import TaskSession

class HintOverlayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_protocol_and_sdk_coordinates(self):
        payload={'protocol_version':1,'type':'hint','kind':'drag','points':[[10,20],[30,20],[30,60]],'label':'向右拖'}
        event=parse_event_line(json.dumps(payload))
        self.assertIsInstance(event,HintEvent)
        self.assertEqual(event.points,((10,20),(30,20),(30,60)))
        for points in ([[True,20],[30,20],[30,60]],[[10,20],[30,40],[30,60]],[[10,20]]):
            with self.assertRaises(ProtocolError):parse_event_line(json.dumps({**payload,'points':points}))
        session=object.__new__(TaskSession)
        with mock.patch.object(session,'_emit_event') as emit:
            session.emit_hint([(10,20),(30,20),(30,60)],'drag','向右拖')
            emit.assert_called_once_with('hint',kind='drag',points=payload['points'],label='向右拖')

    def test_line_protocol_accepts_diagonal_and_rejects_degenerate_or_invalid_coordinates(self):
        payload={'protocol_version':1,'type':'hint','kind':'line','points':[[10,20],[300,450]],'label':'辅助线'}
        self.assertEqual(parse_event_line(json.dumps(payload)).kind,'line')
        for points in ([[10,20],[10,20]],[[True,20],[300,450]],[[10,20]],[[10,20],[30,40],[50,60]]):
            with self.assertRaises(ProtocolError):parse_event_line(json.dumps({**payload,'points':points}))
        session=object.__new__(TaskSession)
        with mock.patch.object(session,'_emit_event') as emit:
            session.emit_hint([(10,20),(300,450)],'line','辅助线')
            self.assertEqual(parse_event_line(json.dumps({'protocol_version':1,**emit.call_args.kwargs,'type':'hint'})).points,((10,20),(300,450)))

    def test_line_renders_without_sprite_and_expires_quickly(self):
        overlay=PointOverlayWindow()
        try:
            points=(QtCore.QPoint(20,30),QtCore.QPoint(300,220))
            overlay.show_hint(points,'line','辅助线')
            self.app.processEvents()
            self.assertFalse(overlay._hint_timer.isActive())
            self.assertEqual(overlay.return_idle_timer.interval(),200)
            self.assertTrue(overlay.windowFlags() & QtCore.Qt.WindowType.WindowTransparentForInput)
            self.assertFalse(overlay.grab().isNull())
            loop=QtCore.QEventLoop()
            QtCore.QTimer.singleShot(260,loop.quit)
            loop.exec()
            self.assertFalse(overlay.isVisible())
            self.assertFalse(overlay.hint_points)
            overlay.show_hint((QtCore.QPoint(10,10),),'click','点击')
            self.assertEqual(overlay.return_idle_timer.interval(),1800)
        finally:overlay.close()

    def test_hint_is_click_through_animated_and_clears_on_finish(self):
        overlay=PointOverlayWindow()
        try:
            points=(QtCore.QPoint(200,150),QtCore.QPoint(300,150),QtCore.QPoint(300,220))
            overlay.show_hint(points,'drag','向右拖一格')
            self.app.processEvents()
            self.assertTrue(overlay._hint_timer.isActive())
            self.assertTrue(overlay.windowFlags() & QtCore.Qt.WindowType.WindowTransparentForInput)
            self.assertEqual(overlay.hint_points,points)
            self.assertFalse(overlay.grab().isNull())
            self.assertEqual(overlay.hint_points,points)
            overlay._finish()
            self.assertFalse(overlay._hint_timer.isActive())
            self.assertFalse(overlay.hint_points)
            self.assertFalse(overlay.isVisible())
            self.assertFalse(overlay.return_idle_timer.isActive())
        finally:overlay.close()

if __name__=='__main__':unittest.main()
