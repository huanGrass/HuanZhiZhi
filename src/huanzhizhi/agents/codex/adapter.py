"""Codex-only discovery, configuration, arguments and JSON event parsing."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

from huanzhizhi.agents.runtime import AgentTaskError, SettingsError
DEFAULT_CODEX_OPTIONS = {"model": "gpt-6-luna", "reasoning_effort": "high"}
CODEX_REASONING_EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra")

def codex_executable() -> str:
    executable = shutil.which('codex')
    if not executable:
        candidates = list((Path(os.environ.get('LOCALAPPDATA', '')) / 'OpenAI/Codex/bin').glob('*/codex.exe'))
        executable = str(max(candidates, key=lambda p: p.stat().st_mtime)) if candidates else None
    if not executable:
        raise SettingsError('未找到 Codex。请先安装并登录 Codex，再到“设置 → AI 设置”启用 AI。')
    return executable


def check_codex_configuration(options: dict[str, str]) -> str:
    """Check local configuration and login status without sending a model request."""
    validate_options(options)
    cache = Path(os.environ.get('CODEX_HOME') or Path.home() / '.codex') / 'models_cache.json'
    try:
        models = json.loads(cache.read_text(encoding='utf-8')).get('models', [])
    except (OSError, ValueError, AttributeError):
        models = []
    for model in models if isinstance(models, list) else []:
        if not isinstance(model, dict) or model.get('slug') != options['model']:
            continue
        levels = model.get('supported_reasoning_levels', [])
        efforts = [level.get('effort') for level in levels if isinstance(level, dict)] if isinstance(levels, list) else []
        if efforts and options['reasoning_effort'] not in efforts:
            raise SettingsError('所选模型不支持这个推理强度，请在“设置 → AI 设置”重新选择。')
    executable = codex_executable()
    try:
        result = subprocess.run([executable, 'login', 'status'], stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, encoding='utf-8', errors='replace',
                                timeout=5, creationflags=subprocess.CREATE_NO_WINDOW)
    except subprocess.TimeoutExpired as error:
        raise SettingsError('检查 Codex 登录状态超时。请确认 Codex 可正常打开并登录后重试。') from error
    except OSError as error:
        raise SettingsError('无法启动 Codex。请检查 Codex 安装，再到“设置 → AI 设置”重试。') from error
    if result.returncode != 0:
        detail = (result.stdout + result.stderr).lower()
        if 'not logged in' in detail:
            raise SettingsError('Codex 尚未登录。请先在 Codex 中完成登录，再到“设置 → AI 设置”启用 AI。')
        if 'configuration' in detail or 'config.toml' in detail:
            raise SettingsError('Codex 配置无法读取，尚不能确认登录状态。请先修正 Codex 配置，再到“设置 → AI 设置”重试。')
        raise SettingsError('无法确认 Codex 登录状态。请先在 Codex 中检查登录和配置，再到“设置 → AI 设置”重试。')
    return executable


def validate_options(options: dict[str, str]) -> None:
    if not isinstance(options, dict) or set(options) != {"model", "reasoning_effort"}:
        raise SettingsError("Codex 设置需要模型和推理强度")
    model = options["model"]
    if not isinstance(model, str) or not model or model != model.strip() or any(c.isspace() for c in model):
        raise SettingsError("Codex 模型必须是非空且不含空白的模型 ID")
    if options["reasoning_effort"] not in CODEX_REASONING_EFFORTS:
        raise SettingsError("Codex 推理强度无效")


def model_catalog():
    cache = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "models_cache.json"
    result = {}
    try:
        for model in json.loads(cache.read_text(encoding="utf-8")).get("models", []):
            slug = model.get("slug")
            if model.get("visibility") != "list" or not isinstance(slug, str) or not slug:
                continue
            efforts = tuple(level["effort"] for level in model.get("supported_reasoning_levels", [])
                            if level.get("effort") in CODEX_REASONING_EFFORTS)
            result[slug] = efforts or CODEX_REASONING_EFFORTS
    except (OSError, ValueError, AttributeError, TypeError):
        pass
    return result


def build_command(executable, options, thread_id, answer, clicks, *, input_mode='background', observation_image=None):
    if input_mode not in ('background', 'background_smooth', 'foreground'):
        raise ValueError('未知鼠标操作模式')
    command = [executable, 'exec', '--sandbox', 'read-only' if observation_image else 'danger-full-access',
               '-c', 'model_reasoning_effort=' + json.dumps(options['reasoning_effort']),
               '-c', 'approval_policy="never"',
               '-c', 'web_search="disabled"', '-c', 'project_doc_max_bytes=0']
    if observation_image:
        command.extend(('--ephemeral', '--image', str(observation_image.resolve())))
        for feature in ('shell_tool', 'unified_exec', 'code_mode', 'image_generation', 'skill_search'):
            command.extend(('--disable', feature))
    else:
        command.extend(('-c', 'shell_environment_policy.set={HUANZHIZHI_CLICK_EVENTS='
                        + json.dumps(str(clicks.resolve()))
                        + ',HUANZHIZHI_INPUT_MODE=' + json.dumps(input_mode) + '}'))
    if thread_id and not observation_image:
        command.extend(('resume', thread_id))
    command.extend(('--model', options['model'], '--ignore-user-config', '--skip-git-repo-check',
                    '--json', '--output-last-message', str(answer)))
    for feature in ('apps', 'plugins', 'hooks',
                    'browser_use', 'computer_use', 'multi_agent'):
        command.extend(('--disable', feature))
    command.append('-')
    return command


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
                # The writer may be halfway through a JSON record or UTF-8 character.
                reader.seek(position)
                break
            try:
                event = json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            if not isinstance(event, dict):
                continue
            if event.get('type') == 'thread.started' and isinstance(event.get('thread_id'), str):
                worker.thread_id = event['thread_id']
            item = event.get('item')
            if (event.get('type') != 'item.completed' or not isinstance(item, dict)
                    or item.get('type') != 'agent_message' or worker.isInterruptionRequested()):
                continue
            text = item.get('text')
            if not isinstance(text, str) or not text.startswith('进度：'):
                continue
            message = text.removeprefix('进度：').strip()
            if (0 < len(message) <= 120 and len(message.splitlines()) == 1
                    and message != last_progress):
                worker.progress.emit(message)
                last_progress = message

    return consume


def _failure_event(turn, code):
    """Prefer the final failed turn; error events alone can precede recovery."""
    failure = None
    failed_turn = False
    try:
        with (turn / 'events.jsonl').open('rb') as events:
            for line in events:
                try:
                    event = json.loads(line)
                except (ValueError, UnicodeDecodeError):
                    continue
                if not isinstance(event, dict):
                    continue
                kind = event.get('type')
                if kind in ('thread.started', 'turn.started', 'turn.completed'):
                    failure, failed_turn = None, False
                elif kind == 'turn.failed':
                    failure, failed_turn = event, True
                elif kind == 'error' and not failed_turn:
                    failure = event
    except OSError:
        pass
    return failure if code or failed_turn else None


def _failure_message(event):
    """Translate verified CLI failure formats without exposing native messages."""
    error = event.get('error') if event and event.get('type') == 'turn.failed' else event
    message = error.get('message') if isinstance(error, dict) else None
    if isinstance(message, str):
        if message == 'Quota exceeded. Check your plan and billing details.':
            return 'Codex 可用额度不足，请检查账户额度和账单设置后重试。'
        if message.replace('’', "'").startswith("You've hit your usage limit."):
            return 'Codex 已达到当前用量上限，请等待额度恢复或检查套餐额度后重试。'
        if message.startswith('stream disconnected before completion:'):
            return 'Codex 响应连接在完成前中断，请重试；若持续发生，请检查网络和服务状态。'
        status = re.match(r'^(?:unexpected status |exceeded retry limit, last status: )([45][0-9]{2})\b', message)
        if status:
            status = int(status[1])
            if status == 401:
                return 'Codex 身份验证未通过，请检查登录状态或 API 凭证后重试。'
            if status == 403:
                return 'Codex 请求被服务端拒绝，请检查账户、模型权限或访问限制。'
            if status == 404:
                return 'Codex 请求的资源未找到，请检查模型和服务地址配置。'
            if status == 429:
                return 'Codex 请求受到限制，请稍后重试或检查账户用量。'
            if status >= 500:
                return 'Codex 请求遇到服务端错误，请稍后重试。'
        # Some API errors remain a JSON body inside the CLI's message field.
        try:
            body = json.loads(message)
        except ValueError:
            body = None
        error = body.get('error') if isinstance(body, dict) else None
        if isinstance(error, dict) and error.get('code') == 'context_length_exceeded':
            return '请求内容超过模型上下文限制，请缩短要求或开始新的对话。'
    return 'Codex 任务失败，暂时无法确认具体原因。请在 Codex 中查看诊断信息后重试。'


def result(turn, code):
    failure = _failure_event(turn, code)
    if code or failure is not None:
        raise AgentTaskError(_failure_message(failure), f'退出码 {code}；本地诊断：{turn}')
    answer = turn / 'answer.txt'
    if not answer.is_file():
        raise AgentTaskError('Codex 未返回有效答复，请重试。', f'本地诊断：{turn}')
    text = answer.read_text(encoding='utf-8').strip()
    if not text:
        raise AgentTaskError('Codex 未返回有效答复，请重试。', f'本地诊断：{turn}')
    return text
