"""Exercise pet input setup and animation coordinates without native input."""

import os
import math
from types import SimpleNamespace
from dataclasses import replace
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6 import QtCore, QtWidgets

from huanzhizhi.tasks.protocol import InputRequestEvent
from huanzhizhi.tasks.component import TaskHostComponent
from huanzhizhi.platform.foreground_input import ForegroundMouse
from tests.pet_factory import create_test_pet
from tests.test_foreground_mouse import FakeDesktopApi, FakeUser32


class ForegroundPetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        cls.assets = Path(__file__).parents[1] / 'src/huanzhizhi/assets'

    def setUp(self):
        self.pet = create_test_pet(display_scale=.3, initial_position=QtCore.QPoint(100, 100))
        self.pet.show()
        self.mouse = Mock()
        self.mouse.cursor_position.return_value = (700, 500)
        self.done = Mock()
        self.changed = []
        self.pet.foreground_changed.connect(self.changed.append)
        mouse = patch('huanzhizhi.platform.foreground_input.ForegroundMouse', return_value=self.mouse)
        mouse.start()
        self.addCleanup(mouse.stop)

    def tearDown(self):
        self.pet.close()
        self.pet.window.deleteLater()
        self.pet.speech_bubble.deleteLater()
        self.pet.point_overlay.deleteLater()
        self.app.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)

    @staticmethod
    def request(kind='click'):
        return InputRequestEvent(
            'a' * 32, kind,
            dict(hwnd=101, title='fixture', left=-300, top=100, width=1200, height=900),
            600, 300, 900 if kind == 'drag' else None, 600 if kind == 'drag' else None,
            .4 if kind == 'drag' else 0.,
        )

    def assert_failed_setup_is_clean(self):
        self.mouse.begin.assert_called_once()
        self.mouse.close.assert_called_once()
        self.mouse.step.assert_not_called()
        self.done.assert_called_once()
        self.assertIsInstance(self.done.call_args.args[0], Exception)
        self.assertFalse(self.pet.foreground_active)
        self.assertIsNone(self.pet._foreground_request_id)
        self.assertEqual(self.changed, [True, False])
        self.assertFalse(self.pet.window._window_policy.input_passthrough)
        self.pet.cancel_foreground_input()
        self.assertFalse(self.pet.foreground_active)
        self.mouse.close.assert_called_once()
        self.done.assert_called_once()

    def test_passthrough_setup_failure_releases_ownership_and_completes_once(self):
        original = self.pet._set_animation_passthrough

        def passthrough(enabled):
            if enabled:
                raise OSError('fixture native setup failure')
            original(enabled)

        self.pet._animation.motion_changed.disconnect(self.pet._set_animation_passthrough)
        with patch.object(self.pet, '_set_animation_passthrough', side_effect=passthrough):
            self.pet.start_foreground_input(self.request(), self.done)
            self.assert_failed_setup_is_clean()

    def test_first_coordinate_failure_restores_clickable_pet_and_completes_once(self):
        self.assertEqual(self.pet._animation.theme, "ufo")
        with patch('huanzhizhi.platform.screen_coordinates.physical_to_qt_global',
                   side_effect=OSError('fixture monitor mismatch')):
            self.pet.start_foreground_input(self.request(), self.done)
        self.assert_failed_setup_is_clean()
        self.pet._animation.advance(self.pet._animation._clock()+5)
        self.assertFalse(self.pet.window._window_policy.input_passthrough)

    def test_drag_physical_input_tracks_painted_fingertip_during_eased_motion(self):
        actor = self.pet._ensure_animation()
        now = [10.]
        actor._clock = lambda: now[0]
        drag_points = []

        def to_qt(x, y):
            return QtCore.QPoint(round((x+300)/1.5), round((y-100)/1.5))

        def to_physical(point):
            return QtCore.QPoint(round(point.x()*1.5-300), round(point.y()*1.5+100))

        def record(phase, position):
            if phase == 'drag':
                drag_points.append((position, to_physical(actor.fingertip).toTuple()))

        self.mouse.step.side_effect = record
        with patch('huanzhizhi.platform.screen_coordinates.physical_to_qt_global', side_effect=to_qt), \
                patch('huanzhizhi.platform.screen_coordinates.qt_to_physical_global', side_effect=to_physical):
            self.pet.start_foreground_input(self.request('drag'), self.done)
            for elapsed in (.2, .4, .48, .58, .68, .78, .88, 1.):
                now[0] = 10.+elapsed
                actor.advance(now[0])
        self.assertGreaterEqual(len(drag_points), 4)
        self.assertTrue(any(300 < point[0] < 600 for point, _ in drag_points))
        for position, fingertip in drag_points:
            with self.subTest(position=position, fingertip=fingertip):
                # Rendering rounds a logical window position before DPI scaling.
                self.assertLessEqual(max(abs(a-b) for a, b in zip(position, fingertip)), 2)
        self.done.assert_called_once_with(None)
        self.assertFalse(self.pet.foreground_active)
        self.mouse.close.assert_called_once()
        self.assertIn(('press', (300, 400)), [call.args for call in self.mouse.step.call_args_list])
        self.assertIn(('release', (600, 700)), [call.args for call in self.mouse.step.call_args_list])

    def test_global_stop_ends_post_release_wait_but_completed_request_cancel_does_not(self):
        self.pet.set_theme('ufo')
        actor = self.pet._animation
        now = [10.]
        actor._clock = lambda: now[0]
        home = QtCore.QPoint(self.pet.window.pos())
        self.pet.start_foreground_input(self.request(), self.done)
        for elapsed in (.51, .09, .6):
            now[0] += elapsed
            actor.advance()
        self.done.assert_called_once_with(None)
        self.assertFalse(self.pet.foreground_active)
        self.assertTrue(actor.visual_active)
        self.pet.cancel_foreground_input('a'*32)
        now[0] += .2
        actor.advance()
        self.assertNotEqual(self.pet.window.pos(), home)
        self.pet.cancel_foreground_input()
        now[0] += 1.3
        actor.advance()
        self.assertFalse(actor.visual_active)
        self.assertEqual(self.pet.window.pos(), home)
        self.done.assert_called_once_with(None)
        self.mouse.close.assert_called_once()

    def test_pause_and_stop_during_wait_return_without_replaying_retraction(self):
        for theme in ('ufo', 'broom'):
            for action in ('pause', 'stop'):
                with self.subTest(theme=theme, action=action):
                    self.pet.set_theme(theme)
                    actor = self.pet._ensure_animation()
                    now = [10.]
                    actor._clock = lambda: now[0]
                    home = QtCore.QPoint(self.pet.window.pos())
                    self.done.reset_mock()
                    self.mouse.reset_mock()
                    self.pet.start_foreground_input(self.request(), self.done)
                    for tick in range(1, 151):
                        now[0] = 10. + tick * .01
                        actor.advance()
                    self.done.assert_called_once_with(None)
                    self.assertFalse(self.pet.foreground_active)
                    self.assertEqual(actor.current_frame, 0)
                    self.assertNotEqual(self.pet.window.pos(), home)
                    self.mouse.step.reset_mock()
                    host = SimpleNamespace(
                        _pet=self.pet, _presentation=Mock(), _process=Mock(state='running'),
                        _active_descriptor=SimpleNamespace(key='test', name='test'),
                        _ensure_open=Mock(),
                    )
                    if action == 'pause':
                        TaskHostComponent._handle_pause_state(host, 'paused')
                    else:
                        TaskHostComponent.stop_task(host)
                    self.assertEqual(actor.current_frame, 22)
                    for _ in range(150):
                        now[0] += .01
                        actor.advance()
                        self.assertNotIn(actor.current_frame, (9, 10, 11, 12, 13))
                    self.assertEqual(self.pet.window.pos(), home)
                    self.assertFalse(actor.visual_active)
                    self.mouse.step.assert_not_called()
                    self.done.assert_called_once_with(None)

    def test_mouse_path_starts_at_cursor_and_stays_continuous_across_poses_and_actions(self):
        for theme in ('ufo', 'broom'):
            with self.subTest(theme=theme):
                self.pet.cancel_foreground_input()
                self.pet.set_theme(theme)
                actor = self.pet._ensure_animation()
                now = [10.]
                actor._clock = lambda: now[0]
                native, api = FakeUser32(), FakeDesktopApi()
                native.cursor = (700, 500)
                records, results = [], []

                def factory(info, kind):
                    mouse = ForegroundMouse(info, kind, api=api, user32=native)
                    step = mouse.step

                    def record(phase, point):
                        before = native.cursor
                        step(phase, point)
                        records.append((now[0], phase, before, native.cursor, actor.current_frame))
                    mouse.step = record
                    return mouse

                with patch('huanzhizhi.platform.foreground_input.ForegroundMouse', side_effect=factory), \
                        patch('huanzhizhi.platform.screen_coordinates.physical_to_qt_global',
                              side_effect=lambda x, y: QtCore.QPoint(x, y)):
                    # Follow-ups start during retreat, return, and after reaching home.
                    for index, gap in enumerate((0., .25, 8.5, 10.)):
                        now[0] += gap
                        actor.advance()
                        records.clear()
                        origin = native.cursor
                        request = replace(self.request(), x=600 if index % 2 == 0 else 950)
                        target = (request.window['left'] + request.x, 400)
                        began = now[0]
                        self.pet.start_foreground_input(request, results.append)
                        self.assertEqual(records[0][2:4], (origin, origin), 'first tick must not teleport')
                        for tick in range(1, 581):
                            now[0] = began + tick*.001
                            actor.advance()
                        moves = [item for item in records if item[1] == 'move']
                        self.assertTrue({7, 9, 10, 11}.issubset({item[4] for item in moves}))
                        self.assertEqual(moves[-1][3], target)
                        self.assertEqual(native.cursor, target)
                        # A 1 ms tick covers < 2 px here. Pose boundaries must
                        # obey that same bound, including multiple steps per tick.
                        for _, phase, before, after, frame in records:
                            self.assertLessEqual(math.dist(before, after), 3,
                                                 (theme, index, phase, frame, before, after))
                        now[0] = began + .7
                        actor.advance()
                        self.assertEqual(results, [None] * (index + 1))

    def test_manual_mouse_movement_still_cancels_smooth_motion_and_releases_drag(self):
        for during_drag in (False, True):
            with self.subTest(during_drag=during_drag):
                actor = self.pet._ensure_animation()
                now = [10.]
                actor._clock = lambda: now[0]
                native, api = FakeUser32(), FakeDesktopApi()
                results = []
                with patch('huanzhizhi.platform.foreground_input.ForegroundMouse',
                           side_effect=lambda info, kind: ForegroundMouse(info, kind, api=api, user32=native)), \
                        patch('huanzhizhi.platform.screen_coordinates.physical_to_qt_global',
                              side_effect=lambda x, y: QtCore.QPoint(x, y)):
                    self.pet.start_foreground_input(self.request('drag'), results.append)
                    now[0] += .41 if during_drag else .1
                    actor.advance()
                    self.assertEqual(1 in native.keys, during_drag)
                    native.cursor = (native.cursor[0] + 11, native.cursor[1])
                    before = len(native.delivered)
                    now[0] += .1
                    actor.advance()
                    self.assertEqual(len(results), 1)
                    self.assertIsInstance(results[0], InterruptedError)
                    self.assertIn('鼠标位置被改变', str(results[0]))
                    self.assertFalse(native.keys)
                    self.assertEqual(len(native.delivered), before + int(during_drag))
                    actor.advance(now[0] + 5)
                    self.assertEqual(len(results), 1)


if __name__ == '__main__':
    unittest.main()
