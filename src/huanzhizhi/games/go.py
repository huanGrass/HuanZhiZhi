"""Go board transitions and the shared KataGo analysis client.

Board values are 0 empty, 1 black, 2 white, indexed from the top-left.
Local transitions check captures and suicide, not ko/superko or scoring.
Pass actual move history and rules to KataGo for those game-level rules.
"""
from __future__ import annotations

import json
import math
from contextlib import contextmanager
from pathlib import Path
from queue import Empty, Queue
import subprocess
import tempfile
import threading
import time

from huanzhizhi.python_runtime import python_executable
from huanzhizhi.shared_components import check_katago, katago_paths, supported_components

LETTERS = 'ABCDEFGHJKLMNOPQRST'


def component():
    return next(item for item in supported_components() if item['id'] == 'katago')


def check_engine():
    rows = list(check_katago(component(), python_executable().parent.parent))
    failed = [row for row in rows if not row['satisfied']]
    if failed:
        detail = '；'.join(f"{row['name']}: {row['detail']}" for row in failed)
        raise RuntimeError(f'{detail}。请到“设置 → 外部组件”检测或安装 KataGo 1.18.2 CUDA 版、b11c768 模型及 CUDA 12.8/cuDNN 9.10.2；官方来源 https://github.com/lightvector/KataGo/releases/tag/v1.18.2 和 https://katagotraining.org/networks/；文件按 dependencies/README.md 放置。')
    return 'KataGo 1.18.2 CUDA 版与模型已完成 19×19 实际分析。'


def board_size(board):
    """Validate the shared square-board format (2..19, values 0/1/2)."""
    size = len(board)
    if not 2 <= size <= 19 or any(len(row) != size for row in board):
        raise ValueError('围棋棋盘必须是 2～19 路方阵')
    if any(type(value) is not int or value not in (0, 1, 2) for row in board for value in row):
        raise ValueError('棋盘只允许整数 0（空）、1（黑）、2（白）')
    return size


def coordinate(x, y, size=19):
    """Convert top-left based (column, row) to a GTP vertex, skipping I."""
    if type(size) is not int or not 2 <= size <= 19:
        raise ValueError('棋盘尺寸必须是 2～19')
    if type(x) is not int or type(y) is not int or not 0 <= x < size or not 0 <= y < size:
        raise ValueError('落点超出棋盘范围')
    return f'{LETTERS[x]}{size-y}'


def parse_coordinate(move, size=19):
    """Convert a GTP vertex to (column, row); pass must be handled by caller."""
    if not isinstance(move, str) or len(move) < 2 or move[0] not in LETTERS or not move[1:].isascii() or not move[1:].isdigit():
        raise ValueError(f'落点格式无效：{move}')
    x, y = LETTERS.index(move[0]), size - int(move[1:])
    coordinate(x, y, size)
    return x, y


def neighbors(x, y, size):
    for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)):
        if 0 <= nx < size and 0 <= ny < size:
            yield nx, ny


def group(board, x, y):
    size = board_size(board)
    coordinate(x, y, size)
    color = board[y][x]
    if color == 0:
        raise ValueError('空交叉点没有棋串')
    stones, liberties = {(x, y)}, set()
    pending = [(x, y)]
    while pending:
        px, py = pending.pop()
        for nx, ny in neighbors(px, py, size):
            if board[ny][nx] == 0:
                liberties.add((nx, ny))
            elif board[ny][nx] == color and (nx, ny) not in stones:
                stones.add((nx, ny))
                pending.append((nx, ny))
    return stones, liberties


def move_result(board, x, y, color):
    """Return the board after captures, rejecting occupied points and suicide.

    This checks a single transition only. Ko/superko and scoring require the
    actual history and selected rules in KataGo; they cannot be inferred here.
    """
    size = board_size(board)
    coordinate(x, y, size)
    if type(color) is not int or color not in (1, 2):
        raise ValueError('落子颜色必须为 1（黑）或 2（白）')
    if board[y][x]:
        raise ValueError('落点已被占用')
    result = [row.copy() for row in board]
    result[y][x] = color
    checked = set()
    for nx, ny in neighbors(x, y, size):
        if result[ny][nx] == 3-color and (nx, ny) not in checked:
            stones, liberties = group(result, nx, ny)
            checked.update(stones)
            if not liberties:
                for sx, sy in stones:
                    result[sy][sx] = 0
    if not group(result, x, y)[1]:
        raise ValueError('落子无气')
    return result


def single_new_stone(before, after, color):
    size = board_size(before)
    if board_size(after) != size:
        raise ValueError('前后棋盘尺寸不一致')
    if type(color) is not int or color not in (1, 2):
        raise ValueError('落子颜色必须为 1（黑）或 2（白）')
    additions = [(x, y) for y in range(size) for x in range(size)
                 if before[y][x] != color and after[y][x] == color]
    if len(additions) != 1:
        raise ValueError(f'棋盘变化无法确认：预期一枚新{("黑", "白")[color-1]}棋，实际 {len(additions)} 枚')
    x, y = additions[0]
    if move_result(before, x, y, color) != after:
        raise ValueError('棋盘变化不是合法单次落子或提子，已停止')
    return x, y


@contextmanager
def start_engine():
    """Keep one analysis process and model alive for this skill run."""
    engine, model, _, environment = katago_paths(component(), python_executable().parent.parent)
    with tempfile.TemporaryDirectory(prefix='huanzhizhi-go-') as temporary:
        config = Path(temporary) / 'analysis.cfg'
        config.write_text('logToStderr = true\nmaxVisits = 80\nnumAnalysisThreads = 1\n'
                          'numSearchThreadsPerAnalysisThread = 2\nnnMaxBatchSize = 4\n'
                          'nnCacheSizePowerOfTwo = 16\nnnMutexPoolSizePowerOfTwo = 14\n'
                          'cudaDeviceToUse = 0\n', encoding='utf-8')
        error_path = Path(temporary) / 'stderr.log'
        with error_path.open('wb') as errors:
            process = subprocess.Popen(
                [str(engine), 'analysis', '-config', str(config), '-model', str(model),
                 '-quit-without-waiting'], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=errors, text=True, encoding='utf-8', errors='replace', cwd=temporary,
                env=environment, creationflags=subprocess.CREATE_NO_WINDOW)
            responses = Queue()
            requests = Queue()

            def write_requests():
                try:
                    while True:
                        request = requests.get()
                        if request is None:
                            break
                        process.stdin.write(request)
                        process.stdin.flush()
                except OSError as error:
                    responses.put(error)
                finally:
                    try:
                        process.stdin.close()
                    except OSError:
                        pass  # The engine may already have exited on an error.

            def read_responses():
                try:
                    for line in process.stdout:
                        if line.strip():
                            responses.put(line)
                finally:
                    responses.put(None)

            reader = threading.Thread(target=read_responses, daemon=True)
            writer = threading.Thread(target=write_requests, daemon=True)
            try:
                reader.start()
                writer.start()
                yield process, requests, responses, error_path
            finally:
                requests.put(None)
                try:
                    process.wait(timeout=.3)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    try:
                        process.wait(timeout=.3)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=.3)
                if reader.ident is not None:
                    reader.join(timeout=.2)
                if writer.ident is not None:
                    writer.join(timeout=.2)
                process.stdin.close()
                process.stdout.close()


def analyze(session, engine, initial, moves, *, initial_player='B', rules='chinese', komi=7.5):
    """Analyze the final position of an observed record using one live engine.

    initial_player is the side to move BEFORE moves; with no history it is the
    current side. Do not invent history from stone order in a screenshot.
    Requests are sequential. On timeout/error/stop, leave start_engine() and
    close the process before a new analysis run.
    """
    process, requests, responses, error_path = engine
    if session.stop_requested:
        raise InterruptedError('用户已停止')
    size = board_size(initial)
    if initial_player not in ('B', 'W'):
        raise ValueError('初始行棋方必须为 B 或 W')
    if not isinstance(rules, str) or not rules.strip():
        raise ValueError('必须指定 KataGo 规则名称')
    if type(komi) not in (int, float) or not math.isfinite(komi) or not -400 <= komi <= 400 or komi * 2 != int(komi * 2):
        raise ValueError('贴目必须是 -400～400 内的整数或半整数')
    player = initial_player
    for move in moves:
        if not isinstance(move, (list, tuple)) or len(move) != 2 or move[0] != player:
            raise ValueError('棋谱必须按已确认的行棋方交替记录')
        if move[1] != 'pass':
            parse_coordinate(move[1], size)
        player = 'W' if player == 'B' else 'B'
    max_visits = session.options.get('max_visits', '80')
    if max_visits not in ('80', '160', '320'):
        raise ValueError('搜索次数必须选择预设档位')
    stones = [[('B' if color == 1 else 'W'), coordinate(x, y, size)]
              for y, row in enumerate(initial) for x, color in enumerate(row) if color]
    request = {'id': str(time.monotonic_ns()), 'initialStones': stones, 'moves': moves,
               'initialPlayer': initial_player, 'rules': rules, 'komi': komi,
               'boardXSize': size, 'boardYSize': size,
               'maxVisits': int(max_visits)}
    requests.put(json.dumps(request) + '\n')
    deadline = time.monotonic() + 45
    while True:
        if session.stop_requested:
            raise InterruptedError('用户已停止')
        if time.monotonic() >= deadline:
            raise TimeoutError('KataGo 分析超过 45 秒，已停止')
        try:
            line = responses.get(timeout=.1)
        except Empty:
            continue
        if line is None:
            raise RuntimeError(f'KataGo 分析进程已退出：{error_path.read_text(encoding="utf-8", errors="replace")[-500:]}')
        if isinstance(line, OSError):
            raise RuntimeError(f'KataGo 请求失败：{error_path.read_text(encoding="utf-8", errors="replace")[-500:]}') from line
        response = json.loads(line)
        if not isinstance(response, dict) or response.get('id') != request['id'] or response.get('error'):
            raise RuntimeError(f'KataGo 返回无效：{str(response)[:500]}')
        if response.get('warning'):
            session.emit_log(f'KataGo：{response["warning"]}')
            continue
        if response.get('isDuringSearch'):
            continue
        if response.get('rootInfo', {}).get('currentPlayer') != player or not response.get('moveInfos'):
            raise RuntimeError('KataGo 未给出当前行棋方的可用落点')
        move = response['moveInfos'][0]['move']
        if move != 'pass':
            parse_coordinate(move, size)
        return move
