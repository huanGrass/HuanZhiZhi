import json
from pathlib import Path
import shutil
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from huanzhizhi.capabilities import VisionCalls
from huanzhizhi.flow_program import run_program
from huanzhizhi.python_runtime import run_code
from huanzhizhi.skill_exploration import explore
from huanzhizhi.skill_methods import call_method, custom_catalog, define_method
from huanzhizhi.visual_flow import VisualFlow, replay
from tests.test_visual_flow import Desktop, color_program, decision, response


SUM_RULE = '''def run(data):
    parts = data["text"].split("+")
    if len(parts) != 2:
        raise ValueError("需要两个加数")
    return str(sum(int(part.strip()) for part in parts))
'''


def sum_spec():
    return dict(description='计算当前画面算式的答案', source=SUM_RULE, tests=[
        {'data': {'text': '8+3'}, 'expected': '11'},
        {'data': {'text': '29+14'}, 'expected': '43'},
        {'data': {'text': '不是算式'}, 'error': '需要两个加数'},
    ])


class SkillMethodTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)

    def test_rule_is_tested_saved_and_reused_on_new_data(self):
        result = define_method(self.directory, 'custom_sum', sum_spec())
        self.assertEqual(result['tests_passed'], 3)
        self.assertEqual(call_method(self.directory, 'custom_sum', {'text': '101+203'}), '304')
        self.assertEqual(custom_catalog(self.directory)[0]['source'], SUM_RULE)
        self.assertFalse((self.directory / 'task.py').exists())
        with self.assertRaisesRegex(ValueError, '已存在'):
            define_method(self.directory, 'custom_sum', sum_spec())
        (self.directory / 'custom_sum.py').write_text('def run(data): return "旧答案"', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '版本不一致'):
            call_method(self.directory, 'custom_sum', {})

    def test_failed_tests_are_not_registered_and_can_be_corrected(self):
        spec = sum_spec()
        spec['source'] = 'def run(data): return "11"'
        with self.assertRaisesRegex(ValueError, '测试2未通过') as failure:
            define_method(self.directory, 'custom_sum', spec)
        self.assertIn('测试3未通过', str(failure.exception))
        self.assertIn('29+14', str(failure.exception))
        self.assertIn('43', str(failure.exception))
        self.assertIn('11', str(failure.exception))
        self.assertIn('需要两个加数', str(failure.exception))
        self.assertEqual(custom_catalog(self.directory), [])
        self.assertFalse((self.directory / 'custom_sum.py').exists())
        define_method(self.directory, 'custom_sum', sum_spec())

    def test_exploration_returns_failed_source_and_tests_for_repair(self):
        desktop = Desktop()
        flow = VisualFlow(desktop.window, self.directory, lambda: False, Mock(), Mock(),
                          desktop=desktop, capture=Mock())
        flow.wait = lambda seconds: flow.check_stop()
        broken = sum_spec()
        broken['source'] = 'def run(data): return "11"'
        count = 0
        def respond(*args, context, **kwargs):
            nonlocal count
            count += 1
            if count == 1:
                return response(decision(0, status='define', action='none', method='custom_sum', arguments=json.dumps(broken)))
            if count == 2:
                failure = context['inspections'][-1]
                self.assertIn('测试2未通过', failure['error'])
                self.assertEqual(failure['definition'], broken)
                self.assertEqual(context['custom_methods'], [])
                self.assertEqual(desktop.clicks, [])
                return response(decision(0, status='define', action='none', method='custom_sum', arguments=json.dumps(sum_spec())))
            return decision(desktop.state)
        with patch('huanzhizhi.skill_exploration.analyze_image', side_effect=respond):
            self.assertIn('技能已保存', explore(flow, '验证后才继续操作', Mock(), Mock()))
        self.assertEqual(call_method(self.directory, 'custom_sum', {'text': '29+14'}), '43')

    def test_code_has_no_file_window_or_process_access(self):
        for source in ('import os\ndef run(data): return os.getcwd()',
                       'def run(data): return data.__class__',
                       'def run(data): return open("unexpected.txt", "w")',
                       'def run(data): return eval(data)',
                       'import math\ndef run(data): return math.__dict__'):
            with self.subTest(source=source), self.assertRaises((ValueError, SyntaxError)):
                define_method(self.directory, 'custom_bad', dict(
                    description='无效规则', source=source, tests=[{'data': {}, 'expected': 0}]))
        self.assertEqual(custom_catalog(self.directory), [])
        self.assertFalse(list(self.directory.rglob('unexpected.txt')))

    def test_compute_imports_and_recursive_rules_work(self):
        source = '''from collections import Counter
from math import gcd
def total(items):
    if not items:
        return 0
    return items[0] + total(items[1:])
def run(data):
    return {"total": total(data), "counts": dict(Counter(data)), "gcd": gcd(*data)}
'''
        # JSON object keys must be strings; invalid output must fail validation.
        with self.assertRaisesRegex(ValueError, '参数名必须是文字'):
            define_method(self.directory, 'custom_count', dict(description='计算', source=source,
                          tests=[{'data': [2, 2, 4], 'expected': {}}]))
        source = source.replace('dict(Counter(data))', '{str(k): v for k, v in Counter(data).items()}')
        define_method(self.directory, 'custom_count', dict(description='计算', source=source,
                      tests=[{'data': [2, 2, 4], 'expected': {'total': 8, 'counts': {'2': 2, '4': 1}, 'gcd': 2}}]))
        self.assertEqual(call_method(self.directory, 'custom_count', [3, 6])['total'], 9)

    def test_timeout_and_stop_terminate_rule_without_publication(self):
        spec = dict(description='无限循环', source='def run(data):\n    while True: pass',
                    tests=[{'data': None, 'expected': None}])
        def short_run(code, **kwargs):
            return run_code(code, **{**kwargs, 'timeout': .3})
        with patch('huanzhizhi.skill_methods.run_code', side_effect=short_run):
            with self.assertRaisesRegex(RuntimeError, '超过10秒'):
                define_method(self.directory, 'custom_loop', spec)
        stop = threading.Event()
        timer = threading.Timer(.3, stop.set)
        timer.start()
        try:
            with self.assertRaises(InterruptedError):
                define_method(self.directory, 'custom_loop', spec, stop.is_set)
        finally:
            timer.cancel()
        self.assertEqual(custom_catalog(self.directory), [])

    def test_custom_pixel_rule_receives_only_arrays(self):
        define_method(self.directory, 'custom_pixel', dict(description='读取当前裁剪图的像素',
                      source='def run(data): return data[0][0]',
                      tests=[{'data': [[[1, 2, 3]]], 'expected': [1, 2, 3]}]))
        calls = VisionCalls(self.directory)
        result = calls.call('custom_pixel', {'data': {'crop': [60, 220, 8, 8]}}, Desktop().images[0])
        self.assertEqual(result['result'], [0, 0, 255])

    def test_rule_cannot_replace_live_recognition_with_test_data(self):
        define_method(self.directory, 'custom_sum', sum_spec())
        program = dict(calls=[dict(id='answer', method='custom_sum', arguments={'data': {'text': '8+3'}})],
                       checks=[{'value': {'result': 'answer'}, 'op': 'exists'}])
        with self.assertRaisesRegex(ValueError, '当前画面'):
            run_program(program, Desktop().images[0], self.directory)

    def test_rule_rejection_on_new_data_is_a_non_match_with_diagnostic(self):
        define_method(self.directory, 'custom_sum', sum_spec())
        program = dict(calls=[dict(id='words', method='ReadText', arguments={}),
                             dict(id='answer', method='custom_sum', arguments={'data': {'result': 'words'}})],
                       checks=[{'value': {'result': 'answer'}, 'op': 'exists'}])
        log = Mock()
        def read(image, roi=None, language=''):
            return {'text': '不是算式'}
        with patch('huanzhizhi.vision.ReadText', new=read):
            self.assertIsNone(run_program(program, Desktop().images[0], self.directory, log=log))
        self.assertTrue(any('需要两个加数' in call.args[0] for call in log.call_args_list))
        with self.assertRaisesRegex(ValueError, '需要两个加数'):
            call_method(self.directory, 'custom_sum', {'text': '不是算式'})

    def test_runtime_rule_error_reaches_next_exploration_decision(self):
        define_method(self.directory, 'custom_sum', sum_spec())
        desktop = Desktop()
        flow = VisualFlow(desktop.window, self.directory, lambda: False, Mock(), Mock(),
                          desktop=desktop, capture=Mock())
        flow.wait = lambda seconds: flow.check_stop()
        program = color_program(0)
        program['calls'] += [dict(id='words', method='ReadText', arguments={}),
                             dict(id='answer', method='custom_sum', arguments={'data': {'result': 'words'}})]
        program['checks'].append({'value': {'result': 'answer'}, 'op': 'exists'})
        rounds = 0
        def respond(*args, context, **kwargs):
            nonlocal rounds
            rounds += 1
            if rounds == 1:
                return decision(0, program=json.dumps(program))
            if rounds == 2:
                self.assertIn('需要两个加数', '\n'.join(context.get('diagnostics', [])), context['rejected_decision'])
                self.assertEqual(desktop.clicks, [])
            return decision(desktop.state)
        def read(image, roi=None, language=''):
            return {'text': '不是算式'}
        with patch('huanzhizhi.skill_exploration.analyze_image', side_effect=respond), \
                patch('huanzhizhi.vision.ReadText', new=read):
            self.assertIn('技能已保存', explore(flow, '完成两步', Mock(), Mock()))
        self.assertEqual(len(desktop.clicks), 2)

    def test_exploration_can_combine_more_than_four_inspection_results(self):
        desktop = Desktop()
        flow = VisualFlow(desktop.window, self.directory, lambda: False, Mock(), Mock(),
                          desktop=desktop, capture=Mock())
        flow.wait = lambda seconds: flow.check_stop()
        def respond(*args, context, **kwargs):
            if not desktop.clicks:
                if len(context['inspections']) < 6:
                    return decision(0, status='inspect', action='none', method='FindColorComponents',
                                    arguments=json.dumps({'color': {'blue_min': 200, 'red_max': 40}, 'min_area': 100}))
                earliest = context['inspections'][0]
                self.assertEqual(earliest['id'], 'inspect-1')
                self.assertTrue(earliest['result'])
                program = color_program(0)
                program['calls'][0] = earliest['id']
                program['calls'][1]['arguments']['items']['result'] = earliest['id']
                return decision(0, program=json.dumps(program))
            return decision(desktop.state)
        with patch('huanzhizhi.skill_exploration.analyze_image', side_effect=respond):
            self.assertIn('技能已保存', explore(flow, '组合已识别的信息完成目标', Mock(), Mock()))
        self.assertEqual(len(desktop.clicks), 2)

    def test_repeated_rule_verifies_state_change_against_before_action(self):
        desktop = Desktop()
        first = desktop.images[0].copy()
        second = first.copy()
        second.putpixel((0, 0), (29, 14, 0))
        define_method(self.directory, 'custom_sum', sum_spec())
        program = dict(calls=[dict(id='words', method='ReadText', arguments={}),
                             dict(id='answer', method='custom_sum', arguments={'data': {'result': 'words'}})],
                       checks=[{'value': {'result': 'answer'}, 'op': 'ne', 'expected': {'previous': 'answer'}}])
        def read(image, roi=None, language=''):
            a, b, _ = image.getpixel((0, 0))
            return {'text': f'{a}+{b}'}
        flow = VisualFlow(desktop.window, self.directory, lambda: False, Mock(), Mock(),
                          desktop=desktop, capture=Mock())
        with patch('huanzhizhi.vision.ReadText', new=read):
            self.assertFalse(flow.condition_matches({'program': program}, first, before=first))
            self.assertTrue(flow.condition_matches({'program': program}, second, before=first))

    def test_continuous_rule_is_saved_and_replayed_with_transition_checks(self):
        desktop = Desktop()
        inputs = []
        stop = False
        def capture(window, **kwargs):
            image = desktop.images[0].copy()
            image.putpixel((0, 0), (8 + len(inputs), 3, 0))
            return image
        def read(image, roi=None, language=''):
            a, b, _ = image.getpixel((0, 0))
            return {'text': f'{a}+{b}'}
        def post_text(window, text, **kwargs):
            nonlocal stop
            inputs.append(text)
            if len(inputs) in (4, 6):
                stop = True
        desktop.capture_client = capture
        desktop.post_text = post_text
        flow = VisualFlow(desktop.window, self.directory, lambda: stop, Mock(), Mock(),
                          desktop=desktop, capture=Mock())
        flow.wait = lambda seconds: flow.check_stop()
        program = color_program(0)
        program['calls'] += [dict(id='words', method='ReadText', arguments={}),
                             dict(id='answer', method='custom_sum', arguments={'data': {'result': 'words'}})]
        first = decision(0, continuous=True, action='text', program=json.dumps(program),
                         arguments=json.dumps({'text': {'result': 'answer'}}))
        program['verification'] = dict(calls=[dict(id='words', method='ReadText', arguments={})],
            checks=[{'value': {'result': 'words', 'path': ['text']}, 'op': 'ne',
                     'expected': {'previous': 'words', 'path': ['text']}}])
        second = {**first, 'previous': 'succeeded', 'program': json.dumps(program)}
        define = decision(0, status='define', action='none', method='custom_sum', arguments=json.dumps(sum_spec()))
        saved = Mock()
        with patch('huanzhizhi.vision.ReadText', new=read), \
                patch('huanzhizhi.skill_exploration.analyze_image', side_effect=[define, first, second]):
            with self.assertRaises(InterruptedError):
                explore(flow, '持续计算每轮的新算式', Mock(), saved)
        saved.assert_called_once()
        self.assertEqual(inputs, ['11', '12', '13', '14'])
        recipe = json.loads((self.directory / 'flow.json').read_text(encoding='utf-8'))
        self.assertTrue(recipe['ready'])
        self.assertEqual(len(recipe['nodes']), 1)
        stop = False
        with patch('huanzhizhi.vision.ReadText', new=read), \
                patch('huanzhizhi.skill_exploration.analyze_image') as ai:
            with self.assertRaises(InterruptedError):
                replay(flow, recipe)
            ai.assert_not_called()
            self.assertEqual(inputs[-2:], ['15', '16'])
            stop = False
            desktop.post_text = Mock()  # Input delivery without a game-state change is not success.
            before = flow.execute(recipe['nodes'][0])
            with self.assertRaisesRegex(TimeoutError, '没有达到预期'):
                flow.verify(recipe['nodes'][0]['after'], timeout=.01, nodes=recipe['nodes'], before=before)

    def test_exploration_defines_inspects_saves_and_replays_rules_with_new_inputs(self):
        desktop = Desktop()
        for image in desktop.images:
            image.putpixel((0, 0), (8, 3, 0))
        inputs = []
        def post_text(window, text, **kwargs):
            inputs.append(text)
            desktop.state += 1
        desktop.post_text = post_text
        flow = VisualFlow(desktop.window, self.directory, lambda: False, Mock(), Mock(),
                          desktop=desktop, capture=Mock())
        flow.wait = lambda seconds: flow.check_stop()
        def read(image, roi=None, language=''):
            a, b, _ = image.getpixel((0, 0))
            return {'text': f'{a}+{b}'}
        def act(state):
            program = color_program(state)
            program['calls'] += [dict(id='words', method='ReadText', arguments={}),
                                 dict(id='answer', method='custom_sum', arguments={'data': {'result': 'words'}})]
            return decision(state, action='text', program=json.dumps(program),
                            arguments=json.dumps({'text': {'result': 'answer'}}))
        decisions = [decision(0, status='define', action='none', method='custom_sum', arguments=json.dumps(sum_spec())),
                     decision(0, status='inspect', action='none', method='ReadText'),
                     decision(0, status='inspect', action='none', method='custom_sum',
                              arguments=json.dumps({'data': {'result': 'inspect-1'}})),
                     act(0), act(1), decision(2)]
        saved = Mock()
        with patch('huanzhizhi.vision.ReadText', new=read), \
                patch('huanzhizhi.skill_exploration.analyze_image', side_effect=decisions) as ai:
            self.assertIn('技能已保存', explore(flow, '计算每页算式并填入答案', Mock(), saved))
            self.assertTrue(ai.call_args.kwargs['context']['custom_methods'])
        self.assertEqual(inputs, ['11', '11'])
        saved.assert_called_once()
        relocated = self.directory / 'relocated'
        relocated.mkdir()
        for name in ('flow.json', 'task.py', 'custom_sum.py', 'custom_sum.json'):
            shutil.copyfile(self.directory / name, relocated / name)
        flow.directory = relocated
        recipe = json.loads((relocated / 'flow.json').read_text(encoding='utf-8'))
        desktop.state = 0
        for image in desktop.images:
            image.putpixel((0, 0), (29, 14, 0))
        with patch('huanzhizhi.vision.ReadText', new=read), \
                patch('huanzhizhi.skill_exploration.analyze_image') as ai:
            self.assertIn('目标已完成', replay(flow, recipe))
            ai.assert_not_called()
        self.assertEqual(inputs, ['11', '11', '43', '43'])


if __name__ == '__main__':
    unittest.main()
