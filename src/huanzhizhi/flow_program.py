"""Saved capability calls, live result bindings and explicit conditions."""
import copy
import json
import math
import re
import hashlib

import numpy as np
from PIL import Image

from huanzhizhi.capabilities import NoMatch, VisionCalls, method, _validate_json


def references(value, kind='result'):
    if isinstance(value, dict):
        if kind in value:
            if set(value) not in ({kind}, {kind, 'path'}) or not isinstance(value[kind], str):
                raise ValueError('结果引用格式无效')
            if not isinstance(value.get('path', []), list):
                raise ValueError('结果path必须是字段或索引列表')
            yield value[kind]
        else:
            for child in value.values():
                yield from references(child, kind)
    elif isinstance(value, list):
        for child in value:
            yield from references(child, kind)


def validate_program(program, *, action=False, parameters=None, directory=None):
    _validate_json(program)
    if not isinstance(program, dict) or not {'calls', 'checks'} <= program.keys() or set(program)-{'calls', 'checks', 'target'}:
        raise ValueError('流程需要calls和checks，不能用旧坐标代替识别步骤')
    calls, checks = program['calls'], program['checks']
    if not isinstance(calls, list) or not 1 <= len(calls) <= 32:
        raise ValueError('流程需要1～32个通用方法调用')
    known = set()
    live = set()
    for call in calls:
        if not isinstance(call, dict) or set(call) != {'id', 'method', 'arguments'}:
            raise ValueError('调用必须包含id、method、arguments')
        identifier = call['id']
        if not isinstance(identifier, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,63}', identifier) or identifier in known:
            raise ValueError('调用id无效或重复')
        function = method(call['method'], directory)
        if not isinstance(call['arguments'], dict):
            raise ValueError('方法参数必须是对象')
        if list(references(call['arguments'], 'previous')):
            raise ValueError('操作前结果只能用于结果核对，不能作为当前方法输入')
        deps = set(references(call['arguments']))
        if not deps <= known:
            raise ValueError('结果引用必须指向前面的调用')
        # A condition must ultimately depend on the current image, not constants.
        import inspect
        first = next(iter(inspect.signature(function).parameters))
        if first in ('image', 'img'):
            source = call['arguments'].get(first)
            if isinstance(source, dict) and 'asset' in source:
                raise ValueError('不能用保存的旧截图充当当前画面')
            if source is None or (isinstance(source, dict) and set(source) == {'crop'}) or deps & live:
                live.add(identifier)
        elif deps & live:
            live.add(identifier)
        known.add(identifier)
    if not isinstance(checks, list) or not 1 <= len(checks) <= 16:
        raise ValueError('需要明确的识别成功条件')
    for check in checks:
        if not isinstance(check, dict) or set(check) not in ({'value', 'op'}, {'value', 'op', 'expected'}):
            raise ValueError('检查条件格式无效')
        if check['op'] not in ('exists', 'empty', 'eq', 'ne', 'gt', 'ge', 'lt', 'le'):
            raise ValueError('未知检查运算')
        if (check['op'] in ('exists', 'empty')) != ('expected' not in check):
            raise ValueError('检查条件参数不匹配')
        deps = set(references(check))
        if not deps <= known or not set(references(check['value'])) & live:
            raise ValueError('检查条件必须引用当前画面的识别结果')
        missing_previous = set(references(check, 'previous')) - live
        if missing_previous:
            raise ValueError('操作前结果必须引用依赖画面的当前流程调用；请在verification.calls中包含：'
                             + ', '.join(sorted(missing_previous)))
    if action:
        target = program.get('target')
        if not isinstance(target, dict) or 'result' not in target or not set(references(target)) <= live:
            raise ValueError('操作目标必须引用重新识别的矩形，不能保存固定点击坐标')
    elif 'target' in program and not set(references(program['target'])) <= known:
        raise ValueError('目标引用无效')
    if parameters is not None and not set(references(parameters)) <= known:
        raise ValueError('动作参数引用不存在的结果')
    if parameters is not None and 'steps' in parameters:
        steps = parameters['steps']
        if (not isinstance(steps, dict) or 'result' not in steps
                or not set(references(steps)) <= live):
            raise ValueError('整批动作必须引用当前画面推导的结果，不能保存固定操作列表')
    if list(references([program.get('target'), parameters], 'previous')):
        raise ValueError('动作目标和参数不能使用操作前的旧结果')
    if action and list(references(checks, 'previous')):
        raise ValueError('动作前条件不能引用尚未发生的状态变化；请放入verification')


def compile_program(spec, inspections, image, prefix, *, action=False, parameters=None):
    """Copy actual call definitions, freeze reference templates, never snapshots as input."""
    if not isinstance(spec, dict) or not isinstance(spec.get('calls'), list):
        raise ValueError('缺少可执行的识别流程')
    program = copy.deepcopy(spec)
    ordered = []
    ids = set()
    visiting = set()
    definitions = dict(inspections.definitions)
    inline_ids = set()
    for entry in program['calls']:
        if isinstance(entry, dict):
            if ('id' not in entry or entry['id'] in inline_ids
                    or entry['id'] in definitions and definitions[entry['id']] != entry):
                raise ValueError(f'流程调用id重复或缺失：{str(entry.get("id", "<缺失>"))[:80]}。'
                                 '已有id不能对应不同定义；请直接在calls中引用其id，或为修改后的定义使用新id。')
            inline_ids.add(entry['id'])
            definitions[entry['id']] = entry

    def include(identifier):
        if identifier in ids:
            return
        if identifier in visiting or identifier not in definitions:
            raise ValueError('调用引用缺失或形成循环：'+str(identifier))
        visiting.add(identifier)
        entry = copy.deepcopy(definitions[identifier])
        for dependency in references(entry.get('arguments', {})):
            include(dependency)
        ordered.append(entry)
        ids.add(identifier)
        visiting.remove(identifier)

    for entry in program['calls']:
        include(entry if isinstance(entry, str) else entry['id'])
    program['calls'] = ordered
    validate_program(program, action=action, parameters=parameters, directory=inspections.directory)
    runner = VisionCalls(inspections.directory, inspections.stopped)
    def evaluate_reference(identifier):
        if identifier in runner.results:
            return
        call = next(entry for entry in ordered if entry['id'] == identifier)
        for dependency in references(call['arguments']):
            evaluate_reference(dependency)
        runner.results[identifier] = runner.evaluate(call['method'], call['arguments'], image)
    # Template images are references learned during exploration; the search
    # image is always captured again. Freeze templates even if built by a crop.
    for index, call in enumerate(ordered):
        for key in ('template', 'templates', 'mask'):
            if key not in call['arguments'] or (key == 'mask' and call['method'] != 'FindTemplateByMask'):
                continue
            raw = call['arguments'][key]
            values = raw if key == 'templates' else [raw]
            frozen = []
            for item_index, value in enumerate(values):
                if isinstance(value, dict) and set(value) == {'asset'}:
                    frozen.append(value)
                    continue
                for dependency in references(value):
                    evaluate_reference(dependency)
                resolved = runner._resolve(value, image)
                if isinstance(resolved, (Image.Image, np.ndarray)):
                    asset = f'{prefix}-{index}-{key}-{item_index}.png'
                    suffix = 0
                    while (inspections.directory / asset).exists():
                        suffix += 1
                        asset = f'{prefix}-{index}-{key}-{item_index}-{suffix}.png'
                    Image.fromarray(np.asarray(resolved).astype(np.uint8)).save(inspections.directory / asset)
                    frozen.append({'asset': asset})
                else:
                    frozen.append(value)
            call['arguments'][key] = frozen if key == 'templates' else frozen[0]
    needed = (set(references([program['checks'], program.get('target'), parameters]))
              | set(references(program['checks'], 'previous')))
    for call in reversed(ordered):
        if call['id'] in needed:
            needed.update(references(call['arguments']))
    program['calls'] = [call for call in ordered if call['id'] in needed]
    validate_program(program, action=action, parameters=parameters, directory=inspections.directory)
    # Later steps reuse the learned reference, even if its original region now
    # contains another control. Search images still come from each new frame.
    for call in program['calls']:
        if call['id'] in inspections.definitions:
            inspections.definitions[call['id']] = copy.deepcopy(call)
    return program


def _check(value, op, expected=None):
    if op == 'exists':
        return value is not None and (len(value) > 0 if isinstance(value, (list, tuple, dict, str)) else bool(value))
    if op == 'empty':
        return not _check(value, 'exists')
    if op == 'eq':
        return value == expected
    if op == 'ne':
        return value != expected
    if op == 'gt':
        return value > expected
    if op == 'ge':
        return value >= expected
    if op == 'lt':
        return value < expected
    if op == 'le':
        return value <= expected
    raise ValueError('未知检查运算')


def run_program(program, image, directory, *, parameters=None, stopped=lambda: False, log=lambda text: None,
                cache=None, before=None):
    """Evaluate all calls on one fresh image; resolve action data from those results."""
    validate_program(program, action=parameters is not None, parameters=parameters, directory=directory)
    runner = VisionCalls(directory, stopped)
    cache = {} if cache is None else cache
    keys = {}
    previous = None

    def resolve_check(value):
        nonlocal previous
        if isinstance(value, dict):
            if 'previous' in value:
                if before is None:
                    raise ValueError('此核对需要操作前的画面')
                if previous is None:
                    previous = VisionCalls(directory, stopped)
                    needed = set(references(program['checks'], 'previous'))
                    for call in reversed(program['calls']):
                        if call['id'] in needed:
                            needed.update(references(call['arguments']))
                    for call in program['calls']:
                        if call['id'] not in needed:
                            continue
                        if stopped():
                            raise InterruptedError('已停止')
                        previous.results[call['id']] = previous.evaluate(call['method'], call['arguments'], before)
                return previous._resolve({'result': value['previous'], 'path': value.get('path', [])}, before)
            if 'result' in value:
                return runner._resolve(value, image)
            return {key: resolve_check(child) for key, child in value.items()}
        if isinstance(value, list):
            return [resolve_check(child) for child in value]
        return value

    def cache_value(value):
        if isinstance(value, dict):
            if 'result' in value:
                return {'call': keys[value['result']], 'path': value.get('path', [])}
            return {key: cache_value(child) for key, child in value.items()}
        if isinstance(value, list):
            return [cache_value(child) for child in value]
        return value

    try:
        for call in program['calls']:
            if stopped():
                raise InterruptedError('已停止')
            key = hashlib.sha256(json.dumps([call['method'], cache_value(call['arguments'])], sort_keys=True).encode()).hexdigest()
            keys[call['id']] = key
            if key not in cache:
                cache[key] = runner.evaluate(call['method'], call['arguments'], image)
                log('执行通用方法：'+call['method'])
            runner.results[call['id']] = cache[key]
        if stopped():
            raise InterruptedError('已停止')
        for check in program['checks']:
            value = resolve_check(check['value'])
            expected = resolve_check(check.get('expected'))
            if not _check(value, check['op'], expected):
                return None
        result = {}
        if parameters is not None:
            box = runner._resolve(program['target'], image)
            if (not isinstance(box, (list, tuple)) or len(box) != 4
                    or any(type(v) not in (int, float) or not math.isfinite(v) for v in box)):
                raise ValueError('识别目标不是有效矩形')
            left, top, right, bottom = box
            if not (0 <= left < right <= image.width and 0 <= top < bottom <= image.height):
                raise ValueError('识别目标超出当前画面')
            result['target'] = [left, top, right, bottom]
            result['parameters'] = runner._resolve(parameters, image)
        return result
    except NoMatch as error:
        log('识别规则未满足：'+str(error))
        return None
