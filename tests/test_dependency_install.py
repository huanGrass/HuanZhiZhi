"""Verify mirror fallback without depending on public network failures."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts.prepare_python import setup_project_python

MIRROR = "https://pypi.tuna.tsinghua.edu.cn/simple"
OFFICIAL = "https://pypi.org/simple"


class PrepareDependenciesTests(unittest.TestCase):
    def run_case(self, effects):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime = root / "python"
            runtime.mkdir()
            (runtime / "python311._pth").touch()
            with patch("scripts.prepare_python.verify_python"), patch(
                "scripts.prepare_python.subprocess.run", side_effect=effects
            ) as run:
                error = None
                try:
                    setup_project_python(runtime, root)
                except (subprocess.CalledProcessError, KeyboardInterrupt) as caught:
                    error = caught
                installs = [call.args[0] for call in run.call_args_list if "install" in call.args[0]]
                return error, [command[command.index("--index-url") + 1] for command in installs]

    def test_mirror_success_does_not_request_official(self):
        error, sources = self.run_case([None, None])
        self.assertIsNone(error)
        self.assertEqual(sources, [MIRROR])

    def test_failed_mirror_falls_back_to_official(self):
        error, sources = self.run_case([subprocess.CalledProcessError(1, "pip"), None, None])
        self.assertIsNone(error)
        self.assertEqual(sources, [MIRROR, OFFICIAL])

    def test_both_failed_reports_failure(self):
        error, sources = self.run_case([subprocess.CalledProcessError(1, "pip")] * 2)
        self.assertIsInstance(error, subprocess.CalledProcessError)
        self.assertEqual(sources, [MIRROR, OFFICIAL])

    def test_keyboard_cancel_does_not_request_official(self):
        error, sources = self.run_case([KeyboardInterrupt()])
        self.assertIsInstance(error, KeyboardInterrupt)
        self.assertEqual(sources, [MIRROR])


FAKE_PYTHON = r"""
using System;
using System.IO;
using System.Threading;
class FakePython {
    static int Main(string[] args) {
        string root = AppDomain.CurrentDomain.BaseDirectory;
        File.AppendAllText(Path.Combine(root, "calls.txt"), String.Join(" ", args) + "\n");
        string mode = File.ReadAllText(Path.Combine(root, "mode.txt"));
        if (mode == "cancel") Thread.Sleep(30000);
        return mode == "both-fail" || (mode == "mirror-fail" &&
            Array.IndexOf(args, "https://pypi.tuna.tsinghua.edu.cn/simple") >= 0) ? 1 : 0;
    }
}
"""

RUNNER = r"""
using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Forms;
class Runner {
    [STAThread]
    static int Main(string[] args) {
        using (var form = new EnvironmentSetup(args[0])) {
            var flags = BindingFlags.Instance | BindingFlags.NonPublic;
            var task = (Task)typeof(EnvironmentSetup).GetMethod("InstallDependencies", flags)
                .Invoke(form, new object[] { args[0] });
            bool cancelled = false;
            var timer = Stopwatch.StartNew();
            while (!task.IsCompleted && timer.Elapsed.TotalSeconds < 10) {
                Application.DoEvents();
                if (args[1] == "cancel" && !cancelled && File.Exists(Path.Combine(args[0], "calls.txt"))) {
                    typeof(EnvironmentSetup).GetField("cancelled", flags).SetValue(form, true);
                    var child = (Process)typeof(EnvironmentSetup).GetField("process", flags).GetValue(form);
                    child.Kill();
                    cancelled = true;
                }
                Thread.Sleep(10);
            }
            if (!task.IsCompleted) return 3;
            try { task.GetAwaiter().GetResult(); return 0; }
            catch (OperationCanceledException) { return 2; }
            catch (InvalidOperationException) { return 1; }
        }
    }
}
"""


@unittest.skipUnless(sys.platform == "win32", "Windows installer")
class LauncherDependencyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="dependency harness ")
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.root = Path(cls.temporary.name)
        compiler = Path(os.environ["SystemRoot"]) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
        project = Path(__file__).resolve().parents[1]
        for name, source, extra in [
            ("python", FAKE_PYTHON, []),
            ("runner", RUNNER, [str(project / "scripts/setup_environment.cs")]),
        ]:
            file = cls.root / (name + ".cs")
            file.write_text(source, encoding="utf-8")
            subprocess.run([
                str(compiler), "/nologo", "/target:exe", "/utf8output",
                "/reference:System.Windows.Forms.dll", "/reference:System.Drawing.dll",
                "/reference:System.IO.Compression.FileSystem.dll",
                "/out:" + str(cls.root / (name + ".exe")), str(file), *extra,
            ], check=True, capture_output=True, timeout=30)

    def test_real_installer_process_fallback_and_cancel(self):
        for mode, expected_exit, expected_sources in [
            ("success", 0, [MIRROR]),
            ("mirror-fail", 0, [MIRROR, OFFICIAL]),
            ("both-fail", 1, [MIRROR, OFFICIAL]),
            ("cancel", 2, [MIRROR]),
        ]:
            with self.subTest(mode=mode):
                calls = self.root / "calls.txt"
                calls.unlink(missing_ok=True)
                (self.root / "mode.txt").write_text(mode, encoding="utf-8")
                result = subprocess.run([str(self.root / "runner.exe"), str(self.root), mode],
                                        timeout=15, capture_output=True)
                self.assertEqual(result.returncode, expected_exit, result.stderr)
                lines = calls.read_text(encoding="utf-8").splitlines()
                self.assertEqual(len(lines), len(expected_sources))
                for line, source in zip(lines, expected_sources):
                    self.assertIn("--index-url " + source, line)
                    self.assertNotIn("--extra-index-url", line)


if __name__ == "__main__":
    unittest.main()
