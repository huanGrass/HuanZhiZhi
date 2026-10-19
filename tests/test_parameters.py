from copy import deepcopy
import unittest

from huanzhizhi.parameters import parse_parameters, validate_values


CHOICE = {'key': 'mode', 'label': '对弈模式', 'type': 'choice', 'default': 'auto',
          'choices': [{'value': 'auto', 'label': '自动对弈'}, {'value': 'hint', 'label': '仅提示'}]}
INTEGER = {'key': 'count', 'label': '次数', 'default': 3, 'minimum': 1, 'maximum': 10}


class ParameterTests(unittest.TestCase):
    def test_runtime_is_explicit_and_strict_for_both_control_types(self):
        parameters = parse_parameters([{**CHOICE, 'runtime': True}, INTEGER])
        self.assertTrue(parameters[0].runtime)
        self.assertFalse(parameters[1].runtime)
        self.assertTrue(parse_parameters([{**INTEGER, 'runtime': True}])[0].runtime)
        for definition in (CHOICE, INTEGER):
            for value in (1, 'true', None):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    parse_parameters([{**definition, 'runtime': value}])

    def test_mixed_parameters_defaults_and_values(self):
        parameters = parse_parameters([CHOICE, INTEGER])
        self.assertEqual(validate_values(parameters, {}), {'mode': 'auto', 'count': 3})
        self.assertEqual(validate_values(parameters, {'mode': 'hint', 'count': 7}), {'mode': 'hint', 'count': 7})
        for values in ({'mode': 'other'}, {'mode': True}, {'mode': []}, {'count': False},
                       {'count': 11}, {'unknown': 1}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                validate_values(parameters, values)

    def test_invalid_choice_definitions_are_rejected(self):
        for changes in ({'default': 'other'}, {'choices': []}, {'choices': 'auto'},
                        {'choices': [{'value': 'auto', 'label': ''}]},
                        {'choices': [CHOICE['choices'][0]] * 2}, {'minimum': 0},
                        {'type': 'unknown'}, {'help': None}, {'default': []}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                parse_parameters([{**deepcopy(CHOICE), **changes}])
        with self.assertRaises(ValueError):
            parse_parameters([CHOICE, CHOICE])


if __name__ == '__main__':
    unittest.main()
