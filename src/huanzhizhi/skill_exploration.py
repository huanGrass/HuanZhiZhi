"""Explore a target through fixed actions, verify transitions and save a flow."""
from __future__ import annotations

import json
from huanzhizhi.agents.codex.analysis import analyze_image


from huanzhizhi.visual_flow import find_node, scene_changed
from huanzhizhi.capabilities import INPUT, VISION, DATA, NoMatch, VisionCalls, arguments, catalog
from huanzhizhi.flow_program import compile_program
from huanzhizhi.skill_methods import custom_catalog, define_method


_SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'properties': {
        'status': {'type': 'string', 'enum': ['batch', 'plan', 'define', 'inspect', 'act', 'wait', 'done', 'blocked']},
        'message': {'type': 'string'},
        'detail': {'type': 'string'},
        'name': {'type': 'string'},
        'continuous': {'type': 'boolean'},
        'previous': {'type': 'string', 'enum': ['none', 'succeeded', 'pending', 'failed']},
        'action': {'type': 'string', 'enum': ['none', *INPUT]},
        'method': {'type': 'string'},
        'arguments': {'type': 'string'},
        'program': {'type': 'string'},
        'target': {'type': 'array', 'items': {'type': 'integer'}, 'minItems': 4, 'maxItems': 4},
        'evidence': {'type': 'array', 'items': {'type': 'integer'}, 'minItems': 4, 'maxItems': 4},
        'expected': {'type': 'string'},
    },
    'required': ['status', 'message', 'detail', 'name', 'continuous', 'previous',
                 'action', 'target', 'evidence', 'expected', 'method', 'arguments', 'program'],
}


def _decision_from_response(result, pending):
    operation = result['operation']
    if not isinstance(operation, dict):
        raise ValueError('operation必须是JSON对象')
    params = operation.get('arguments', {})
    program = operation.get('program') or {}
    if not isinstance(params, dict) or not isinstance(program, dict):
        raise ValueError('arguments和program必须直接使用JSON对象，不要编码为字符串')
    previous = 'none'
    if pending is not None:
        previous = 'pending'
        if operation['status'] == 'blocked':
            previous = 'failed'
        elif operation['status'] in ('act', 'plan', 'done', 'wait') and program:
            previous = 'succeeded'
    return {**{key: result[key] for key in ('message', 'detail', 'name', 'continuous')},
            'action': 'none', 'method': '', 'expected': '', **operation,
            'arguments': json.dumps(params, ensure_ascii=False),
            'program': json.dumps(program, ensure_ascii=False), 'previous': previous,
            'target': [0, 0, 0, 0], 'evidence': [0, 0, 0, 0]}



def validate_decision(result, size, actions, pending, custom_methods=()):
    if isinstance(result, dict) and set(result) == {'invalid_response', 'error'}:
        raise ValueError(result['error'])
    if isinstance(result, dict) and set(result) == {'message', 'detail', 'name', 'continuous', 'operation'}:
        result = _decision_from_response(result, pending)
    if not isinstance(result, dict) or set(result) != set(_SCHEMA['required']):
        raise ValueError('AI 返回的步骤结构无效，未操作。')
    for key in ('message', 'detail', 'name', 'expected'):
        if not isinstance(result[key], str) or len(result[key]) > 2000:
            raise ValueError('AI 返回的步骤文字无效，未操作。')
    if not result['message'].strip() or not result['name'].strip() or type(result['continuous']) is not bool:
        raise ValueError('AI 没有说明本步意图，未操作。')
    for key in ('status', 'previous', 'action'):
        if result[key] not in _SCHEMA['properties'][key]['enum']:
            raise ValueError('AI 选择了未知动作，未操作。')
    if (pending is not None and result['previous'] == 'succeeded'
            and result['status'] in ('inspect', 'define', 'batch')):
        # Read-only preparation can gather evidence, but cannot certify an input.
        result = {**result, 'previous': 'pending'}
    if (pending is None) != (result['previous'] == 'none'):
        raise ValueError('AI 没有核对上一步结果，未继续操作。')
    if result['previous'] == 'pending' and result['status'] not in ('wait', 'inspect', 'define', 'batch'):
        raise ValueError('上一步尚未确认，未继续操作。')
    if result['previous'] == 'failed' and result['status'] != 'blocked':
        raise ValueError('上一步失败，未继续操作。')
    params = arguments(result['arguments'])
    program = arguments(result['program'])
    if result['status'] == 'plan':
        # Only validated node actions execute; the redundant top-level action
        # cannot add an input and need not trigger another model round.
        result = {**result, 'action': 'none'}
    if result['status'] == 'inspect':
        if result['method'] not in (VISION.keys() | DATA.keys() | set(custom_methods)) or result['previous'] not in ('none', 'pending'):
            raise ValueError('识别方法无效或尚不能确认上一步')
    elif result['status'] == 'define':
        if not isinstance(result['method'], str) or result['previous'] not in ('none', 'pending'):
            raise ValueError('定义方法尚不能确认上一步')
    elif result['status'] in ('batch', 'plan'):
        if result['method'] or result['previous'] not in (('none', 'pending') if result['status'] == 'batch' else ('none', 'succeeded')):
            raise ValueError('有待核对操作时，plan需要独立verification，核对通过后才执行新规则。')
    elif result['method'] != '':
        raise ValueError('只有inspect可以调用识别方法')
    if result['status'] == 'act':
        if result['action'] not in actions or not result['expected'].strip():
            raise ValueError('缺少可执行能力或操作后的核对条件，未操作。')
    elif result['action'] != 'none':
        raise ValueError('当前状态不允许操作。')
    elif result['status'] not in ('inspect', 'define', 'batch', 'plan') and params:
        raise ValueError('当前状态不允许动作参数')
    if result['status'] in ('act', 'done', 'plan') or result['previous'] == 'succeeded':
        if not program:
            raise ValueError('缺少可重新执行的方法组合，未操作或保存技能')
    elif program:
        raise ValueError('当前状态不应携带执行流程')
    if result['continuous'] and result['status'] == 'done':
        raise ValueError('持续任务不能以单轮完成结束，请补充目标。')
    if result['status'] == 'act' and result['previous'] == 'none' and 'verification' in program:
        # A prospective result check is the existing one-node plan protocol.
        # It receives exactly the same pre-input, post-input and persistence checks.
        after = program.pop('verification')
        node = dict(action=result['action'], message=result['message'], expected=result['expected'],
                    parameters=params, program=program, after=after)
        result = {**result, 'status': 'plan', 'action': 'none', 'arguments': '{}',
                  'program': json.dumps(dict(nodes=[node], done=None), ensure_ascii=False)}
    return result


def explore(flow, goal, step, saved):
    directory = flow.directory
    recipe = dict(version=2, ready=False, name='', goal=goal, continuous=False,
                  window=dict(title_contains=flow.target.title, client_width=flow.target.width,
                              client_height=flow.target.height), nodes=[], done=None)
    pending = None
    history = []
    waits = 0
    decisions = 0
    reused = 0
    pending_image = None
    inspections = []
    inspected_image = None
    rejected_decision = None
    local_plan = None
    plan_active = False
    vision_calls = VisionCalls(directory, flow.stopped)

    def persist(ready=False):
        recipe['ready'] = recipe['ready'] or ready
        temporary = directory / 'flow.tmp'
        temporary.write_text(json.dumps(recipe, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(directory / 'flow.json')
        if ready:
            manifest = dict(protocol_version=1, id=directory.name,
                            name=recipe['name'], description=recipe['goal'])
            temporary = directory / 'skill.tmp'
            temporary.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
            temporary.replace(directory / 'skill.json')
            # The host entry is fixed; optional Python rules are loaded through
            # the tested data-method runner, not executed as task entry points.
            entry = directory / 'task.py'
            if not entry.exists():
                temporary = directory / 'task.tmp'
                temporary.write_text('from huanzhizhi.visual_flow import task_main\n'
                                     'if __name__ == "__main__":\n    task_main(__file__)\n', encoding='utf-8')
                temporary.replace(entry)
                saved(str(directory))

    def reuse(node):
        nonlocal reused, pending, pending_image, inspections
        reused += 1
        if reused > 200 and not recipe['continuous']:
            raise ValueError('流程重复运行但尚未完成，已停止。')
        before = flow.execute(node)
        try:
            flow.verify(node['after'], nodes=recipe['nodes'], before=before)
        except TimeoutError:
            pending = dict(node)
            pending_image = before
            before.save(directory / 'pending-before.png')
            inspections = []
            history.append('已执行“'+node['message']+'”，但原核对规则尚未确认结果；'
                           '请根据操作前后画面继续核对，不得重复执行这个待核对操作。')
            flow.log(history[-1])
            flow.progress('出现尚未验证的结果，正在继续核对…')
            return
        inspections = []
        history.append('已执行并已核对：'+node['message']+'；'+node['expected'])
        flow.log(history[-1])
        step(node['message']+'，已核对。', node['expected'])

    while True:
        image = flow.observe_settled()
        image.save(directory / 'latest.png')
        if inspected_image is not None and scene_changed(inspected_image, image):
            inspections = []
        if plan_active and pending is None:
            node = None
            try:
                if local_plan['done'] and flow.condition_matches(local_plan['done'], image):
                    recipe['done'] = local_plan['done']
                    if recipe['nodes']:
                        persist(ready=True)
                        return '目标已完成并核对，技能已保存，可在任务列表再次运行。'
                    return '当前画面已满足目标，没有产生新的操作步骤。'
                node = find_node(flow, local_plan['nodes'], image)
                if node is not None:
                    if flow.condition_matches(node['after'], image, before=image):
                        raise ValueError('核对依据在操作前已经成立，未操作')
                else:
                    history.append('本地流程没有匹配当前画面的规则，请批量补充或修正完整流程。')
            except (ValueError, TypeError, KeyError) as error:
                node = None
                rejected_decision = dict(decision='local_plan', error=str(error))
                flow.log('本地流程需修正：'+str(error))
            if node is not None:
                reuse(node)
                if pending is None:
                    if node not in recipe['nodes']:
                        recipe['nodes'].append(node)
                    persist(ready=recipe['continuous'])
                continue
            # Keep the candidate visible for repair, but do not rerun a failed
            # plan and overwrite feedback about the model's next proposal.
            plan_active = False
            # Do not run older rules after a new plan has rejected the scene.
        known = find_node(flow, recipe['nodes'], image) if pending is None and local_plan is None else None
        if known is not None:
            reuse(known)
            if pending is None and recipe['continuous']:
                persist(ready=True)
            continue
        if decisions >= 40:
            raise ValueError('已探索40次仍有未掌握的画面，已停止；记录已保留。')
        flow.progress('正在查看画面，确定下一步…' if pending is None else '正在核对刚才的操作结果…')
        # New exploration only composes basic actions. Legacy saved tasks may
        # still use whole-task actions through the replay executor.
        custom_methods = custom_catalog(directory)
        context_inspections = []
        remaining = 64000
        for record in reversed(inspections):
            size = len(json.dumps(record, ensure_ascii=False))
            if size > remaining:
                record = {key: value for key, value in record.items() if key not in ('result', 'definition')}
                record['result_omitted'] = True
            remaining -= len(json.dumps(record, ensure_ascii=False))
            context_inspections.insert(0, record)
        context = dict(size=list(image.size), available_actions=list(INPUT), custom_methods=custom_methods,
                       capabilities=catalog(), input_capabilities=INPUT, inspections=context_inspections,
                       known_calls=list(vision_calls.definitions.values()),
                       rejected_decision=rejected_decision,
                       local_plan=local_plan,
                       pending=pending and {key: pending[key] for key in
                                            ('action', 'message', 'expected', 'program', 'parameters')},
                       previous_image=str(directory / 'pending-before.png') if pending else None,
                       history=history[-8:], diagnostics=list(flow.diagnostics))
        flow.diagnostics.clear()
        result = analyze_image(directory / 'latest.png', goal, flow.target.title, flow.stopped, context=context)
        flow.check_stop()
        decisions += 1
        flow.log(json.dumps(result, ensure_ascii=False))
        try:
            result = validate_decision(result, image.size, context['available_actions'], pending,
                                       [method['name'] for method in custom_methods])
        except (ValueError, TypeError, KeyError) as error:
            rejected_decision = dict(decision=result, error=str(error))
            flow.log('提议未执行，正在修正：'+str(error))
            continue
        rejected_decision = None
        if result['status'] == 'batch' or (result['status'] == 'plan' and arguments(result['arguments'])):
            inspected_image = image
            try:
                params = arguments(result['arguments'])
                items = params.get('items')
                if set(params) != {'items'} or not isinstance(items, list) or not 1 <= len(items) <= 32:
                    raise ValueError('batch需要1～32个items')
                for item in items:
                    flow.check_stop()
                    if not isinstance(item, dict):
                        raise ValueError('batch条目必须是对象')
                    if item.get('kind') == 'define' and set(item) == {'kind', 'method', 'arguments'}:
                        record = define_method(directory, item['method'], item['arguments'], flow.stopped)
                    elif item.get('kind') == 'inspect' and set(item) == {'kind', 'id', 'method', 'arguments'}:
                        record = vision_calls.call(item['method'], item['arguments'], image, identifier=item['id'])
                    else:
                        raise ValueError('batch条目需要kind/method/arguments；inspect还需要id')
                    inspections.append(record)
                    flow.log('通用能力：'+json.dumps(record, ensure_ascii=False))
            except (ValueError, TypeError, KeyError, RuntimeError, SyntaxError) as error:
                rejected_decision = dict(decision=result, error=str(error))
                flow.log('批量准备停止：'+str(error))
                continue
            if result['status'] == 'batch':
                continue
        if pending is not None and result['previous'] == 'succeeded':
            spec = arguments(result['program'])
            verification_spec = spec.get('verification')
            if verification_spec is None and result['status'] != 'plan':
                verification_spec = spec
            try:
                verification = {'program': compile_program(verification_spec, vision_calls, image,
                                                            f'verify-{decisions}')}
                if not flow.condition_matches(verification, image, before=pending_image):
                    raise ValueError('上一次已执行的“'+pending['message']+'”未达到声明的核对条件，未保存步骤；'
                                     'verification应核对上一次实际变化，不是本次新动作的预期结果')
                if not flow.condition_matches(verification, flow.observe(), before=pending_image):
                    flow.progress('核对期间状态已变化，正在重新观察…')
                    continue
                if flow.condition_matches(verification, pending_image, before=pending_image):
                    raise ValueError('完成依据在操作前就已存在，不能确认操作有效，未保存步骤')
            except (ValueError, TypeError, KeyError) as error:
                rejected_decision = dict(decision=result, error=str(error))
                flow.log('核对提议未通过，正在修正：'+str(error))
                continue
            pending['after'] = verification
            recipe['nodes'].append(pending)
            history.append(pending['message']+'：已核对结果')
            step(pending['message']+'，已核对。', result['detail'])
            flow.log('已核对：'+pending['message']+'；'+result['detail'])
            pending = None
            waits = 0
            persist()
        if result['status'] == 'plan':
            try:
                spec = arguments(result['program'])
                spec.pop('verification', None)
                if (set(spec) != {'nodes', 'done'} or not isinstance(spec['nodes'], list)
                        or not 1 <= len(spec['nodes']) <= 16):
                    raise ValueError('plan需要1～16个nodes和done')
                nodes = []
                for index, node in enumerate(spec['nodes']):
                    if (not isinstance(node, dict) or set(node) != {'action', 'message', 'expected', 'parameters', 'program', 'after'}
                            or node['action'] not in INPUT or not isinstance(node['parameters'], dict)
                            or not all(isinstance(node[key], str) and node[key].strip() for key in ('message', 'expected'))):
                        raise ValueError('plan节点的动作或字段无效')
                    program = compile_program(node['program'], vision_calls, image, f'plan-{decisions}-{index}',
                                              action=True, parameters=node['parameters'])
                    after = compile_program(node['after'], vision_calls, image, f'plan-{decisions}-{index}-after')
                    nodes.append({**node, 'program': program, 'after': {'program': after}})
                done = ({'program': compile_program(spec['done'], vision_calls, image, f'plan-{decisions}-done')}
                        if spec['done'] is not None else None)
                if result['continuous'] and done is not None:
                    raise ValueError('持续任务不能有终止done条件')
                if recipe['name'] and recipe['continuous'] != result['continuous']:
                    raise ValueError('是否持续运行的判断不一致')
                recipe['name'] = result['name'].strip()[:40]
                recipe['continuous'] = result['continuous']
                local_plan = dict(nodes=nodes, done=done)
                plan_active = True
                flow.log(f'已编译本地流程：{len(nodes)}个规则，开始连续执行和核对。')
            except (ValueError, TypeError, KeyError) as error:
                rejected_decision = dict(decision=result, error=str(error))
                flow.log('流程提议未执行：'+str(error))
            continue
        if result['status'] in ('inspect', 'define'):
            flow.progress(result['message'])
            try:
                if result['status'] == 'define':
                    record = define_method(directory, result['method'], arguments(result['arguments']), flow.stopped)
                else:
                    record = vision_calls.call(result['method'], arguments(result['arguments']), image)
                    record['purpose'] = result['message']
            except (ValueError, TypeError, KeyError, RuntimeError, SyntaxError) as error:
                record = dict(method=result['method'], error=str(error))
                if result['status'] == 'define':
                    record['definition'] = arguments(result['arguments'])
            flow.check_stop()
            inspected_image = image
            # Full outputs remain on disk; keep the model context bounded.
            if result['status'] == 'inspect' and len(json.dumps(record, ensure_ascii=False)) > 16000:
                record = dict(id=record.get('id'), method=result['method'],
                              error='结果过大，完整记录已保存；请缩小ROI或max_count后再识别。')
            inspections.append(record)
            flow.log('通用能力：'+json.dumps(record, ensure_ascii=False))
            continue
        if result['status'] in ('wait', 'blocked') and scene_changed(image, flow.observe()):
            flow.progress('画面已更新，正在重新核对…')
            continue
        if not recipe['name']:
            recipe['name'] = result['name'].strip()[:40]
            recipe['continuous'] = result['continuous']
        elif recipe['continuous'] != result['continuous']:
            raise ValueError('AI 对是否持续运行的判断不一致，已停止。')
        condition = None
        try:
            if result['status'] in ('act', 'done'):
                spec = arguments(result['program'])
                verification_spec = spec.pop('verification', None)
                if verification_spec is not None and result['previous'] != 'succeeded':
                    raise ValueError('只有核对上一步成功时才能提供verification')
                program = compile_program(spec, vision_calls, image, f'node-{decisions}',
                                          action=result['status'] == 'act', parameters=arguments(result['arguments']))
                condition = {'program': program}
                if result['status'] == 'act':
                    matched = flow.resolve_node(dict(action=result['action'], program=program,
                                                     parameters=arguments(result['arguments'])), image) is not None
                else:
                    matched = flow.condition_matches(condition, image)
                if not matched:
                    raise ValueError('当前画面不满足声明的识别条件，未操作或保存技能')
                if not flow.condition_matches(condition, flow.observe()):
                    history.append('未执行“'+result['message']+'”：识别条件在新截图复核时未通过；'
                                   '请重新检查识别结果并调整方案，不能把这次提议当成已执行的操作。')
                    flow.log(history[-1])
                    flow.progress('识别区域已更新，正在重新核对…')
                    continue
        except (ValueError, TypeError, KeyError) as error:
            rejected_decision = dict(decision=result, error=str(error))
            flow.log('提议未执行，正在修正：'+str(error))
            continue
        if result['status'] == 'blocked':
            raise ValueError(result['message']+'\n'+result['detail'])
        if result['status'] == 'done':
            recipe['done'] = condition
            flow.check_stop()
            if recipe['nodes']:
                persist(ready=True)
                return result['message']+'\n'+result['detail']+'\n技能已保存，可在任务列表再次运行。'
            return result['message']+'\n'+result['detail']+'\n当前画面已满足目标，没有产生新的操作步骤。'
        if result['status'] == 'wait':
            waits += 1
            if waits > 3:
                raise ValueError('连续观察仍未看到预期变化，已停止，未重复点击。')
            flow.progress(result['message'])
            flow.wait(1)
            continue
        waits = 0
        proposed = dict(action=result['action'], message=result['message'], expected=result['expected'],
                        program=program, parameters=arguments(result['arguments']))
        known = find_node(flow, recipe['nodes'], flow.observe(), requested=proposed)
        if known is not None:
            reuse(known)
            continue
        try:
            pending_image = flow.execute(proposed)
        except NoMatch as error:
            rejected_decision = dict(decision=result, error=str(error))
            flow.log('输入前复核未通过，重新观察：'+str(error))
            continue
        pending = proposed
        pending_image.save(directory / 'pending-before.png')
        inspections = []
