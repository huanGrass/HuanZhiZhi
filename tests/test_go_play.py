"""Shared Go turns and controls work without a website, pixels or desktop input."""
from contextlib import contextmanager
from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from huanzhizhi.games import go
from huanzhizhi.task_sdk import ObservationPending, confirm_observation


class GoPlayTests(unittest.TestCase):
    def setUp(self):
        self.empty = [[0] * 9 for _ in range(9)]
        self.session = SimpleNamespace(
            skill={'id': '测试围棋'}, options={}, stop_requested=False,
            emit_status=Mock(), emit_log=Mock(), emit_finished=Mock(), emit_hint=Mock(),
            wait_if_paused=Mock(return_value=False))
        self.session.apply_parameter_updates = Mock(side_effect=lambda: self.session.options)
        self.waits = []
        self.on_wait = lambda: None

        def wait(seconds):
            self.waits.append(seconds)
            self.on_wait()
            if len(self.waits) >= 35:
                self.fail('Replay exceeded its expected bounded waits')
            return self.session.stop_requested

        self.session.wait_for_stop = wait
        self.frames = []
        self.index = 0
        self.diagnostic = {'stage': 'startup'}
        self.click = Mock()
        self.point = Mock(return_value=(300, 400))
        self.closed = []
        self.analyses = []
        self.recommend = lambda: 'D4'
        self.on_observe = lambda: None
        self.change_checks = []

        def analyze(session, engine, initial, moves, **options):
            self.analyses.append((deepcopy(initial), deepcopy(moves), dict(options)))
            return self.recommend()

        @contextmanager
        def engine():
            try:
                yield object()
            finally:
                self.closed.append(True)

        for name, replacement in (('analyze', analyze), ('start_engine', engine)):
            patcher = patch.object(go, name, side_effect=replacement)
            patcher.start()
            self.addCleanup(patcher.stop)

    def observe(self, validate=None, *, changed_only=False, retries=3, timeout=None):
        self.change_checks.append(changed_only)
        def read():
            self.assertLess(self.index, len(self.frames), 'Unexpected extra observation')
            frame = self.frames[self.index]
            self.index += 1
            self.on_observe()
            if frame is None:
                self.assertTrue(changed_only, 'Unchanged shortcut used for a mandatory check')
                return None
            if isinstance(frame, Exception):
                raise frame
            board, last = frame
            self.diagnostic['after_board'] = board
            result = validate(board, last) if validate else None
            return board, last, result
        return confirm_observation(self.session, read, retries=retries, timeout=timeout)

    def play(self):
        go.play(self.session, observe=self.observe, click=self.click, point=self.point,
                diagnostic=self.diagnostic, rules='japanese', komi=6.5)

    def stop(self):
        self.session.stop_requested = True

    def test_both_colors_record_two_turns_and_reuse_engine(self):
        for color in (1, 2):
            with self.subTest(color=color):
                self.session.stop_requested = False
                self.session.options = {'color': 'B' if color == 1 else 'W'}
                self.index = 0
                self.analyses.clear()
                self.closed.clear()
                self.click.reset_mock()
                before = deepcopy(self.empty)
                if color == 2:
                    before[0][0] = 1
                first = go.move_result(before, 3, 5, color)
                reply = go.move_result(first, 7, 7, 3-color)
                second = go.move_result(reply, 4, 5, color)
                self.frames = [(before, 0 if color == 1 else 1)] * 2 + [
                    (first, color), (reply, 3-color), (reply, 3-color), (second, color)]
                self.recommend = lambda: 'D4' if len(self.analyses) == 1 else 'E4'
                self.session.emit_status.side_effect = (
                    lambda *_: self.stop() if self.click.call_count == 2 else None)
                self.play()
                player = self.session.options['color']
                opponent = 'W' if player == 'B' else 'B'
                self.assertEqual(self.analyses[1][1], [[player, 'D4'], [opponent, 'H2']])
                self.assertEqual(self.analyses[0][2],
                                 {'initial_player': player, 'rules': 'japanese', 'komi': 6.5})
                self.assertEqual(self.click.call_count, 2)
                self.assertEqual(self.closed, [True])
                self.assertTrue(self.session.emit_finished.call_args.kwargs['stopped_by_request'])

    def test_hint_records_actual_move_capture_and_fast_reply_for_both_colors(self):
        for color in (1, 2):
            with self.subTest(color=color):
                self.session.stop_requested = False
                self.session.options = {'mode': 'hint', 'color': 'B' if color == 1 else 'W'}
                self.index = 0
                self.analyses.clear()
                before = deepcopy(self.empty)
                before[0][0], before[0][1] = 3-color, color
                own = go.move_result(before, 0, 1, color)
                reply = go.move_result(own, 7, 7, 3-color)
                self.frames = [(before, 3-color)] * 2 + [(reply, 3-color)] * 2
                self.recommend = lambda: 'D4' if len(self.analyses) == 1 else 'pass'
                self.play()
                player = self.session.options['color']
                opponent = 'W' if player == 'B' else 'B'
                self.assertEqual(own[0][0], 0)
                self.assertEqual(self.analyses[1][1], [[player, 'A8'], [opponent, 'H2']])
                self.click.assert_not_called()
                self.assertEqual(self.session.emit_hint.call_args.args[1], 'clear')

    def test_white_empty_board_and_unknown_turn_wait_without_analysis(self):
        nonempty = deepcopy(self.empty)
        nonempty[0][0] = 1
        for options, frame in (({'color': 'W'}, (self.empty, 0)), ({}, (nonempty, 0))):
            with self.subTest(options=options):
                self.session.stop_requested = False
                self.session.options = options
                self.index = 0
                self.frames = [frame]
                self.on_wait = self.stop
                self.play()
                self.assertFalse(self.analyses)
                self.click.assert_not_called()

    def test_analysis_and_mode_switch_cannot_click_changed_board(self):
        changed = go.move_result(self.empty, 0, 0, 1)
        self.frames = [(self.empty, 0)] + [(changed, 1)] * 4
        with self.assertRaisesRegex(ObservationPending, '棋盘或回合已变化'):
            self.play()
        self.click.assert_not_called()
        self.assertEqual(self.closed, [True])
        self.assertEqual(self.diagnostic['moves'], [])

    def test_pause_resume_rechecks_even_when_waiting_pixels_were_unchanged(self):
        black = go.move_result(self.empty, 0, 0, 1)
        self.frames = [(black, 1), (black, 1)]
        self.session.wait_if_paused.side_effect = [False, True]
        self.on_observe = lambda: self.stop() if self.index == 2 else None
        self.play()
        self.click.assert_not_called()
        self.assertEqual(self.index, 2)
        self.assertEqual(self.change_checks, [False, False])

    def test_auto_to_hint_during_analysis_does_not_click(self):
        self.frames = [(self.empty, 0)] * 2
        def recommend():
            self.session.options['mode'] = 'hint'
            return 'D4'
        self.recommend = recommend
        self.on_wait = self.stop
        self.play()
        self.click.assert_not_called()
        self.assertTrue(self.point.called)
        self.assertEqual(self.session.emit_hint.call_args.args[1], 'clear')

    def test_hint_to_auto_rechecks_before_click(self):
        self.session.options['mode'] = 'hint'
        after = go.move_result(self.empty, 3, 5, 1)
        self.frames = [(self.empty, 0)] * 2 + [None, (self.empty, 0), (after, 1)]
        self.on_wait = lambda: self.session.options.update(mode='auto')
        self.session.emit_status.side_effect = lambda *_: self.stop() if self.click.called else None
        self.play()
        self.click.assert_called_once_with(3, 5)
        self.assertEqual(self.index, 5)
        self.assertEqual(self.session.emit_hint.call_args.args[1], 'clear')

    def test_invalid_feedback_retries_without_repeating_click(self):
        after = go.move_result(self.empty, 3, 5, 1)
        self.frames = [(self.empty, 0)] * 3 + [(after, 1)]
        self.session.emit_status.side_effect = lambda *_: self.stop() if self.click.called else None
        self.play()
        self.click.assert_called_once()
        self.assertEqual(self.index, 4)
        self.assertTrue(any('复检通过' in c.args[0] for c in self.session.emit_log.call_args_list))

    def test_persistent_feedback_failure_never_repeats_click(self):
        self.frames = [(self.empty, 0)] * 27
        with self.assertRaisesRegex(ObservationPending, '未见完整合法落子'):
            self.play()
        self.click.assert_called_once()
        self.assertEqual(self.diagnostic['moves'], [])
        self.assertEqual(self.closed, [True])

    def test_stop_during_recheck_or_after_analysis_never_clicks(self):
        for phase in ('analysis', 'recheck'):
            with self.subTest(phase=phase):
                self.session.stop_requested = False
                self.index = 0
                self.frames = [(self.empty, 0)] * 2
                if phase == 'analysis':
                    self.recommend = lambda: (self.stop(), 'D4')[1]
                else:
                    self.recommend = lambda: 'D4'
                    self.on_observe = lambda: self.stop() if self.index == 2 else None
                self.play()
                self.click.assert_not_called()
                self.assertTrue(self.session.emit_finished.call_args.kwargs['stopped_by_request'])

    def test_stop_during_result_wait_does_not_confirm_or_send_again(self):
        self.frames = [(self.empty, 0)] * 2
        self.on_wait = self.stop
        self.play()
        self.click.assert_called_once()
        self.assertEqual(self.index, 2)
        self.assertEqual(self.closed, [True])

    def test_invalid_options_fail_before_observation(self):
        for options in ({'color': 'red'}, {'mode': 'invalid'}):
            with self.subTest(options=options):
                self.session.options = options
                with self.assertRaisesRegex(ValueError, '无效'):
                    self.play()
                self.assertEqual(self.index, 0)
                self.click.assert_not_called()

    def test_pass_ends_without_click_or_hint(self):
        self.frames = [(self.empty, 0)]
        self.recommend = lambda: 'pass'
        self.play()
        self.click.assert_not_called()
        self.session.emit_hint.assert_not_called()
        self.assertEqual(self.session.emit_finished.call_args.args[1], '建议虚手')
        self.assertEqual(self.closed, [True])

    def test_engine_error_preserves_evidence_and_closes_process(self):
        self.frames = [(self.empty, 0)]
        self.recommend = Mock(side_effect=RuntimeError('引擎故障'))
        with self.assertRaisesRegex(RuntimeError, '引擎故障'):
            self.play()
        self.assertEqual(self.diagnostic['initial_board'], self.empty)
        self.assertEqual(self.diagnostic['stage'], 'engine_analysis')
        self.click.assert_not_called()
        self.assertEqual(self.closed, [True])


if __name__ == '__main__':
    unittest.main()
