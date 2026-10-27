import unittest
import json
import os
from pathlib import Path
import tempfile
from unittest.mock import Mock, patch

from huanzhizhi.platform.target_window import DesktopWindowsError, Win32DesktopApi, WindowInfo


class WindowDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.native = Mock()
        self.native.enum_windows.return_value = [1, 2, 3, 4, 5, 6]
        self.native.is_window.return_value = True
        self.native.is_window_visible.side_effect = lambda hwnd: hwnd != 4
        self.native.is_window_minimized.return_value = False
        self.native.is_window_cloaked.side_effect = lambda hwnd: hwnd == 2
        self.native.is_shell_window.side_effect = lambda hwnd: hwnd == 3
        self.native.get_window_title.side_effect = lambda hwnd: '' if hwnd == 5 else f'window {hwnd}'
        self.native.get_client_rect.side_effect = lambda hwnd: (0, 0, 50, 50) if hwnd == 6 else (0, 0, 800, 600)
        self.native.client_to_screen.side_effect = lambda hwnd, x, y: (x, y)
        self.api = Win32DesktopApi(native=self.native, configure_dpi_awareness=False)

    def test_cloaked_and_shell_windows_are_excluded_with_existing_filters(self):
        self.assertEqual([window.hwnd for window in self.api.list_windows()], [1])
        queried = [call.args[0] for call in self.native.get_window_title.call_args_list]
        self.assertNotIn(2, queried)
        self.assertNotIn(3, queried)
        self.assertNotIn(4, queried)

    def test_failure_to_check_cloaking_is_reported(self):
        self.native.is_window_cloaked.side_effect = DesktopWindowsError('DWM unavailable')
        with self.assertRaisesRegex(DesktopWindowsError, 'DWM unavailable'):
            self.api.list_windows()

    def test_minimized_browser_is_listed_alongside_another_window_with_same_title(self):
        self.native.get_window_title.side_effect = lambda hwnd: '' if hwnd == 5 else 'Microsoft Edge'
        self.native.get_client_rect.side_effect = lambda hwnd: (0, 0, 288, 37) if hwnd == 6 else (0, 0, 800, 600)
        self.native.is_window_minimized.side_effect = lambda hwnd: hwnd == 6
        self.assertEqual([window.hwnd for window in self.api.list_windows()], [1, 6])
        self.native.show_window.assert_not_called()

    def test_restore_reads_client_dimensions_after_restoring_minimized_window(self):
        info = self.api.list_windows()[0]
        self.native.is_window_minimized.side_effect = [True, False]
        self.native.show_window.side_effect = lambda *args: setattr(
            self.native.get_client_rect, 'side_effect', lambda hwnd: (0, 0, 1024, 768))
        restored = self.api.restore(info)
        self.native.show_window.assert_called_once_with(info.hwnd, 9)
        self.assertEqual((restored.width, restored.height), (1024, 768))

    def test_restore_does_not_change_normal_window_state(self):
        info = self.api.list_windows()[0]
        self.assertEqual(self.api.restore(info), info)
        self.native.show_window.assert_not_called()

    def test_failed_restore_is_reported(self):
        info = self.api.list_windows()[0]
        self.native.is_window_minimized.return_value = True
        with self.assertRaisesRegex(DesktopWindowsError, 'still minimized'):
            self.api.restore(info)

    def test_minimized_window_does_not_wait_for_capture_or_restore_itself(self):
        info = self.api.list_windows()[0]
        self.native.is_window_minimized.return_value = True
        capture = Mock()
        self.api._capture = capture
        with self.assertRaisesRegex(DesktopWindowsError, 'target window is minimized'):
            self.api.capture_client(info)
        capture.capture_client.assert_not_called()
        self.native.show_window.assert_not_called()
        self.native.is_window_minimized.return_value = False
        self.native.get_extended_frame_bounds.return_value = (0, 0, 800, 600)
        from PIL import Image
        capture.capture_client.return_value = Image.new('RGB', (800, 600))
        self.assertEqual(self.api.capture_client(info).size, (800, 600))


class AnimatedClickTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.events = Path(temporary.name) / 'clicks.jsonl'
        self.events.touch()
        environment = patch.dict(os.environ, HUANZHIZHI_CLICK_EVENTS=str(self.events))
        environment.start()
        self.addCleanup(environment.stop)
        self.native = Mock()
        self.native.post_message.return_value = True
        self.api = Win32DesktopApi(native=self.native, configure_dpi_awareness=False)
        self.window = WindowInfo(123, 'fixture', -200, 100, 320, 200)
        self.api._require_unchanged = Mock(return_value=self.window)

    def test_all_click_actions_report_one_physical_point_and_preserve_messages(self):
        for action, messages in (
            ('click', [0x200, 0x201, 0x202]),
            ('right_click', [0x200, 0x204, 0x205]),
            ('double_click', [0x200, 0x201, 0x202, 0x203, 0x202]),
        ):
            with self.subTest(action=action):
                self.events.write_bytes(b'')
                self.native.reset_mock()
                self.api.post_click(self.window, 30, 40, action, wait=lambda _: None)
                self.assertEqual([c.args[1] for c in self.native.post_message.call_args_list], messages)
                records = [json.loads(line) for line in self.events.read_bytes().splitlines()]
                self.assertEqual(records, [dict(protocol_version=1, type='point', x=-170, y=140)])

    def test_invalid_or_moved_target_does_not_click_or_animate(self):
        for x, y, action in ((320, 40, 'click'), (True, 40, 'click'), (30, -1, 'click'), (30, 40, 'other')):
            with self.assertRaises((ValueError, TypeError, DesktopWindowsError)):
                self.api.post_click(self.window, x, y, action)
        self.api._require_unchanged.side_effect = DesktopWindowsError('window moved')
        with self.assertRaises(DesktopWindowsError):
            self.api.post_click(self.window, 30, 40)
        self.native.post_message.assert_not_called()
        self.assertEqual(self.events.read_bytes(), b'')

    def test_hint_reuses_click_coordinates_without_any_input_in_all_input_modes(self):
        for mode in ('background', 'background_smooth', 'foreground'):
            for action in ('click', 'right_click', 'double_click'):
                with self.subTest(mode=mode, action=action), \
                        patch.dict(os.environ, HUANZHIZHI_INPUT_MODE=mode), \
                        patch('huanzhizhi.platform.target_window._request_foreground_input') as request:
                    self.events.write_bytes(b'')
                    self.api.post_click(self.window, 30, 40, action, hint=True)
                    event = json.loads(self.events.read_text(encoding='utf-8'))
                    self.assertEqual((event['type'], event['kind'], event['points']),
                                     ('hint', action, [[-170, 140]]))
                    request.assert_not_called()
        self.native.post_message.assert_not_called()

    def test_hint_drag_reuses_both_endpoints_and_duration_without_input(self):
        self.api.post_drag(self.window, 30, 40, 100, 150, duration=.8, hint=True)
        from huanzhizhi.tasks.protocol import parse_event_line
        event = parse_event_line(self.events.read_text(encoding='utf-8'))
        self.assertEqual((event.kind, event.points, event.duration), ('drag', ((-170, 140), (-100, 250)), .8))
        self.native.post_message.assert_not_called()

    def test_hint_keeps_target_and_coordinate_validation_before_display(self):
        for action in (lambda: self.api.post_click(self.window, 320, 40, hint=True),
                       lambda: self.api.post_drag(self.window, 30, 40, 100, 200, hint=True),
                       lambda: self.api.post_click(self.window, 30, 40, hint='yes')):
            with self.assertRaises((ValueError, TypeError)):
                action()
        self.api._require_unchanged.side_effect = DesktopWindowsError('window moved')
        with self.assertRaises(DesktopWindowsError):
            self.api.post_click(self.window, 30, 40, hint=True)
        self.assertEqual(self.events.read_bytes(), b'')
        self.native.post_message.assert_not_called()

    def test_closed_or_missing_feedback_channel_prevents_silent_click(self):
        self.events.unlink()
        with self.assertRaises(FileNotFoundError):
            self.api.post_click(self.window, 30, 40)
        with patch.dict(os.environ, {}, clear=True), patch('huanzhizhi.task_sdk._active_session', None):
            with self.assertRaisesRegex(RuntimeError, '点击需要连接桌宠'):
                self.api.post_click(self.window, 30, 40)
        self.native.post_message.assert_not_called()

    def test_double_click_interruption_does_not_send_second_press(self):
        with self.assertRaises(InterruptedError):
            self.api.post_click(self.window, 30, 40, 'double_click', wait=Mock(side_effect=InterruptedError))
        self.assertEqual([c.args[1] for c in self.native.post_message.call_args_list], [0x200, 0x201, 0x202])
        self.assertEqual(len(self.events.read_bytes().splitlines()), 1)

    def test_failed_button_down_still_releases_button(self):
        self.native.post_message.side_effect = [True, False, True]
        with self.assertRaises(DesktopWindowsError):
            self.api.post_click(self.window, 30, 40)
        self.assertEqual(self.native.post_message.call_args.args[1], 0x202)


if __name__ == '__main__':
    unittest.main()
