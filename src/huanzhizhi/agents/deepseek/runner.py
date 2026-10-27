"""Translate one host turn to the installed Harness SDK's stdio protocol.

This child is owned by agents.runtime, including its Harness descendants. No
third-party SDK is needed for the six sequential JSON-RPC requests used here.
"""
import base64
import json
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
import uuid

from huanzhizhi.python_runtime import _stop_process


def emit(value):
    print(json.dumps(value, ensure_ascii=False), flush=True)


def run(job, prompt, directory):
    session = job['session_id'] or 'session-' + uuid.uuid4().hex
    patch = [{'id': 'approval', 'config': {'policy': 'never'}}]
    if job['image']:
        # Observation has no tools, even if a user's SDK profile adds tools of
        # its own. The native monotonic guard cannot be overridden by an allow.
        guard = directory / 'observe.mjs'
        guard.write_text("export const name = 'zhizhi-observation';\n"
                         "export const inject = ['tools'];\n"
                         "export function apply(ctx) {\n"
                         "  ctx.tools.guard(() => '窗口观察禁止调用工具，请直接分析附图。');\n"
                         "}\n", encoding='utf-8')
        patch += [{'id': 'sandbox-policy', 'config': {'mode': 'read-only', 'workspaceRoot': str(directory.parent)}},
                  {'insert': [{'id': 'zhizhi-observation', 'name': str(guard.resolve())}]}]
    overlay = directory / 'harness.patch.json'
    overlay.write_text(json.dumps(patch, ensure_ascii=False), encoding='utf-8')
    process = subprocess.Popen(job['launch'] + ['--profile', 'sdk', '--patch', str(overlay)],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=sys.stderr,
                               text=True, encoding='utf-8', errors='replace',
                               creationflags=subprocess.CREATE_NO_WINDOW)
    lines = queue.Queue()
    def read():
        try:
            for line in process.stdout:
                lines.put(line)
        finally:
            lines.put(None)
    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    request_id = 0
    answer = ''
    reason = None
    observing_turn = False

    def notification(frame):
        nonlocal answer, reason
        params = frame.get('params', {})
        if (not observing_turn or frame.get('method') != 'session.event'
                or not isinstance(params, dict) or params.get('sessionId') != session):
            return
        event = params.get('event', {})
        if not isinstance(event, dict):
            return
        data = event.get('data', {})
        if event.get('type') == 'turn/start':
            answer, reason = '', None
        elif event.get('type') == 'assistant/message':
            content = data.get('message', {}).get('content', [])
            text = ''.join(b['text'] for b in content if b.get('type') == 'text' and isinstance(b.get('text'), str))
            if text.strip():
                answer = text
                emit(dict(type='text', text=text))
        elif event.get('type') == 'turn/end':
            reason = data.get('reason')
            emit(dict(type='turn_end', reason=reason))

    def request(method, params=None, *, timeout=45):
        nonlocal request_id
        request_id += 1
        process.stdin.write(json.dumps(dict(jsonrpc='2.0', id=request_id, method=method,
                                           params=params or {}), ensure_ascii=False) + '\n')
        process.stdin.flush()
        # Only session/wait may take as long as the user's task. Startup and
        # shutdown remain bounded even when a broken runtime emits no response.
        deadline = time.monotonic() + timeout if timeout is not None else None
        while True:
            remaining = max(0, deadline - time.monotonic()) if deadline is not None else None
            try:
                line = lines.get(timeout=remaining)
            except queue.Empty as error:
                raise TimeoutError('Harness SDK response timed out') from error
            if line is None:
                raise RuntimeError('Harness SDK exited before replying')
            try:
                frame = json.loads(line)
            except ValueError:
                continue
            if not isinstance(frame, dict):
                continue
            if frame.get('id') == request_id:
                if 'error' in frame:
                    # Raw protocol diagnostics stay local; the host reports only
                    # classified terminal codes, never provider credential text.
                    print(json.dumps(frame, ensure_ascii=False), file=sys.stderr, flush=True)
                    raise RuntimeError('Harness SDK rejected ' + method)
                return frame.get('result')
            notification(frame)

    try:
        provider, model = job['options']['model'].split('/', 1)
        initialized = request('initialize', dict(cwd=str(directory.parent), provider=provider, model=model,
                                                 reasoningEffort=job['options']['reasoning_effort']))
        if initialized.get('serverInfo', {}).get('name') != 'deepseek-harness-sdk-runtime':
            raise RuntimeError('Unexpected Harness SDK server')
        if job['session_id']:
            request('session/resume', dict(sessionId=session))
        content = [dict(type='text', text=prompt)]
        if job['image']:
            content.append(dict(type='image', mimeType='image/png',
                                data=base64.b64encode(Path(job['image']).read_bytes()).decode('ascii')))
        observing_turn = True
        request('session/prompt', dict(sessionId=session, contentBlocks=content))
        emit(dict(type='session', id=session))
        request('session/wait', dict(sessionId=session), timeout=None)
        request('shutdown', timeout=10)
        process.wait(timeout=10)
        completed = isinstance(reason, dict) and reason.get('kind') == 'completed' and process.returncode == 0
        code = reason.get('error', {}).get('code') if isinstance(reason, dict) and reason.get('kind') == 'error' else None
        emit(dict(type='final', text=answer, completed=completed, code=code))
        return 0 if completed else 1
    except Exception as error:
        print(f'{type(error).__name__}: {error}', file=sys.stderr, flush=True)
        code = reason.get('error', {}).get('code') if isinstance(reason, dict) and reason.get('kind') == 'error' else None
        emit(dict(type='error', code=code or ('TIMEOUT' if isinstance(error, TimeoutError) else None)))
        return 1
    finally:
        _stop_process(process)
        process.stdin.close()
        reader.join(timeout=5)
        process.stdout.close()


def main():
    path = Path(sys.argv[1]).resolve()
    return run(json.loads(path.read_text(encoding='utf-8')), sys.stdin.read(), path.parent)


if __name__ == '__main__':
    raise SystemExit(main())
