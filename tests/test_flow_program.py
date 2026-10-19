import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from PIL import Image, ImageDraw

from huanzhizhi.capabilities import VisionCalls
from huanzhizhi.flow_program import compile_program, run_program, validate_program
from tests.test_visual_flow import color_program, Desktop
from huanzhizhi.visual_flow import VisualFlow, replay, save_condition


class ProgramTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.image = Desktop().images[0]

    def test_saved_result_references_use_new_data_not_recorded_values(self):
        program = color_program(0)
        program['calls'].append(dict(id='data', method='ReadText', arguments={}))
        program = json.loads(json.dumps(program))  # Same representation as saved skills.
        def read(image, roi=None, language=''):
            return {'text': str(image.getpixel((0, 0))[0])}
        with patch('huanzhizhi.vision.ReadText', new=read):
            for number in (17, 93):
                self.image.putpixel((0, 0), (number, 0, 0))
                result = run_program(program, self.image, self.directory,
                                     parameters={'text': {'result': 'data', 'path': ['text']}})
                self.assertEqual(result['parameters']['text'], str(number))

    def test_compiled_template_is_reference_asset_and_search_is_live(self):
        calls = VisionCalls(self.directory)
        sample = calls.call('CropRect', {'rect': [60, 220, 271, 311]}, self.image)
        found = calls.call('FindTemplateList', {'template': {'result': sample['id']}, 'min_score': .99}, self.image)
        spec = dict(calls=[found['id'], dict(id='one', method='SelectOne', arguments={'items': {'result': found['id']}})],
                    checks=[{'value': {'result': 'one'}, 'op': 'exists'}], target={'result': 'one', 'path': ['rect']})
        program = compile_program(spec, calls, self.image, 'reference', action=True, parameters={})
        new = Image.new('RGB', self.image.size, 'white')
        new.paste(self.image.crop((60, 220, 271, 311)), (340, 50))
        result = run_program(program, new, self.directory, parameters={})
        self.assertEqual(result['target'], [340, 50, 551, 141])
        self.assertIsNone(run_program(program, Image.new('RGB', self.image.size, 'white'), self.directory, parameters={}))

    def test_ambiguous_and_missing_recognition_never_choose_first_or_old_target(self):
        program = color_program(0)
        self.assertIsNone(run_program(program, Image.new('RGB', self.image.size, 'white'), self.directory, parameters={}))
        ImageDraw.Draw(self.image).rectangle((360, 60, 550, 160), fill='blue')
        with self.assertRaisesRegex(ValueError, '多个候选'):
            run_program(program, self.image, self.directory, parameters={})

    def test_reusing_call_keeps_learned_template_after_original_region_changes(self):
        calls = VisionCalls(self.directory)
        found = calls.call('FindTemplateList', {'template': {'crop': [60, 220, 211, 91]}, 'min_score': .99}, self.image)
        spec = dict(calls=[found['id'], dict(id='one', method='SelectOne', arguments={'items': {'result': found['id']}})],
                    checks=[{'value': {'result': 'one'}, 'op': 'exists'}], target={'result': 'one', 'path': ['rect']})
        compile_program(spec, calls, self.image, 'first', action=True, parameters={})
        changed = Desktop().images[1].copy()
        changed.paste(self.image.crop((60, 220, 271, 311)), (340, 50))
        reused = compile_program(spec, calls, changed, 'second', action=True, parameters={})
        result = run_program(reused, changed, self.directory, parameters={})
        self.assertEqual(result['target'], [340, 50, 551, 141])

    def test_reused_template_shares_recognition_cache_between_nodes(self):
        calls = VisionCalls(self.directory)
        found = calls.call('FindTemplateList', {'template': {'crop': [60, 220, 211, 91]}, 'min_score': .99}, self.image)
        spec = dict(calls=[found['id']], checks=[dict(value={'result': found['id']}, op='exists')])
        first = compile_program(spec, calls, self.image, 'first')
        second = compile_program(spec, calls, self.image, 'second')
        from huanzhizhi import vision
        recognize = vision.FindTemplateList
        count = 0
        def counted(image, template, **kwargs):
            nonlocal count
            count += 1
            return recognize(image, template, **kwargs)
        with patch('huanzhizhi.vision.FindTemplateList', new=counted):
            cache = {}
            self.assertIsNotNone(run_program(first, self.image, self.directory, cache=cache))
            self.assertIsNotNone(run_program(second, self.image, self.directory, cache=cache))
            self.assertEqual(count, 1)

    def test_identical_inline_inspected_call_reuses_definition_but_conflicts_fail(self):
        calls = VisionCalls(self.directory)
        record = calls.call('CountRgb', {'color': {'red_min': 200}}, self.image,
                            identifier='known_pixels')
        definition = calls.definitions[record['id']]
        spec = dict(calls=[definition], checks=[dict(value={'result': record['id']}, op='ge', expected=0)])
        program = compile_program(spec, calls, self.image, 'reuse-inline')
        self.assertIsNotNone(run_program(program, self.image, self.directory))
        conflicting = json.loads(json.dumps(spec))
        conflicting['calls'][0]['arguments']['color']['red_min'] = 100
        with self.assertRaisesRegex(ValueError, '重复.*known_pixels'):
            compile_program(conflicting, calls, self.image, 'conflicting')
        with self.assertRaisesRegex(ValueError, '重复'):
            compile_program({**spec, 'calls': [definition, definition]}, calls, self.image, 'duplicate')

    def test_freezing_new_template_never_overwrites_an_existing_reference(self):
        spec = dict(calls=[dict(id='found', method='FindTemplate', arguments={
                        'template': {'crop': [60, 220, 211, 91]}, 'min_score': .99})],
                    checks=[dict(value={'result': 'found'}, op='exists')])
        first = compile_program(spec, VisionCalls(self.directory), self.image, 'same-prefix')
        first_asset = first['calls'][0]['arguments']['template']['asset']
        original = (self.directory/first_asset).read_bytes()
        second = compile_program(spec, VisionCalls(self.directory), Desktop().images[1], 'same-prefix')
        second_asset = second['calls'][0]['arguments']['template']['asset']
        self.assertNotEqual(first_asset, second_asset)
        self.assertEqual((self.directory/first_asset).read_bytes(), original)

    def test_program_rejects_constant_targets_stale_images_and_forward_references(self):
        for change in ('target', 'image', 'reference'):
            program = color_program(0)
            if change == 'target':
                program['target'] = [60, 220, 271, 311]
            elif change == 'image':
                program['calls'][0]['arguments']['image'] = {'asset': 'latest.png'}
            else:
                program['calls'][1]['arguments']['items'] = {'result': 'missing'}
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_program(program, action=True, parameters={})
        spec = color_program(0)
        spec['calls'][0]['arguments']['image'] = {'result': 'one'}
        with self.assertRaisesRegex(ValueError, '循环'):
            compile_program(spec, VisionCalls(self.directory), self.image, 'cycle', action=True)

    def test_changed_data_during_recognition_prevents_input(self):
        desktop = Desktop()
        first = desktop.images[0].copy()
        first.putpixel((0, 0), (17, 0, 0))
        second = first.copy()
        second.putpixel((0, 0), (93, 0, 0))
        desktop.capture_client = Mock(side_effect=[first, second])
        desktop.post_text = Mock()
        flow = VisualFlow(desktop.window, self.directory, lambda: False, Mock(), Mock(), desktop=desktop, capture=Mock())
        flow.wait = Mock()
        program = color_program(0)
        program['calls'].append(dict(id='data', method='ReadText', arguments={}))
        def read(image, roi=None, language=''):
            return {'text': str(image.getpixel((0, 0))[0])}
        node = dict(action='text', message='输入当前内容', program=program,
                    parameters={'text': {'result': 'data', 'path': ['text']}})
        with patch('huanzhizhi.vision.ReadText', new=read):
            with self.assertRaisesRegex(ValueError, '变化'):
                flow.execute(node)
        desktop.post_text.assert_not_called()

    def test_old_version_one_skills_still_replay(self):
        desktop = Desktop()
        flow = VisualFlow(desktop.window, self.directory, lambda: False, Mock(), Mock(), desktop=desktop, capture=Mock())
        flow.wait = Mock()
        states = [save_condition(image, [20, 30, 180, 50], self.directory, f'state-{i}.png')
                  for i, image in enumerate(desktop.images)]
        nodes = [dict(action='click', message='旧步骤', expected='下一页', before=states[i], after=states[i+1],
                      target=save_condition(desktop.images[i], [60, 220, 210, 90], self.directory, f'target-{i}.png'))
                 for i in range(2)]
        self.assertIn('目标已完成', replay(flow, dict(version=1, nodes=nodes, done=states[2], continuous=False)))
        self.assertEqual(len(desktop.clicks), 2)

    def test_previous_results_only_evaluate_their_own_dependencies(self):
        program = color_program(0)
        program['calls'].extend([
            dict(id='count', method='CountItems', arguments={'items': {'result': 'items'}}),
            dict(id='center', method='RectCenter', arguments={'rect': {'result': 'one', 'path': ['rect']}}),
        ])
        program['checks'] = [
            dict(value={'result': 'center'}, op='exists'),
            dict(value={'result': 'count'}, op='gt', expected={'previous': 'count'}),
        ]
        before = Image.new('RGB', self.image.size, 'white')
        self.assertIsNotNone(run_program(program, self.image, self.directory, before=before))
        self.assertIsNone(run_program(program, self.image, self.directory, before=self.image))


if __name__ == '__main__':
    unittest.main()
