"""Contact timing and one-window animation, without sending native input."""

import os
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore, QtGui, QtWidgets

from huanzhizhi.pet.animation import AnimationCancelled, PetAnimationController
from huanzhizhi.pet.window import PetWindow


class Clock:
    def __init__(self):
        self.now = 0.

    def __call__(self):
        return self.now


class PetAnimationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        cls.assets = Path(__file__).parents[1] / "src/huanzhizhi/assets"

    def setUp(self):
        self.clock = Clock()
        self.window = PetWindow()
        self.window.move(100, 100)
        self.window.show()
        self.actor = PetAnimationController(self.window, theme="ufo", clock=self.clock)
        self.home = QtCore.QPoint(self.window.pos())
        self.events = []
        self.results = []

    def tearDown(self):
        self.actor.close()
        self.window.close()
        self.window.deleteLater()
        self.app.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)

    def advance(self, seconds):
        self.clock.now += seconds
        self.actor.advance()

    def step(self, phase, point, progress):
        self.events.append((phase, QtCore.QPointF(point), progress, self.actor.current_frame, self.clock.now))

    def start(self, kind="click", target=QtCore.QPoint(450, 350), end=None, **kwargs):
        self.actor.start_action(kind, target, end, on_step=kwargs.pop("on_step", self.step),
                                on_done=kwargs.pop("on_done", self.results.append), **kwargs)

    def test_press_is_drawn_before_input_and_done_follows_release(self):
        ordering = []
        self.window.repaint = lambda: ordering.append(("paint", self.actor.current_frame))

        def step(phase, point, progress):
            ordering.append((phase, self.actor.current_frame))
            self.step(phase, point, progress)
            if phase == "press":
                self.assertEqual(self.actor.fingertip, point)

        self.start(on_step=step)
        self.advance(.499)
        self.assertFalse(any(e[0] == "press" for e in self.events))
        self.assertTrue(self.actor.busy)
        self.advance(.002)
        self.assertEqual([e[0] for e in self.events if e[0] in ("press", "release")], ["press"])
        press_index = ordering.index(("press", 12))
        self.assertEqual(ordering[press_index-1], ("paint", 12))
        self.assertEqual(self.results, [])
        self.advance(.149)
        self.assertEqual(self.actor.current_frame, 12)
        self.assertEqual(self.events[-1][0], "press")
        self.assertEqual(self.results, [])
        self.advance(.002)
        self.assertEqual(self.results, [None])
        self.assertFalse(self.actor.busy)
        self.assertTrue(self.actor.visual_active)
        self.assertEqual(self.events[-1][0], "release")
        self.assertEqual(self.events[-1][1], QtCore.QPointF(450, 350))
        release_index = next(i for i, item in enumerate(ordering) if item[0] == "release")
        self.assertEqual(ordering[release_index-1][0], "paint")

    def test_runtime_transparency_survives_actions_cancel_theme_and_feedback(self):
        for transparent, opacity in ((True, .6), (False, 1.)):
            with self.subTest(transparent=transparent):
                self.actor.set_runtime_transparency(True, transparent)
                self.assertAlmostEqual(self.window.windowOpacity(), opacity, delta=.01)
                self.start()
                for _ in range(600):
                    self.advance(.02)
                self.assertFalse(self.actor.visual_active)
                self.assertAlmostEqual(self.window.windowOpacity(), opacity, delta=.01)
                self.actor.show_hint([QtCore.QPoint(500, 340)])
                self.actor.cancel(return_home=False)
                self.assertAlmostEqual(self.window.windowOpacity(), opacity, delta=.01)
                self.actor.set_theme('broom')
                self.assertAlmostEqual(self.window.windowOpacity(), opacity, delta=.01)
                self.actor.show_feedback(QtCore.QPoint(450, 350), hold=True)
                self.assertAlmostEqual(self.window.windowOpacity(), opacity, delta=.01)
        self.actor.set_runtime_transparency(True, True)
        self.actor.set_runtime_transparency(False, True)
        self.assertAlmostEqual(self.window.windowOpacity(), 1., delta=.01)

    def test_runtime_transparency_can_switch_during_an_action(self):
        self.actor.set_runtime_transparency(True, True)
        self.start()
        self.advance(.2)
        self.actor.set_runtime_transparency(True, False)
        self.assertAlmostEqual(self.window.windowOpacity(), 1., delta=.01)
        self.assertTrue(self.actor.busy)
        self.actor.set_runtime_transparency(True, True)
        self.assertAlmostEqual(self.window.windowOpacity(), .6, delta=.01)
        self.assertTrue(self.actor.busy)

    def test_drag_holds_contact_pose_and_releases_at_exact_end(self):
        self.start("drag", QtCore.QPoint(420, 330), QtCore.QPoint(650, 410), duration=.3)
        for _ in range(50):
            self.advance(.02)
        contact = [e for e in self.events if e[0] in ("press", "release")]
        self.assertEqual([e[0] for e in contact], ["press", "release"])
        self.assertEqual(contact[0][1], QtCore.QPointF(420, 330))
        self.assertEqual(contact[1][1], QtCore.QPointF(650, 410))
        drag = [e for e in self.events if e[0] == "drag"]
        self.assertGreater(len(drag), 5)
        self.assertTrue(all(e[3] == 12 for e in drag))
        for _, point, progress, _, _ in drag:
            expected = QtCore.QPointF(420+230*progress, 330+80*progress)
            self.assertLessEqual((point-expected).manhattanLength(), 1.)
        self.assertEqual(drag[-1][1], QtCore.QPointF(650, 410))
        self.assertEqual(self.results, [None])

    def test_double_click_has_two_press_release_pairs_and_one_done(self):
        self.start("double_click")
        for _ in range(55):
            self.advance(.02)
        contact = [e for e in self.events if e[0] in ("press", "release")]
        self.assertEqual([e[0] for e in contact], ["press", "release", "press", "release"])
        self.assertGreaterEqual(contact[2][4]-contact[1][4], .059)
        self.assertLessEqual(contact[2][4]-contact[1][4], .101)
        self.assertEqual(self.results, [None])

    def test_cancel_invalidates_future_press_and_notifies_only_once(self):
        self.start()
        self.advance(.2)
        self.actor.cancel()
        self.actor.cancel()
        self.advance(5)
        self.assertEqual(len(self.results), 1)
        self.assertIsInstance(self.results[0], AnimationCancelled)
        self.assertFalse(any(e[0] == "press" for e in self.events))
        self.assertFalse(self.actor.busy)

    def test_step_error_cancels_remaining_input(self):
        error = RuntimeError("native input failed")

        def step(phase, point, progress):
            self.step(phase, point, progress)
            if phase == "press":
                raise error

        self.start(on_step=step)
        self.advance(.51)
        self.advance(5)
        self.assertEqual(self.results, [error])
        self.assertEqual([e[0] for e in self.events if e[0] in ("press", "release")], ["press"])

    def test_done_can_start_next_action_without_old_return_overwriting_it(self):
        first = []

        def done(error):
            first.append(error)
            self.start(target=QtCore.QPoint(610, 280))

        self.start(on_done=done)
        self.advance(.501)
        self.advance(.151)
        self.assertEqual(first, [None])
        self.assertTrue(self.actor.busy)
        self.advance(.501)
        self.advance(.151)
        self.assertEqual(self.results, [None])
        presses = [e[1] for e in self.events if e[0] == "press"]
        self.assertEqual(presses, [QtCore.QPointF(450, 350), QtCore.QPointF(610, 280)])

    def test_one_window_returns_to_home_and_reports_transient_motion(self):
        active, positions = [], []
        self.actor.motion_changed.connect(active.append)
        self.actor.position_changed.connect(positions.append)
        actors_before = [w for w in self.app.topLevelWidgets() if isinstance(w, PetWindow)]
        self.start()
        self.advance(.501)
        self.advance(.151)
        self.assertEqual(active, [True])
        self.assertFalse(self.actor.busy)
        self.advance(9)
        self.assertEqual(self.window.pos(), self.home)
        self.assertEqual(active, [True, False])
        self.assertGreater(len(positions), 2)
        self.assertEqual([w for w in self.app.topLevelWidgets() if isinstance(w, PetWindow)], actors_before)

    def test_idle_changes_pose_and_does_not_drift_after_full_cycles(self):
        poses = set()
        for _ in range(108):
            self.advance(.1)
            poses.add(self.actor.current_frame)
        self.assertEqual(poses, {0, 1})
        self.advance(.1)
        self.assertEqual(self.actor.current_frame, 0)
        self.assertEqual(self.window.pos(), self.home)

    def test_background_feedback_is_single_actor_and_emits_no_input(self):
        self.actor.show_feedback(QtCore.QPoint(430, 380), hold=True)
        self.assertFalse(self.actor.busy)
        self.assertTrue(self.actor.visual_active)
        self.assertEqual(self.actor.fingertip, QtCore.QPointF(430, 380))
        self.actor.move_feedback(QtCore.QPoint(480, 410))
        self.assertEqual(self.actor.fingertip, QtCore.QPointF(480, 410))
        self.actor.release_feedback(QtCore.QPoint(490, 420))
        self.advance(9)
        self.assertEqual(self.window.pos(), self.home)
        self.assertEqual(self.events, [])
        self.assertFalse(self.actor.visual_active)

    def test_edge_mirror_preserves_exact_contact(self):
        area = self.window.screen().availableGeometry()
        for x, expected in ((area.left()+1, True), (area.right()-1, False)):
            self.actor.show_feedback(QtCore.QPoint(x, area.center().y()), hold=True)
            self.assertEqual(self.actor.mirrored, expected)
            self.assertEqual(self.actor.fingertip, QtCore.QPointF(x, area.center().y()))

    def test_approach_near_desktop_edge_keeps_cursor_on_desktop(self):
        area = self.window.screen().geometry()
        target = QtCore.QPoint(area.left()+1, area.top()+1)
        self.start(target=target)
        for _ in range(32):
            self.advance(.02)
        for phase, point, *_ in self.events:
            self.assertTrue(any(screen.geometry().contains(point.toPoint()) for screen in self.app.screens()),
                            f"{phase} emitted an off-desktop cursor position {point}")
        self.assertEqual([e[1] for e in self.events if e[0] == "press"], [QtCore.QPointF(target)])

    def test_theme_scale_and_contact_assets_work(self):
        for theme in ("ufo", "broom"):
            self.actor.set_theme(theme)
            self.actor.set_display_scale(.56)
            self.actor.show_feedback(QtCore.QPoint(510, 390), hold=True)
            self.assertEqual(self.actor.fingertip, QtCore.QPointF(510, 390))
            self.assertFalse(self.window._pixmap.isNull())
            self.actor.set_display_scale(.32)
            self.assertFalse(self.actor.visual_active)

    def test_theme_switch_retains_user_home_and_final_idle_position(self):
        user_home = QtCore.QPoint(220, 140)
        self.window.move(user_home)
        self.actor.set_home(user_home)
        for theme in ("broom", "ufo"):
            self.actor.set_theme(theme)
            self.assertEqual(self.window.pos(), user_home)
            self.actor.show_feedback(QtCore.QPoint(530, 390))
            self.advance(9)
            self.assertEqual(self.actor.current_frame, 0)
            self.assertEqual(self.window.pos(), user_home)
            self.advance(5.4)
            self.assertEqual(self.window.pos(), user_home)

    def test_scale_during_feedback_restores_original_home_and_final_idle(self):
        user_home = QtCore.QPoint(230, 150)
        self.window.move(user_home)
        self.actor.set_home(user_home)
        for scale in (.56, .19, .32):
            self.actor.show_feedback(QtCore.QPoint(530, 390), hold=True)
            self.actor.set_display_scale(scale)
            self.assertFalse(self.actor.visual_active)
            self.assertEqual(self.actor.current_frame, 0)
            self.assertEqual(self.window.pos(), user_home)
            self.advance(5.4)
            self.assertEqual(self.actor.current_frame, 0)
            self.assertEqual(self.window.pos(), user_home)

    def test_hint_uses_guide_pose_without_input_and_hiding_stops_timer(self):
        self.actor.show_hint([QtCore.QPoint(420, 300)])
        self.advance(.5)
        self.assertEqual(self.actor.current_frame, 14)
        self.assertEqual(self.actor.fingertip, QtCore.QPointF(420, 300))
        self.assertFalse(self.actor.busy)
        self.actor.set_visible(False)
        self.assertFalse(self.actor._timer.isActive())
        self.assertFalse(self.actor.visual_active)
        self.assertFalse(self.window.isVisible())

    def test_repeated_hint_refresh_keeps_arrival_and_next_point_progress(self):
        points = [QtCore.QPoint(420, 300), QtCore.QPoint(560, 350)]
        visited = set()
        for _ in range(35):
            self.actor.show_hint(points)
            self.advance(.1)
            if self.actor.current_frame == 14:
                visited.add(self.actor.fingertip.toPoint().toTuple())
        self.assertEqual(visited, {point.toTuple() for point in points})
        self.assertFalse(self.actor.busy)
        self.assertFalse(self.events)

        self.actor.clear_hint()
        self.advance(10.)
        self.assertEqual(self.window.pos(), self.home)
        self.actor.show_hint(points)
        self.advance(.5)
        self.assertEqual(self.actor.current_frame, 14)
        self.assertEqual(self.actor.fingertip, QtCore.QPointF(points[0]))

    def test_delayed_tick_does_not_compress_press_release_holds(self):
        self.start("double_click")
        self.advance(5)
        self.assertEqual([e[0] for e in self.events if e[0] in ("press", "release")], ["press"])
        self.assertTrue(self.actor.busy)
        self.advance(.149)
        self.assertEqual(self.results, [])
        self.assertEqual([e[0] for e in self.events if e[0] == "release"], [])
        self.advance(.002)
        self.advance(.081)
        self.advance(.151)
        self.assertEqual([e[0] for e in self.events if e[0] in ("press", "release")],
                         ["press", "release", "press", "release"])
        self.assertEqual(self.results, [None])
        self.advance(9)
        self.assertEqual(self.window.pos(), self.home)

    def place_home(self, center_x, y=80):
        home = QtCore.QPoint(round(center_x-self.actor.home_geometry.width()/2), y)
        self.window.move(home)
        self.actor.set_home(home)
        return home

    def test_home_faces_inward_and_center_dead_zone_keeps_previous_direction(self):
        area = self.window.screen().availableGeometry()
        for theme in ("ufo", "broom"):
            self.actor.set_theme(theme)
            for fraction, expected in ((.25, False), (.75, True), (.48, True),
                                       (.52, True), (.25, False), (.52, False)):
                with self.subTest(theme=theme, fraction=fraction, expected=expected):
                    home = self.place_home(area.left()+area.width()*fraction)
                    self.assertEqual(self.actor.mirrored, expected)
                    self.assertEqual(self.window.pos(), home)

    def test_home_orientation_uses_its_own_monitor(self):
        left = Mock()
        left.availableGeometry.return_value = QtCore.QRect(-1200, 0, 1200, 900)
        right = Mock()
        right.availableGeometry.return_value = QtCore.QRect(0, 0, 1600, 1000)
        with patch.object(QtGui.QGuiApplication, "screenAt", side_effect=lambda p: left if p.x() < 0 else right):
            self.place_home(-300)
            self.assertTrue(self.actor.mirrored)
            self.place_home(300)
            self.assertFalse(self.actor.mirrored)

    def test_manual_drag_defers_turn_until_release_and_finishes_at_saved_home(self):
        area = self.window.screen().availableGeometry()
        self.place_home(area.left()+area.width()*.25)
        self.actor.set_paused(True)
        destination = self.place_home(area.left()+area.width()*.75)
        self.advance(.2)
        self.assertFalse(self.actor.mirrored)
        self.assertEqual(self.window.pos(), destination)
        self.actor.set_paused(False)
        self.assertEqual(self.actor.current_frame, 1)
        self.advance(.061)
        self.assertTrue(self.actor.mirrored)
        self.advance(.061)
        self.assertEqual(self.actor.current_frame, 0)
        self.assertEqual(self.window.pos(), destination)
        self.advance(5.4)
        self.assertTrue(self.actor.mirrored)
        self.assertEqual(self.window.pos(), destination)

    def test_action_faces_target_when_both_sides_fit_and_mirrors_contact_mask(self):
        area = self.window.screen().availableGeometry()
        target = QtCore.QPoint(area.center().x(), area.center().y())
        for fraction, expected in ((.75, True), (.25, False)):
            self.place_home(area.left()+area.width()*fraction)
            self.start(target=target)
            self.assertEqual(self.actor.mirrored, expected)
            self.advance(.501)
            self.assertEqual(self.actor.fingertip, QtCore.QPointF(target))
            self.assertEqual(self.window._hit_mask, self.actor._prepared(12, expected)[1])
            self.assertEqual(self.window._pixmap.cacheKey(), self.actor._prepared(12, expected)[0].cacheKey())
            self.advance(.151)
            self.advance(9)

    def test_edge_fit_takes_priority_over_motion_direction(self):
        area = self.window.screen().availableGeometry()
        self.place_home(area.left()-100)
        target = QtCore.QPoint(area.left()+1, area.center().y())
        self.start(target=target)
        self.advance(.501)
        self.assertTrue(self.actor.mirrored)
        self.assertEqual(self.actor.fingertip, QtCore.QPointF(target))

    def test_drag_faces_approach_then_drag_direction_without_delaying_contact(self):
        area = self.window.screen().availableGeometry()
        for home_fraction, start_fraction, end_fraction, approach_mirror in (
                (.75, .35, .65, True), (.25, .65, .35, False)):
            self.place_home(area.left()+area.width()*home_fraction)
            start = QtCore.QPoint(round(area.left()+area.width()*start_fraction), area.center().y())
            end = QtCore.QPoint(round(area.left()+area.width()*end_fraction), area.center().y())
            self.start("drag", start, end, duration=.5)
            self.assertEqual(self.actor.mirrored, approach_mirror)
            self.advance(.501)
            self.assertEqual(self.actor.mirrored, not approach_mirror)
            self.assertEqual(self.actor.fingertip, QtCore.QPointF(start))
            self.assertEqual(self.events[-1][0], "press")
            self.advance(.081)
            self.advance(.501)
            self.assertEqual(self.events[-1][0], "release")
            self.assertEqual(self.events[-1][1], QtCore.QPointF(end))
            self.advance(9)

    def test_return_restores_inward_orientation_without_home_drift(self):
        area = self.window.screen().availableGeometry()
        home = self.place_home(area.left()+area.width()*.75)
        target = QtCore.QPoint(round(area.left()+area.width()*.88), area.center().y())
        for _ in range(3):
            self.start(target=target)
            self.assertFalse(self.actor.mirrored)
            self.advance(.501)
            self.advance(.151)
            self.advance(9)
            self.assertTrue(self.actor.mirrored)
            self.assertEqual(self.window.pos(), home)
            self.advance(5.4)
            self.assertEqual(self.window.pos(), home)

    def test_return_artwork_faces_travel_direction_and_turns_only_once_at_home(self):
        area = self.window.screen().availableGeometry()
        for theme in ("ufo", "broom"):
            self.actor.set_theme(theme)
            for home_fraction, target_fraction in ((.75, .3), (.25, .7)):
                self.place_home(area.left()+area.width()*home_fraction)
                target = QtCore.QPoint(round(area.left()+area.width()*target_fraction), area.center().y())
                self.actor.show_feedback(target)
                self.advance(8.67)
                self.assertEqual(self.actor.current_frame, 22)
                image = self.window._pixmap.toImage().convertToFormat(QtGui.QImage.Format.Format_RGBA8888)
                pixels = bytes(image.constBits())
                # In both supplied return poses, the head is ahead of the
                # vehicle anchor. Inspect the actual painted silhouette.
                head_x = [i % image.width() for i, alpha in enumerate(pixels[3::4])
                          if alpha > 200 and i//image.width() < image.height()*.5]
                anchor = self.actor._prepared(22, self.actor.mirrored)[2].x()
                head_faces_left = sum(head_x)/len(head_x) < anchor
                with self.subTest(theme=theme, home_fraction=home_fraction):
                    self.assertEqual(head_faces_left, home_fraction < target_fraction)
                directions = [head_faces_left]
                for _ in range(100):
                    self.advance(.02)
                    direction = head_faces_left if self.actor.current_frame == 22 else self.actor.mirrored
                    if direction != directions[-1]:
                        directions.append(direction)
                self.assertEqual(directions, [home_fraction < target_fraction, home_fraction > .5])
                resting = self.actor.mirrored
                self.advance(.08)
                self.assertEqual(self.actor.mirrored, resting)
                self.assertEqual(resting, home_fraction > .5)

    def test_idle_blink_is_brief_and_action_has_no_half_closed_hold(self):
        for theme in ("ufo", "broom"):
            self.actor.set_theme(theme)
            frames = []
            for _ in range(540):
                self.advance(.01)
                frames.append(self.actor.current_frame)
            with self.subTest(theme=theme):
                self.assertIn(1, frames)
                self.assertNotIn(2, frames)
                self.assertLessEqual(frames.count(1)*.01, .14)
            self.start()
            action_frames = []
            for _ in range(150):
                self.advance(.01)
                action_frames.append(self.actor.current_frame)
            self.assertNotIn(2, action_frames)
            self.assertLessEqual(action_frames.count(1)*.01, .14)

    def test_idle_blink_does_not_repeat_the_head_lowering_pose(self):
        for theme in ("ufo", "broom"):
            self.actor.set_theme(theme)
            sequence = [self.actor.current_frame]
            for _ in range(540):
                self.advance(.01)
                if self.actor.current_frame != sequence[-1]:
                    sequence.append(self.actor.current_frame)
            with self.subTest(theme=theme):
                self.assertEqual(sequence, [0, 1, 0])

    def test_click_retracts_and_waits_near_work_before_returning(self):
        self.start()
        self.advance(.501)
        contact = QtCore.QPointF(self.actor._pivot)
        self.advance(.151)
        self.assertEqual(self.results, [None])
        self.assertFalse(self.actor.busy)
        self.advance(.7)
        self.assertEqual(self.actor.current_frame, 0)
        self.assertAlmostEqual(self.actor._pivot.x(), contact.x()-20, delta=1)
        self.assertNotEqual(self.window.pos(), self.home)
        resting = QtCore.QPoint(self.window.pos())
        self.advance(7.3)
        self.assertEqual(self.window.pos(), resting)
        self.advance(2)
        self.assertEqual(self.window.pos(), self.home)
        self.assertFalse(self.actor.visual_active)

    def test_next_action_continues_from_wait_and_can_interrupt_return(self):
        self.actor.show_feedback(QtCore.QPoint(500, 340))
        self.advance(.8)
        self.assertNotEqual(self.window.pos(), self.home)
        previous = QtCore.QPointF(self.actor._pivot)
        self.start(target=QtCore.QPoint(570, 390))
        self.assertLessEqual((self.actor._segments[0].start-previous).manhattanLength(), 1)
        self.advance(.501)
        self.advance(.151)
        self.assertEqual(self.results, [None])
        self.advance(8.65)
        self.assertEqual(self.actor.current_frame, 22)
        previous = QtCore.QPointF(self.actor._pivot)
        self.start(target=QtCore.QPoint(390, 280))
        self.assertLessEqual((self.actor._segments[0].start-previous).manhattanLength(), 1)
        self.advance(.501)
        self.advance(.151)
        self.assertEqual(self.results, [None, None])
        self.assertEqual([event[1] for event in self.events if event[0] == 'press'],
                         [QtCore.QPointF(570, 390), QtCore.QPointF(390, 280)])

    def test_hint_click_matches_original_cycle_and_repeated_refresh(self):
        points = [QtCore.QPoint(420, 300), QtCore.QPoint(560, 350)]
        self.actor.show_hint(points)
        for elapsed, expected in ((.5, 0), (1.81, 0), (1.83, 1), (2.59, 1), (2.61, 0)):
            self.advance(elapsed-self.clock.now)
            self.actor.show_hint(points)
            self.assertEqual(self.actor.current_frame, 14)
            self.assertEqual(self.actor.fingertip, QtCore.QPointF(points[expected]))
        self.assertAlmostEqual(self.window.windowOpacity(), .6, delta=.01)
        self.assertFalse(self.events)

    def test_hint_drag_uses_only_first_two_points_and_original_smoothstep(self):
        points = [QtCore.QPoint(380, 300), QtCore.QPoint(580, 300), QtCore.QPoint(580, 430)]
        self.actor.show_hint(points, 'drag')
        for elapsed in (.5, .8, 1.69, 2.3, 2.61, 2.9):
            self.advance(elapsed-self.clock.now)
            phase = (elapsed % 2.6)/2.6
            progress = min(1, max(0, (phase-.15)/.5))
            progress = progress*progress*(3-2*progress)
            expected = QtCore.QPointF(380+200*progress, 300)
            self.assertEqual(self.actor.current_frame, 14)
            self.assertLessEqual((self.actor.fingertip-expected).manhattanLength(), 1)
        self.assertFalse(self.events)

    def test_hint_kind_change_restarts_but_same_content_does_not(self):
        points = [QtCore.QPoint(420, 300), QtCore.QPoint(560, 350)]
        self.actor.show_hint(points, 'click')
        self.advance(.6)
        previous_generation = self.actor._generation
        self.actor.show_hint(points, 'click')
        self.assertEqual(self.actor._generation, previous_generation)
        self.actor.show_hint(points, 'drag')
        self.assertGreater(self.actor._generation, previous_generation)

    def test_clear_hint_waits_but_cancel_finishes_work_and_restores_opacity(self):
        self.actor.show_hint([QtCore.QPoint(500, 340)])
        self.advance(.5)
        self.actor.clear_hint()
        self.assertAlmostEqual(self.window.windowOpacity(), 1, delta=.01)
        self.advance(.7)
        self.assertEqual(self.actor.current_frame, 0)
        self.assertNotEqual(self.window.pos(), self.home)
        self.actor.cancel()
        self.advance(1.5)
        self.assertEqual(self.window.pos(), self.home)
        self.assertFalse(self.actor.visual_active)
        self.actor.show_hint([QtCore.QPoint(500, 340)])
        self.actor.set_theme('broom')
        self.assertAlmostEqual(self.window.windowOpacity(), 1, delta=.01)
        self.assertEqual(self.window.pos(), self.home)

    def test_hint_clock_sync_updates_phase_without_restarting_approach(self):
        points = [QtCore.QPoint(420, 300), QtCore.QPoint(560, 350)]
        self.actor.show_hint(points, elapsed_ms=0)
        self.advance(.5)
        generation = self.actor._generation
        self.actor.show_hint(points, elapsed_ms=1900)
        self.assertEqual(self.actor._generation, generation)
        self.assertEqual(self.actor.current_frame, 14)
        self.assertEqual(self.actor.fingertip, QtCore.QPointF(points[1]))
        self.actor.show_hint(points, elapsed_ms=0)
        self.assertEqual(self.actor._generation, generation)
        self.assertEqual(self.actor.current_frame, 14)
        self.assertEqual(self.actor.fingertip, QtCore.QPointF(points[0]))

    def test_hint_validation_keeps_invalid_paths_out_of_animation(self):
        for kind, points in (('drag', [QtCore.QPoint(420, 300)]),
                             ('line', [QtCore.QPoint(420, 300)])):
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                self.actor.show_hint(points, kind)
        for value in (-1, float('inf'), float('nan'), True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.actor.show_hint([QtCore.QPoint(420, 300)], elapsed_ms=value)

    def test_line_hint_keeps_actor_at_home_and_cancel_restores_hint_opacity(self):
        self.actor.show_hint([QtCore.QPoint(420, 300), QtCore.QPoint(560, 350)], 'line')
        self.advance(.6)
        self.assertEqual(self.window.pos(), self.home)
        self.assertFalse(self.actor.visual_active)
        self.actor.show_hint([QtCore.QPoint(420, 300)])
        self.assertAlmostEqual(self.window.windowOpacity(), .6, delta=.01)
        self.actor.cancel(return_home=False)
        self.assertAlmostEqual(self.window.windowOpacity(), 1, delta=.01)
        self.assertFalse(self.actor.visual_active)
        self.actor.show_hint([QtCore.QPoint(420, 300)])
        self.start()
        self.assertAlmostEqual(self.window.windowOpacity(), .6, delta=.01)

    def test_action_transparency_and_runtime_anchor_survive_travel_then_restore(self):
        for theme in ('ufo', 'broom'):
            for kind in ('click', 'double_click', 'drag'):
                with self.subTest(theme=theme, kind=kind):
                    self.actor.set_theme(theme)
                    anchor = self.actor.home_anchor_geometry
                    self.start(kind, QtCore.QPoint(450, 350), QtCore.QPoint(600, 400) if kind == 'drag' else None)
                    self.advance(.2)
                    self.assertEqual(self.actor.home_anchor_geometry, anchor)
                    self.assertNotEqual(self.actor.anchored_geometry, anchor)
                    self.assertAlmostEqual(self.window.windowOpacity(), .6, delta=.01)
                    for _ in range(600):
                        self.advance(.02)
                    self.assertFalse(self.actor.visual_active)
                    self.assertEqual(self.actor.home_anchor_geometry, anchor)
                    self.assertAlmostEqual(self.window.windowOpacity(), 1, delta=.01)

    def test_background_feedback_is_transparent_until_it_returns_home(self):
        self.actor.show_feedback(QtCore.QPoint(450, 350), hold=True)
        self.assertAlmostEqual(self.window.windowOpacity(), .6, delta=.01)
        self.actor.release_feedback(QtCore.QPoint(500, 350))
        for _ in range(600):
            self.advance(.02)
        self.assertAlmostEqual(self.window.windowOpacity(), 1, delta=.01)

    def test_hint_clock_sync_keeps_exact_cycle_boundary_on_first_point(self):
        points = [QtCore.QPoint(350, 300), QtCore.QPoint(530, 340), QtCore.QPoint(530, 430)]
        for theme in ('ufo', 'broom'):
            for kind in ('click', 'drag'):
                with self.subTest(theme=theme, kind=kind):
                    self.actor.set_theme(theme)
                    self.clock.now = 5.83 if theme == 'ufo' else 5.81
                    self.actor.show_hint(points[:2] if kind == 'click' else points, kind, elapsed_ms=0)
                    self.advance(.5)
                    self.clock.now = 8.43 if theme == 'ufo' else 8.41
                    self.actor.show_hint(points[:2] if kind == 'click' else points, kind, elapsed_ms=2599.999)
                    self.assertEqual(self.actor.fingertip, QtCore.QPointF(points[1]))
                    self.actor.show_hint(points[:2] if kind == 'click' else points, kind, elapsed_ms=2600)
                    self.assertEqual(self.actor.fingertip, QtCore.QPointF(points[0]))

    def test_approach_keeps_moving_through_pose_changes_without_position_jumps(self):
        target = QtCore.QPoint(540, 360)
        for theme in ('ufo', 'broom'):
            for mode, duration in (('click', .5), ('hint', .45)):
                with self.subTest(theme=theme, mode=mode):
                    self.clock.now = 0
                    self.actor.set_theme(theme)
                    if mode == 'click':
                        self.start(target=target)
                    else:
                        self.actor.show_hint([target])
                    rendered = []
                    render = self.actor._render

                    def record(frame, pivot, mirrored):
                        rendered.append(QtCore.QPointF(pivot))
                        render(frame, pivot, mirrored)

                    with patch.object(self.actor, '_render', side_effect=record):
                        for fraction in (.55, .76, .9):
                            boundary = duration*fraction
                            samples = []
                            for offset in (-.005, -.001, 0, .001, .005):
                                self.advance(boundary+offset-self.clock.now)
                                samples.append(self.actor._pivot.x())
                            local_speed = (samples[4]-samples[0])/.01
                            before = (samples[2]-samples[1])/.001
                            after = (samples[3]-samples[2])/.001
                            self.assertGreater(min(before, after), local_speed*.8,
                                               f'{fraction}: speed dips from {local_speed:.2f} to {before:.2f}/{after:.2f}')
                            self.assertLess(abs(before-after), local_speed*.1)
                    self.assertTrue(all(right.x() >= left.x()-1e-6 for left, right in zip(rendered, rendered[1:])),
                                    'Entering a new pose must not render an earlier position')


if __name__ == "__main__":
    unittest.main()
