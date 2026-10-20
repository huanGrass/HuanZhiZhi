"""Shared Xiangqi flow and UCI transport, without a website or live input."""
from pathlib import Path
import json
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from huanzhizhi.games import xiangqi


class XiangqiFlowTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.checkpoint = Path(temporary.name) / 'checkpoint.json'
        self.session = Mock(stop_requested=False)
        self.session.wait_for_stop.return_value = False
        self.session.wait_if_paused.return_value = False
        self.view = Mock()
        self.view.finish_reason.return_value = None
        self.view.initial_history.return_value = ([], None, True, 'verified settings')
        self.view.stable.return_value = self.observation(xiangqi.START)
        self.game = xiangqi.Game(self.session, self.view, checkpoint_file=self.checkpoint)
        self.engine = Mock()
        self.engine.legal.return_value = {'b2e2', 'b9c7', 'c3c4'}
        self.engine.search.return_value = 'b2e2'
        patcher = patch.object(xiangqi, 'Engine', return_value=self.engine)
        patcher.start()
        self.addCleanup(patcher.stop)

    def observation(self, board, *, selected=None, layout='layout'):
        return object(), list(board), layout, [i == selected for i in range(90)]

    def replay_board(self, moves):
        board = list(xiangqi.START)
        for move in moves:
            board = xiangqi.apply_move(board, move)
        return board

    def test_initial_history_is_replayed_and_persisted_before_play(self):
        moves = ['b2e2', 'b9c7']
        self.view.initial_history.return_value = (moves, 2, True, 'verified settings')
        self.view.stable.return_value = self.observation(self.replay_board(moves))
        self.game.initialize()
        self.assertEqual(self.game.moves, moves)
        self.assertEqual(json.loads(self.checkpoint.read_text())['moves'], moves)
        self.view.click.assert_not_called()
        self.assertIn((['b2e2'],), [c.args for c in self.engine.legal.call_args_list])

    def test_stopped_game_can_resume_one_or_two_confirmed_moves_later(self):
        for tail in (['b9c7'], ['b9c7', 'c3c4']):
            with self.subTest(tail=tail):
                saved = ['b2e2']
                moves = saved + tail
                self.checkpoint.write_text(json.dumps({'moves': saved}), encoding='utf-8')
                self.view.initial_history.return_value = (moves, len(moves), False, 'verified settings')
                self.view.stable.return_value = self.observation(self.replay_board(moves))
                self.game.initialize()
                self.assertEqual(self.game.moves, moves)
                self.view.click.assert_not_called()

    def test_incomplete_or_mismatched_history_never_clicks_or_overwrites_checkpoint(self):
        saved = json.dumps({'moves': ['b2e2']})
        self.checkpoint.write_text(saved, encoding='utf-8')
        for rows, ply in ((['c3c4'], 1), (['b2e2', 'b9c7'], 5), ([], None)):
            with self.subTest(rows=rows, ply=ply):
                self.view.initial_history.return_value = (rows, ply, False, 'verified settings')
                with self.assertRaisesRegex(ValueError, '棋谱|走法'):
                    self.game.initialize()
                self.assertEqual(self.checkpoint.read_text(encoding='utf-8'), saved)
                self.view.click.assert_not_called()

    def test_invalid_checkpoint_and_illegal_history_are_rejected(self):
        self.view.initial_history.return_value = (['b2e2'], 1, False, 'verified settings')
        self.checkpoint.write_text(json.dumps({'moves': ['bad move']}), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '历史记录无效'):
            self.game.initialize()
        self.view.initial_history.return_value = (['a0a9'], 1, True, 'verified settings')
        with self.assertRaisesRegex(ValueError, '初始棋谱'):
            self.game.initialize()
        self.view.click.assert_not_called()

    def test_history_board_mismatch_and_missing_history_do_not_start(self):
        self.view.initial_history.return_value = (['b2e2'], 1, True, 'verified settings')
        with self.assertRaisesRegex(ValueError, '完整棋谱与当前棋盘'):
            self.game.initialize()
        self.view.initial_history.return_value = ([], None, False, 'verified settings')
        with self.assertRaisesRegex(ValueError, '缺少完整'):
            self.game.initialize()
        self.view.click.assert_not_called()
        self.assertFalse(self.checkpoint.exists())

    def test_active_turn_skips_history_but_resume_rechecks_it(self):
        self.game.engine = self.engine
        self.game.moves = ['b2e2', 'b9c7']
        self.game.expected = self.replay_board(self.game.moves)
        self.view.stable.return_value = self.observation(self.game.expected)
        self.game.checkpoint()
        self.view.history.assert_not_called()
        self.session.wait_if_paused.return_value = True
        self.view.history.return_value = (['b2e2'], 1)
        with self.assertRaisesRegex(ValueError, '棋谱'):
            self.game.checkpoint()
        self.view.click.assert_not_called()

    def test_fast_reply_and_separate_reply_preserve_the_same_actual_history(self):
        for combined in (False, True):
            with self.subTest(combined=combined):
                self.game.moves = []
                self.game.expected = list(xiangqi.START)
                self.game.engine = self.engine
                self.engine.legal.return_value = {'b9c7', 'c3c4'}
                own = self.replay_board(['b2e2'])
                reply = self.replay_board(['b2e2', 'b9c7'])
                self.view.stable.return_value = self.observation(reply if combined else own)
                self.game.confirm_player('b2e2')
                if not combined:
                    self.view.stable.return_value = self.observation(reply)
                    self.game.wait_opponent()
                self.assertEqual(self.game.moves, ['b2e2', 'b9c7'])
                self.assertEqual(self.game.expected, reply)
                self.view.history.assert_not_called()
                self.view.click.assert_not_called()

    def prepare_click(self):
        self.game.initialize = Mock()
        self.game.engine = self.engine
        self.game.checkpoint = Mock(return_value=self.observation(xiangqi.START))
        source = (9 - 2) * 9 + 1
        self.view.observe.return_value = self.observation(xiangqi.START, selected=source)
        self.game.confirm_player = Mock(side_effect=InterruptedError('end of replay'))

    def test_two_clicks_share_one_fresh_confirmation_without_saving_a_frame(self):
        self.prepare_click()
        with self.assertRaises(InterruptedError):
            self.game.run()
        self.assertEqual([c.args[1] for c in self.view.click.call_args_list], ['b2', 'e2'])
        self.view.observe.assert_called_once()
        self.assertIn('after', self.view.observe.call_args.kwargs)
        self.view.record_frame.assert_not_called()
        self.assertEqual(self.view.mark_move.call_count, 1)

    def test_stop_during_selection_prevents_destination(self):
        self.prepare_click()
        def observe(**kwargs):
            self.session.stop_requested = True
            return self.observation(xiangqi.START, selected=64)
        self.view.observe.side_effect = observe
        with self.assertRaises(InterruptedError):
            self.game.run()
        self.assertEqual(self.view.click.call_count, 1)

    def test_selection_layout_change_never_sends_destination(self):
        self.prepare_click()
        self.view.observe.return_value = self.observation(xiangqi.START, selected=64, layout='changed')
        with self.assertRaisesRegex(ValueError, '局面变化'):
            self.game.run()
        self.assertEqual(self.view.click.call_count, 1)

    def test_no_response_and_opponent_timeout_never_retry_input(self):
        self.game.engine = self.engine
        self.view.stable.return_value = self.observation(xiangqi.START)
        with patch.object(xiangqi.time, 'monotonic', side_effect=[0, 1, 11]):
            with self.assertRaisesRegex(TimeoutError, '未重复点击'):
                self.game.confirm_player('b2e2')
        with patch.object(xiangqi.time, 'monotonic', side_effect=[0, 1, 21]):
            with self.assertRaisesRegex(TimeoutError, '等待电脑'):
                self.game.wait_opponent()
        self.view.click.assert_not_called()

    def test_terminal_site_checks_and_engine_terminal_are_both_required(self):
        self.game.engine = self.engine
        self.game.moves = ['b2e2']
        self.game.expected = self.replay_board(self.game.moves)
        observation = self.observation(self.game.expected)
        self.engine.legal.return_value = set()
        self.view.finish_reason.side_effect = ValueError('终局标记冲突')
        with self.assertRaisesRegex(ValueError, '终局标记'):
            self.game.check_finished(observation)
        self.view.finish_reason.side_effect = None
        self.view.finish_reason.return_value = None
        with self.assertRaisesRegex(xiangqi.GameEnded, '玩家获胜'):
            self.game.check_finished(observation)
        self.view.click.assert_not_called()

    def test_site_draw_rules_remain_outside_shared_flow(self):
        self.game.engine = self.engine
        self.view.finish_reason.return_value = '网站特定和棋条件'
        with self.assertRaisesRegex(xiangqi.GameEnded, '网站特定和棋条件'):
            self.game.check_finished(self.observation(xiangqi.START))
        self.view.click.assert_not_called()


FAKE_ENGINE = '''
import sys, time
mode = MODE
for line in sys.stdin:
    command = line.strip()
    if command == 'uci':
        if mode == 'startup_exit': sys.exit(3)
        print('uciok', flush=True)
    elif command == 'isready': print('readyok', flush=True)
    elif command == 'go perft 1':
        print('b2e2: 1\\nc3c4: 1\\nNodes searched: 2', flush=True)
    elif command.startswith('go movetime'):
        if mode == 'exit': sys.exit(4)
        if mode == 'hang': time.sleep(60)
        print('bestmove '+{'none':'0000', 'invalid':'z9z8'}.get(mode,'b2e2'), flush=True)
    elif command == 'quit': sys.exit(0)
'''


class XiangqiEngineTests(unittest.TestCase):
    def setUp(self):
        self.session = Mock(stop_requested=False)
        self.mode = 'normal'
        self.processes = []
        self.client = None
        real_popen = subprocess.Popen

        def launch(_command, **kwargs):
            process = real_popen([sys.executable, '-u', '-c',
                                 FAKE_ENGINE.replace('MODE', repr(self.mode))], **kwargs)
            self.processes.append(process)
            return process

        for patcher in (
            patch.object(xiangqi.subprocess, 'Popen', side_effect=launch),
            patch.object(xiangqi, 'component_paths', return_value=(None, None, 'unused.exe', 'model.nnue')),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(self.cleanup)

    def cleanup(self):
        if self.client:
            self.client.close()
        for process in self.processes:
            self.assertIsNotNone(process.poll(), 'Pikafish process leaked')
            self.assertTrue(process.stdin.closed)
            self.assertTrue(process.stdout.closed)

    def start(self):
        self.client = xiangqi.Engine(self.session, Mock())
        return self.client

    def test_multiple_legal_and_search_calls_reuse_one_process(self):
        engine = self.start()
        for history in ([], ['b2e2', 'b9c7']):
            self.assertEqual(engine.legal(history), {'b2e2', 'c3c4'})
            self.assertEqual(engine.search(history), 'b2e2')
        self.assertEqual(len(self.processes), 1)

    def test_startup_exit_is_reported_and_cleaned_up(self):
        self.mode = 'startup_exit'
        with self.assertRaisesRegex(RuntimeError, '退出'):
            self.start()

    def test_search_exit_and_invalid_moves_fail(self):
        self.mode = 'invalid'
        engine = self.start()
        with self.assertRaisesRegex(ValueError, '无效走法'):
            engine.search([])

    def test_search_process_exit_is_reported(self):
        self.mode = 'exit'
        engine = self.start()
        with self.assertRaisesRegex(RuntimeError, '退出'):
            engine.search([])

    def test_no_legal_move_result_is_explicit(self):
        self.mode = 'none'
        self.assertIsNone(self.start().search([]))

    def test_stop_interrupts_waiting_and_cleanup_reaps_hung_engine(self):
        self.mode = 'hang'
        engine = self.start()
        timer = threading.Timer(.08, lambda: setattr(self.session, 'stop_requested', True))
        timer.start()
        try:
            with self.assertRaises(InterruptedError):
                engine.search([])
        finally:
            timer.join()

    def test_response_timeout_is_bounded_and_engine_is_reaped(self):
        self.mode = 'hang'
        engine = self.start()
        engine.send('go movetime 500')
        with self.assertRaisesRegex(TimeoutError, '响应超时'):
            engine.until(lambda line: line.startswith('bestmove '), timeout=.06)


if __name__ == '__main__':
    unittest.main()
