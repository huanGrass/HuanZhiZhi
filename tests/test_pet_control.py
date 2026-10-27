"""Exercise ownership, pose geometry and the real skill subprocess channel."""
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6 import QtCore, QtWidgets, QtTest
from huanzhizhi.tasks.protocol import parse_event_line, ProtocolError, PetUpdateEvent
from huanzhizhi.tasks.component import TaskHostComponent
from tests.pet_factory import create_test_pet
from tests.skill_factory import write_skill_manifest


LEASE = 'a'*32


class PetControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.pet = create_test_pet(initial_position=QtCore.QPoint(120, 120))
        self.pet.show()
        self.home = self.pet.window.pos()

    def tearDown(self):
        self.pet.close()

    def test_protocol_rejects_invalid_coordinates_pose_token_and_extra_fields(self):
        message = dict(protocol_version=1, type='pet_update', lease=LEASE,
                       x=200, y=300, facing='left', pose='reach')
        self.assertIsInstance(parse_event_line(json.dumps(message).encode()), PetUpdateEvent)
        for changes in ({'x': True}, {'y': float('nan')}, {'x': 1e7}, {'facing': []},
                        {'pose': 'click'}, {'lease': 'wrong'}, {'extra': 1}):
            with self.subTest(changes=changes), self.assertRaises(ProtocolError):
                parse_event_line(json.dumps(message | changes).encode())

    def test_latest_state_wins_and_geometry_matches_rendered_contact(self):
        for theme in ('ufo', 'broom'):
            self.pet.set_theme(theme)
            geometry = self.pet.acquire_control(LEASE)
            self.assertEqual(geometry['coordinate_space'], 'qt_global_logical')
            self.pet.update_control(LEASE, 250, 250, 'right', 'move')
            self.pet.update_control(LEASE, 320, 290, 'left', 'reach')
            self.pet._flush_control()
            animation = self.pet._animation
            expected = geometry['poses']['left']['reach']['contact']
            self.assertAlmostEqual(animation.fingertip.x(), 320+expected[0], delta=.6)
            self.assertAlmostEqual(animation.fingertip.y(), 290+expected[1], delta=.6)
            position = self.pet.window.pos()
            self.pet.set_task_state('working')
            self.pet.show_point_at_click(100, 100)
            self.pet.begin_manual_point_at_click(100, 100)
            self.pet._animation.advance(time.monotonic()+8)
            self.assertEqual(self.pet.window.pos(), position)
            self.pet.release_control('b'*32)
            self.assertTrue(self.pet.control_active)
            self.pet.release_control(LEASE)
            self.assertEqual(self.pet.window.pos(), self.home)
            self.assertFalse(self.pet.control_active)
            self.assertFalse(self.pet._control_timer.isActive())
            self.pet.release_control(LEASE)

    def test_hidden_pet_rejects_acquisition_and_hide_revokes_once(self):
        revoked = []
        self.pet.control_revoked.connect(revoked.append)
        self.pet.acquire_control(LEASE)
        self.pet.update_control(LEASE, 300, 300, 'left', 'move')
        self.pet._flush_control()
        self.pet.hide()
        self.assertFalse(self.pet.control_active)
        self.assertEqual(self.pet.window.pos(), self.home)
        self.assertEqual(len(revoked), 1)
        self.pet.hide()
        self.assertEqual(len(revoked), 1)
        with self.assertRaisesRegex(RuntimeError, '显示桌宠'):
            self.pet.acquire_control(LEASE)

    def test_scale_theme_and_native_input_cannot_take_over_control(self):
        self.pet.acquire_control(LEASE)
        with self.assertRaises(RuntimeError):
            self.pet.set_theme('broom')
        with self.assertRaises(RuntimeError):
            self.pet.set_display_scale(.4)
        with mock.patch('huanzhizhi.platform.foreground_input.ForegroundMouse') as mouse:
            with self.assertRaises(RuntimeError):
                self.pet.start_foreground_input(None, lambda error: None)
            mouse.assert_not_called()
        with self.assertRaises(ValueError):
            self.pet.update_control(LEASE, 999999, 999999, 'left', 'idle')


class PetControlProcessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def wait_for(self, predicate, timeout=6):
        until = time.monotonic()+timeout
        while not predicate() and time.monotonic() < until:
            QtTest.QTest.qWait(10)
        self.assertTrue(predicate(), self.errors)

    def run_probe(self, ending):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)/'tasks'
            task = root/'接管测试'/'task.py'
            task.parent.mkdir(parents=True)
            task.write_text('''from huanzhizhi.task_sdk import TaskSession, describe
import os
if describe(__file__):
    raise SystemExit(0)
s = TaskSession(__file__)
g = s.acquire_pet()
assert g['coordinate_space'] == 'qt_global_logical'
for x in range(200, 320):
    s.update_pet(x, 260, facing='left', pose='reach')
s.emit_status('接管成功', '测试', 'ready')
'''+ending, encoding='utf-8')
            write_skill_manifest(task)
            pet = create_test_pet(initial_position=QtCore.QPoint(100, 100))
            pet.set_theme('broom')
            host = TaskHostComponent(pet, root)
            self.errors = []
            host.error.connect(self.errors.append)
            pet.show()
            home = pet.window.pos()
            statuses = []
            host.status_received.connect(lambda *args: statuses.append(args))
            try:
                host.start_task('接管测试')
                self.wait_for(lambda: bool(statuses))
                yield host, pet, home
                self.wait_for(lambda: host.process.state == 'idle')
                self.assertFalse(pet.control_active)
                # Completion poses change the canvas padding, not the saved home.
                self.assertEqual(pet._animation.home_geometry.topLeft(), home)
                self.wait_for(lambda: pet._animation.current_frame == 0)
                self.assertEqual(pet.window.pos(), home)
            finally:
                host.close()
                pet.close()

    def test_sdk_roundtrip_and_normal_release(self):
        for host, pet, home in self.run_probe("s.wait_for_stop(.15)\ns.release_pet()\ns.emit_finished('完成', '测试', 'done')\n"):
            self.assertTrue(pet.control_active)
            self.wait_for(lambda: pet.window.pos() != home)
        self.assertEqual(self.errors, [])

    def test_killed_skill_restores_pet_without_finally(self):
        for host, pet, home in self.run_probe("s.wait_for_stop(.15)\nos._exit(7)\n"):
            self.assertTrue(pet.control_active)
        self.assertTrue(self.errors)

    def test_hiding_pet_stops_owning_skill_and_clears_pending_frames(self):
        for host, pet, home in self.run_probe("s.wait_for_stop(10)\ns.release_pet()\ns.emit_finished('停止', '测试', 'done', stopped_by_request=True)\n"):
            pet.hide()
            self.assertFalse(pet.control_active)
            self.assertIsNone(pet._control_pending)
        self.assertEqual(self.errors, [])

    def test_stop_button_restores_pet(self):
        for host, pet, home in self.run_probe("s.wait_for_stop(10)\ns.release_pet()\ns.emit_finished('停止', '测试', 'done', stopped_by_request=True)\n"):
            host.stop_task()
            self.assertFalse(pet.control_active)
        self.assertEqual(self.errors, [])

    def test_display_change_revokes_and_stops_owning_skill(self):
        for host, pet, home in self.run_probe("s.wait_for_stop(10)\ns.release_pet()\ns.emit_finished('停止', '测试', 'done', stopped_by_request=True)\n"):
            pet.restore_display()
            self.assertFalse(pet.control_active)
        self.assertEqual(self.errors, [])

    def test_controlled_skill_acknowledges_host_pause_and_resume(self):
        ending = ("while not s.stop_requested:\n"
                  "    s.wait_if_paused()\n"
                  "    s.wait_for_stop(.02)\n"
                  "s.release_pet()\n"
                  "s.emit_finished('停止', '测试', 'done', stopped_by_request=True)\n")
        for host, pet, home in self.run_probe(ending):
            host.process.set_paused(True)
            self.wait_for(lambda: host.process.pause_state == 'paused')
            self.assertTrue(pet.control_active)
            host.process.set_paused(False)
            self.wait_for(lambda: host.process.pause_state == 'running')
            host.stop_task()
        self.assertEqual(self.errors, [])
