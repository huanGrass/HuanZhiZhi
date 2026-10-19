"""Shared child lifecycle and visual click channel for skill development."""
import os
import subprocess
import threading

from huanzhizhi.python_runtime import _stop_process
from huanzhizhi.tasks.protocol import (
    PointEvent, InputRequestEvent, InputCancelEvent, encode_input_result, parse_event_line,
)
from huanzhizhi.task_sdk import input_result_path


class SettingsError(ValueError):
    """Persisted settings or local Agent configuration cannot be used."""


class AgentTaskError(RuntimeError):
    """Keep a fixed, safe user message separate from local diagnostics."""

    def __init__(self, user_message, detail):
        super().__init__(f'{user_message}；{detail}')
        self.user_message = user_message


def run_agent(worker, command, turn, *, stdout_name, consume=None, environment=None):
    """Run one child, drain its output, and stop the complete process tree."""
    clicks = turn / 'clicks.jsonl'
    clicks.touch()
    env = os.environ.copy()
    env.update(environment or {})
    env['HUANZHIZHI_CLICK_EVENTS'] = str(clicks.resolve())
    input_mode = getattr(worker, 'input_mode', 'background')
    if input_mode not in ('background', 'foreground'):
        raise ValueError('未知鼠标操作模式')
    env['HUANZHIZHI_INPUT_MODE'] = input_mode
    channel_open = True
    pending = set()
    seen = set()
    channel_lock = threading.RLock()

    def reply(request_id, error):
        with channel_lock:
            if not channel_open or request_id not in pending:
                return
            path = input_result_path(clicks, request_id)
            path.parent.mkdir(exist_ok=True)
            temporary = path.with_suffix('.tmp')
            temporary.write_bytes(encode_input_result(request_id, error))
            temporary.replace(path)
            pending.discard(request_id)
    output = turn / stdout_name
    with output.open('wb') as stdout, output.open('rb') as reader, \
            (turn / 'stderr.txt').open('wb') as stderr, \
            (turn / 'prompt.txt').open('rb') as stdin, clicks.open('rb') as click_reader:
        def drain(*, final=False):
            if consume is not None:
                consume(reader, worker, final=final)
            while True:
                position = click_reader.tell()
                line = click_reader.readline()
                if not line:
                    break
                if not line.endswith(b'\n'):
                    click_reader.seek(position)
                    break
                event = parse_event_line(line)
                if isinstance(event, InputCancelEvent):
                    with channel_lock:
                        pending.discard(event.request_id)
                    worker.input_cancel.emit(event.request_id)
                elif isinstance(event, InputRequestEvent):
                    if event.request_id in seen:
                        raise ValueError('重复的前台输入请求')
                    seen.add(event.request_id)
                    with channel_lock:
                        pending.add(event.request_id)
                    if final or worker.isInterruptionRequested():
                        reply(event.request_id, '本轮工作已停止')
                    elif input_mode != 'foreground':
                        reply(event.request_id, '本轮没有启用前台操作')
                    else:
                        worker.input_request.emit(event, lambda error=None, request_id=event.request_id:
                                                  reply(request_id, None if error is None else str(error)))
                elif isinstance(event, PointEvent) and not worker.isInterruptionRequested():
                    worker.click_point.emit(event.x, event.y)
                elif not isinstance(event, PointEvent):
                    raise ValueError('点击通道事件类型无效')

        process = None
        try:
            worker.check_stop()
            process = subprocess.Popen(command, cwd=worker.directory, stdin=stdin,
                                       stdout=stdout, stderr=stderr, env=env,
                                       creationflags=subprocess.CREATE_NO_WINDOW)
            while True:
                worker.check_stop()
                drain()
                try:
                    code = process.wait(timeout=.2)
                    break
                except subprocess.TimeoutExpired:
                    pass
        finally:
            try:
                if process is not None:
                    try:
                        _stop_process(process)
                    except Exception:
                        worker.stop_error = '无法确认 Agent 子进程树已全部停止，请在原 Agent 中检查；草稿已保留。'
                        raise
            finally:
                try:
                    drain(final=True)
                finally:
                    with channel_lock:
                        channel_open = False
                        cancelled = tuple(pending)
                        pending.clear()
                        click_reader.close()
                        clicks.rename(turn / 'clicks.finished.jsonl')
                    for request_id in cancelled:
                        worker.input_cancel.emit(request_id)
    worker.check_stop()
    return code
