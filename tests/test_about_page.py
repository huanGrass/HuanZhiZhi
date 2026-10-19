"""Exercise real asynchronous update responses without using public services."""
import json
import os
from pathlib import Path
import threading
import time
import tomllib
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6 import QtCore, QtNetwork, QtTest, QtWidgets
from huanzhizhi.settings import about


def release(tag="v0.3.0", **overrides):
    return dict(tag_name=tag, draft=False, prerelease=False, body="修复问题并优化体验。", **overrides)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.server.requests += 1
        time.sleep(self.server.delay)
        self.send_response(self.server.code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(self.server.payload)))
        self.end_headers()
        try:
            self.wfile.write(self.server.payload)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def log_message(self, *args):
        pass


class AboutPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.server.daemon_threads = True
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def setUp(self):
        self.server.code = 200
        self.server.payload = b"[]"
        self.server.delay = 0
        self.server.requests = 0
        self.page = about.AboutPage()
        self.page.version = "0.2.0"

    def tearDown(self):
        self.page.stop()
        self.page.close()
        self.page.deleteLater()
        self.app.processEvents()

    def start(self, data=None, code=200):
        self.server.code = code
        self.server.payload = json.dumps(data).encode() if data is not None else b"[]"
        url = f"http://127.0.0.1:{self.server.server_port}/releases"
        direct = QtNetwork.QNetworkProxy(QtNetwork.QNetworkProxy.ProxyType.NoProxy)
        with patch.object(about, "RELEASES_API", url), patch.object(
            QtNetwork.QNetworkProxyFactory, "systemProxyForQuery", return_value=[direct]
        ):
            self.page.check_updates()

    def wait(self):
        deadline = time.monotonic() + 3
        while self.page._reply is not None and time.monotonic() < deadline:
            QtTest.QTest.qWait(10)
        self.assertIsNone(self.page._reply)
        self.assertTrue(self.page.check_button.isEnabled())

    def test_version_copy_and_open_do_not_check_network(self):
        expected = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
        self.assertEqual(about.current_version(), expected)
        self.page.show()
        QtTest.QTest.qWait(30)
        self.assertEqual(self.server.requests, 0)
        self.assertIsNone(self.page._reply)
        self.page.copy_button.click()
        self.assertEqual(self.app.clipboard().text(), "1064560882")
        with patch.object(about.QtGui.QDesktopServices, "openUrl", return_value=True) as open_url:
            self.page.download_button.click()
            self.assertEqual(open_url.call_args.args[0].toString(), about.RELEASES_URL)

    def test_newest_version_notes_and_download_are_safe(self):
        body = "<script>alert('test')</script>"
        data = [release("v0.3.0"), release("v0.10.0"), release("v99.0.0")]
        data[1].update(body=body, html_url="https://untrusted.example/download")
        data[2]["prerelease"] = True
        self.start(data)
        self.assertFalse(self.page.check_button.isEnabled())
        self.page.check_updates()
        self.wait()
        self.assertEqual(self.server.requests, 1)
        self.assertIn("发现新版本 v0.10.0", self.page.status.text())
        self.assertEqual(self.page.notes.toPlainText(), body)
        with patch.object(about.QtGui.QDesktopServices, "openUrl", return_value=True) as open_url:
            self.page.download_button.click()
            self.assertEqual(open_url.call_args.args[0].toString(), about.RELEASES_URL + "/tag/v0.10.0")

    def test_current_newer_and_no_release_are_distinct(self):
        for data, expected in [
            ([release("0.2.0")], "已是最新正式版本"),
            ([release("v0.1.0")], "比最新正式发行版更新"),
            ([], "暂无正式发布版本"),
            ([{**release(), "draft": True}, {**release(), "prerelease": True}], "暂无正式发布版本"),
        ]:
            with self.subTest(expected=expected):
                self.start(data)
                self.wait()
                self.assertIn(expected, self.page.status.text())

    def test_http_and_invalid_data_never_claim_up_to_date(self):
        for data, code in [({}, 200), ([release("nightly")], 200), ([], 403), ([], 404), ([], 500)]:
            with self.subTest(data=data, code=code):
                self.start(data, code)
                self.wait()
                self.assertIn("检查更新失败", self.page.status.text())
                self.assertNotIn("已是最新", self.page.status.text())

    def test_timeout_cancel_and_recheck(self):
        self.server.delay = .2
        self.page.timer.setInterval(40)
        self.start([release()])
        self.wait()
        self.assertIn("连接超时", self.page.status.text())
        self.page.timer.setInterval(20000)
        self.start([release()])
        self.page.stop()
        self.assertIsNone(self.page._reply)
        self.assertIn("已取消", self.page.status.text())
        QtTest.QTest.qWait(250)
        self.server.delay = 0
        self.start([release("v0.2.0")])
        self.wait()
        self.assertIn("已是最新", self.page.status.text())

    def test_size_limit_and_unknown_version(self):
        with patch.object(about, "MAX_RESPONSE", 20):
            self.start([release()])
            self.wait()
        self.assertIn("响应过大", self.page.status.text())
        previous = self.server.requests
        self.page.version = "未知"
        self.page.check_updates()
        self.assertEqual(previous, self.server.requests)
        self.assertIn("无法读取当前版本", self.page.status.text())


if __name__ == "__main__":
    unittest.main()
