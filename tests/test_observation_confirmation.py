"""Shared read-only confirmation: bounded retries, explicit opt-in, and stop."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from huanzhizhi.task_sdk import ObservationPending, confirm_observation


class ObservationConfirmationTests(unittest.TestCase):
    def setUp(self):
        self.session = SimpleNamespace(stop_requested=False, emit_log=Mock(),
                                       wait_for_stop=Mock(return_value=False))

    def test_success_returns_result_without_waiting(self):
        value = object()
        observe = Mock(return_value=value)
        self.assertIs(confirm_observation(self.session, observe), value)
        observe.assert_called_once_with()
        self.session.wait_for_stop.assert_not_called()

    def test_fresh_check_recovers_and_records_each_attempt(self):
        records = []
        observe = Mock(side_effect=[ObservationPending('加载中'), ObservationPending('动画中'), 42])
        self.assertEqual(confirm_observation(self.session, observe, records=records), 42)
        self.assertEqual([r['error'] for r in records], ['加载中', '动画中', None])
        self.assertEqual([r['attempt'] for r in records], [1, 2, 3])
        self.assertEqual(self.session.wait_for_stop.call_count, 2)
        self.session.wait_for_stop.assert_called_with(.2)
        self.assertIn('通过', self.session.emit_log.call_args.args[0])

    def test_identical_and_changing_errors_exhaust_the_same_budget(self):
        for messages in (['未完成'] * 4, ['动画', '遮挡', '不完整', '未完成']):
            with self.subTest(messages=messages):
                self.setUp()
                errors = [ObservationPending(message) for message in messages]
                observe, records = Mock(side_effect=errors), []
                with self.assertRaises(ObservationPending) as caught:
                    confirm_observation(self.session, observe, records=records)
                self.assertIs(caught.exception, errors[-1])
                self.assertEqual(observe.call_count, 4)
                self.assertEqual(self.session.wait_for_stop.call_count, 3)
                self.assertEqual(len(records), 4)

    def test_unclassified_errors_and_interruptions_are_not_retried(self):
        for error in (ValueError('参数错误'), PermissionError('无权限'),
                      RuntimeError('程序错误'), InterruptedError('鼠标中断')):
            with self.subTest(error=error):
                observe = Mock(side_effect=error)
                with self.assertRaises(type(error)) as caught:
                    confirm_observation(self.session, observe)
                self.assertIs(caught.exception, error)
                observe.assert_called_once()
        self.session.wait_for_stop.assert_not_called()

    def test_stop_before_during_wait_and_after_observation_blocks_further_work(self):
        self.session.stop_requested = True
        observe = Mock()
        with self.assertRaises(InterruptedError):
            confirm_observation(self.session, observe)
        observe.assert_not_called()
        self.session.stop_requested = False
        self.session.wait_for_stop.return_value = True
        observe.side_effect = ObservationPending('动画中')
        with self.assertRaises(InterruptedError):
            confirm_observation(self.session, observe)
        observe.assert_called_once()

        def stopped_observation():
            self.session.stop_requested = True
            return '成功但用户已停止'
        with self.assertRaises(InterruptedError):
            confirm_observation(self.session, stopped_observation)

    def test_invalid_budgets_do_not_run_observation(self):
        observe = Mock()
        for kwargs in ({'retries': -1}, {'retries': True}, {'retries': 1.5},
                       {'interval': 0}, {'interval': float('inf')},
                       {'interval': float('nan')}, {'interval': True},
                       {'timeout': 0}, {'timeout': True}, {'timeout': float('inf')}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                confirm_observation(self.session, observe, **kwargs)
        observe.assert_not_called()

    def test_elapsed_budget_includes_checks_and_prevents_an_extra_observation(self):
        now, errors, records = 0., [], []
        waits = []
        def observe():
            nonlocal now
            now += .3
            error = ObservationPending(f'检测 {len(errors) + 1}')
            errors.append(error)
            raise error
        def wait(seconds):
            nonlocal now
            waits.append(seconds)
            now += seconds
            return False
        self.session.wait_for_stop = wait
        with patch('huanzhizhi.task_sdk.time.monotonic', side_effect=lambda: now):
            with self.assertRaises(ObservationPending) as caught:
                confirm_observation(self.session, observe, timeout=.9, retries=20, records=records)
        self.assertIs(caught.exception, errors[-1])
        self.assertEqual(len(errors), 2)
        self.assertAlmostEqual(now, .9)
        self.assertAlmostEqual(waits[-1], .1)
        self.assertAlmostEqual(records[-1]['elapsed'], .8)


if __name__ == '__main__':
    unittest.main()
