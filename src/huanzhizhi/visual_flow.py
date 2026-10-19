"""Execute verified states and rule results through host-owned input actions."""
from __future__ import annotations

import json
from pathlib import Path
import time
import math

import cv2
import numpy as np
from PIL import Image

from huanzhizhi.platform.target_window import Win32DesktopApi, WindowRule
from huanzhizhi.platform.window_capture import WgcWindowCapture
from huanzhizhi.capabilities import INPUT, NoMatch
from huanzhizhi.flow_program import run_program, validate_program, references


def validate_input(action, params, size, *, directory=None):
    if action not in INPUT or not isinstance(params, dict):
        raise ValueError('未知输入动作')
    required = set(INPUT[action][1])
    if set(params) != required:
        raise ValueError('动作参数不匹配：'+action)
    if action == 'sequence':
        steps = params['steps']
        if not isinstance(steps, list) or not 1 <= len(steps) <= 200:
            raise ValueError('整批动作需要1～200项')
        for step in steps:
            if (not isinstance(step, dict) or set(step) != {'action', 'target', 'parameters', 'when', 'after', 'skip'}
                    or step['action'] not in ('click', 'right_click', 'key') or type(step['skip']) is not bool):
                raise ValueError('整批动作条目无效；仅支持click/right_click/key和明确的局部核对')
            validate_input(step['action'], step['parameters'], size, directory=directory)
            box = step['target']
            if (not isinstance(box, list) or len(box) != 4
                    or any(type(v) not in (int, float) or not math.isfinite(v) for v in box)
                    or not (0 <= box[0] < box[2] <= size[0] and 0 <= box[1] < box[3] <= size[1])):
                raise ValueError('整批动作目标超出当前画面')
            validate_program(step['when'], directory=directory)
            if list(references(step['when'], 'previous')):
                raise ValueError('批内操作前提不能使用previous')
            validate_program(step['after'], directory=directory)
    if action == 'drag':
        end, duration = params['end'], params['duration']
        if (not isinstance(end, list) or len(end) != 2 or any(type(v) is not int for v in end)
                or not 0 <= end[0] < size[0] or not 0 <= end[1] < size[1]
                or type(duration) not in (float, int) or not .1 <= duration <= 5):
            raise ValueError('拖动终点或时长无效')
    if action == 'scroll':
        delta = params['delta']
        if type(delta) is not int or delta == 0 or delta % 120 or abs(delta) > 1200:
            raise ValueError('滚轮delta需为±120的倍数，最多10格')
    if action == 'key':
        from huanzhizhi.platform.target_window import INPUT_KEYS
        if not isinstance(params['key'], str) or params['key'] not in INPUT_KEYS:
            raise ValueError('不支持的按键：key必须是支持的按键字符串，数字键也需使用字符串')
    if action == 'text':
        text = params['text']
        if not isinstance(text, str) or not 1 <= len(text) <= 1000 or any(ord(c) < 32 for c in text):
            raise ValueError('文字需为1～1000个可打印字符；提交请单独按键')


def rectangle(value, size):
    if (not isinstance(value, list) or len(value) != 4
            or any(type(v) is not int for v in value)):
        raise ValueError('识别区域必须是四个整数')
    x, y, width, height = value
    if not (0 <= x < x+width <= size[0] and 0 <= y < y+height <= size[1]
            and width >= 8 and height >= 8):
        raise ValueError('识别区域超出画面或太小，未操作')
    return x, y, x+width, y+height


def save_condition(image, box, directory, name):
    crop = image.crop(rectangle(box, image.size))
    if np.asarray(crop).std(axis=(0, 1)).max() < 8:
        raise ValueError('识别区域只有纯色，缺少可核对的文字或图案，未操作')
    crop.save(directory / name)
    return {'box': box, 'image': name}


def matches(image, condition, directory):
    box = rectangle(condition['box'], image.size)
    path = (directory / condition['image']).resolve()
    if path.parent != directory.resolve() or path.suffix != '.png':
        raise ValueError('识别素材路径无效')
    with Image.open(path) as source:
        expected = np.asarray(source.convert('RGB'))
    actual = np.asarray(image.crop(box))
    if actual.shape != expected.shape:
        return False
    # Match in the recorded client region: unrelated identical controls elsewhere
    # must not silently redirect a click. Allow minor rendering noise only.
    score = cv2.matchTemplate(actual, expected, cv2.TM_SQDIFF_NORMED)[0, 0]
    return bool(np.isfinite(score) and score <= .012)


def scene_changed(before, after):
    if before.size != after.size:
        return True
    first = np.asarray(before.resize((256, 256))).astype(np.int16)
    second = np.asarray(after.resize((256, 256))).astype(np.int16)
    return np.mean(np.max(np.abs(first-second), axis=2) > 20) > .01


class VisualFlow:
    def __init__(self, target, directory, stopped, progress, log, *, desktop=None, capture=None, point=None, pause=None):
        self.target = target
        self.rule = WindowRule(target.title, target.width, target.height)
        self.directory = Path(directory)
        self.stopped, self.progress, self.log = stopped, progress, log
        self._pause = pause
        self.emit_point = point if point is not None else lambda x, y: None
        self.capture = capture if capture is not None else WgcWindowCapture()
        self.desktop = desktop if desktop is not None else Win32DesktopApi(
            capture=self.capture, configure_dpi_awareness=False)
        self.last_input = None
        self._program_image = None
        self._program_cache = {}
        self.diagnostics = []

    def check_stop(self):
        if self.stopped():
            raise InterruptedError('已停止，已完成的操作保留。')

    def wait(self, seconds):
        deadline = time.monotonic()+seconds
        while time.monotonic() < deadline:
            self.check_stop()
            time.sleep(min(.05, max(0, deadline-time.monotonic())))
        self.check_stop()

    def observe(self):
        self.check_stop()
        self.target = self.desktop.refresh(self.target, self.rule)
        if self.target.title != self.rule.title_contains:
            raise ValueError('目标窗口标题已改变，已停止。')
        return self.desktop.capture_client(self.target, after=self.last_input)

    def prepare(self):
        try:
            from tasks.数独留一格.recognition import TITLE, SIZE
        except ImportError:
            return
        if self.target.title == TITLE and (self.target.width, self.target.height) != SIZE:
            self.check_stop()
            self.progress('正在调整数独窗口大小，以核对棋盘…')
            self.target = self.desktop.set_client_size(self.target, *SIZE)
            self.rule = WindowRule(self.target.title, *SIZE)
            self.log(f'数独窗口已调整到校准尺寸：{SIZE[0]}×{SIZE[1]}')
            self.wait(.3)

    def observe_settled(self):
        previous = self.observe()
        for _ in range(8):
            self.wait(.2)
            current = self.observe()
            if not scene_changed(previous, current):
                return current
            previous = current
        return previous

    def available_actions(self):
        actions = list(INPUT)
        try:
            from tasks.数独留一格.recognition import TITLE, SIZE
        except ImportError:
            return actions
        if self.target.title == TITLE and (self.target.width, self.target.height) == SIZE:
            actions += ['sudoku_fill', 'sudoku_leave_one']
        return actions

    def accepts(self, node, image):
        if 'program' in node:
            return self.resolve_node(node, image) is not None
        if node['action'] not in self.available_actions():
            return False
        if not matches(image, node['before'], self.directory):
            return False
        if node['action'] in INPUT:
            return matches(image, node['target'], self.directory)
        from tasks.数独留一格.recognition import read_board, templates
        try:
            return 0 in read_board(image, templates())
        except ValueError:
            return False

    def program_result(self, program, image, parameters=None, before=None):
        if self._program_image is not image:
            self._program_image = image
            self._program_cache = {}
        def record(message):
            if message.startswith('识别规则未满足：'):
                self.diagnostics.append(message)
                del self.diagnostics[:-8]
            self.log(message)
        return run_program(program, image, self.directory, parameters=parameters,
                           stopped=self.stopped, log=record, cache=self._program_cache, before=before)

    def condition_matches(self, condition, image, *, before=None):
        if 'program' in condition:
            return self.program_result(condition['program'], image, before=before) is not None
        return matches(image, condition, self.directory)

    def resolve_node(self, node, image):
        if 'program' in node:
            if node['action'] not in INPUT:
                raise ValueError('新流程不能调用整套旧任务')
            result = self.program_result(node['program'], image, node.get('parameters', {}))
            if result is not None:
                validate_input(node['action'], result['parameters'], image.size, directory=self.directory)
                if node['action'] == 'sequence':
                    bounds = result['target']
                    if any(not (bounds[0] <= step['target'][0] < step['target'][2] <= bounds[2]
                                and bounds[1] <= step['target'][1] < step['target'][3] <= bounds[3])
                           for step in result['parameters']['steps']):
                        raise ValueError('整批动作必须位于本次识别出的目标区域内')
            return result
        if not self.accepts(node, image):
            return None
        if node['action'] in INPUT:
            return {'target': rectangle(node['target']['box'], image.size), 'parameters': node.get('parameters', {})}
        return {}

    def execute(self, node):
        # Never click the old AI screenshot. Re-capture and validate immediately
        # before input, including after a long model response or a pet drag.
        image = self.observe()
        resolved = self.resolve_node(node, image)
        if resolved is None:
            raise NoMatch('画面已变化或目标未核对通过，未执行这一步。')
        if 'program' in node:
            # OCR and feature matching can take time. Confirm the operation's
            # visual target has not changed while its coordinates were computed.
            current = self.observe()
            if scene_changed(image, current):
                raise NoMatch('识别期间画面已变化，未执行操作，请重新观察')
            left, top, right, bottom = resolved['target']
            box = (math.floor(left), math.floor(top), math.ceil(right), math.ceil(bottom))
            if scene_changed(image.crop(box), current.crop(box)):
                raise NoMatch('识别期间目标已变化，未执行操作')
            if self.resolve_node(node, current) != resolved:
                raise NoMatch('识别期间目标或输入数据已变化，未执行操作')
        self.check_stop()
        self.progress(node['message'])
        if node['action'] == 'sequence':
            # The queue was solved once and freshly checked above. Only the
            # item's small visual guards run between inputs, never its solver.
            for index, step in enumerate(resolved['parameters']['steps'], 1):
                self.wait_if_paused()
                current = self.observe()
                if not self.condition_matches({'program': step['when']}, current):
                    if step['skip']:
                        self.log(f'批内第{index}项已不适用，跳过。')
                        continue
                    raise ValueError(f'批内第{index}项局部前提已改变，未继续操作')
                condition = {'program': step['after']}
                if self.condition_matches(condition, current, before=current):
                    raise ValueError(f'批内第{index}项的结果在操作前已成立，未操作')
                if step['action'] == 'key' and not self.desktop.is_foreground(self.target):
                    raise ValueError('目标窗口已失去输入焦点，未继续整批输入')
                self._input(step['action'], step['target'], step['parameters'])
                self.last_input = time.perf_counter()
                self.verify(condition, timeout=4, before=current)
                self.log(f'批内第{index}项已执行并核对：{step["action"]}')
            return image
        if node['action'] in INPUT:
            action = node['action']
            params = resolved['parameters']
            validate_input(action, params, image.size)
            self._input(action, resolved['target'], params)
        else:
            from tasks.数独留一格.controller import run
            run(self, desktop=self.desktop, capture=self.capture,
                root=self.directory / 'sudoku', target=self.target,
                leave_last_cell=node['action'] == 'sudoku_leave_one', close_capture=False)
        self.last_input = time.perf_counter()
        self.wait(.4)
        return image

    def _input(self, action, target, params):
        self.check_stop()
        x, y, right, bottom = target
        x, y = int((x+right)/2), int((y+bottom)/2)
        if action in ('drag', 'scroll'):
            self.emit_point(self.target.left+x, self.target.top+y)
        if action in ('click', 'right_click', 'double_click'):
            self.desktop.post_click(self.target, x, y, action, wait=self.wait)
        elif action == 'drag':
            self.desktop.post_drag(self.target, x, y, *params['end'], params['duration'], wait=self.wait)
        elif action == 'scroll':
            self.desktop.post_scroll(self.target, x, y, params['delta'])
        elif action == 'key':
            self.desktop.post_key(self.target, params['key'], wait=self.wait)
        elif action == 'text':
            self.desktop.post_text(self.target, params['text'], wait=self.wait)

    # The existing Sudoku controller uses this small part of TaskSession.
    @property
    def stop_requested(self):
        return self.stopped()

    def wait_if_paused(self):
        return self._pause() if self._pause is not None else False

    def wait_for_stop(self, seconds):
        try:
            self.wait(seconds)
        except InterruptedError:
            return True
        return False

    def emit_status(self, title, name, detail):
        self.progress(detail)

    def emit_log(self, text):
        self.log(text)

    def verify(self, condition, timeout=6, *, nodes=(), before=None):
        conditions = condition if isinstance(condition, list) else [condition]
        deadline = time.monotonic()+timeout
        while time.monotonic() < deadline:
            image = self.observe()
            if any(self.condition_matches(expected, image, before=before) for expected in conditions):
                return image
            if nodes and before is not None:
                next_node = find_node(self, nodes, image)
                if next_node is not None and not self.accepts(next_node, before):
                    # Optional popups may be absent on a later run. A different
                    # already-verified state is valid progress; the original
                    # unchanged screen is never accepted as a successful click.
                    return image
            self.wait(.15)
        # Recognition of an alternative state can consume the polling budget
        # while the expected result appears. Check the latest frame once more
        # instead of reporting failure based on the older pre-recognition frame.
        image = self.observe()
        if any(self.condition_matches(expected, image, before=before) for expected in conditions):
            return image
        raise TimeoutError('操作后的画面没有达到预期，未重复点击。')

    def close(self):
        self.capture.close()


def find_node(flow, nodes, image, *, requested=None):
    found = []
    operations = set()
    requested_result = flow.resolve_node(requested, image) if requested is not None else None
    for node in nodes:
        resolved = flow.resolve_node(node, image)
        if resolved is None:
            continue
        if requested is not None and (node['action'] != requested['action'] or resolved != requested_result):
            continue
        found.append(node)
        operation = (node['action'],)
        if node['action'] in INPUT:
            left, top, right, bottom = resolved['target']
            operation += (int((left+right)/2), int((top+bottom)/2))
            operation += (json.dumps(resolved['parameters'], sort_keys=True),)
        operations.add(operation)
    if len(operations) > 1:
        raise ValueError('当前画面同时匹配多个操作，无法确定下一步，已停止。')
    if len(found) > 1:
        # The same verified action can lead to different observed result pages,
        # such as a victory page with or without a dismissible popup.
        return {**found[0], 'after': [node['after'] for node in found]}
    return found[0] if found else None


def replay(flow, recipe):
    count = 0
    while True:
        flow.wait_if_paused()
        image = flow.observe_settled()
        if recipe.get('done') and flow.condition_matches(recipe['done'], image):
            return f'目标已完成，已核对 {count} 个步骤。'
        node = find_node(flow, recipe['nodes'], image)
        deadline = time.monotonic()+4
        while node is None and time.monotonic() < deadline:
            if flow.wait_if_paused():
                deadline = time.monotonic()+4
            flow.wait(.2)
            image = flow.observe()
            if recipe.get('done') and flow.condition_matches(recipe['done'], image):
                return f'目标已完成，已核对 {count} 个步骤。'
            node = find_node(flow, recipe['nodes'], image)
        if node is None:
            raise ValueError('当前画面不在已验证流程中，请回到技能起始画面或重新探索。')
        before = flow.execute(node)
        flow.verify(node['after'], nodes=recipe['nodes'], before=before)
        count += 1
        flow.log(f'已核对第 {count} 步：{node["message"]}')
        if count >= 200 and not recipe['continuous']:
            raise ValueError('流程重复运行但尚未完成，已停止。')


def task_main(path):
    from huanzhizhi.task_sdk import TaskSession, describe
    directory = Path(path).resolve().parent
    recipe = json.loads((directory / 'flow.json').read_text(encoding='utf-8'))
    if recipe.get('version') not in (1, 2) or not recipe.get('ready'):
        raise ValueError('技能流程尚未验证完成')
    if describe(path):
        return
    session = TaskSession(path)
    flow = None
    capture = WgcWindowCapture()
    try:
        desktop = Win32DesktopApi(capture=capture)
        rule = WindowRule(**recipe['window'])
        if any(node['action'].startswith('sudoku_') for node in recipe['nodes']):
            candidates = [window for window in desktop.list_windows() if window.title == rule.title_contains]
            if len(candidates) != 1:
                raise ValueError('请保持一个目标窗口打开，以便确定技能操作对象。')
            target = candidates[0]
        else:
            target = desktop.resolve(rule)
        flow = VisualFlow(target, directory, lambda: session.stop_requested,
                          lambda text: session.emit_status('执行中', session.skill['name'], text),
                          session.emit_log, desktop=desktop, capture=capture, point=session.emit_point,
                          pause=session.wait_if_paused)
        if any(node['action'].startswith('sudoku_') for node in recipe['nodes']):
            flow.prepare()
        if not rule.matches(flow.target):
            raise ValueError('当前窗口尺寸与保存的技能不匹配，未操作。')
        detail = replay(flow, recipe)
        session.emit_finished('完成', session.skill['name'], detail)
    except InterruptedError as error:
        session.emit_finished('已停止', session.skill['name'], str(error), stopped_by_request=True)
    except Exception as error:
        session.emit_finished('失败', session.skill['name'], str(error))
    finally:
        capture.close()
