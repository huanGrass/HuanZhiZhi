"""Verify the shared CPU engine probe without depending on an installed engine."""
import hashlib
import json
import os
from pathlib import Path
from queue import Empty
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from huanzhizhi.shared_components import _analyze_pikafish, check_pikafish, main, supported_components


class PikafishTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.component = dict(next(c for c in supported_components() if c['id'] == 'pikafish'))
        self.engine = self.root / 'dependencies' / self.component['engine']
        self.model = self.root / 'dependencies' / self.component['model']

    def test_missing_files_report_all_requirements_without_starting_analysis(self):
        before = dict(os.environ)
        with patch('huanzhizhi.shared_components._analyze_pikafish') as analyze:
            rows = list(check_pikafish(self.component, self.root))
        self.assertEqual([r['name'] for r in rows], ['pikafish.engine', 'pikafish.model', 'pikafish.analysis'])
        self.assertFalse(any(r['satisfied'] for r in rows))
        self.assertIn('前置条件不满足', rows[-1]['detail'])
        analyze.assert_not_called()
        self.assertEqual(list(self.root.iterdir()), [])
        self.assertEqual(dict(os.environ), before)

    def test_wrong_version_and_corrupt_model_block_analysis(self):
        self.engine.parent.mkdir(parents=True)
        self.engine.touch()
        self.model.write_bytes(b'broken')
        for output in ('id name Pikafish other\nuciok\n', 'id name Pikafish 2026-09-06\n'):
            with self.subTest(output=output), patch('huanzhizhi.shared_components.subprocess.run',
                    return_value=subprocess.CompletedProcess([], 0, output, '')), \
                    patch('huanzhizhi.shared_components._analyze_pikafish') as analyze:
                rows = list(check_pikafish(self.component, self.root))
                self.assertFalse(any(row['satisfied'] for row in rows))
                self.assertIn('模型校验失败', rows[1]['detail'])
                analyze.assert_not_called()

    def test_matching_files_and_analysis_mark_component_available(self):
        self.engine.parent.mkdir(parents=True)
        self.engine.touch()
        self.model.write_bytes(b'model')
        self.component['model_sha256'] = hashlib.sha256(b'model').hexdigest()
        with patch('huanzhizhi.shared_components.subprocess.run', return_value=subprocess.CompletedProcess(
                [], 0, 'id name Pikafish 2026-09-06\nuciok\n', '')), \
                patch('huanzhizhi.shared_components._analyze_pikafish', return_value=('ok', 'searched')) as analyze:
            rows = list(check_pikafish(self.component, self.root))
        self.assertTrue(all(row['satisfied'] for row in rows))
        analyze.assert_called_once_with(self.engine, self.model)

    def run_probe(self, result, *, timeout=False, expected_error=None):
        # A real subprocess catches premature quit/EOF and leaked children that
        # a mocked CompletedProcess would miss.
        script = '''import sys
for line in sys.stdin:
    command = line.strip()
    if command == 'uci': print('uciok', flush=True)
    elif command == 'isready': print('readyok', flush=True)
    elif command == 'go depth 4':
        print(RESULT, flush=True)
        if not RESULT: sys.exit(3)
    elif command == 'quit': sys.exit(0)
'''.replace('RESULT', repr(result))
        launched = []
        real_popen = subprocess.Popen

        def launch(_args, **kwargs):
            process = real_popen([sys.executable, '-u', '-c', script], **kwargs)
            launched.append((process, Path(kwargs['cwd'])))
            return process

        with patch('huanzhizhi.shared_components.subprocess.Popen', side_effect=launch):
            if timeout:
                with patch('huanzhizhi.shared_components.Queue.get', side_effect=Empty), \
                        self.assertRaises(subprocess.TimeoutExpired):
                    _analyze_pikafish(self.engine, self.model)
            elif expected_error:
                with self.assertRaisesRegex(RuntimeError, expected_error):
                    _analyze_pikafish(self.engine, self.model)
            else:
                summary, detail = _analyze_pikafish(self.engine, self.model)
                self.assertIn('分析通过', summary)
                self.assertIn('b2e2', detail)
        for process, workdir in launched:
            self.assertIsNotNone(process.poll())
            self.assertFalse(workdir.exists())
            self.assertTrue(process.stdin.closed)
            self.assertTrue(process.stdout.closed)

    def test_analysis_waits_for_search_before_quit_and_cleans_up(self):
        self.run_probe('info string NNUE evaluation using model\ninfo depth 4 score cp 10 nodes 720 pv b2e2\nbestmove b2e2')

    def test_analysis_rejects_exit_invalid_move_and_incomplete_search(self):
        self.run_probe('', expected_error='提前退出')
        for result in ('bestmove b2e2', 'bestmove (none)',
                       'info string NNUE evaluation using model\ninfo depth 4 nodes 720\nbestmove z9z8'):
            with self.subTest(result=result):
                self.run_probe(result, expected_error='未返回完整')

    def test_timeout_reaps_process_and_temporary_files(self):
        self.run_probe('', timeout=True)

    def test_cli_dispatches_both_components(self):
        with patch('huanzhizhi.shared_components.python_executable', return_value=self.root / 'python/python.exe'), \
                patch('huanzhizhi.shared_components.check_katago', return_value=iter([{'name': 'katago'}])) as go, \
                patch('huanzhizhi.shared_components.check_pikafish', return_value=iter([{'name': 'pikafish'}])) as chess, \
                patch('builtins.print') as output:
            main()
        go.assert_called_once()
        chess.assert_called_once()
        self.assertEqual([json.loads(c.args[0])['name'] for c in output.call_args_list], ['katago', 'pikafish'])
