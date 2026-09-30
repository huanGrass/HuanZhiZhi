"""Single-task subprocess lifecycle manager."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6 import QtCore

from huanzhizhi.tasks.protocol import (
    FinishedEvent,
    ProtocolError,
    TaskEvent,
    encode_start_message,
    encode_step_message,
    encode_stop_message,
    parse_event_line,
    validate_options,
)


class TaskProcessManager(QtCore.QObject):
    event_received = QtCore.Signal(object)
    state_changed = QtCore.Signal(str)
    error = QtCore.Signal(str)

    def __init__(
        self,
        parent: QtCore.QObject | None = None,
        *,
        terminate_timeout_ms: int = 1_500,
        kill_timeout_ms: int = 1_000,
    ) -> None:
        super().__init__(parent)
        if (
            isinstance(terminate_timeout_ms, bool)
            or not isinstance(terminate_timeout_ms, int)
            or isinstance(kill_timeout_ms, bool)
            or not isinstance(kill_timeout_ms, int)
        ):
            raise TypeError("任务停止超时必须是整数毫秒")
        if terminate_timeout_ms <= 0 or kill_timeout_ms <= 0:
            raise ValueError("任务停止超时必须大于零")
        self._terminate_timeout_ms = int(terminate_timeout_ms)
        self._kill_timeout_ms = int(kill_timeout_ms)
        self._state = "idle"
        self._process: QtCore.QProcess | None = None
        self._task_id: str | None = None
        self._start_message = b""
        self._stdout_buffer = bytearray()
        self._stderr_buffer = bytearray()
        self._received_finished = False
        self._protocol_failed = False
        self._error_emitted = False
        self._stop_requested = False

        self._terminate_timer = QtCore.QTimer(self)
        self._terminate_timer.setSingleShot(True)
        self._terminate_timer.timeout.connect(self._terminate_after_timeout)
        self._kill_timer = QtCore.QTimer(self)
        self._kill_timer.setSingleShot(True)
        self._kill_timer.timeout.connect(self._kill_after_timeout)

    @property
    def state(self) -> str:
        return self._state

    @property
    def task_id(self) -> str | None:
        return self._task_id

    @property
    def stderr_text(self) -> str:
        return bytes(self._stderr_buffer).decode("utf-8", errors="replace")

    def start(self, task_id: str, script_path: Path, options: dict | None = None) -> None:
        if self._state != "idle" or self._process is not None:
            raise RuntimeError("已有任务正在运行")
        if not isinstance(task_id, str) or not task_id.strip():
            raise ValueError("task_id 必须是非空字符串")
        path = Path(script_path).resolve()
        if not path.is_file():
            raise FileNotFoundError(f"任务脚本不存在：{path}")
        validated_options = validate_options({} if options is None else options)

        self._reset_run_state(task_id.strip())
        self._start_message = encode_start_message(self._task_id, validated_options)
        process = QtCore.QProcess(self)
        compiled = path.suffix.lower() == ".exe"
        process.setProgram(str(path) if compiled else sys.executable)
        process.setArguments(["--run"] if compiled else [str(path), "--run"])
        process.setWorkingDirectory(str(path.parent))
        environment = QtCore.QProcessEnvironment.systemEnvironment()
        environment.insert("PYTHONUNBUFFERED", "1")
        environment.insert("PYTHONIOENCODING", "utf-8")
        process.setProcessEnvironment(environment)
        process.setProcessChannelMode(QtCore.QProcess.ProcessChannelMode.SeparateChannels)
        process.started.connect(self._handle_started)
        process.readyReadStandardOutput.connect(self._read_stdout)
        process.readyReadStandardError.connect(self._read_stderr)
        process.errorOccurred.connect(self._handle_process_error)
        process.finished.connect(self._handle_finished)
        self._process = process
        self._set_state("starting")
        process.start()

    def stop(self) -> None:
        process = self._process
        if process is None or self._state == "idle":
            return
        if self._state == "stopping":
            return
        self._stop_requested = True
        self._set_state("stopping")
        if process.state() == QtCore.QProcess.ProcessState.Running:
            try:
                self._write_message(encode_stop_message())
            except RuntimeError as error:
                self._abort_process(str(error))
            else:
                self._terminate_timer.start(self._terminate_timeout_ms)

    def step(self) -> None:
        if self._process is None or self._process.state() != QtCore.QProcess.ProcessState.Running:
            raise RuntimeError("任务进程尚未运行，无法执行下一步")
        self._write_message(encode_step_message())

    def close(self) -> None:
        process = self._process
        if process is None:
            self._set_state("idle")
            return
        self.stop()
        if process.state() != QtCore.QProcess.ProcessState.NotRunning:
            process.waitForFinished(self._terminate_timeout_ms)
        if process.state() != QtCore.QProcess.ProcessState.NotRunning:
            process.terminate()
            process.waitForFinished(self._kill_timeout_ms)
        if process.state() != QtCore.QProcess.ProcessState.NotRunning:
            process.kill()
            process.waitForFinished(self._kill_timeout_ms)
        if process.state() != QtCore.QProcess.ProcessState.NotRunning:
            raise RuntimeError("任务进程在 kill 后仍未退出")
        if self._process is process:
            self._finalize_process(process)

    @QtCore.Slot()
    def _handle_started(self) -> None:
        if self._process is None:
            return
        try:
            self._write_message(self._start_message)
        except RuntimeError as error:
            self._abort_process(str(error))
            return
        if self._stop_requested:
            try:
                self._write_message(encode_stop_message())
            except RuntimeError as error:
                self._abort_process(str(error))
                return
            self._set_state("stopping")
            self._terminate_timer.start(self._terminate_timeout_ms)
        else:
            self._set_state("running")

    @QtCore.Slot()
    def _read_stdout(self) -> None:
        process = self._process
        if process is None:
            return
        self._stdout_buffer.extend(bytes(process.readAllStandardOutput()))
        self._consume_stdout_lines()

    @QtCore.Slot()
    def _read_stderr(self) -> None:
        if self._process is not None:
            self._stderr_buffer.extend(bytes(self._process.readAllStandardError()))

    def _consume_stdout_lines(self) -> None:
        while b"\n" in self._stdout_buffer and not self._protocol_failed:
            raw_line, _, remainder = self._stdout_buffer.partition(b"\n")
            self._stdout_buffer = bytearray(remainder)
            raw_line = raw_line.rstrip(b"\r")
            if not raw_line.strip():
                continue
            try:
                event = parse_event_line(bytes(raw_line))
            except (ProtocolError, TypeError) as error:
                self._fail_protocol(str(error))
                return
            if self._received_finished:
                self._fail_protocol("任务在 finished 之后继续发送事件")
                return
            if isinstance(event, FinishedEvent):
                self._received_finished = True
            self.event_received.emit(event)

    @QtCore.Slot(QtCore.QProcess.ProcessError)
    def _handle_process_error(self, process_error: QtCore.QProcess.ProcessError) -> None:
        if process_error == QtCore.QProcess.ProcessError.FailedToStart:
            self._emit_error_once(f"任务进程启动失败：{self._process.errorString() if self._process else ''}")
            if self._process is not None:
                self._finalize_process(self._process)

    @QtCore.Slot(int, QtCore.QProcess.ExitStatus)
    def _handle_finished(self, exit_code: int, exit_status: QtCore.QProcess.ExitStatus) -> None:
        process = self._process
        if process is None:
            return
        self._read_stdout()
        self._read_stderr()
        if self._stdout_buffer.strip() and not self._protocol_failed:
            self._fail_protocol("任务 stdout 最后一条 JSONL 事件缺少换行")
        if not self._protocol_failed:
            if exit_status != QtCore.QProcess.ExitStatus.NormalExit or exit_code != 0:
                diagnosis = self.stderr_text.strip()
                suffix = f"；stderr：{diagnosis}" if diagnosis else ""
                self._emit_error_once(f"任务进程异常退出，exit_code={exit_code}{suffix}")
            elif not self._received_finished:
                self._emit_error_once("任务进程退出前未发送 finished 事件")
        self._finalize_process(process)

    def _fail_protocol(self, message: str) -> None:
        if self._protocol_failed:
            return
        self._protocol_failed = True
        self._emit_error_once(f"任务协议错误：{message}")
        process = self._process
        if process is not None and process.state() != QtCore.QProcess.ProcessState.NotRunning:
            self._set_state("stopping")
            process.terminate()
            self._kill_timer.start(self._kill_timeout_ms)

    def _abort_process(self, message: str) -> None:
        self._protocol_failed = True
        self._emit_error_once(f"任务进程通信失败：{message}")
        process = self._process
        if process is not None and process.state() != QtCore.QProcess.ProcessState.NotRunning:
            self._set_state("stopping")
            process.terminate()
            self._kill_timer.start(self._kill_timeout_ms)

    def _write_message(self, message: bytes) -> None:
        process = self._process
        if process is None or process.state() != QtCore.QProcess.ProcessState.Running:
            raise RuntimeError("任务进程尚未运行，无法写入协议消息")
        if process.write(message) != len(message):
            raise RuntimeError("无法完整写入任务协议消息")

    @QtCore.Slot()
    def _terminate_after_timeout(self) -> None:
        process = self._process
        if process is None or process.state() == QtCore.QProcess.ProcessState.NotRunning:
            return
        process.terminate()
        self._kill_timer.start(self._kill_timeout_ms)

    @QtCore.Slot()
    def _kill_after_timeout(self) -> None:
        process = self._process
        if process is not None and process.state() != QtCore.QProcess.ProcessState.NotRunning:
            process.kill()

    def _emit_error_once(self, message: str) -> None:
        if self._error_emitted:
            return
        self._error_emitted = True
        self.error.emit(message)

    def _reset_run_state(self, task_id: str) -> None:
        self._task_id = task_id
        self._stdout_buffer.clear()
        self._stderr_buffer.clear()
        self._received_finished = False
        self._protocol_failed = False
        self._error_emitted = False
        self._stop_requested = False
        self._terminate_timer.stop()
        self._kill_timer.stop()

    def _finalize_process(self, process: QtCore.QProcess) -> None:
        self._terminate_timer.stop()
        self._kill_timer.stop()
        if self._process is process:
            self._process = None
        process.deleteLater()
        self._task_id = None
        self._start_message = b""
        self._set_state("idle")

    def _set_state(self, state: str) -> None:
        if state not in {"idle", "starting", "running", "stopping"}:
            raise ValueError(f"未知任务进程状态：{state}")
        if state == self._state:
            return
        self._state = state
        self.state_changed.emit(state)


__all__ = ("TaskProcessManager",)
