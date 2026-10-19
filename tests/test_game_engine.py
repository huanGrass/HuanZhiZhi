"""Exercise the shared Go streaming protocol and process cleanup with real pipes."""
import subprocess
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch


from huanzhizhi.python_runtime import python_executable
from huanzhizhi.games import go

FAKE_ENGINE = '''
import json, sys, time
mode = MODE
if mode == 'slow_start':
    time.sleep(2)
if mode == 'large_stderr':
    sys.stderr.write('diagnostic ' * 20000)
    sys.stderr.flush()
for line in sys.stdin:
    request = json.loads(line)
    if mode != 'visits':
        assert request['maxVisits'] == 80
    if mode == 'hang':
        time.sleep(60)
    if mode == 'exit':
        sys.stderr.write('engine failed deliberately\\n')
        sys.stderr.flush()
        sys.exit(7)
    response = {'id': request['id'], 'rootInfo': {'currentPlayer': 'B'},
                'isDuringSearch': False, 'moveInfos': [{'move': 'D4'}]}
    if mode == 'visits':
        response['moveInfos'][0]['move'] = {80:'A1', 160:'A2', 320:'A3'}[request['maxVisits']]
    if mode == 'wrong_id':
        response['id'] = 'stale'
    elif mode == 'error':
        response['error'] = 'invalid board'
    elif mode == 'white':
        response['rootInfo']['currentPlayer'] = 'W'
    elif mode == 'white_small':
        assert request['boardXSize'] == request['boardYSize'] == 9
        assert request['initialPlayer'] == 'W'
        assert request['initialStones'] == [['B', 'J9']]
        assert request['rules'] == 'japanese' and request['komi'] == 6.5
        assert request['moves'] == []
        response['rootInfo']['currentPlayer'] = 'W'
    elif mode == 'white_history':
        assert request['initialPlayer'] == 'W'
        assert request['moves'] == [['W', 'pass']]
    elif mode == 'empty':
        response['moveInfos'] = []
    elif mode == 'progress':
        print(json.dumps({'id': request['id'], 'warning': 'test warning'}), flush=True)
        print(json.dumps({'id': request['id'], 'isDuringSearch': True}), flush=True)
    elif mode == 'malformed':
        print('invalid-json', flush=True)
        continue
    print(json.dumps(response), flush=True)
'''


class GoEngineTests(unittest.TestCase):
    def setUp(self):
        self.board = [[0] * 19 for _ in range(19)]
        self.logs = []
        self.session = SimpleNamespace(stop_requested=False, emit_log=self.logs.append, options={})
        self.created = []
        self.real_popen = subprocess.Popen
        self.mode = 'reply'
        def launch(command, **kwargs):
            process = self.real_popen(
                [str(python_executable()), '-I', '-u', '-c',
                 FAKE_ENGINE.replace('MODE', repr(self.mode))], **kwargs)
            self.created.append(process)
            return process
        self.patcher = patch.object(go.subprocess, 'Popen', side_effect=launch)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def tearDown(self):
        for process in self.created:
            self.assertIsNotNone(process.poll(), 'engine process leaked')
            self.assertTrue(process.stdin.closed)
            self.assertTrue(process.stdout.closed)

    def test_multiple_queries_share_one_process_and_clean_temporary_files(self):
        self.mode = 'large_stderr'
        with go.start_engine() as engine:
            error_path = engine[3]
            for moves in ([], [['B', 'D4'], ['W', 'Q16']]):
                self.assertEqual(go.analyze(self.session, engine, self.board, moves), 'D4')
                self.assertIsNone(engine[0].poll())
            self.assertEqual(len(self.created), 1)
        self.assertFalse(error_path.parent.exists())

    def test_search_presets_reach_engine_and_change_without_restart(self):
        self.mode = 'visits'
        with go.start_engine() as engine:
            for value, expected in (('80','A1'), ('160','A2'), ('320','A3')):
                self.session.options['max_visits'] = value
                self.assertEqual(go.analyze(self.session, engine, self.board, []), expected)
            self.session.options['max_visits'] = '999'
            with self.assertRaisesRegex(ValueError, '预设档位'):
                go.analyze(self.session, engine, self.board, [])
        self.assertEqual(len(self.created), 1)

    def test_warnings_and_partial_search_do_not_end_query(self):
        self.mode = 'progress'
        with go.start_engine() as engine:
            self.assertEqual(go.analyze(self.session, engine, self.board, []), 'D4')
        self.assertEqual(self.logs, ['KataGo：test warning'])

    def test_shared_client_preserves_board_rules_player_and_observed_history(self):
        self.mode = 'white_small'
        board = [[0] * 9 for _ in range(9)]
        board[0][8] = 1
        with go.start_engine() as engine:
            self.assertEqual(go.analyze(self.session, engine, board, [],
                                       initial_player='W', rules='japanese', komi=6.5), 'D4')
        self.mode = 'white_history'
        with go.start_engine() as engine:
            self.assertEqual(go.analyze(self.session, engine, board, [['W', 'pass']],
                                       initial_player='W'), 'D4')

    def test_invalid_or_failed_responses_stop_without_move(self):
        for mode in ('wrong_id', 'error', 'white', 'empty', 'malformed'):
            with self.subTest(mode=mode):
                self.mode = mode
                with self.assertRaises((RuntimeError, ValueError)):
                    with go.start_engine() as engine:
                        go.analyze(self.session, engine, self.board, [])

    def test_engine_exit_includes_diagnostic(self):
        self.mode = 'exit'
        with self.assertRaisesRegex(RuntimeError, 'engine failed deliberately'):
            with go.start_engine() as engine:
                go.analyze(self.session, engine, self.board, [])

    def test_stop_interrupts_pending_query_and_reaps_process_promptly(self):
        self.mode = 'hang'
        timer = threading.Timer(.15, lambda: setattr(self.session, 'stop_requested', True))
        start = time.perf_counter()
        try:
            with self.assertRaises(InterruptedError):
                with go.start_engine() as engine:
                    timer.start()
                    go.analyze(self.session, engine, self.board, [])
        finally:
            timer.cancel()
            timer.join()
        self.assertLess(time.perf_counter()-start, 1.5)

    def test_query_timeout_cleans_up_engine(self):
        self.mode = 'hang'
        with self.assertRaises(TimeoutError):
            with go.start_engine() as engine:
                with patch.object(go.time, 'monotonic', side_effect=[0, 46]):
                    go.analyze(self.session, engine, self.board, [])

    def test_stop_while_engine_initializes_with_large_board_request(self):
        self.mode = 'slow_start'
        timer = threading.Timer(.15, lambda: setattr(self.session, 'stop_requested', True))
        start = time.perf_counter()
        try:
            with self.assertRaises(InterruptedError):
                with go.start_engine() as engine:
                    timer.start()
                    go.analyze(self.session, engine, [[1]*19 for _ in range(19)], [])
        finally:
            timer.cancel()
            timer.join()
        self.assertLess(time.perf_counter()-start, 1.5)



if __name__ == '__main__':
    unittest.main()
