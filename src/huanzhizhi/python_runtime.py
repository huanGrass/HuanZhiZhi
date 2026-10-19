"""Execute local scripts with the bundled Python. This is not a security sandbox."""
from dataclasses import dataclass
import math
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time


@dataclass(frozen=True)
class ScriptResult:
    returncode: int
    stdout: str
    stderr: str
    workdir: Path
    timed_out: bool
    stopped: bool


def python_executable(root: Path | None = None) -> Path:
    if root is None:
        root = Path(__file__).resolve().parents[2]
    executable = Path(root).resolve() / "python" / "python.exe"
    if not executable.is_file():
        raise FileNotFoundError(f"独立 Python 环境不存在：{executable}；请先运行 scripts/prepare_python.py")
    return executable


def _stop_process(process):
    if process.poll() is not None:
        return
    # Kill descendants too, rather than only the interpreter which launched them.
    killer = Path(os.environ["SystemRoot"]) / "System32" / "taskkill.exe"
    try:
        result = subprocess.run(
            [str(killer), "/PID", str(process.pid), "/T", "/F"],
            capture_output=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if result.returncode and process.poll() is None:
            raise RuntimeError(f"停止脚本进程树失败：{result.stderr!r}")
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)


def run_code(code: str, *, root: Path | None = None, runs_dir: Path | None = None,
             timeout: float = 60, stop_event: threading.Event | None = None) -> ScriptResult:
    """Blocking call: use a worker thread from the UI; set stop_event to cancel.

    Each invocation retains script.py, stdout.txt, stderr.txt and generated files.
    Scripts run with the current user's permissions, not in a sandbox.
    """
    if not isinstance(code, str) or not code.strip():
        raise ValueError("脚本不能为空")
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("超时必须为大于零的有限秒数")
    if stop_event is not None and stop_event.is_set():
        raise InterruptedError("脚本启动前已取消")
    executable = python_executable(root)
    if runs_dir is None:
        if (executable.parent.parent / "build-info.json").is_file():
            runs_dir = Path(os.environ["LOCALAPPDATA"]) / "HuanZhiZhi" / "python_runs"
        else:
            runs_dir = executable.parent.parent / "artifacts" / "python_runs"
    runs_dir = Path(runs_dir).resolve()
    runs_dir.mkdir(parents=True, exist_ok=True)
    workdir = Path(tempfile.mkdtemp(prefix="run-", dir=runs_dir))
    script = workdir / "script.py"
    script.write_text(code, encoding="utf-8")
    environment = os.environ.copy()
    environment.pop("PYTHONHOME", None)
    environment.pop("PYTHONPATH", None)
    timed_out = stopped = False
    # Files avoid pipe deadlocks and preserve output even on timeout or interruption.
    with (workdir / "stdout.txt").open("wb") as stdout, (workdir / "stderr.txt").open("wb") as stderr:
        process = subprocess.Popen(
            [str(executable), "-I", "-X", "utf8", "-u", "-m", "huanzhizhi.script_runner", str(script)],
            cwd=workdir, env=environment, stdin=subprocess.DEVNULL,
            stdout=stdout, stderr=stderr, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        deadline = time.monotonic() + timeout
        try:
            while process.poll() is None:
                stopped = stop_event is not None and stop_event.is_set()
                timed_out = time.monotonic() >= deadline
                if stopped or timed_out:
                    _stop_process(process)
                    break
                try:
                    process.wait(timeout=min(0.05, max(0.001, deadline - time.monotonic())))
                except subprocess.TimeoutExpired:
                    pass
        finally:
            _stop_process(process)
    return ScriptResult(process.returncode,
                        (workdir / "stdout.txt").read_text(encoding="utf-8", errors="replace"),
                        (workdir / "stderr.txt").read_text(encoding="utf-8", errors="replace"),
                        workdir, timed_out, stopped)
