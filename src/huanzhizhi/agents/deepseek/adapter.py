"""Discovery, model metadata and host-facing results for DeepSeek Harness."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time

from huanzhizhi.agents.runtime import AgentTaskError, SettingsError
from huanzhizhi.python_runtime import python_executable, _stop_process


DEFAULT_OPTIONS = {'model': 'deepseek-account/deepseek-flash', 'reasoning_effort': 'high'}


def validate_options(options):
    if not isinstance(options, dict) or set(options) != set(DEFAULT_OPTIONS):
        raise SettingsError('DeepSeek Harness 设置需要模型和推理强度。')
    if (not isinstance(options['model'], str)
            or not re.fullmatch(r'deepseek-account/[a-zA-Z0-9._-]+', options['model'])):
        raise SettingsError('请选择 DeepSeek Harness 的账号模型。')
    effort = options['reasoning_effort']
    if not isinstance(effort, str) or not re.fullmatch(r'[a-zA-Z0-9_-]+', effort):
        raise SettingsError('DeepSeek Harness 推理强度无效。')


def _desktop_roots():
    if os.name != 'nt':
        return
    import winreg
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, r'Software\Microsoft\Windows\CurrentVersion\Uninstall') as root:
                for index in range(winreg.QueryInfoKey(root)[0]):
                    try:
                        with winreg.OpenKey(root, winreg.EnumKey(root, index)) as entry:
                            name = winreg.QueryValueEx(entry, 'DisplayName')[0]
                            if isinstance(name, str) and name.startswith('DeepSeek Harness'):
                                yield Path(winreg.QueryValueEx(entry, 'InstallLocation')[0])
                    except OSError:
                        continue
        except OSError:
            continue


def launcher():
    """Use the installed carrier without passing task text through cmd.exe."""
    executable = shutil.which('dsh')
    if executable and Path(executable).suffix.lower() not in ('.cmd', '.bat', '.ps1'):
        return [executable], {}
    for root in _desktop_roots():
        binary = root / 'DeepSeek Harness.exe'
        archive = root / 'resources/app.asar'
        wrapper = root / 'resources/runtime/cli/bin/dsh.cmd'
        if binary.is_file() and archive.is_file() and wrapper.is_file():
            script = archive / 'dsh/node_modules/@deepseek-ai/dsh-desktop-host/lib/cli.js'
            return [str(binary), '--expose-internals', str(script)], {'ELECTRON_RUN_AS_NODE': '1'}
    if executable:
        entry = Path(executable).parent / 'node_modules/@deepseek-ai/dsh/lib/bin.js'
        node = shutil.which('node')
        if entry.is_file() and node:
            return [node, str(entry)], {}
    raise SettingsError('未找到 DeepSeek Harness，请先安装并在 Harness 中登录 DeepSeek 账号。')


def check_configuration(options):
    validate_options(options)
    return launcher()


_CATALOG_SCRIPT = """export const inject = ['llm'];
export function apply(ctx) {
  (async () => {
    await ctx.get('loader')?.await();
    const models = [];
    for (const model of await ctx.llm.listModels('deepseek-account')) {
      const info = await ctx.llm.resolveModelInfo('deepseek-account', model.id);
      models.push({id: 'deepseek-account/' + model.id, name: model.name,
        efforts: (info.reasoning?.efforts ?? []).map(e => e.id),
        image: (info.inputModalities ?? model.inputModalities ?? []).includes('image')});
    }
    console.log('ZHIZHI_MODELS:' + JSON.stringify({models,
      error: models.length ? null : 'ACCOUNT_SIGN_IN_REQUIRED'}));
    ctx.get('appExit')(models.length ? 0 : 1);
  })().catch(error => {
    console.log('ZHIZHI_MODELS:' + JSON.stringify({error: error.code}));
    ctx.get('appExit')(1);
  });
}
"""


def parse_catalog(document):
    """Retain public model fields; credentials remain owned by Harness."""
    try:
        models = []
        for model in document['models']:
            efforts = model['efforts']
            if not isinstance(efforts, list) or not efforts:
                raise ValueError()
            for effort in efforts:
                validate_options(dict(model=model['id'], reasoning_effort=effort))
            if not isinstance(model['name'], str) or not model['name'] or type(model['image']) is not bool:
                raise ValueError()
            models.append(dict(id=model['id'], name=model['name'], efforts=list(efforts), image=model['image']))
        if not models or len({m['id'] for m in models}) != len(models):
            raise ValueError()
        return models
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        raise SettingsError('Harness 返回的模型目录格式无效，请检查安装或更新后重试。') from error


def model_catalog(check_stop=lambda: None):
    command, overrides = launcher()
    with tempfile.TemporaryDirectory(prefix='zhizhi-dsh-catalog-') as directory:
        root = Path(directory)
        script = root / 'catalog.mjs'
        script.write_text(_CATALOG_SCRIPT, encoding='utf-8')
        overlay = root / 'catalog.patch.json'
        overlay.write_text(json.dumps([
            {'id': 'sdk-app-startup', 'disabled': True},
            {'id': 'sdk-jsonrpc-server', 'disabled': True},
            {'insert': [{'id': 'zhizhi-catalog', 'name': str(script)}]},
        ]), encoding='utf-8')
        env = os.environ.copy()
        env.update(overrides)
        with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
            process = subprocess.Popen(command + ['--profile', 'sdk', '--patch', str(overlay)],
                                       cwd=directory, env=env, stdin=subprocess.DEVNULL,
                                       stdout=stdout, stderr=stderr, creationflags=subprocess.CREATE_NO_WINDOW)
            deadline = time.monotonic() + 45
            try:
                while process.poll() is None:
                    check_stop()
                    if time.monotonic() >= deadline:
                        raise SettingsError('读取 Harness 账号模型目录超时，请检查网络后重试。')
                    try:
                        process.wait(timeout=.1)
                    except subprocess.TimeoutExpired:
                        pass
            finally:
                _stop_process(process)
            check_stop()
            stdout.seek(0)
            document = None
            for line in stdout:
                if line.startswith(b'ZHIZHI_MODELS:'):
                    try:
                        document = json.loads(line.removeprefix(b'ZHIZHI_MODELS:'))
                    except ValueError:
                        pass
            if process.returncode or not isinstance(document, dict):
                code = document.get('error') if isinstance(document, dict) else None
                raise SettingsError(_FAILURES.get(code, '无法读取 Harness 账号模型目录，请检查登录、网络或安装后重试。'))
            return parse_catalog(document)


def build_command(launch, options, turn, thread_id=None, *, observation_image=None):
    validate_options(options)
    # The SDK's duplex protocol carries images directly; the shared host runtime
    # still owns stdin, events, input routing and termination of the process tree.
    job = dict(launch=launch, options=options,
               session_id=thread_id if observation_image is None else None,
               image=str(observation_image.resolve()) if observation_image is not None else None)
    path = turn / 'harness.json'
    path.write_text(json.dumps(job, ensure_ascii=False), encoding='utf-8')
    return [str(python_executable()), '-I', '-X', 'utf8', '-u', '-m',
            'huanzhizhi.agents.deepseek.runner', str(path.resolve())]


_FAILURES = {
    'ACCOUNT_QUOTA': 'DeepSeek 账户额度不足，请在 Harness 中检查可用额度后重试。',
    'QUOTA': 'DeepSeek 账户额度不足，请在 Harness 中检查可用额度后重试。',
    'ACCOUNT_SIGN_IN_REQUIRED': '请先在 DeepSeek Harness 中登录并授权账号。',
    'ACCOUNT_SIGNED_OUT': 'DeepSeek 账户已退出，请在 Harness 中重新登录。',
    'ACCOUNT_TOKEN_INVALID': 'DeepSeek 登录已失效，请在 Harness 中重新登录。',
    'AUTH': 'DeepSeek 认证或模型权限未通过，请在 Harness 中检查。',
    'RATE_LIMIT': 'DeepSeek 请求受到限制，请稍后重试。',
    'TIMEOUT': 'DeepSeek Harness 响应超时，请重试。',
    'TRANSPORT': 'DeepSeek 连接中断，请检查网络后重试。',
    'CONTEXT_WINDOW_EXCEEDED': 'DeepSeek 会话超过上下文限制，请开始新的对话。',
    'SERVER': 'DeepSeek 服务暂时不可用，请稍后重试。',
}


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
            try:
                event = json.loads(line)
            except (ValueError, UnicodeDecodeError):
                continue
            if not isinstance(event, dict):
                continue
            if event.get('type') == 'session' and isinstance(event.get('id'), str):
                worker.thread_id = event['id']
            text = event.get('text')
            if event.get('type') == 'text' and isinstance(text, str) and text.startswith('进度：'):
                message = text.removeprefix('进度：').strip()
                if (0 < len(message) <= 120 and len(message.splitlines()) == 1
                        and message != last_progress and not worker.isInterruptionRequested()):
                    worker.progress.emit(message)
                    last_progress = message
    return consume


def result(turn, code):
    final = None
    try:
        with (turn / 'events.jsonl').open(encoding='utf-8') as stream:
            for line in stream:
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if isinstance(event, dict) and event.get('type') in ('final', 'error'):
                    final = event
    except OSError:
        pass
    if code or not final or final.get('type') != 'final' or final.get('completed') is not True:
        message = _FAILURES.get((final or {}).get('code'),
                               'DeepSeek Harness 未完成请求，请检查 Harness 配置或本地诊断后重试。')
        raise AgentTaskError(message, f'退出码 {code}；本地诊断：{turn}')
    text = final.get('text')
    if not isinstance(text, str) or not text.strip():
        raise AgentTaskError('DeepSeek Harness 未返回有效答复，请重试。', f'本地诊断：{turn}')
    return text.strip()
