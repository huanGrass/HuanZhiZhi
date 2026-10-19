from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import textwrap
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
from tests.skill_factory import write_skill_manifest

from huanzhizhi.tasks.protocol import PreviewEvent, ProtocolError, parse_event_line


class TaskPreviewProtocolTests(unittest.TestCase):
    def test_host_parses_exact_preview_event(self) -> None:
        payload = {
            "protocol_version": 1,
            "type": "preview",
            "image_path": "artifacts/预览.png",
            "width": 640,
            "height": 480,
            "analysis": {"state": "游戏中", "confidence": 0.95, "items": [1, None]},
            "grid_overlay": {"rows": [0, 120, 479], "columns": [5, 320, 639]},
        }

        event = parse_event_line(json.dumps(payload, ensure_ascii=False))

        self.assertEqual(
            event,
            PreviewEvent(
                "artifacts/预览.png",
                640,
                480,
                payload["analysis"],
                payload["grid_overlay"],
            ),
        )
        with self.assertRaises(FrozenInstanceError):
            event.width = 1  # type: ignore[misc]

    def test_host_rejects_wrong_top_level_fields_and_json(self) -> None:
        valid = self._valid_payload()
        invalid_payloads = []
        for field in ("image_path", "width", "height", "analysis", "grid_overlay"):
            payload = dict(valid)
            del payload[field]
            invalid_payloads.append(payload)
        payload = dict(valid)
        payload["extra"] = True
        invalid_payloads.append(payload)
        payload = dict(valid)
        payload["image_path"] = "   "
        invalid_payloads.append(payload)
        payload = dict(valid)
        payload["analysis"] = []
        invalid_payloads.append(payload)
        for payload in invalid_payloads:
            with self.subTest(payload=payload), self.assertRaises(ProtocolError):
                parse_event_line(json.dumps(payload, ensure_ascii=False))

        invalid_lines = (
            '{"protocol_version":1,"type":"preview","image_path":"a","width":1,"height":1,"analysis":{"x":NaN},"grid_overlay":{"rows":[],"columns":[]}}',
            '{"protocol_version":1,"type":"preview","image_path":"a","image_path":"b","width":1,"height":1,"analysis":{},"grid_overlay":{"rows":[],"columns":[]}}',
            '{"protocol_version":1,"type":"preview","image_path":"a","width":1,"height":1,"analysis":{"x":1,"x":2},"grid_overlay":{"rows":[],"columns":[]}}',
        )
        for line in invalid_lines:
            with self.subTest(line=line), self.assertRaises(ProtocolError):
                parse_event_line(line)

    def test_host_rejects_invalid_dimensions_and_grid_coordinates(self) -> None:
        variants = (
            ("width", True),
            ("width", 0),
            ("height", -1),
            ("rows", [True]),
            ("rows", [-1]),
            ("rows", [1, 1]),
            ("rows", [2, 1]),
            ("rows", [480]),
            ("columns", [640]),
            ("columns", [1.0]),
        )
        for field, value in variants:
            payload = self._valid_payload()
            if field in {"rows", "columns"}:
                payload["grid_overlay"][field] = value
            else:
                payload[field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ProtocolError):
                parse_event_line(json.dumps(payload))

        for overlay in ({"rows": []}, {"rows": [], "columns": [], "extra": []}, []):
            payload = self._valid_payload()
            payload["grid_overlay"] = overlay
            with self.subTest(overlay=overlay), self.assertRaises(ProtocolError):
                parse_event_line(json.dumps(payload))

    def test_real_sdk_preview_is_accepted_by_host_parser(self) -> None:
        result = self._run_sdk(
            """
            session.emit_preview(
                "frames/当前.png",
                320,
                200,
                {"state": "ready", "score": 0.8},
                {"rows": [0, 100, 199], "columns": [10, 319]},
            )
            session.emit_finished("完成", "预览", "结束")
            """
        )

        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
        lines = result.stdout.splitlines()
        self.assertEqual(len(lines), 2)
        preview = parse_event_line(lines[0])
        self.assertIsInstance(preview, PreviewEvent)
        self.assertEqual(preview.grid_overlay, {"rows": [0, 100, 199], "columns": [10, 319]})
        self.assertEqual(parse_event_line(lines[1]).type, "finished")

    def test_sdk_rejects_every_host_invalid_preview_before_stdout(self) -> None:
        calls = (
            'session.emit_preview("", 10, 10, {}, {"rows": [], "columns": []})',
            'session.emit_preview("x.png", True, 10, {}, {"rows": [], "columns": []})',
            'session.emit_preview("x.png", 10, 0, {}, {"rows": [], "columns": []})',
            'session.emit_preview("x.png", 10, 10, [], {"rows": [], "columns": []})',
            'session.emit_preview("x.png", 10, 10, {1: "bad"}, {"rows": [], "columns": []})',
            'session.emit_preview("x.png", 10, 10, {"x": float("nan")}, {"rows": [], "columns": []})',
            'session.emit_preview("x.png", 10, 10, {}, {"rows": [True], "columns": []})',
            'session.emit_preview("x.png", 10, 10, {}, {"rows": [10], "columns": []})',
            'session.emit_preview("x.png", 10, 10, {}, {"rows": [], "columns": [], "extra": []})',
        )
        for call in calls:
            with self.subTest(call=call):
                result = self._run_sdk(call)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, b"")
                self.assertIn("幻之之任务 SDK 错误".encode("utf-8"), result.stderr)

    @staticmethod
    def _valid_payload() -> dict[str, object]:
        return {
            "protocol_version": 1,
            "type": "preview",
            "image_path": "preview.png",
            "width": 640,
            "height": 480,
            "analysis": {},
            "grid_overlay": {"rows": [0, 479], "columns": [0, 639]},
        }

    def _run_sdk(self, body: str) -> subprocess.CompletedProcess[bytes]:
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "preview_test" / "task.py"
            script.parent.mkdir()
            write_skill_manifest(script)
            script.write_text(
                (
                    "from huanzhizhi.task_sdk import TaskSession\n"
                    'session = TaskSession(__file__)\n'
                    + textwrap.dedent(body).lstrip()
                    + "\n"
                ),
                encoding="utf-8",
            )
            start = (
                json.dumps(
                    {
                        "protocol_version": 1,
                        "type": "start",
                        "task_id": "preview_test",
                        "options": {},
                    },
                    separators=(",", ":"),
                )
                + "\n"
            ).encode("utf-8")
            process = subprocess.Popen(
                [sys.executable, str(script), "--run"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            assert process.stdin is not None
            process.stdin.write(start)
            process.stdin.flush()
            try:
                return_code = process.wait(timeout=5)
            finally:
                process.stdin.close()
            assert process.stdout is not None
            assert process.stderr is not None
            stdout = process.stdout.read()
            stderr = process.stderr.read()
            process.stdout.close()
            process.stderr.close()
            return subprocess.CompletedProcess(
                process.args,
                return_code,
                stdout,
                stderr,
            )


if __name__ == "__main__":
    unittest.main()
