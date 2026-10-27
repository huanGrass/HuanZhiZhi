"""Verify hints before actions through the existing host channel."""
from dataclasses import asdict
from datetime import datetime
import argparse
import json
import math
import os
from pathlib import Path
import time

from PySide6 import QtCore

from huanzhizhi.parameters import ChoiceParameter
from huanzhizhi.task_sdk import runtime_directory, _write_event_file
from huanzhizhi.tasks.catalog import TaskCatalog
from huanzhizhi.tasks.process import TaskProcessManager
from huanzhizhi.tasks.storage import copy_skill_package


def trial_options(task, options):
    """Use the declared dual-mode contract; other tasks keep their own options."""
    mode = next((p for p in task.parameters if isinstance(p, ChoiceParameter)
                 and p.key == 'mode' and {'hint', 'auto'} <= {v for v, _ in p.choices}), None)
    if mode is None:
        return [(False, options)]
    selected = options.get('mode', mode.default)
    if selected == 'auto':
        return [(True, dict(options, mode='hint')), (False, dict(options, mode='auto'))]
    return [(selected == 'hint', options)]


def run_trial(task, options, *, channel, directory, result_path, origin, report, until_log, timeout):
    app = QtCore.QCoreApplication.instance() or QtCore.QCoreApplication([])
    manager = TaskProcessManager()
    host_path = Path(channel).with_name('host_hints.jsonl')
    report.update(status='running', runs=[], host_hints_path=str(host_path),
                  events_path=str(directory / 'events.jsonl'))

    def save():
        report['elapsed_seconds'] = round(time.perf_counter()-origin, 3)
        pending = result_path.with_suffix('.tmp')
        pending.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        pending.replace(result_path)

    def clear():
        _write_event_file(channel, dict(protocol_version=1, type='hint', kind='clear',
                                       points=[], label='试运行结束'))

    save()
    try:
        for is_hint, run_options in trial_options(task, options):
            report['status'] = 'running'
            started = time.perf_counter()
            run = dict(mode=run_options.get('mode'), status='running', matched_at_seconds=None,
                       timed_out=False, errors=[], finished=None, final_state=None,
                       stop_requested_at_seconds=None, stop_seconds=None, input_attempts=0,
                       host_hint=None, host_cleared=False)
            report['runs'].append(run)
            save()
            loop = QtCore.QEventLoop()
            timer = QtCore.QTimer()
            timer.setInterval(50)
            offset = host_path.stat().st_size if host_path.exists() else 0
            idle_at = None

            def stop():
                if run['stop_requested_at_seconds'] is None:
                    run['stop_requested_at_seconds'] = round(time.perf_counter()-origin, 3)
                    manager.stop()

            def error(message):
                run['errors'].append(message)
                save()

            def record(event):
                data = dict(creation_seconds=round(time.perf_counter()-origin, 3),
                            run_seconds=round(time.perf_counter()-started, 3), **asdict(event))
                with (directory / 'events.jsonl').open('a', encoding='utf-8') as stream:
                    stream.write(json.dumps(dict(mode=run['mode'], **data), ensure_ascii=False)+'\n')
                if data['type'] == 'finished':
                    run['finished'] = data
                elif data['type'] == 'hint':
                    if run['stop_requested_at_seconds'] is None or data['kind'] == 'clear':
                        _write_event_file(channel, dict(protocol_version=1, **asdict(event)))
                elif is_hint and data['type'] in ('input_request', 'point', 'input_blocked'):
                    run['input_attempts'] += 1
                    error('提示验收期间禁止输入')
                    if data['type'] == 'input_request':
                        manager.send_input_result(data['request_id'], '提示验收期间禁止输入')
                    stop()
                elif data['type'] == 'log':
                    print(json.dumps(dict(event='skill_log', message=data['message']), ensure_ascii=False), flush=True)
                    if not is_hint and data['message'] == until_log and run['matched_at_seconds'] is None:
                        run['matched_at_seconds'] = data['creation_seconds']
                        stop()
                save()

            def state_changed(state):
                nonlocal idle_at
                run['final_state'] = state
                if state == 'idle':
                    idle_at = time.perf_counter()
                    if run['stop_requested_at_seconds'] is not None:
                        run['stop_seconds'] = round(idle_at-origin-run['stop_requested_at_seconds'], 3)
                    clear()
                    if not is_hint:
                        loop.quit()
                save()

            def poll():
                nonlocal offset
                if is_hint and host_path.exists():
                    with host_path.open('rb') as stream:
                        stream.seek(offset)
                        while line := stream.readline():
                            if not line.endswith(b'\n'):
                                break
                            offset = stream.tell()
                            state = json.loads(line)
                            if state['at_counter'] < started:
                                continue
                            run['host_hint'] = state
                            since = state['visible_since']
                            # Require host observations spanning two seconds, not a stale local guess.
                            if (state['visible'] and since is not None and since >= started
                                    and state['at_counter']-since >= 2
                                    and run['stop_requested_at_seconds'] is None):
                                run['matched_at_seconds'] = round(state['at_counter']-origin, 3)
                                stop()
                            if idle_at is not None and state['at_counter'] >= idle_at and not state['visible']:
                                run['host_cleared'] = True
                    save()
                if idle_at is not None:
                    if not is_hint or run['host_cleared']:
                        loop.quit()
                    elif time.perf_counter()-idle_at >= 2:
                        error('未收到宿主提示清除记录')
                        loop.quit()
                elif time.perf_counter()-started >= timeout and run['stop_requested_at_seconds'] is None:
                    run['timed_out'] = True
                    stop()
                    save()

            manager.event_received.connect(record)
            manager.error.connect(error)
            manager.state_changed.connect(state_changed)
            timer.timeout.connect(poll)
            try:
                # Reject mouse requests here and block native messages in the child.
                previous_channel = os.environ.pop('HUANZHIZHI_CLICK_EVENTS', None) if is_hint else None
                try:
                    manager.start(task.id, task.script_path, run_options,
                                  input_mode='foreground' if is_hint else os.environ.get('HUANZHIZHI_INPUT_MODE', 'background'),
                                  allow_input=not is_hint)
                finally:
                    if previous_channel is not None:
                        os.environ['HUANZHIZHI_CLICK_EVENTS'] = previous_channel
                timer.start()
                loop.exec()
            finally:
                timer.stop()
                manager.close()
                manager.event_received.disconnect(record)
                manager.error.disconnect(error)
                manager.state_changed.disconnect(state_changed)
            finished = run['finished'] or {}
            passed = (run['matched_at_seconds'] is not None and not run['timed_out']
                      and not run['errors'] and bool(finished) and not finished.get('error')
                      and finished.get('stopped_by_request') is True and run['final_state'] == 'idle'
                      and (not is_hint or (run['host_cleared'] and run['input_attempts'] == 0)))
            run['status'] = 'passed' if passed else 'failed'
            report.update(run)
            if passed:
                report['status'] = 'running'
            save()
            if not passed:
                return False
        report['status'] = 'passed'
        return True
    finally:
        manager.close()
        clear()
        if report['status'] == 'running':
            report['status'] = 'failed'
        save()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--until-log', default='')
    parser.add_argument('--timeout', type=float, default=30)
    parser.add_argument('--options', default='{}')
    parser.add_argument('--timing', default='creation_timing.json')
    parser.add_argument('--result', default='first_action.json')
    args = parser.parse_args()
    if not math.isfinite(args.timeout) or not 0 < args.timeout <= 180:
        parser.error('运行时限须在 0～180 秒内')
    if any(Path(value).name != value or not value.endswith('.json') for value in (args.timing, args.result)):
        parser.error('计时与结果文件必须是当前草稿内的 JSON 文件名')
    channel = os.environ.get('HUANZHIZHI_CLICK_EVENTS')
    if not channel or not Path(channel).is_file():
        parser.error('请从桌宠创建入口运行，输入必须使用本轮宿主通道')
    creation = Path.cwd().resolve()
    timing = json.loads((creation / args.timing).read_text(encoding='utf-8'))
    origin = timing['started_counter']
    source = args.source.resolve()
    project = Path(__file__).resolve().parents[2]
    allowed = (project / 'tasks', project / 'test_tasks', creation / 'working')
    if source.parent not in allowed or not (source / 'task.py').is_file():
        parser.error('仅运行本项目技能或本草稿 working 下的普通 Python 技能')
    working = creation / 'working' / source.name
    if source != working:
        working.parent.mkdir(exist_ok=True)
        copy_skill_package(source, working)
    task = next(t for t in TaskCatalog(working.parent).reload() if t.script_path == working / 'task.py')
    options = json.loads(args.options)
    if not isinstance(options, dict):
        parser.error('options 必须是 JSON 对象')
    if any(not is_hint for is_hint, _ in trial_options(task, options)) and not args.until_log.strip():
        parser.error('自动或普通模式需要明确的业务结果确认日志 --until-log')
    directory = runtime_directory(task.script_path) / 'runs' / ('trial_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
    directory.mkdir(parents=True)
    report = dict(source=str(source), entry=str(task.script_path), until_log=args.until_log,
                  creation_started_at=timing['started_at'])
    passed = run_trial(task, options, channel=channel, directory=directory, result_path=creation / args.result,
                       origin=origin, report=report, until_log=args.until_log, timeout=args.timeout)
    print(json.dumps(report, ensure_ascii=False), flush=True)
    if not passed:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
