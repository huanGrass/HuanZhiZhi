"""Skill-local Python rules over data, evaluated in a cancellable subprocess."""
import ast
import hashlib
import json
from pathlib import Path
import re
from types import SimpleNamespace

from huanzhizhi.python_runtime import run_code


_NODES = {
    'Module', 'FunctionDef', 'arguments', 'arg', 'Return', 'Assign', 'AugAssign',
    'Expr', 'For', 'While', 'If', 'IfExp', 'Break', 'Continue', 'Pass', 'Raise',
    'Try', 'ExceptHandler', 'Assert', 'Name', 'Constant', 'List', 'Tuple', 'Dict',
    'Set', 'Subscript', 'Slice', 'Attribute', 'Call', 'keyword', 'BinOp', 'UnaryOp',
    'BoolOp', 'Compare', 'ListComp', 'SetComp', 'DictComp', 'GeneratorExp',
    'comprehension', 'Lambda', 'JoinedStr', 'FormattedValue', 'Import', 'ImportFrom',
    'alias', 'Starred', 'Load', 'Store', 'Add', 'Sub', 'Mult', 'Div', 'FloorDiv', 'Mod', 'Pow',
    'BitAnd', 'BitOr', 'BitXor', 'LShift', 'RShift', 'Invert', 'Not', 'UAdd', 'USub',
    'And', 'Or', 'Eq', 'NotEq', 'Lt', 'LtE', 'Gt', 'GtE', 'Is', 'IsNot', 'In', 'NotIn',
}
_ATTRIBUTES = set('append extend insert pop remove clear index count sort reverse copy '
                  'get keys values items setdefault update add discard union intersection '
                  'difference symmetric_difference issubset issuperset isdisjoint '
                  'join split rsplit strip lstrip rstrip replace startswith endswith '
                  'lower upper casefold isdigit isdecimal isalpha isalnum find rfind '
                  'bit_count bit_length most_common elements appendleft popleft rotate'.split())
_IMPORTS = {
    'math': 'ceil floor sqrt isqrt hypot fabs gcd lcm prod inf pi e isfinite log log2 exp sin cos tan',
    'collections': 'Counter defaultdict deque',
    'itertools': 'combinations permutations product chain accumulate',
}


def validate_source(source):
    if not isinstance(source, str) or not 1 <= len(source) <= 20000:
        raise ValueError('自定义方法源码需为1～20000个字符')
    tree = ast.parse(source)
    run = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'run']
    if (len(run) != 1 or len(run[0].args.args) != 1 or run[0].args.args[0].arg != 'data'
            or run[0].args.posonlyargs or run[0].args.kwonlyargs or run[0].args.vararg
            or run[0].args.kwarg or run[0].args.defaults):
        raise ValueError('自定义方法必须定义 run(data)，接收当前识别数据并返回JSON数据')
    attributes = _ATTRIBUTES | {name for names in _IMPORTS.values() for name in names.split()}
    for node in ast.walk(tree):
        if type(node).__name__ not in _NODES:
            raise ValueError('自定义方法不支持此语法：'+type(node).__name__)
        if isinstance(node, ast.Name) and node.id.startswith('__'):
            raise ValueError('自定义方法不能访问内部名称')
        if isinstance(node, ast.Attribute) and node.attr not in attributes:
            raise ValueError('自定义方法不支持属性：'+node.attr)
        if isinstance(node, ast.FunctionDef) and (node.decorator_list or node.returns):
            raise ValueError('自定义方法不支持装饰器或类型注解')
        if isinstance(node, ast.arg) and node.annotation:
            raise ValueError('自定义方法不支持类型注解')
        if isinstance(node, ast.Import) and any(alias.name not in _IMPORTS for alias in node.names):
            raise ValueError('自定义方法只支持 math、collections、itertools')
        if isinstance(node, ast.ImportFrom):
            if (node.level or node.module not in _IMPORTS
                    or any(alias.name not in _IMPORTS[node.module].split() for alias in node.names)):
                raise ValueError('自定义方法导入了未提供的计算函数')
    return tree


def _worker(source, cases):
    """Only the host wrapper has IO; generated code receives data and compute helpers."""
    import builtins
    import importlib
    from huanzhizhi.capabilities import _validate_json

    tree = validate_source(source)
    modules = {}
    for module, names in _IMPORTS.items():
        original = importlib.import_module(module)
        modules[module] = SimpleNamespace(**{name: getattr(original, name) for name in names.split()})

    def import_compute(name, globals=None, locals=None, fromlist=(), level=0):
        if level or name not in modules:
            raise ValueError('不允许导入此模块')
        return modules[name]

    names = ('bool int float str list tuple dict set frozenset len range enumerate zip sorted '
             'min max sum any all abs round divmod pow reversed iter next map filter isinstance '
             'Exception ValueError TypeError IndexError KeyError AssertionError StopIteration')
    results = []
    for data in cases:
        scope = {'__builtins__': {name: getattr(builtins, name) for name in names.split()}}
        scope['__builtins__']['__import__'] = import_compute
        try:
            exec(compile(tree, '<skill-method>', 'exec'), scope)
            value = scope['run'](data)
            _validate_json(value)
            results.append({'value': value})
        except Exception as error:
            results.append({'error': str(error), 'type': type(error).__name__})
    output = json.dumps(results, ensure_ascii=False, allow_nan=False)
    if len(output) > 1000000:
        raise ValueError('自定义方法结果过大，请只返回所需数据')
    print(output)


def _evaluate(source, cases, directory, stopped):
    validate_source(source)
    payload = json.dumps(cases, ensure_ascii=False, allow_nan=False)
    if len(payload) > 4000000:
        raise ValueError('自定义方法输入过大，请先缩小识别区域')
    code = ('import json\nfrom huanzhizhi.skill_methods import _worker\n'
            f'_worker({source!r}, json.loads({payload!r}))\n')
    result = run_code(code, runs_dir=directory / '.runtime' / 'runs' / 'method-runs', timeout=10,
                      stop_event=SimpleNamespace(is_set=stopped))
    if result.stopped:
        raise InterruptedError('自定义方法已停止')
    if result.timed_out:
        raise RuntimeError('自定义方法计算超过10秒，已终止；请改进算法')
    if result.returncode:
        raise ValueError('自定义方法执行失败：'+result.stderr[-1600:])
    return json.loads(result.stdout)


def _path(directory, name):
    if not isinstance(name, str) or not re.fullmatch(r'custom_[a-z][a-z0-9_]{0,47}', name):
        raise ValueError('自定义方法名必须是 custom_ 开头的小写字母、数字和下划线')
    return Path(directory) / (name+'.json')


def load_method(directory, name):
    path = _path(directory, name)
    definition = json.loads(path.read_text(encoding='utf-8'))
    source = path.with_suffix('.py').read_text(encoding='utf-8')
    if hashlib.sha256(source.encode()).hexdigest() != definition['sha256']:
        raise ValueError('自定义方法源码与已验证版本不一致：'+name)
    return {**definition, 'source': source}


def custom_catalog(directory):
    return [load_method(directory, path.stem) for path in sorted(Path(directory).glob('custom_*.json'))]


def define_method(directory, name, spec, stopped=lambda: False):
    path = _path(directory, name)
    if path.exists() or path.with_suffix('.py').exists():
        raise ValueError('方法名已存在；修改规则请使用新的方法名，不能改写已验证步骤')
    if not isinstance(spec, dict) or set(spec) != {'description', 'source', 'tests'}:
        raise ValueError('定义方法需要 description、source、tests')
    if not isinstance(spec['description'], str) or not 1 <= len(spec['description']) <= 1000:
        raise ValueError('请说明自定义方法的用途及输入输出')
    tests = spec['tests']
    if (not isinstance(tests, list) or not 1 <= len(tests) <= 8
            or any(not isinstance(case, dict) or set(case) not in ({'data', 'expected'}, {'data', 'error'}) for case in tests)):
        raise ValueError('提供1～8个测试：data及expected，或data及预期错误error')
    results = _evaluate(spec['source'], [case['data'] for case in tests], Path(directory), stopped)
    failures = []
    for index, (case, result) in enumerate(zip(tests, results), 1):
        passed = (result.get('value') == case['expected'] and 'value' in result if 'expected' in case
                  else isinstance(case['error'], str) and bool(case['error']) and case['error'] in result.get('error', ''))
        if not passed:
            expected = {'expected': case['expected']} if 'expected' in case else {'error': case['error']}
            failures.append(f'自定义方法测试{index}未通过：'
                            f'输入={json.dumps(case["data"], ensure_ascii=False)[:1000]}；'
                            f'预期={json.dumps(expected, ensure_ascii=False)[:1000]}；'
                            f'实际={json.dumps(result, ensure_ascii=False)[:2000]}')
    if failures:
        raise ValueError('\n'.join(failures))
    if stopped():
        raise InterruptedError('自定义方法已停止')
    definition = dict(name=name, description=spec['description'], parameters='data', tests=tests,
                      sha256=hashlib.sha256(spec['source'].encode()).hexdigest())
    path.with_suffix('.py').write_text(spec['source'], encoding='utf-8')
    path.write_text(json.dumps(definition, ensure_ascii=False, indent=2), encoding='utf-8')
    return dict(method=name, tests_passed=len(tests))


def call_method(directory, name, data, stopped=lambda: False):
    definition = load_method(directory, name)
    result = _evaluate(definition['source'], [data], Path(directory), stopped)[0]
    if 'error' in result:
        from huanzhizhi.capabilities import NoMatch
        raise NoMatch(f'{name}：{result["type"]}: {result["error"]}')
    return result['value']
