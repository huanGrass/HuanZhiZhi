"""Background messages must follow the pet's painted contact and release."""
import os
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6 import QtCore, QtWidgets
from huanzhizhi.platform.foreground_input import BackgroundMouse
from huanzhizhi.platform.target_window import WindowInfo
from huanzhizhi.tasks.protocol import InputRequestEvent
from tests.pet_factory import create_test_pet


class BackgroundMouseTests(unittest.TestCase):
    def setUp(self):
        self.info = WindowInfo(123, 'fixture', -100, 50, 800, 600)
        self.api = Mock()

    def test_button_messages_and_client_coordinates_without_focus_or_cursor_calls(self):
        for kind, edges in (('click', [0x201, 0x202]), ('right_click', [0x204, 0x205]),
                            ('double_click', [0x201, 0x202, 0x203, 0x202])):
            with self.subTest(kind=kind):
                self.api.reset_mock()
                mouse = BackgroundMouse(self.info, kind, api=self.api)
                mouse.begin()
                mouse.step('move', (-900, 400))
                self.api._post_input.assert_not_called()
                for _ in range(len(edges)//2):
                    mouse.step('press', (-70, 90))
                    mouse.step('release', (-70, 90))
                mouse.close()
                calls = self.api._post_input.call_args_list
                self.assertEqual([c.args[1] for c in calls if c.args[1] != 0x200], edges)
                self.assertTrue(all(c.args[3] == (40 << 16) | 30 for c in calls))
                self.assertEqual({c[0] for c in self.api.mock_calls}, {'_require_unchanged', '_post_input'})

    def test_drag_cancellation_releases_at_last_client_position(self):
        mouse = BackgroundMouse(self.info, 'drag', api=self.api)
        mouse.begin()
        mouse.step('press', (-70, 90))
        mouse.step('drag', (0, 200))
        self.api._post_input.assert_called_with(self.info, 0x200, 1, (150 << 16) | 100)
        mouse.close()
        self.api._post_input.assert_called_with(self.info, 0x202, 0, (150 << 16) | 100)
        self.assertFalse(mouse.pressed)

    def test_window_movement_before_contact_never_sends_input(self):
        mouse = BackgroundMouse(self.info, 'click', api=self.api)
        mouse.begin()
        self.api._require_unchanged.side_effect = RuntimeError('target moved')
        with self.assertRaisesRegex(RuntimeError, 'target moved'):
            mouse.step('press', (-70, 90))
        mouse.close()
        self.api._post_input.assert_not_called()

    def test_failed_press_still_releases_the_button(self):
        mouse = BackgroundMouse(self.info, 'click', api=self.api)
        mouse.begin()
        self.api._post_input.side_effect = [None, OSError('post failed'), None]
        with self.assertRaises(OSError):
            mouse.step('press', (-70, 90))
        mouse.close()
        self.assertEqual([c.args[1] for c in self.api._post_input.call_args_list], [0x200, 0x201, 0x202])
        self.assertFalse(mouse.pressed)


class BackgroundAnimationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.pet = create_test_pet(initial_position=QtCore.QPoint(100, 100))
        self.pet.show()
        self.actor = self.pet._ensure_animation()
        self.now = 10.
        self.actor._clock = lambda: self.now
        self.api = Mock()
        self.messages = []
        self.api._post_input.side_effect = lambda info, message, flags, packed: self.messages.append(
            (message, packed, self.actor.current_frame, QtCore.QPointF(self.actor.fingertip), self.now))
        native = patch('huanzhizhi.platform.foreground_input.Win32DesktopApi', return_value=self.api)
        native.start()
        self.addCleanup(native.stop)
        foreground = patch('huanzhizhi.platform.foreground_input.ForegroundMouse',
                           side_effect=AssertionError('background must not use foreground input'))
        foreground.start()
        self.addCleanup(foreground.stop)
        coordinates = patch('huanzhizhi.platform.screen_coordinates.physical_to_qt_global',
                            side_effect=lambda x, y: QtCore.QPoint(x, y))
        coordinates.start()
        self.addCleanup(coordinates.stop)
        self.done = Mock()

    def tearDown(self):
        self.pet.close()
        self.pet.window.deleteLater()
        self.pet.speech_bubble.deleteLater()
        self.pet.point_overlay.deleteLater()
        self.app.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)

    def start(self, kind='click'):
        request = InputRequestEvent('a'*32, kind,
                                    dict(hwnd=123, title='fixture', left=0, top=0, width=1000, height=800),
                                    450, 350, 650 if kind == 'drag' else None,
                                    400 if kind == 'drag' else None, .4 if kind == 'drag' else 0.)
        self.pet.start_foreground_input(request, self.done, background=True)

    def advance(self, seconds):
        self.now += seconds
        self.actor.advance()

    def test_no_click_before_arrival_and_reply_only_after_release(self):
        self.start()
        self.advance(.499)
        self.assertEqual(self.messages, [])
        self.done.assert_not_called()
        self.advance(.002)
        self.assertEqual([m[0] for m in self.messages], [0x200, 0x201])
        self.assertEqual(self.messages[-1][2:4], (12, QtCore.QPointF(450, 350)))
        self.done.assert_not_called()
        self.advance(.151)
        self.assertEqual(self.messages[-1][0], 0x202)
        self.done.assert_called_once_with(None)
        self.assertFalse(self.pet.foreground_active)

    def test_stop_before_arrival_cannot_click_later(self):
        self.start()
        self.advance(.2)
        self.pet.cancel_foreground_input()
        self.advance(2.)
        self.assertEqual(self.messages, [])
        self.done.assert_called_once()
        self.assertIsInstance(self.done.call_args.args[0], InterruptedError)

    def test_stop_while_pressed_releases_once(self):
        self.start()
        self.advance(.501)
        self.pet.cancel_foreground_input()
        self.advance(2.)
        self.assertEqual([m[0] for m in self.messages], [0x200, 0x201, 0x202])
        self.done.assert_called_once()
        self.assertIsInstance(self.done.call_args.args[0], InterruptedError)

    def test_drag_sends_intermediate_positions_and_releases_at_destination(self):
        self.start('drag')
        for _ in range(110):
            self.advance(.01)
        self.assertEqual(self.messages[1][0], 0x201)
        self.assertEqual(self.messages[-1][0:2], (0x202, (400 << 16) | 650))
        travel = [m[1] for m in self.messages[2:-1] if m[0] == 0x200]
        self.assertGreater(len(travel), 5)
        self.assertTrue(any(450 < p & 0xffff < 650 for p in travel))
        self.done.assert_called_once_with(None)


if __name__ == '__main__':
    unittest.main()
