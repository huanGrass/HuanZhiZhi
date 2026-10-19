from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtCore

from huanzhizhi.platform.global_mouse import GlobalMouseWatcher, POLL_INTERVAL_MS


class GlobalMouseWatcherTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtCore.QCoreApplication.instance() or QtCore.QCoreApplication([])

    def setUp(self) -> None:
        self.states = [False]
        self.position = QtCore.QPoint(10, 20)
        self.watcher = GlobalMouseWatcher(
            button_state_provider=lambda: self.states[-1],
            cursor_provider=lambda: QtCore.QPoint(self.position),
        )

    def tearDown(self) -> None:
        self.watcher.close()

    def test_poll_interval_matches_original(self) -> None:
        self.assertEqual(POLL_INTERVAL_MS, 20)

    def test_emits_one_press_changed_moves_and_release(self) -> None:
        events: list[tuple[str, int, int]] = []
        self.watcher.pressed.connect(lambda x, y: events.append(("pressed", x, y)))
        self.watcher.moved.connect(lambda x, y: events.append(("moved", x, y)))
        self.watcher.released.connect(lambda x, y: events.append(("released", x, y)))

        self.watcher.start()
        self.states.append(True)
        self.watcher.poll_once()
        self.watcher.poll_once()
        self.position = QtCore.QPoint(11, 22)
        self.watcher.poll_once()
        self.states.append(False)
        self.watcher.poll_once()

        self.assertEqual(
            events,
            [("pressed", 10, 20), ("moved", 11, 22), ("released", 11, 22)],
        )

    def test_starting_while_button_is_down_adopts_existing_drag(self) -> None:
        self.states.append(True)
        moved: list[tuple[int, int]] = []
        released: list[tuple[int, int]] = []
        self.watcher.moved.connect(lambda x, y: moved.append((x, y)))
        self.watcher.released.connect(lambda x, y: released.append((x, y)))

        self.watcher.start()
        self.position = QtCore.QPoint(30, 40)
        self.watcher.poll_once()
        self.states.append(False)
        self.watcher.poll_once()

        self.assertEqual(moved, [(30, 40)])
        self.assertEqual(released, [(30, 40)])

    def test_poll_failure_stops_and_reports_error(self) -> None:
        messages: list[str] = []
        self.watcher.error.connect(messages.append)
        self.watcher.start()
        self.watcher._button_state_provider = lambda: (_ for _ in ()).throw(OSError("boom"))

        self.watcher.poll_once()

        self.assertFalse(self.watcher.enabled)
        self.assertEqual(messages, ["全局鼠标监听失败：boom"])

    def test_invalid_cursor_result_fails_start(self) -> None:
        self.states.append(True)
        watcher = GlobalMouseWatcher(
            button_state_provider=lambda: True,
            cursor_provider=lambda: (1, 2),
        )
        with self.assertRaisesRegex(RuntimeError, "QPoint"):
            watcher.start()
        self.assertFalse(watcher.enabled)


if __name__ == "__main__":
    unittest.main()
