"""OpenCode 2 discovery, free-model checks and verified JSON event handling."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time

from huanzhizhi.agents.runtime import AgentTaskError, SettingsError
from huanzhizhi.python_runtime import _stop_process
from .install import managed_executable

DEFAULT_OPTIONS = {'executable': '', 'model': ''}


def validate_options(options):
    if not isinstance(options, dict) or set(options) != set(DEFAULT_OPTIONS):
        raise SettingsError('OpenCode 设置需要程序位置和模型。')
    if any(not isinstance(v, str) or '\x00' in v for v in options.values()):
        raise SettingsError('OpenCode 设置格式无效。')
    model = options['model']
    if model and not re.fullmatch(r'opencode/[a-zA-Z0-9._-]+', model):
        raise SettingsError('免费体验请选择 OpenCode 官方模型。')
    path = options['executable']
    if path and (not Path(path).is_absolute() or Path(path).suffix.lower() != '.exe'):
        raise SettingsError('请选择 OpenCode 原生 .exe 程序的完整路径。')


def executable(options):
    validate_options(options)
    if options['executable']:
        candidate = Path(options['executable'])
        if not candidate.is_file():
            raise SettingsError('保存的 OpenCode 程序不存在，请重新检测或安装。')
        return str(candidate)
    found = shutil.which('opencode.exe')
    candidates = [Path(found)] if found else []
    candidates += [Path.home() / '.opencode/bin/opencode.exe', managed_executable()]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate.resolve())
    raise SettingsError('未找到 OpenCode，请在 AI 设置中安装，或选择已安装的 OpenCode 2 程序。')


def invoke(command, *, cwd=None, prompt=None, timeout=45, check_stop=lambda: None, environment=None):
    """Bounded subprocess calls for the guide, cancellable without a blocked pipe."""
    with tempfile.TemporaryFile() as stdin, tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        if prompt is not None:
            stdin.write(prompt.encode('utf-8'))
            stdin.seek(0)
        process = subprocess.Popen(command, cwd=cwd, stdin=stdin, stdout=stdout, stderr=stderr,
                                   env=environment, creationflags=subprocess.CREATE_NO_WINDOW)
        deadline = time.monotonic() + timeout
        try:
            while process.poll() is None:
                check_stop()
                if time.monotonic() >= deadline:
                    raise SettingsError('OpenCode 响应超时，请检查网络或换一个免费模型后重试。')
                try:
                    process.wait(timeout=.1)
                except subprocess.TimeoutExpired:
                    pass
        finally:
            _stop_process(process)
        check_stop()
        stdout.seek(0)
        text = stdout.read().decode('utf-8', errors='replace')
        if process.returncode:
            for event in events(text):
                if event.get('type') == 'error':
                    raise SettingsError(failure_message(event.get('error', {})))
            raise SettingsError('OpenCode 命令未完成，请确认程序版本和本机配置后重试。')
        return text


def check_configuration(options, check_stop=lambda: None):
    path = executable(options)
    version = invoke([path, '--version'], timeout=15, check_stop=check_stop).strip()
    if not re.search(r'\b(?:v)?2\.\d+\.\d+\b', version):
        raise SettingsError('此接入需要 OpenCode 2，请通过引导安装或选择官方 OpenCode 2 程序。')
    return path


def free_models(path, check_stop=lambda: None):
    # API metadata can contain provider secrets. Return only the fields shown in the UI.
    with tempfile.TemporaryDirectory(prefix='zhizhi-models-') as temporary:
        raw = invoke([path, 'api', '--standalone', 'model.list'], cwd=temporary, check_stop=check_stop)
    try:
        entries = json.loads(raw)['data']
        if not isinstance(entries, list):
            raise ValueError()
        models = []
        for item in entries:
            cost = item.get('cost')
            if (item.get('providerID') != 'opencode' or not item.get('enabled') or
                    item.get('status') != 'active' or not item.get('capabilities', {}).get('tools') or
                    'image' not in item.get('capabilities', {}).get('input', []) or
                    not isinstance(cost, list) or not cost):
                continue
            if not all(isinstance(c, dict) and c.get('input') == 0 and c.get('output') == 0
                       and c.get('cache', {}).get('read', 0) == 0
                       and c.get('cache', {}).get('write', 0) == 0 for c in cost):
                continue
            model = 'opencode/' + item['id']
            validate_options({'executable': '', 'model': model})
            models.append({'id': model, 'name': item.get('name') or model,
                           'image': 'image' in item.get('capabilities', {}).get('input', [])})
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        raise SettingsError('无法读取 OpenCode 模型目录，请检查 OpenCode 版本或网络。') from error
    return sorted(models, key=lambda m: (not m['image'], m['name']))


def require_free_model(path, model, check_stop=lambda: None):
    if not model or model not in {m['id'] for m in free_models(path, check_stop)}:
        raise SettingsError('所选模型已不在当前免费目录，请重新检测并选择免费模型；不会切换到付费模型。')


def build_command(path, options, thread_id=None, *, probe=False):
    validate_options(options)
    command = [path, 'run', '--standalone', '--format', 'json', '--model', options['model'],
               '--agent', 'build', '--title', '之之技能开发']
    if thread_id:
        command += ['--session', thread_id]
    if not probe:
        command.append('--auto')
    return command


def environment():
    # Session-local overlay only: never overwrite the user's OpenCode configuration.
    # Development uses --auto; OpenCode still honors explicit user denials.
    permissions = [{'action': 'subagent', 'resource': '*', 'effect': 'deny'},
                   {'action': 'question', 'resource': '*', 'effect': 'deny'}]
    try:
        config = json.loads(os.environ.get('OPENCODE_CONFIG_CONTENT', '{}'))
        if not isinstance(config, dict) or not isinstance(config.get('permissions', []), list):
            raise ValueError()
    except (ValueError, TypeError) as error:
        raise SettingsError('现有 OpenCode 环境配置无效，请先修正 OPENCODE_CONFIG_CONTENT。') from error
    config['warming'] = False
    config['permissions'] = config.get('permissions', []) + permissions
    return {'OPENCODE_CONFIG_CONTENT': json.dumps(config)}


def events(text):
    for line in text.splitlines():
        try:
            item = json.loads(line)
        except (ValueError, UnicodeDecodeError):
            continue
        if isinstance(item, dict):
            yield item


def failure_message(error):
    data = error.get('data', error) if isinstance(error, dict) else {}
    message = str(data.get('message', ''))
    status = data.get('status', data.get('statusCode'))
    if 'free tier can only be used' in message.lower():
        return 'OpenCode 免费服务拒绝了此模型请求。请更新官方 OpenCode 或选择其他免费模型后测试。'
    if status == 429:
        return 'OpenCode 免费模型已限流或额度用尽，请稍后重试或选择其他免费模型。'
    if status in (401, 403):
        return 'OpenCode 模型认证或访问权限未通过，请检查账号、地区限制或选择其他免费模型。'
    return 'OpenCode 未完成请求，请检查网络、模型可用性和 OpenCode 配置后重试。'


def read_result(text, code=0):
    messages, completed, failure = [], False, None
    for event in events(text):
        part = event.get('part', {})
        if not isinstance(part, dict):
            part = {}
        if event.get('type') == 'error':
            failure = failure_message(event.get('error', {}))
        elif event.get('type') == 'text' and isinstance(part.get('text'), str):
            messages.append((part.get('messageID'), part['text']))
        elif event.get('type') == 'step_finish':
            cost = part.get('cost')
            if not isinstance(cost, (int, float)) or isinstance(cost, bool) or cost < 0:
                failure = 'OpenCode 未报告有效的费用信息，请重新测试免费模型。'
            elif cost != 0:
                failure = 'OpenCode 报告了非零模型费用，免费体验已停止，请检查模型配置。'
            completed = part.get('reason') == 'stop'
    if failure or code:
        raise AgentTaskError(failure or 'OpenCode 运行失败，请重试。', f'退出码 {code}')
    if not completed or not any(m.strip() for _, m in messages):
        raise AgentTaskError('OpenCode 未返回完整答复，请重试；已有草稿已保留。', '缺少最终完成事件或文本')
    last_message = messages[-1][0]
    return '\n\n'.join(m.strip() for message_id, m in messages if message_id == last_message and m.strip())


def output_reader():
    last_progress = None
    def consume(reader, worker, *, final=False):
        nonlocal last_progress
        while True:
            position = reader.tell()
            line = reader.readline()
            if not line:
                break
            if not line.endswith(b'\n') and not final:
                reader.seek(position)
                break
            for event in events(line):
                session = event.get('sessionID')
                if isinstance(session, str) and session.startswith('ses_'):
                    worker.thread_id = session
                part = event.get('part', {})
                text = part.get('text') if isinstance(part, dict) else None
                if event.get('type') == 'text' and isinstance(text, str) and text.startswith('进度：'):
                    message = text.removeprefix('进度：').strip()
                    if (0 < len(message) <= 120 and len(message.splitlines()) == 1
                            and message != last_progress and not worker.isInterruptionRequested()):
                        worker.progress.emit(message)
                        last_progress = message
    return consume


def complete_result(text, code, path, *, cwd=None, check_stop=lambda: None):
    items = list(events(text))
    # OpenCode 2 can omit step_finish from run output. Its exported session is
    # authoritative; do not mistake exit 0 or a partial text event for completion.
    has_error = any(e.get('type') == 'error' for e in items)
    has_finish = any(e.get('type') == 'step_finish' and isinstance(e.get('part'), dict)
                     and e['part'].get('reason') == 'stop' for e in items)
    if not code and not has_error and not has_finish:
        session = next((e.get('sessionID') for e in reversed(items) if e.get('sessionID')), None)
        if not isinstance(session, str) or not re.fullmatch(r'ses_[a-zA-Z0-9]+', session):
            raise AgentTaskError('OpenCode 未返回可确认的会话，请重试。', '缺少会话 ID')
        raw = invoke([path, 'session', 'export', '--standalone', session], cwd=cwd, check_stop=check_stop)
        try:
            record = json.loads(raw)
            info = record['info']
            assistants = [m for m in record['messages'] if m.get('type') == 'assistant']
            if (info.get('id') != session or info.get('outcome') != 'succeeded' or not assistants
                    or assistants[-1].get('finish') != 'stop'):
                raise ValueError()
            text += '\n' + json.dumps({'type': 'step_finish', 'part': {'reason': 'stop', 'cost': info.get('cost')}})
        except (KeyError, TypeError, ValueError, AttributeError) as error:
            raise AgentTaskError('OpenCode 会话尚未成功完成，请重试；草稿已保留。', '会话状态未确认') from error
    return read_result(text, code)


def result(turn, code, options, check_stop=lambda: None):
    try:
        return complete_result((turn / 'events.jsonl').read_text(encoding='utf-8'), code,
                               executable(options), cwd=turn.parent, check_stop=check_stop)
    except AgentTaskError as error:
        raise AgentTaskError(error.user_message, f'本地诊断：{turn}') from error


def test_connection(options, check_stop=lambda: None):
    path = check_configuration(options, check_stop)
    require_free_model(path, options['model'], check_stop)
    env = os.environ.copy()
    env.update(environment())
    with tempfile.TemporaryDirectory(prefix='zhizhi-opencode-test-') as directory:
        text = invoke(build_command(path, options, probe=True), cwd=directory, timeout=90, check_stop=check_stop,
                      prompt='只回复：之之连接成功。不要调用任何工具。', environment=env)
        return complete_result(text, 0, path, cwd=directory, check_stop=check_stop)
