import unittest
from unittest.mock import patch

from huanzhizhi.vision.ocr import FindText


class OcrTextMatchingTests(unittest.TestCase):
    def test_chinese_ocr_spacing_preserves_original_boxes_and_ambiguity(self):
        lines = [dict(text='关 闭', rect=[10, 20, 40, 50]),
                 dict(text='关闭', rect=[60, 20, 90, 50]),
                 dict(text='关闭窗口', rect=[10, 60, 90, 90])]
        with patch('huanzhizhi.vision.ocr.ReadText', return_value={'lines': lines}):
            self.assertEqual(FindText(None, '关闭', exact=True), lines[:2])
            self.assertEqual(FindText(None, '关 闭'), lines)
        self.assertEqual(lines[0]['text'], '关 闭')

    def test_latin_word_boundaries_remain_significant(self):
        lines = [dict(text='Sign in'), dict(text='Signin')]
        with patch('huanzhizhi.vision.ocr.ReadText', return_value={'lines': lines}):
            self.assertEqual(FindText(None, 'Sign in', exact=True), lines[:1])
            self.assertEqual(FindText(None, 'Signin'), lines[1:])
