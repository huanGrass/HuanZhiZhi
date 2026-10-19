import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from PIL import Image

from huanzhizhi.skill_exploration import analyze_image, validate_decision


class SkillAnalysisTests(unittest.TestCase):
    def test_invalid_model_json_remains_available_for_repair(self):
        with tempfile.TemporaryDirectory() as folder:
            image = Path(folder) / 'latest.png'
            Image.new('RGB', (20, 20), 'white').save(image)
            process = Mock(returncode=0)
            process.poll.return_value = 0
            process.communicate.return_value = (b'', b'')
            malformed = '{"operation": {'
            def launch(command, **kwargs):
                Path(command[command.index('--output-last-message')+1]).write_text(malformed, encoding='utf8')
                return process
            with patch('huanzhizhi.agents.codex.analysis.shutil.which', return_value='codex.exe'), \
                    patch('huanzhizhi.agents.codex.analysis.ReadText', return_value={}), \
                    patch('huanzhizhi.agents.codex.analysis.subprocess.Popen', side_effect=launch):
                result = analyze_image(image, '目标', '窗口', lambda: False)
            self.assertEqual(result['invalid_response'], malformed)
            with self.assertRaisesRegex(ValueError, '返回JSON在第1行'):
                validate_decision(result, (20, 20), [], None)

    def test_all_four_requested_image_quadrants_reach_the_model(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            names = ['latest.png', 'top-left.png', 'top-right.png', 'bottom-left.png', 'bottom-right.png']
            for name in names:
                Image.new('RGB', (40, 40), 'white').save(directory / name)
            process = Mock(returncode=0)
            process.poll.return_value = 0
            process.communicate.return_value = (b'', b'')
            commands = []
            def launch(command, **kwargs):
                commands.append(command)
                Path(command[command.index('--output-last-message')+1]).write_text('{}', encoding='utf8')
                return process
            with patch('huanzhizhi.agents.codex.analysis.shutil.which', return_value='codex.exe'), \
                    patch('huanzhizhi.agents.codex.analysis.ReadText', return_value={}), \
                    patch('huanzhizhi.agents.codex.analysis.subprocess.Popen', side_effect=launch):
                analyze_image(directory / 'latest.png', '查看棋盘', '窗口', lambda: False,
                              context={'inspections': [{'result': {'asset': name}} for name in names[1:]]})
            command = commands[0]
            attached = [Path(command[i+1]).name for i, value in enumerate(command) if value == '--image']
            self.assertCountEqual(attached, names)

    def test_crop_images_reach_model_and_ocr_failure_does_not_block_visual_analysis(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            directory = root / 'skill'
            directory.mkdir()
            for path in (directory / 'latest.png', directory / 'before.png',
                         directory / 'crop.png', root / 'outside.png'):
                Image.new('RGB', (20, 20), 'white').save(path)
            process = Mock(returncode=0)
            process.poll.return_value = 0
            process.communicate.return_value = (b'', b'')
            commands = []
            def launch(command, **kwargs):
                commands.append(command)
                Path(command[command.index('--output-last-message')+1]).write_text(
                    '{"status":"inspect"}', encoding='utf8')
                return process
            context = dict(previous_image=str(directory / 'before.png'), inspections=[
                {'result': {'asset': 'crop.png'}},
                {'result': {'asset': '../outside.png'}},
                {'result': {'asset': 'crop.png'}},
            ])
            with patch('huanzhizhi.agents.codex.analysis.shutil.which', return_value='codex.exe'), \
                    patch('huanzhizhi.agents.codex.analysis.ReadText', side_effect=RuntimeError('language unavailable')), \
                    patch('huanzhizhi.agents.codex.analysis.subprocess.Popen', side_effect=launch):
                self.assertEqual(analyze_image(directory / 'latest.png', '目标', '窗口', lambda: False,
                                               context=context), {'status': 'inspect'})
            command = commands[0]
            attached = [Path(command[i+1]).resolve() for i, value in enumerate(command) if value == '--image']
            self.assertEqual(attached, [(directory / name).resolve() for name in ('latest.png', 'before.png', 'crop.png')])
            prompt = process.communicate.call_args.kwargs['input'].decode('utf8')
            payload = json.loads(prompt.rsplit('\n', 1)[-1])['context']
            self.assertEqual(payload['inspection_images'], ['crop.png'])
            self.assertEqual(payload['screen_text_error'], 'language unavailable')
            self.assertNotIn('screen_text_error', context)
