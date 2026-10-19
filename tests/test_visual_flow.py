import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from PIL import Image, ImageDraw

from huanzhizhi.platform.target_window import WindowInfo, WindowRule
from huanzhizhi.skill_exploration import explore
from huanzhizhi.visual_flow import VisualFlow, replay, save_condition
from huanzhizhi.capabilities import INPUT, NoMatch


class Desktop:
    def __init__(self):
        self.state = 0
        self.clicks = []
        self.window = WindowInfo(123, 'Test', 0, 0, 640, 400)
        self.images = []
        for state in range(3):
            image = Image.new('RGB', (640, 400), ('white', 'lightblue', 'lightgreen')[state])
            draw = ImageDraw.Draw(image)
            draw.text((30, 40), f'STATE {state}', fill='black')
            draw.rectangle((60, 220, 270, 310), fill=('blue', 'red', 'green')[state])
            draw.text((90, 250), f'BUTTON {state}', fill='white')
            self.images.append(image)

    def refresh(self, window, rule):
        return self.window

    def capture_client(self, window, **kwargs):
        return self.images[self.state].copy()

    def post_click(self, window, x, y, action='click', *, wait=None):
        assert self.state < 2 and 60 <= x <= 270 and 220 <= y <= 310
        self.clicks.append((x, y))
        self.state += 1


def color_program(state):
    rules = [{'blue_min': 200, 'red_max': 40, 'green_max': 40},
             {'red_min': 200, 'blue_max': 40, 'green_max': 40},
             {'green_min': 100, 'red_max': 40, 'blue_max': 40}]
    return dict(calls=[dict(id='items', method='FindColorComponents', arguments={'color': rules[state], 'min_area': 100}),
                       dict(id='one', method='SelectOne', arguments={'items': {'result': 'items'}})],
                checks=[{'value': {'result': 'one'}, 'op': 'exists'}],
                target={'result': 'one', 'path': ['rect']})


def decision(state, **changes):
    result = dict(status='done' if state == 2 else 'act', message=f'第{state+1}步',
                  detail=f'已看到STATE {state}', name='测试技能', continuous=False,
                  previous='none' if state == 0 else 'succeeded',
                  action='none' if state == 2 else 'click', target=[60, 220, 210, 90],
                  evidence=[20, 30, 180, 50], expected=f'进入STATE {state+1}', method='', arguments='{}', program='{}')
    result.update(changes)
    if 'program' not in changes and (result['status'] in ('act', 'done') or result['previous'] == 'succeeded'):
        result['program'] = json.dumps(color_program(state))
    return result


def response(result):
    status = result['status']
    fields = {'plan': ('arguments', 'program'), 'batch': ('arguments',),
              'inspect': ('method', 'arguments'), 'define': ('method', 'arguments'),
              'act': ('action', 'arguments', 'expected', 'program'), 'wait': ('program',),
              'done': ('program',), 'blocked': ()}[status]
    values = {**result, 'program': json.loads(result['program']), 'arguments': json.loads(result['arguments'])}
    return {**{key: result[key] for key in ('message', 'detail', 'name', 'continuous')},
            'operation': {'status': status, **{key: values[key] for key in fields}}}


class VisualFlowTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.desktop = Desktop()
        self.stop = False
        self.point = Mock()
        self.flow = VisualFlow(self.desktop.window, self.directory, lambda: self.stop,
                               Mock(), Mock(), desktop=self.desktop, capture=Mock(), point=self.point)
        self.flow.wait = lambda seconds: self.flow.check_stop()

    def explore(self, decisions):
        with patch('huanzhizhi.skill_exploration.analyze_image', side_effect=decisions):
            return explore(self.flow, '完成两步', Mock(), Mock())

    def test_replay_reobserves_after_pause_before_selecting_the_next_action(self):
        recipe = dict(continuous=False, done={'program': color_program(2)}, nodes=[
            dict(action='click', message=f'步骤{i}', parameters={}, program=color_program(i),
                 after={'program': color_program(i+1)}) for i in range(2)])
        def checkpoint():
            if self.desktop.clicks:
                # The target completes independently while the task is paused.
                self.desktop.state = 2
                return True
            return False
        self.flow._pause = checkpoint
        self.assertIn('目标已完成', replay(self.flow, recipe))
        self.assertEqual(len(self.desktop.clicks), 1)

    def test_pending_input_can_be_verified_before_a_new_plan_in_one_round(self):
        node = dict(action='click', message='继续', expected='完成', parameters={},
                    program=color_program(1), after=color_program(2))
        plan = decision(1, status='plan', action='none', program=json.dumps(dict(
            nodes=[node], done=color_program(2), verification=color_program(1))))
        self.assertIn('技能已保存', self.explore([decision(0), plan]))
        self.assertEqual(len(self.desktop.clicks), 2)
        recipe = json.loads((self.directory / 'flow.json').read_text(encoding='utf8'))
        self.assertTrue(recipe['ready'])
        self.assertEqual(len(recipe['nodes']), 2)
        from huanzhizhi.tasks.catalog import TaskCatalog
        task = TaskCatalog(self.directory.parent).reload()[0]
        self.assertEqual(task.name, self.directory.name)
        manifest = json.loads((self.directory / 'skill.json').read_text(encoding='utf-8'))
        self.assertEqual(manifest['name'], recipe['name'])
        self.assertEqual(task.description, recipe['goal'])

    def test_structured_response_uses_host_pending_state_and_replays_without_ai(self):
        node = dict(action='click', message='继续', expected='完成', parameters={},
                    program=color_program(1), after=color_program(2))
        plan = decision(1, status='plan', action='none', program=json.dumps(dict(
            nodes=[node], done=color_program(2), verification=color_program(1))))
        with patch('huanzhizhi.skill_exploration.analyze_image', side_effect=[response(decision(0)), response(plan)]) as ai:
            self.assertIn('技能已保存', explore(self.flow, '完成两步', Mock(), Mock()))
        self.assertEqual(ai.call_count, 2)
        self.desktop.state = 0
        recipe = json.loads((self.directory / 'flow.json').read_text(encoding='utf8'))
        with patch('huanzhizhi.skill_exploration.analyze_image', side_effect=AssertionError('重放不能调用AI')):
            replay(self.flow, recipe)
        self.assertEqual(len(self.desktop.clicks), 4)

    def test_new_plan_does_not_execute_when_pending_verification_is_wrong(self):
        node = dict(action='click', message='继续', expected='完成', parameters={},
                    program=color_program(1), after=color_program(2))
        plan = decision(1, status='plan', action='none', program=json.dumps(dict(
            nodes=[node], done=color_program(2), verification=color_program(0))))
        with patch('huanzhizhi.skill_exploration.analyze_image', side_effect=[response(decision(0)), response(plan),
                    response(decision(1, status='blocked', action='none', program='{}', message='需要调查'))]):
            with self.assertRaisesRegex(ValueError, '需要调查'):
                explore(self.flow, '完成两步', Mock(), Mock())
        self.assertEqual(len(self.desktop.clicks), 1)
        self.assertFalse((self.directory / 'flow.json').exists())

    def test_verification_rechecks_latest_frame_after_slow_fallback_exhausts_deadline(self):
        self.desktop.state = 1
        now = [0.0]
        def slow_fallback(*args, **kwargs):
            self.desktop.state = 2
            now[0] = 2.0
            return None
        with patch('huanzhizhi.visual_flow.time.monotonic', side_effect=lambda:now[0]), patch('huanzhizhi.visual_flow.find_node', side_effect=slow_fallback):
            self.flow.verify({'program':color_program(2)}, timeout=1, nodes=[{}], before=self.desktop.images[0])

    def sequence_node(self):
        steps = [dict(action='click', target=[60, 220, 271, 311], parameters={},
                      when=color_program(i), after=color_program(i+1), skip=False) for i in range(2)]
        program = color_program(0)
        program['calls'].append(dict(id='queue', method='ReadText', arguments={}))
        node = dict(action='sequence', message='执行已求出的整批动作', expected='两步完成',
                    program=program, parameters={'steps': {'result': 'queue'}},
                    after={'program': color_program(2)})
        return node, steps

    def test_sequence_reuses_computed_actions_and_only_checks_local_state_between_inputs(self):
        node, steps = self.sequence_node()
        solves = []
        def solve(image, roi=None, language=''):
            solves.append(self.desktop.state)
            return steps
        with patch('huanzhizhi.vision.ReadText', new=solve):
            before = self.flow.execute(node)
            self.flow.verify(node['after'], before=before)
        self.assertEqual(self.desktop.state, 2)
        self.assertEqual(solves, [0, 0])  # Initial plan + fresh pre-input check, never per cell.

    def test_sequence_validates_the_entire_queue_before_first_input(self):
        node, steps = self.sequence_node()
        steps[-1]['target'] = [700, 220, 800, 311]
        def solve(image, roi=None, language=''):
            return steps
        with patch('huanzhizhi.vision.ReadText', new=solve), self.assertRaises(ValueError):
            self.flow.execute(node)
        self.assertEqual(self.desktop.clicks, [])

    def test_sequence_stops_when_local_verification_fails_without_repeating(self):
        node, steps = self.sequence_node()
        steps[0]['after'] = color_program(2)
        def solve(image, roi=None, language=''):
            return steps
        with patch('huanzhizhi.vision.ReadText', new=solve), patch.object(self.flow, 'verify', side_effect=TimeoutError('局部核对失败')):
            with self.assertRaisesRegex(TimeoutError, '局部核对失败'):
                self.flow.execute(node)
        self.assertEqual(len(self.desktop.clicks), 1)

    def test_sequence_skips_targets_already_changed_by_an_earlier_input(self):
        node, steps = self.sequence_node()
        steps.insert(1, {**steps[0], 'skip': True})
        def solve(image, roi=None, language=''):
            return steps
        with patch('huanzhizhi.vision.ReadText', new=solve):
            self.flow.execute(node)
        self.assertEqual(len(self.desktop.clicks), 2)

    def test_sequence_plan_saves_only_after_batch_verification_and_replays(self):
        node, steps = self.sequence_node()
        node['after'] = node['after']['program']
        plan = decision(0, status='plan', action='none', program=json.dumps(dict(nodes=[node], done=color_program(2))))
        def solve(image, roi=None, language=''):
            return steps
        with patch('huanzhizhi.vision.ReadText', new=solve):
            self.assertIn('技能已保存', self.explore([plan]))
            recipe = json.loads((self.directory/'flow.json').read_text(encoding='utf8'))
            self.assertEqual(len(recipe['nodes']), 1)
            self.desktop.state = 0
            self.assertIn('目标已完成', replay(self.flow, recipe))

    def test_plan_can_prepare_and_execute_in_the_same_ai_round(self):
        node, steps = self.sequence_node()
        node['after'] = node['after']['program']
        node['program']['calls'][-1] = 'queue'
        plan = decision(0, status='plan', action='none',
                        arguments=json.dumps({'items': [dict(kind='inspect', id='queue', method='ReadText', arguments={})]}),
                        program=json.dumps(dict(nodes=[node], done=color_program(2))))
        def solve(image, roi=None, language=''):
            return steps
        with patch('huanzhizhi.vision.ReadText', new=solve), patch('huanzhizhi.skill_exploration.analyze_image', return_value=plan) as ai:
            self.assertIn('技能已保存', explore(self.flow, '完成两步', Mock(), Mock()))
            self.assertEqual(ai.call_count, 1)

    def test_action_with_prospective_verification_uses_local_plan(self):
        seen = []
        def respond(*args, context, **kwargs):
            state = len(seen)
            self.assertEqual(self.desktop.state, state)
            self.assertIsNone(context['pending'])
            if state:
                self.assertTrue(any(f'第{state}步' in entry and '已核对' in entry
                                    for entry in context['history']))
            seen.append(state)
            if state == 2:
                return decision(2, previous='none')
            spec = {**color_program(state), 'verification': color_program(state+1)}
            return decision(state, previous='none', program=json.dumps(spec))
        self.assertIn('技能已保存', self.explore(respond))
        self.assertEqual(len(self.desktop.clicks), 2)

    def test_prospective_verification_already_true_prevents_action(self):
        spec = {**color_program(0), 'verification': color_program(0)}
        first = decision(0, program=json.dumps(spec))
        def respond(*args, context, **kwargs):
            if context['rejected_decision'] is None:
                return first
            self.assertIn('操作前', context['rejected_decision']['error'])
            return decision(0, status='blocked', action='none', message='核对规则无效')
        with self.assertRaisesRegex(ValueError, '核对规则无效'):
            self.explore(respond)
        self.assertEqual(self.desktop.clicks, [])

    def test_explore_saves_verified_skill_and_replays_from_start_without_ai(self):
        result = self.explore([decision(0), decision(1), decision(2)])
        self.assertIn('技能已保存', result)
        self.assertEqual(self.desktop.state, 2)
        recipe = json.loads((self.directory / 'flow.json').read_text(encoding='utf-8'))
        self.assertTrue(recipe['ready'])
        self.assertEqual(len(recipe['nodes']), 2)
        self.assertTrue((self.directory / 'task.py').is_file())
        self.desktop.state = 0
        with patch('huanzhizhi.skill_exploration.analyze_image') as ai:
            self.assertIn('目标已完成', replay(self.flow, recipe))
            ai.assert_not_called()
        self.assertEqual(len(self.desktop.clicks), 4)
        self.point.assert_not_called()  # The desktop API owns click feedback.

    def plan(self):
        nodes = [dict(action='click', message=f'步骤{i}', expected='颜色改变',
                      parameters={}, program=color_program(i), after=color_program(i+1))
                 for i in range(2)]
        return decision(0, status='plan', action='none', program=json.dumps(
            dict(nodes=nodes, done=color_program(2))))

    def test_one_plan_runs_and_verifies_multiple_steps_then_replays_without_ai(self):
        with patch('huanzhizhi.skill_exploration.analyze_image', return_value=self.plan()) as ai:
            self.assertIn('技能已保存', explore(self.flow, '完成两步', Mock(), Mock()))
            self.assertEqual(ai.call_count, 1)
        recipe = json.loads((self.directory / 'flow.json').read_text(encoding='utf-8'))
        self.assertEqual(len(recipe['nodes']), 2)
        self.desktop.state = 0
        self.assertIn('目标已完成', replay(self.flow, recipe))

    def test_plan_top_level_action_does_not_add_an_extra_input(self):
        proposed = self.plan()
        proposed['action'] = 'click'
        with patch('huanzhizhi.skill_exploration.analyze_image', return_value=proposed) as ai:
            self.assertIn('技能已保存', explore(self.flow, '完成两步', Mock(), Mock()))
        self.assertEqual(ai.call_count, 1)
        self.assertEqual(len(self.desktop.clicks), 2)

    def test_plan_cannot_accept_verification_already_true_before_input(self):
        proposed = self.plan()
        spec = json.loads(proposed['program'])
        spec['nodes'][0]['after'] = color_program(0)
        proposed['program'] = json.dumps(spec)
        def respond(*args, context, **kwargs):
            if not context['rejected_decision']:
                return proposed
            self.assertEqual(self.desktop.clicks, [])
            self.assertIn('操作前', context['rejected_decision']['error'])
            return decision(0, status='blocked', action='none', message='核对规则无效')
        with self.assertRaisesRegex(ValueError, '核对规则无效'):
            self.explore(respond)
        self.assertFalse((self.directory / 'task.py').exists())

    def test_plan_timeout_returns_pending_without_repeating_input(self):
        calls = 0
        def respond(*args, context, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                return self.plan()
            self.assertIsNotNone(context['pending'])
            self.assertEqual(len(self.desktop.clicks), 1)
            return decision(1, previous='failed', status='blocked', action='none', message='需要调查')
        with patch.object(self.flow, 'verify', side_effect=TimeoutError('未确认')):
            with self.assertRaisesRegex(ValueError, '需要调查'):
                self.explore(respond)
        self.assertFalse((self.directory / 'task.py').exists())

    def test_inspection_does_not_confirm_pending_input_without_verification(self):
        calls = 0
        def respond(*args, context, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                return self.plan()
            if calls == 2:
                return decision(1, status='inspect', action='none', previous='succeeded',
                                method='FindColorComponents', program='{}',
                                arguments=json.dumps(color_program(1)['calls'][0]['arguments']))
            self.assertIsNotNone(context['pending'])
            self.assertEqual(len(self.desktop.clicks), 1)
            self.assertEqual(context['inspections'][-1]['method'], 'FindColorComponents')
            return decision(1, previous='failed', status='blocked', action='none', message='需要调查')
        with patch.object(self.flow, 'verify', side_effect=TimeoutError('未确认')):
            with self.assertRaisesRegex(ValueError, '需要调查'):
                self.explore(respond)
        self.assertEqual(calls, 3)
        self.assertFalse((self.directory / 'task.py').exists())

    def test_batch_returns_dependent_results_in_one_model_round(self):
        batch = decision(0, status='batch', action='none', arguments=json.dumps({'items': [
            dict(kind='inspect', id='blue', method='FindColorComponents',
                 arguments=color_program(0)['calls'][0]['arguments']),
            dict(kind='inspect', id='count', method='CountItems', arguments={'items': {'result': 'blue'}}),
        ]}))
        calls = 0
        def respond(*args, context, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                return batch
            self.assertEqual(context['inspections'][-1]['id'], 'count')
            self.assertEqual(context['inspections'][-1]['result'], 1)
            self.assertEqual(self.desktop.clicks, [])
            return self.plan()
        self.explore(respond)
        self.assertEqual(calls, 2)

    def test_verified_local_input_clears_stale_inspections_even_for_small_visual_changes(self):
        calls = 0
        def respond(*args, context, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                return decision(0, status='inspect', action='none', method='FindColorComponents',
                                arguments=json.dumps(color_program(0)['calls'][0]['arguments']))
            if calls == 2:
                plan = self.plan()
                spec = json.loads(plan['program'])
                spec['nodes'] = spec['nodes'][:1]
                spec['done'] = None
                plan['program'] = json.dumps(spec)
                return plan
            self.assertEqual(context['inspections'], [])
            self.assertTrue(context['known_calls'])
            self.assertEqual(len(self.desktop.clicks), 1)
            return decision(0, status='blocked', action='none', message='检查完成')
        with patch('huanzhizhi.skill_exploration.scene_changed', return_value=False):
            with self.assertRaisesRegex(ValueError, '检查完成'):
                self.explore(respond)

    def test_batch_failure_retains_completed_results_and_skips_later_calls(self):
        batch = decision(0, status='batch', action='none', arguments=json.dumps({'items': [
            dict(kind='inspect', id='blue', method='FindColorComponents',
                 arguments=color_program(0)['calls'][0]['arguments']),
            dict(kind='inspect', id='bad', method='CountItems', arguments={'items': {'result': 'missing'}}),
            dict(kind='inspect', id='skipped', method='CountItems', arguments={'items': {'result': 'blue'}}),
        ]}))
        calls = 0
        def respond(*args, context, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                return batch
            self.assertEqual([row['id'] for row in context['inspections']], ['blue'])
            self.assertIn('missing', context['rejected_decision']['error'])
            self.assertEqual(self.desktop.clicks, [])
            return self.plan()
        self.explore(respond)
        self.assertEqual(calls, 2)

    def test_saved_skill_recognizes_buttons_again_after_they_move(self):
        self.explore([decision(0), decision(1), decision(2)])
        recipe = json.loads((self.directory / 'flow.json').read_text(encoding='utf-8'))
        for state in range(3):
            image = Image.new('RGB', (640, 400), 'white')
            draw = ImageDraw.Draw(image)
            draw.rectangle((360, 80, 570, 170), fill=('blue', 'red', 'green')[state])
            draw.text((380, 110), f'NEW {state+123}', fill='white')
            self.desktop.images[state] = image
        self.desktop.state = 0
        def moved_click(window, x, y, action='click', *, wait=None):
            self.assertTrue(360 <= x <= 570 and 80 <= y <= 170)
            self.desktop.clicks.append((x, y))
            self.desktop.state += 1
        self.desktop.post_click = moved_click
        with patch('huanzhizhi.skill_exploration.analyze_image') as ai:
            self.assertIn('目标已完成', replay(self.flow, recipe))
            ai.assert_not_called()

        self.assertEqual(self.desktop.state, 2)

    def test_rejected_replacement_keeps_its_error_instead_of_rerunning_old_plan(self):
        first = self.plan()
        spec = json.loads(first['program'])
        spec['nodes'][0]['after'] = color_program(0)
        first['program'] = json.dumps(spec)
        replacement = self.plan()
        spec = json.loads(replacement['program'])
        spec['nodes'][0]['program']['calls'] = ['missing_replacement_call']
        replacement['program'] = json.dumps(spec)
        rounds = 0
        def answer(*args, context, **kwargs):
            nonlocal rounds
            rounds += 1
            if rounds == 1:
                return first
            if rounds == 2:
                self.assertIn('操作前已经成立', context['rejected_decision']['error'])
                return replacement
            self.assertIn('missing_replacement_call', context['rejected_decision']['error'])
            self.assertEqual(self.desktop.clicks, [])
            return self.plan()
        with patch('huanzhizhi.skill_exploration.analyze_image', side_effect=answer):
            self.assertIn('技能已保存', explore(self.flow, '完成两步', Mock(), Mock()))
        self.assertEqual(rounds, 3)
        self.assertEqual(self.desktop.state, 2)

    def test_changed_target_after_model_response_is_not_clicked(self):
        def changed(*args, **kwargs):
            if self.desktop.state == 1:
                return decision(1, status='blocked', action='none', previous='none', message='画面变了，不能继续')
            self.desktop.state = 1
            return decision(0)
        with self.assertRaisesRegex(ValueError, '画面变了'):
            self.explore(changed)
        self.assertEqual(self.desktop.clicks, [])

    def test_rejected_pre_action_check_is_reported_to_next_analysis(self):
        calls = 0
        def respond(*args, context, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                self.desktop.state = 1
                return decision(0)
            self.assertIsNone(context['pending'])
            self.assertTrue(any('未执行' in item and '识别条件' in item
                                for item in context['history']))
            return decision(1, status='blocked', action='none', previous='none',
                            message='需要重新定位')
        with self.assertRaisesRegex(ValueError, '需要重新定位'):
            self.explore(respond)
        self.assertEqual(self.desktop.clicks, [])
        self.assertFalse((self.directory / 'task.py').exists())

    def test_invalid_plan_is_repaired_before_any_input(self):
        for bad_program in ({**color_program(0), 'unknown': color_program(1)},
                            {**color_program(0), 'target': [60, 220, 270, 310]}):
            with self.subTest(program=bad_program):
                self.desktop.state = 0
                self.desktop.clicks = []
                proposed = decision(0, program=json.dumps(bad_program))
                calls = 0
                def respond(*args, context, **kwargs):
                    nonlocal calls
                    calls += 1
                    if calls == 1:
                        return proposed
                    if calls == 2:
                        self.assertEqual(self.desktop.clicks, [])
                        self.assertIsNone(context['pending'])
                        self.assertEqual(context['rejected_decision']['decision'], proposed)
                        self.assertTrue(context['rejected_decision']['error'])
                    return decision(self.desktop.state)
                self.explore(respond)
                self.assertEqual(len(self.desktop.clicks), 2)

    def test_invalid_plans_still_consume_the_exploration_limit(self):
        invalid = decision(0, action='unsupported')
        with self.assertRaisesRegex(ValueError, '已探索40次'):
            self.explore([invalid] * 40)
        self.assertEqual(self.desktop.clicks, [])

    def test_invalid_dynamic_input_is_returned_for_repair_before_execution(self):
        calls = 0
        def respond(*args, context, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                return decision(0, action='key', arguments=json.dumps({'key': {'result': 'one', 'path': ['area']}}))
            if calls == 2:
                self.assertEqual(self.desktop.clicks, [])
                self.assertIsNone(context['pending'])
                self.assertIn('按键', context['rejected_decision']['error'])
            return decision(self.desktop.state)
        self.explore(respond)
        self.assertEqual(len(self.desktop.clicks), 2)

    def test_reuse_cannot_override_a_different_proposed_action(self):
        selected = self.desktop.images[0].copy()
        ImageDraw.Draw(selected).rectangle((360, 60, 570, 150), fill='red')
        self.desktop.images[1] = selected
        def click(window, x, y, action='click', *, wait=None):
            expected = (165, 265) if self.desktop.state == 0 else (465, 105)
            self.assertEqual((x, y), expected)
            self.desktop.clicks.append((x, y))
            self.desktop.state += 1
        self.desktop.post_click = click
        self.explore([decision(0), decision(1), decision(2)])
        self.assertEqual(self.desktop.clicks, [(165, 265), (465, 105)])

    def test_new_exploration_does_not_offer_existing_sudoku_task_or_resize_window(self):
        for size in ((826, 1551), (830, 1555)):
            with self.subTest(size=size):
                self.desktop.state = 0
                self.desktop.window = WindowInfo(123, '天天数独游戏', 0, 0, *size)
                self.flow.target = self.desktop.window
                self.flow.rule = WindowRule('天天数独游戏', *size)
                self.desktop.set_client_size = Mock(side_effect=AssertionError('不应调整窗口'))

                def respond(*args, context, **kwargs):
                    self.assertEqual(context['available_actions'], list(INPUT))
                    return decision(self.desktop.state)

                self.explore(respond)
                self.desktop.set_client_size.assert_not_called()

    def test_new_exploration_rejects_whole_task_even_in_supported_sudoku_window(self):
        self.desktop.window = WindowInfo(123, '天天数独游戏', 0, 0, 826, 1551)
        self.flow.target = self.desktop.window
        self.flow.rule = WindowRule('天天数独游戏', 826, 1551)
        with self.assertRaisesRegex(ValueError, '未知动作|缺少可执行能力'):
            self.explore([decision(0, action='sudoku_fill'),
                          decision(0, status='blocked', action='none', message='未知动作，无法修正')])
        self.assertEqual(self.desktop.clicks, [])
        self.assertFalse((self.directory / 'task.py').exists())

    def test_same_action_can_have_multiple_verified_result_pages(self):
        self.explore([decision(0), decision(1), decision(2)])
        recipe = json.loads((self.directory / 'flow.json').read_text(encoding='utf-8'))
        recipe['nodes'].append({**recipe['nodes'][0], 'after': recipe['done']})
        self.desktop.state = 0
        self.assertIn('目标已完成', replay(self.flow, recipe))
        self.assertEqual(len(self.desktop.clicks), 4)

    def test_ambiguous_click_positions_stop_before_input(self):
        self.explore([decision(0), decision(1), decision(2)])
        recipe = json.loads((self.directory / 'flow.json').read_text(encoding='utf-8'))
        recipe['nodes'].append({**recipe['nodes'][0], 'action': 'right_click'})
        self.desktop.state = 0
        with self.assertRaisesRegex(ValueError, '多个操作'):
            replay(self.flow, recipe)
        self.assertEqual(len(self.desktop.clicks), 2)

    def test_optional_page_can_be_skipped_only_to_another_verified_state(self):
        self.explore([decision(0), decision(1), decision(2)])
        recipe = json.loads((self.directory / 'flow.json').read_text(encoding='utf-8'))
        recipe['nodes'][0]['after'] = recipe['done']
        self.desktop.state = 0
        self.assertIn('目标已完成', replay(self.flow, recipe))
        self.assertEqual(len(self.desktop.clicks), 4)
        self.desktop.state = 0
        self.desktop.post_click = Mock()
        before = self.flow.execute(recipe['nodes'][0])
        with self.assertRaisesRegex(TimeoutError, '没有达到预期'):
            self.flow.verify(recipe['nodes'][0]['after'], timeout=.01,
                             nodes=recipe['nodes'], before=before)

    def test_stale_failure_during_animation_is_reobserved_without_reclicking(self):
        def animation_finished(*args, **kwargs):
            self.desktop.state = 2
            return decision(1, status='blocked', action='none', previous='failed')
        with patch('huanzhizhi.skill_exploration.analyze_image', side_effect=[decision(0), None, decision(2)]) as ai:
            def respond(*args, **kwargs):
                if ai.call_count == 1:
                    return decision(0)
                if ai.call_count == 2:
                    return animation_finished()
                return decision(2)
            ai.side_effect = respond
            self.assertIn('技能已保存', explore(self.flow, '完成流程', Mock(), Mock()))
        self.assertEqual(len(self.desktop.clicks), 1)

    def test_no_visible_result_cannot_be_saved_as_success(self):
        self.desktop.post_click = Mock()
        with self.assertRaisesRegex(ValueError, '操作前就已存在'):
            self.explore([decision(0), decision(0, status='done', action='none', previous='succeeded'),
                          decision(0, status='blocked', action='none', previous='failed', message='完成依据在操作前就已存在')])
        self.assertFalse((self.directory / 'task.py').exists())

    def test_wrong_result_check_is_repaired_without_losing_pending_input(self):
        calls = 0
        def respond(*args, context, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                return decision(0)
            if calls == 2:
                return decision(1, program=json.dumps({**color_program(1), 'verification': color_program(2)}))
            if calls == 3:
                self.assertEqual(len(self.desktop.clicks), 1)
                self.assertIsNotNone(context['pending'])
                self.assertIn('核对条件', context['rejected_decision']['error'])
            return decision(self.desktop.state)
        self.explore(respond)
        self.assertEqual(len(self.desktop.clicks), 2)

    def test_stop_after_first_click_prevents_next_action_and_publication(self):
        click = self.desktop.post_click
        def stop_after_click(*args, **kwargs):
            click(*args, **kwargs)
            self.stop = True
        self.desktop.post_click = stop_after_click
        with self.assertRaises(InterruptedError):
            self.explore([decision(0)])
        self.assertEqual(len(self.desktop.clicks), 1)
        self.assertFalse((self.directory / 'task.py').exists())

    def test_invalid_or_unavailable_action_is_rejected(self):
        for result in (decision(0, program=json.dumps({**color_program(0), 'target': [60, 220, 270, 310]})),
                       decision(0, action='sudoku_fill'), decision(0, previous='succeeded')):
            with self.subTest(result=result), self.assertRaises(ValueError):
                self.explore([result, decision(0, status='blocked', action='none', message='无法修正方案')])
        self.assertEqual(self.desktop.clicks, [])

    def test_ai_can_inspect_using_catalog_before_click_and_results_are_saved(self):
        def respond(*args, context, **kwargs):
            if not context['inspections'] and not self.desktop.clicks:
                names = {item['name'] for item in context['capabilities']}
                self.assertIn('FindColorComponents', names)
                return decision(0, status='inspect', action='none', method='FindColorComponents',
                                arguments=json.dumps({'color': {'blue_min': 180, 'red_max': 40}, 'min_area': 100}))
            if not self.desktop.clicks:
                self.assertEqual(context['inspections'][0]['method'], 'FindColorComponents')
                self.assertTrue(context['inspections'][0]['result'])
                program = color_program(0)
                program['calls'][0] = 'inspect-1'
                program['calls'][1]['arguments']['items']['result'] = 'inspect-1'
                return decision(0, program=json.dumps(program))
            return decision(self.desktop.state)
        self.explore(respond)
        recipe = json.loads((self.directory / 'flow.json').read_text(encoding='utf-8'))
        self.assertEqual(recipe['nodes'][0]['program']['calls'][0]['id'], 'inspect-1')
        self.assertEqual(recipe['nodes'][0]['program']['calls'][0]['method'], 'FindColorComponents')
        self.assertTrue((self.directory / 'inspect-1.json').is_file())
        self.assertEqual(len(self.desktop.clicks), 2)

    def test_call_definitions_survive_transition_without_stale_results(self):
        def respond(*args, context, **kwargs):
            if not self.desktop.clicks:
                if not context['inspections']:
                    return decision(0, status='inspect', action='none', method='FindColorComponents',
                                    arguments=json.dumps({'color': {'blue_min': 180, 'red_max': 40}, 'min_area': 100}))
                return decision(0)
            self.assertEqual(context['inspections'], [])
            previous_call = next(call for call in context['known_calls'] if call['id'] == 'inspect-1')
            self.assertEqual(previous_call['method'], 'FindColorComponents')
            self.assertNotIn('result', previous_call)
            return decision(self.desktop.state)
        self.explore(respond)
        self.assertEqual(len(self.desktop.clicks), 2)

    def test_failed_verification_does_not_retry_click(self):
        with self.assertRaisesRegex(ValueError, '按钮没有响应'):
            self.explore([decision(0), decision(1, status='blocked', action='none',
                                               previous='failed', message='按钮没有响应')])
        self.assertEqual(len(self.desktop.clicks), 1)
        self.assertFalse((self.directory / 'task.py').exists())

    def test_pre_input_rejection_returns_to_observation_without_pending_action(self):
        execute = self.flow.execute
        rejected = [False]
        def changed_once(node):
            if not rejected[0]:
                rejected[0] = True
                raise NoMatch('识别期间目标已变化，未执行操作')
            return execute(node)
        replies = [decision(0), decision(0), decision(1), decision(2)]
        def respond(*args, context, **kwargs):
            if len(replies) == 3:
                self.assertIsNone(context['pending'])
                self.assertEqual(self.desktop.clicks, [])
                self.assertIn('未执行', context['rejected_decision']['error'])
            return replies.pop(0)
        with patch.object(self.flow, 'execute', side_effect=changed_once):
            self.assertIn('技能已保存', self.explore(respond))
        self.assertEqual(len(self.desktop.clicks), 2)

    def test_reused_step_can_learn_a_new_result_without_repeating_input(self):
        self.desktop.images = [self.desktop.images[0], self.desktop.images[1],
                               self.desktop.images[0].copy(), self.desktop.images[2]]
        def click(window, x, y, action='click', *, wait=None):
            self.assertLess(self.desktop.state, 3)
            self.desktop.clicks.append((x, y))
            self.desktop.state += 1
        self.desktop.post_click = click
        original_verify = self.flow.verify
        self.flow.verify = lambda condition, **kwargs: original_verify(condition, timeout=.01, **kwargs)
        decisions = [decision(0), decision(1), decision(0, previous='succeeded')]
        def respond(*args, context, **kwargs):
            if decisions:
                return decisions.pop(0)
            self.assertEqual(len(self.desktop.clicks), 3)
            self.assertIsNotNone(context['pending'])
            self.assertTrue(Path(context['previous_image']).exists())
            self.assertIn('核对', context['history'][-1])
            return decision(2)
        self.assertIn('技能已保存', self.explore(respond))
        self.assertEqual(len(self.desktop.clicks), 3)
        recipe = json.loads((self.directory / 'flow.json').read_text(encoding='utf8'))
        self.assertEqual(len(recipe['nodes']), 3)

    def test_continuous_cycle_is_saved_then_runs_until_stop(self):
        click = self.desktop.post_click
        def cycle(*args, **kwargs):
            click(*args, **kwargs)
            if self.desktop.state == 2:
                self.desktop.state = 0
            if len(self.desktop.clicks) == 5:
                self.stop = True
        self.desktop.post_click = cycle
        with self.assertRaises(InterruptedError):
            self.explore([decision(0, continuous=True), decision(1, continuous=True),
                          decision(0, continuous=True, previous='succeeded')])
        self.assertEqual(len(self.desktop.clicks), 5)
        recipe = json.loads((self.directory / 'flow.json').read_text(encoding='utf-8'))
        self.assertTrue(recipe['ready'])
        self.assertTrue(recipe['continuous'])
        self.assertEqual(len(recipe['nodes']), 2)


if __name__ == '__main__':
    unittest.main()
