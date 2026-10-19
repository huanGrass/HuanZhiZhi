"""Supported shared components and their read-only, isolated environment checks."""
import ctypes
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
from queue import Empty, Queue
import re
import subprocess
import tempfile
import threading
import time

from huanzhizhi.python_runtime import python_executable


CATALOG_PATH = Path(__file__).with_suffix('.json')


def supported_components():
    return json.loads(CATALOG_PATH.read_text(encoding='utf-8'))


def katago_paths(component, root):
    """Resolve the catalog's files; only the child process receives this PATH."""
    dependencies = Path(root) / 'dependencies'
    engine = dependencies / component['engine']
    model = dependencies / component['model']
    cudnn = dependencies / component['cudnn']
    candidates = [dependencies / component['cuda']]
    candidates.extend(Path(os.environ[key]) / 'bin' for key in ('CUDA_PATH_V12_8', 'CUDA_PATH')
                      if os.environ.get(key))
    candidates.append(Path(os.environ.get('ProgramFiles', 'C:/Program Files')) /
                      'NVIDIA GPU Computing Toolkit/CUDA/v12.8/bin')
    candidates.extend(Path(path) for path in os.environ.get('PATH', '').split(os.pathsep) if path)
    cuda = next((path for path in candidates if (path / 'cudart64_12.dll').is_file()), None)
    directories = [engine.parent, cudnn] + ([cuda] if cuda else [])
    environment = os.environ.copy()
    environment['PATH'] = os.pathsep.join([*(str(path) for path in directories), environment.get('PATH', '')])
    return engine, model, directories, environment


def _load_dlls(directory, names):
    libraries = []
    for name in names:
        path = directory / name
        if not path.is_file():
            raise FileNotFoundError(f'缺少 {path}')
        libraries.append(ctypes.WinDLL(str(path)))
    return libraries


def _gpu():
    driver = ctypes.WinDLL('nvcuda.dll')
    if driver.cuInit(0) != 0:
        raise RuntimeError('NVIDIA 驱动初始化失败')
    count, version = ctypes.c_int(), ctypes.c_int()
    if driver.cuDeviceGetCount(ctypes.byref(count)) != 0 or count.value < 1:
        raise RuntimeError('没有可用的 CUDA GPU')
    if driver.cuDriverGetVersion(ctypes.byref(version)) != 0 or version.value < 12080:
        raise RuntimeError('驱动不支持 CUDA 12.8，请更新 NVIDIA 驱动')
    name = ctypes.create_string_buffer(256)
    if driver.cuDeviceGetName(name, len(name), 0) != 0:
        raise RuntimeError('无法读取 GPU 名称')
    summary = name.value.decode('utf-8', errors='replace')
    return summary, f'{summary}；驱动支持 CUDA {version.value // 1000}.{version.value % 1000 // 10}'


def _analyze(engine, model, environment):
    # Configuration and engine logs never touch the shared component directory.
    with tempfile.TemporaryDirectory(prefix='huanzhizhi-katago-') as temporary:
        directory = Path(temporary)
        config = directory / 'analysis.cfg'
        config.write_text('logToStderr = true\nmaxVisits = 8\nnumAnalysisThreads = 1\n'
                          'numSearchThreadsPerAnalysisThread = 2\nnnMaxBatchSize = 4\n'
                          'nnCacheSizePowerOfTwo = 16\nnnMutexPoolSizePowerOfTwo = 14\n'
                          'cudaDeviceToUse = 0\n', encoding='utf-8')
        request = {'id': 'environment-check', 'moves': [], 'rules': 'chinese', 'komi': 7.5,
                   'boardXSize': 19, 'boardYSize': 19, 'maxVisits': 8}
        result = subprocess.run([str(engine), 'analysis', '-config', str(config), '-model', str(model)],
                                input=json.dumps(request) + '\n', capture_output=True, text=True,
                                encoding='utf-8', errors='replace', cwd=directory, env=environment,
                                timeout=25, creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode:
            raise RuntimeError(f'分析进程退出 {result.returncode}：{result.stderr[-1500:]}')
        responses = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
        if len(responses) != 1 or responses[0].get('id') != request['id']:
            raise RuntimeError('未收到对应的分析结果')
        response = responses[0]
        if (response.get('error') or response.get('isDuringSearch') or
                response.get('rootInfo', {}).get('visits', 0) < 8 or not response.get('moveInfos')):
            raise RuntimeError(f'分析结果无效：{str(response)[:1000]}')
        return '19×19 分析通过', f'引擎与模型已完成 19×19 分析，搜索访问 {response["rootInfo"]["visits"]} 次。'


def check_katago(component, root):
    engine, model, directories, environment = katago_paths(component, root)
    checks = {}
    with ExitStack() as stack:
        for directory in directories:
            if directory.is_dir():
                stack.enter_context(os.add_dll_directory(str(directory)))

        def engine_check():
            if not engine.is_file():
                raise FileNotFoundError(f'缺少 {engine}')
            result = subprocess.run([str(engine), 'version'], capture_output=True, text=True,
                                    encoding='utf-8', errors='replace', env=environment,
                                    timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
            if (result.returncode or 'KataGo v1.18.2' not in result.stdout or
                    'Using CUDA backend' not in result.stdout):
                raise RuntimeError(f'需要 KataGo 1.18.2 CUDA 版：{(result.stdout + result.stderr)[-1000:]}')
            return 'KataGo 1.18.2 · 已启动', f'KataGo 1.18.2 CUDA 版已启动：{engine}'

        def model_check():
            if not model.is_file() or model.stat().st_size == 0:
                raise FileNotFoundError(f'缺少模型或文件为空：{model}')
            return '模型文件已找到', f'文件已找到：{model}；完整性由实际分析验证。'

        def cuda_check():
            if len(directories) < 3:
                raise FileNotFoundError('未找到 CUDA 12 运行库，请按清单来源安装 CUDA 12.8。')
            runtime, *_ = _load_dlls(directories[2], ['cudart64_12.dll', 'cublasLt64_12.dll', 'cublas64_12.dll'])
            version = ctypes.c_int()
            if runtime.cudaRuntimeGetVersion(ctypes.byref(version)) != 0 or not 12080 <= version.value < 13000:
                raise RuntimeError(f'需要 CUDA 12.8 或兼容的 CUDA 12 运行库，检测值 {version.value}')
            summary = f'CUDA {version.value // 1000}.{version.value % 1000 // 10} · 已加载'
            return summary, f'{summary}：{directories[2]}'

        def cudnn_check():
            # Keep all handles alive while querying the API.
            libraries = _load_dlls(directories[1], ['cudnn_graph64_9.dll', 'cudnn_ops64_9.dll',
                'cudnn_adv64_9.dll', 'cudnn_cnn64_9.dll', 'cudnn_heuristic64_9.dll',
                'cudnn_engines_precompiled64_9.dll', 'cudnn_engines_runtime_compiled64_9.dll', 'cudnn64_9.dll'])
            libraries[-1].cudnnGetVersion.restype = ctypes.c_size_t
            version = libraries[-1].cudnnGetVersion()
            if not 90800 <= version < 100000:
                raise RuntimeError(f'需要 cuDNN 9.8 或兼容的 cuDNN 9，检测值 {version}')
            summary = f'cuDNN {version // 10000}.{version % 10000 // 100}.{version % 100} · 已加载'
            return summary, f'{summary}，8 个 DLL：{directories[1]}'

        def msvc_check():
            _load_dlls(engine.parent, ['vcruntime140.dll', 'vcruntime140_1.dll', 'msvcp140.dll'])
            return 'x64 运行库已加载', '引擎配套的 x64 Visual C++ 运行库已加载。'

        def analysis_check():
            missing = [name for name, satisfied in checks.items() if not satisfied]
            if missing:
                raise RuntimeError('前置条件不满足，未运行分析：' + '、'.join(missing))
            return _analyze(engine, model, environment)

        callbacks = dict(engine=engine_check, model=model_check, gpu=_gpu, cuda=cuda_check,
                         cudnn=cudnn_check, msvc=msvc_check, analysis=analysis_check)
        for requirement in component['requirements']:
            try:
                summary, detail = callbacks[requirement['id']]()
                satisfied = True
            except Exception as exc:
                detail, satisfied = str(exc), False
                summary = (f'未找到{requirement["name"]}' if isinstance(exc, FileNotFoundError) else
                           '检测超时' if isinstance(exc, subprocess.TimeoutExpired) else
                           '前置依赖未满足' if requirement['id'] == 'analysis' and not all(checks.values()) else
                           '检测未通过，详见报告')
            checks[requirement['name']] = satisfied
            yield {'name': component['id'] + '.' + requirement['id'], 'satisfied': satisfied,
                   'summary': summary, 'detail': detail}


def _analyze_pikafish(engine, model):
    with tempfile.TemporaryDirectory(prefix='huanzhizhi-pikafish-') as temporary, tempfile.TemporaryFile() as errors:
        process = subprocess.Popen([str(engine)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=errors, text=True, encoding='utf-8', errors='replace',
                                   cwd=temporary, creationflags=subprocess.CREATE_NO_WINDOW)
        responses = Queue()

        def read_output():
            try:
                for line in process.stdout:
                    responses.put(line.strip())
            finally:
                responses.put(None)

        reader = threading.Thread(target=read_output, daemon=True)
        reader.start()
        deadline = time.monotonic() + 20
        transcript = []

        def send(command):
            process.stdin.write(command + '\n')
            process.stdin.flush()

        def receive_until(predicate):
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(str(engine), 20)
                try:
                    line = responses.get(timeout=remaining)
                except Empty:
                    raise subprocess.TimeoutExpired(str(engine), 20) from None
                if line is None:
                    errors.seek(0)
                    detail = errors.read().decode('utf-8', errors='replace')[-1000:]
                    raise RuntimeError('皮卡鱼分析进程提前退出：' + '\n'.join(transcript[-10:]) + detail)
                transcript.append(line)
                if predicate(line):
                    return line

        try:
            send('uci')
            receive_until(lambda line: line == 'uciok')
            send('setoption name Threads value 1')
            send('setoption name Hash value 16')
            send(f'setoption name EvalFile value {model}')
            send('isready')
            receive_until(lambda line: line == 'readyok')
            send('position startpos')
            send('go depth 4')
            best = receive_until(lambda line: line.startswith('bestmove ')).split()
            searched = any(re.search(r'^info depth ([4-9]|[1-9][0-9]+) .*\bnodes [1-9][0-9]*\b', line)
                           for line in transcript)
            if (len(best) < 2 or not re.fullmatch(r'[a-i][0-9][a-i][0-9]', best[1])
                    or best[1][:2] == best[1][2:] or not searched
                    or not any('NNUE evaluation using' in line for line in transcript)):
                raise RuntimeError('皮卡鱼未返回完整的 NNUE 搜索结果：' + '\n'.join(transcript[-10:]))
            send('quit')
            process.wait(timeout=2)
            if process.returncode:
                raise RuntimeError(f'皮卡鱼分析进程退出 {process.returncode}')
            return '象棋开局分析通过', f'已加载指定 NNUE 模型并完成深度 4 搜索，建议走法 {best[1]}。'
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()
            reader.join(timeout=2)
            process.stdin.close()
            process.stdout.close()


def check_pikafish(component, root):
    dependencies = Path(root).resolve() / 'dependencies'
    engine, model = dependencies / component['engine'], dependencies / component['model']
    checks = {}

    def engine_check():
        if not engine.is_file():
            raise FileNotFoundError(f'缺少 {engine}')
        with tempfile.TemporaryDirectory(prefix='huanzhizhi-pikafish-') as temporary:
            result = subprocess.run([str(engine)], input='uci\nquit\n', capture_output=True, text=True,
                                    encoding='utf-8', errors='replace', cwd=temporary,
                                    timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
        if (result.returncode or f'id name Pikafish {component["version"]}' not in result.stdout.splitlines()
                or 'uciok' not in result.stdout.splitlines()):
            raise RuntimeError(f'需要 Pikafish {component["version"]} Windows x64 版：'
                               + (result.stdout + result.stderr)[-1000:])
        return f'Pikafish {component["version"]} · 已启动', f'已完成 UCI 握手：{engine}'

    def model_check():
        if not model.is_file():
            raise FileNotFoundError(f'缺少 NNUE 模型：{model}')
        with model.open('rb') as source:
            if hashlib.file_digest(source, 'sha256').hexdigest() != component['model_sha256']:
                raise ValueError(f'NNUE 模型校验失败，请重新安装：{model}')
        return 'NNUE 模型校验通过', f'已校验配套模型 SHA-256：{model}；加载能力由实际分析验证。'

    def analysis_check():
        if not all(checks.values()):
            raise RuntimeError('前置条件不满足，未运行分析：' + '、'.join(name for name in checks if not checks[name]))
        return _analyze_pikafish(engine, model)

    callbacks = dict(engine=engine_check, model=model_check, analysis=analysis_check)
    for requirement in component['requirements']:
        try:
            summary, detail = callbacks[requirement['id']]()
            satisfied = True
        except Exception as exc:
            detail, satisfied = str(exc), False
            summary = (f'未找到{requirement["name"]}' if isinstance(exc, FileNotFoundError) else
                       '检测超时' if isinstance(exc, subprocess.TimeoutExpired) else
                       '前置依赖未满足' if requirement['id'] == 'analysis' and not all(checks.values()) else
                       '检测未通过，详见报告')
        checks[requirement['name']] = satisfied
        yield dict(name=component['id'] + '.' + requirement['id'], satisfied=satisfied,
                   summary=summary, detail=detail)


def main():
    root = python_executable().parent.parent
    for component in supported_components():
        checker = {'katago': check_katago, 'pikafish': check_pikafish}.get(component['id'])
        if checker is None:
            raise ValueError(f'组件尚未实现检测：{component["id"]}')
        for row in checker(component, root):
            print(json.dumps(row, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
