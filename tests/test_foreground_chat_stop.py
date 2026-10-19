"""Chat stop must release foreground input before interrupting or waiting."""
from __future__ import annotations

import unittest
from unittest import mock

from tests import test_chat_window


class ForegroundChatStopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        test_chat_window.ChatWindowTests.setUpClass.__func__(cls)

    def setUp(self):
        test_chat_window.ChatWindowTests.setUp(self)
        self.worker = mock.Mock()
        self.window._exploration = self.worker
        self.window._exploration_stopping = False

    def tearDown(self):
        # The fixture owns no real worker; avoid additional fake stop events in teardown.
        self.window._exploration = None
        test_chat_window.ChatWindowTests.tearDown(self)

    def test_stop_emits_synchronous_cancellation_before_worker_interruption(self):
        order = []
        self.window.foreground_input_stop_requested.connect(
            lambda: order.append(("cancel", self.window._exploration_stopping)))
        self.worker.requestInterruption.side_effect = lambda: order.append(("interrupt",))
        self.window._stop_exploration()
        self.assertEqual(order, [("cancel", True), ("interrupt",)])
        self.worker.wait.assert_not_called()
        self.assertFalse(self.window.conversation.stop_button.isEnabled())
        self.assertEqual(self.window.conversation.entries[-1]["text"], "正在停止……")

    def test_stop_and_wait_cancels_before_interruption_and_blocking_wait(self):
        order = []
        self.window.foreground_input_stop_requested.connect(
            lambda: order.append(("cancel", self.window._exploration_stopping)))
        self.worker.requestInterruption.side_effect = lambda: order.append(("interrupt",))
        self.worker.wait.side_effect = lambda: order.append(("wait",))
        self.window.stop_exploration_and_wait()
        self.assertEqual(order, [("cancel", True), ("interrupt",), ("wait",)])

    def test_cancel_callback_and_repeated_stop_do_not_reenter_interruption(self):
        order = []

        def cancel():
            order.append("cancel")
            # Cancelling the mouse can synchronously report InterruptedError and stop again.
            self.window._stop_exploration()
            self.window._stop_exploration()
            order.append("cancel returned")

        def interrupt():
            order.append("interrupt")
            self.window._stop_exploration()

        self.window.foreground_input_stop_requested.connect(cancel)
        self.worker.requestInterruption.side_effect = interrupt
        self.window._stop_exploration()
        self.window._stop_exploration()
        self.assertEqual(order, ["cancel", "cancel returned", "interrupt"])
        self.worker.requestInterruption.assert_called_once()
        self.assertEqual(sum(entry["text"] == "正在停止……" for entry in self.window.conversation.entries), 1)

    def test_stop_and_wait_blocks_stop_reentry_from_the_cancel_callback(self):
        order = []

        def cancel():
            order.append("cancel")
            self.window._stop_exploration()
            order.append("cancel returned")

        self.window.foreground_input_stop_requested.connect(cancel)
        self.worker.requestInterruption.side_effect = lambda: order.append("interrupt")
        self.worker.wait.side_effect = lambda: order.append("wait")
        self.window.stop_exploration_and_wait()
        self.window._stop_exploration()
        self.assertEqual(order, ["cancel", "cancel returned", "interrupt", "wait"])
        self.worker.requestInterruption.assert_called_once()
        self.worker.wait.assert_called_once()


if __name__ == "__main__":
    unittest.main()
