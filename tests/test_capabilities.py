import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import numpy as np

from PIL import Image, ImageDraw

from huanzhizhi.capabilities import VisionCalls, arguments, catalog
from huanzhizhi.platform.target_window import Win32DesktopApi, WindowInfo, WM_LBUTTONUP
from huanzhizhi.visual_flow import validate_input


class CapabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.calls = VisionCalls(self.directory)
        self.image = Image.new('RGB', (320, 200), 'white')
        draw = ImageDraw.Draw(self.image)
        draw.rectangle((50, 60, 99, 99), fill='red')
        draw.line((50, 60, 99, 99), fill='black', width=3)

    def test_invalid_json_reports_the_failing_region_without_accepting_it(self):
        broken = '{"padding":"' + 'x'*3000 + '","broken_call":{,}}'
        with self.assertRaises(ValueError) as failure:
            arguments(broken)
        message = str(failure.exception)
        self.assertIn('broken_call', message)
        self.assertIn('错误附近', message)
        self.assertLess(len(message), 600)

    def test_local_recognition_converts_only_its_roi(self):
        from huanzhizhi.vision import rects
        with patch.object(rects, '_rgb_array', wraps=rects._rgb_array) as convert:
            rects._crop_array(self.image, [50, 60, 70, 90])
            self.assertEqual(convert.call_args.args[0].size, (20, 30))

    def test_full_grid_candidates_are_not_forced_into_small_batches(self):
        image = Image.new('RGB', (320, 320), 'white')
        draw = ImageDraw.Draw(image)
        for row in range(16):
            for col in range(16):
                draw.rectangle((col*20+4, row*20+4, col*20+15, row*20+15), fill='black')
        params = dict(color={'red_max': 10, 'green_max': 10, 'blue_max': 10}, min_area=1, max_count=256)
        found = self.calls.evaluate('FindColorComponents', params, image)
        self.assertEqual(len(found), 256)
        self.assertEqual(len({tuple(item['rect']) for item in found}), 256)
        for value in (0, 1001, True, 1.5):
            with self.subTest(max_count=value), self.assertRaisesRegex(ValueError, 'max_count'):
                self.calls.evaluate('FindColorComponents', {**params, 'max_count': value}, image)

    def test_batch_definition_accepts_nested_sequence_test_expectations(self):
        check = {'calls': [{'id': 'closed', 'method': 'FindTemplate',
                            'arguments': {'template': {'asset': 'cell.png'}, 'roi': [0, 0, 20, 20]}}],
                 'checks': [{'value': {'result': 'closed'}, 'op': 'exists'}]}
        expected = {'steps': [{'action': 'click', 'target': [0, 0, 20, 20], 'parameters': {},
                               'when': check, 'after': check, 'skip': True}]}
        batch = {'items': [{'kind': 'define', 'method': 'custom_queue', 'arguments': {
            'description': 'sequence fixture', 'source': 'def run(data): return data',
            'tests': [{'data': expected, 'expected': expected}]}}]}
        self.assertEqual(arguments(json.dumps(batch)), batch)
        too_deep = {}
        for _ in range(18):
            too_deep = {'nested': too_deep}
        with self.assertRaisesRegex(ValueError, '嵌套过深'):
            arguments(json.dumps(too_deep))

    def test_crop_before_conversion_preserves_pixels_and_empty_regions(self):
        from huanzhizhi.vision.rects import _crop_array, _rgb_array, ToGameRect
        for image in (self.image, self.image.convert('L'), self.image.convert('RGBA'), np.asarray(self.image)):
            for roi in (None, [50, 60, 100, 100], [.1, .2, .8, .9], [-10, -5, 30, 40], [100, 100, 50, 60], [0, 0, 0, 0]):
                with self.subTest(mode=getattr(image, 'mode', 'array'), roi=roi):
                    box = ToGameRect(image, roi)
                    expected = _rgb_array(image)[box.top:box.bottom, box.left:box.right]
                    actual, actual_box = _crop_array(image, roi)
                    np.testing.assert_array_equal(actual, expected)
                    self.assertEqual(actual_box, box)

    def test_color_mask_result_can_feed_component_detection(self):
        first = self.calls.call('BuildColorMask', {'color': {'red_min': 200, 'green_max': 40}}, self.image)
        second = self.calls.call('FindMaskComponents', {'mask': {'result': first['id']}, 'nms_threshold': 1.0}, self.image)
        boxes = [item['rect'] for item in second['result']]
        self.assertEqual(len(boxes), 2)  # The black diagonal splits the red area.
        self.assertEqual([min(b[0] for b in boxes), min(b[1] for b in boxes),
                          max(b[2] for b in boxes), max(b[3] for b in boxes)], [50, 60, 100, 100])
        self.assertTrue((self.directory / first['result']['asset']).is_file())

    def test_template_crop_and_saved_asset_have_same_coordinates(self):
        crop = self.calls.call('CropRect', {'rect': [50, 60, 100, 100]}, self.image)
        for source in ({'crop': [50, 60, 50, 40]}, {'asset': crop['result']['asset']}):
            found = self.calls.call('FindTemplate', {'template': source, 'min_score': .98}, self.image)
            self.assertEqual(found['result']['rect'], [50, 60, 100, 100])

    def test_raw_signatures_remain_composable_without_serializing_binary_keys(self):
        record = self.calls.call('BuildColorBlockSignature', {'image': {'crop': [50, 60, 50, 40]}}, self.image)
        match = self.calls.call('CompareColorBlockSignatures', {
            'query_signature': {'result': record['id']}, 'template_signature': {'result': record['id']}}, self.image)
        self.assertEqual(match['result']['score'], 1)
        self.assertNotIn('blocks', record['result'])

    def test_catalog_and_callable_signatures_agree(self):
        names = {entry['name'] for entry in catalog()}
        self.assertIn('ReadText', names)
        self.assertIn('FindFeature', names)
        self.assertNotIn('sudoku_fill', names)
        with self.assertRaises(TypeError):
            self.calls.call('FindTemplate', {'nonsense': 1}, self.image)
        with self.assertRaises(ValueError):
            self.calls.call('__import__', {}, self.image)

    def test_asset_path_and_nonfinite_parameters_are_rejected(self):
        for asset in ('../outside.png', 'C:/outside.png', 'task.py'):
            with self.assertRaises(ValueError):
                self.calls.call('ImageHash', {'image': {'asset': asset}}, self.image)
        with self.assertRaises(ValueError):
            arguments('{"min_score":NaN}')
        with self.assertRaises(ValueError):
            validate_input('drag', {'end': [999, 3], 'duration': .5}, self.image.size)
        with self.assertRaises(ValueError):
            validate_input('text', {'text': 'abc\n'}, self.image.size)

    def test_feature_matching_does_not_report_blank_image_as_match(self):
        record = self.calls.call('FindFeature', {'template': {'crop': [150, 50, 80, 80]}}, self.image)
        self.assertFalse(record['result']['matched'])
        self.assertIsNone(record['result']['rect'])

    def test_drag_stop_and_key_stop_release_pressed_input(self):
        native = Mock()
        native.post_message.return_value = True
        api = Win32DesktopApi(native=native, configure_dpi_awareness=False)
        window = WindowInfo(123, 'fixture', -200, 100, 320, 200)
        api._require_unchanged = Mock(return_value=window)
        stop = Mock(side_effect=InterruptedError)
        with self.assertRaises(InterruptedError):
            api.post_drag(window, 30, 30, 180, 100, wait=stop)
        self.assertEqual(native.post_message.call_args.args[1], WM_LBUTTONUP)
        with self.assertRaises(InterruptedError):
            api.post_key(window, 'ENTER', wait=stop)
        self.assertEqual(native.post_message.call_args.args[1], 0x101)
        api.post_scroll(window, 50, 30, -120)
        _, message, wparam, lparam = native.post_message.call_args.args
        self.assertEqual(message, 0x20A)
        self.assertEqual(wparam >> 16, (-120) & 0xFFFF)
        self.assertEqual(lparam & 0xFFFF, (-150) & 0xFFFF)


if __name__ == '__main__':
    unittest.main()
