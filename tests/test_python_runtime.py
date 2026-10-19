"""Integration checks against the real, relocatable script interpreter."""
import ctypes
import os
from pathlib import Path
import shutil
import tempfile
import threading
import unittest
from unittest.mock import patch

from huanzhizhi.python_runtime import python_executable, run_code
from scripts.prepare_python import prepare_python, verify_python


@unittest.skipUnless(os.name == "nt", "Windows runtime")
class PythonRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="之之-python-test-")
        cls.addClassCleanup(cls.temp.cleanup)
        cls.root = Path(cls.temp.name).resolve()
        # Moving a copy must not reference the development install or original directory.
        shutil.copytree(python_executable().parent, cls.root / "python")
        shutil.copytree(Path(__file__).resolve().parents[1] / "src", cls.root / "src")

    def run_script(self, code, **kwargs):
        return run_code(code, root=self.root, runs_dir=self.root / "runs", **kwargs)

    def test_relocated_runtime_ignores_external_python_paths(self):
        with patch.dict(os.environ, {"PYTHONHOME": "Z:/missing", "PYTHONPATH": "Z:/missing"}):
            report = verify_python(self.root / "python")
            result = self.run_script("import sys; print(sys.prefix); print('中文输出')")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(Path(report["prefix"]), self.root / "python")
        self.assertIn(str(self.root / "python"), result.stdout)
        self.assertIn("中文输出", result.stdout)

    def test_script_output_errors_and_generated_files(self):
        result = self.run_script(
            "import sys; from pathlib import Path; print('成功'); "
            "print('提示', file=sys.stderr); Path('result.txt').write_text('产物', encoding='utf-8')")
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "成功")
        self.assertEqual(result.stderr.strip(), "提示")
        self.assertEqual((result.workdir / "result.txt").read_text(encoding="utf-8"), "产物")
        failure = self.run_script("print('报错前'); raise ValueError('测试错误')")
        self.assertNotEqual(failure.returncode, 0)
        self.assertIn("ValueError: 测试错误", failure.stderr)
        self.assertIn("报错前", failure.stdout)
        self.assertNotEqual(result.workdir, failure.workdir)

    def test_timeout_stops_child_process_too(self):
        result = self.run_script(
            "import subprocess, sys, time; "
            "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
            "print(child.pid, flush=True); time.sleep(60)", timeout=2)
        self.assertTrue(result.timed_out)
        self.assertFalse(result.stopped)
        self.assertNotEqual(result.returncode, 0)
        child_pid = int(result.stdout.strip())
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel.OpenProcess(0x100000, False, child_pid)
        if handle:
            try:
                self.assertEqual(kernel.WaitForSingleObject(handle, 3000), 0)
            finally:
                kernel.CloseHandle(handle)
        else:
            self.assertEqual(ctypes.get_last_error(), 87)  # PID no longer exists.

    def test_manual_stop(self):
        stop = threading.Event()
        timer = threading.Timer(0.5, stop.set)
        timer.start()
        try:
            result = self.run_script("import time; print('started'); time.sleep(60)", stop_event=stop)
        finally:
            timer.cancel()
        self.assertTrue(result.stopped)
        self.assertFalse(result.timed_out)
        self.assertNotEqual(result.returncode, 0)

    def test_invalid_requests_do_not_start(self):
        for timeout in (0, -1, float("nan"), float("inf"), True):
            with self.assertRaises(ValueError):
                self.run_script("pass", timeout=timeout)
        with self.assertRaises(ValueError):
            self.run_script(" ")
        stop = threading.Event()
        stop.set()
        with self.assertRaises(InterruptedError):
            self.run_script("raise AssertionError('must not run')", stop_event=stop)
        with self.assertRaises(FileNotFoundError):
            python_executable(self.root / "missing")

    def test_preparation_preserves_existing_environment(self):
        with self.assertRaises(FileExistsError):
            prepare_python(self.root / "python")
        self.assertTrue((self.root / "python" / "python.exe").is_file())


if __name__ == "__main__":
    unittest.main()
