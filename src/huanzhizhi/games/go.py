"""Go board transitions, play flow and the shared KataGo analysis client.

Board values are 0 empty, 1 black, 2 white, indexed from the top-left.
Local transitions check captures and suicide, not ko/superko or scoring.
Pass actual move history and rules to KataGo for those game-level rules.
"""
from __future__ import annotations

import json
import math
from contextlib import contextmanager, ExitStack
from pathlib import Path
from queue import Empty, Queue
import subprocess
import tempfile
import threading
import time

from huanzhizhi.python_runtime import python_executable
from huanzhizhi.shared_components import check_katago, katago_paths, supported_components
from huanzhizhi.task_sdk import ObservationPending

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


def _observed_move(before, after, color):
    try:
        return single_new_stone(before, after, color)
    except ValueError as error:
        raise ObservationPending(str(error)) from error


def _wait_for_manual_move(session, observe, point, board, x, y, move, color, diagnostic):
    opponent = 3 - color
    color_name = '黑棋' if color == 1 else '白棋'
    size = len(board)

    def validate(after, last_color):
        if after == board:
            return None
        session.emit_hint([], 'clear', '正在确认棋盘')
        additions = [(bx, by) for by in range(size) for bx in range(size)
                     if board[by][bx] != color and after[by][bx] == color]
        if len(additions) != 1:
            raise ObservationPending(f'无法确认手动{color_name}落点，已停止')
        bx, by = additions[0]
        try:
            expected = move_result(board, bx, by, color)
        except ValueError as error:
            raise ObservationPending(str(error)) from error
        diagnostic.update(expected_board=expected, manual_move=coordinate(bx, by, size))
        if last_color == opponent:
            _observed_move(expected, after, opponent)
        elif expected != after:
            raise ObservationPending(f'棋盘变化不是合法单次{color_name}落子或提子，已停止')
        return coordinate(bx, by, size), expected

    session.emit_status('等待你落子', '仅提示', f'建议{color_name}下 {move}；也可以选择其他落点。')
    try:
        while not session.stop_requested:
            if session.wait_if_paused():
                return None  # The caller rechecks before renewing a hint or input.
            if session.apply_parameter_updates().get('mode', 'hint') == 'auto':
                return None
            session.emit_hint([point(x, y)], 'click', f'建议{color_name}：{move}')
            if session.wait_for_stop(.1):
                break
            diagnostic.update(stage='manual_move', before_board=board,
                              before_source='observed_before_own_move', expected_board=None)
            observation = observe(validate=validate, changed_only=True)
            if observation is None or observation[2] is None:
                continue  # Unchanged pixels or hover previews do not commit a move.
            return observation[2]
        raise InterruptedError('用户已停止')
    finally:
        session.emit_hint([], 'clear', '已结束本手提示')


def play(session, *, observe, click, point, diagnostic, rules='chinese', komi=7.5):
    """Run auto/hint play using three ordinary site callbacks, without pixels.

    observe(validate=None, changed_only=False, retries=3, timeout=None) returns
    (board, last_color, validation_result). last_color is 0 unknown, 1 black,
    2 white. It calls validate(board, last_color) against each fresh observation,
    using SDK confirm_observation for bounded, read-only retries. Only when
    changed_only=True may it return None for an unchanged, still-valid view.
    Every successful observation becomes the next change-detection reference.

    click(x, y) sends one input through the host using the latest verified view;
    point(x, y) maps a board index to a screen point for hints. The callbacks
    retain window/pixel state and add capture evidence to diagnostic. Errors
    propagate with game history attached; the skill saves its existing evidence.
    """
    name = session.skill['id']
    moves_played = 0
    initial, moves = None, []
    resources = ExitStack()
    engine = None
    try:
        mode = session.options.get('mode', 'auto')
        if mode not in ('auto', 'hint'):
            raise ValueError('对弈模式无效')
        player = session.options.get('color', 'B')
        if player not in ('B', 'W'):
            raise ValueError('执子颜色无效')
        color = 1 if player == 'B' else 2
        opponent = 3 - color
        opponent_player = 'W' if player == 'B' else 'B'
        color_name = '黑棋' if color == 1 else '白棋'
        opponent_name = '白棋' if color == 1 else '黑棋'
        previous = None
        waiting = False
        session.emit_status('对弈中', name, '正在识别棋盘与回合')
        while not session.stop_requested:
            if session.wait_if_paused():
                waiting = False
            if session.stop_requested:
                break
            session.apply_parameter_updates()
            diagnostic.update(stage='wait_opponent' if previous is not None else 'observe_board',
                              before_board=previous if previous is not None else diagnostic.get('after_board'),
                              before_source='expected_after_own_move' if previous is not None else 'last_observed',
                              expected_board=None, move=None, manual_move=None)

            def validate_opponent(after, last_color):
                if previous is None or after == previous:
                    return None
                if last_color != opponent:
                    raise ObservationPending(f'棋盘已变化，但无法确认{opponent_name}回合完成')
                return _observed_move(previous, after, opponent)

            observation = observe(validate=validate_opponent, changed_only=waiting)
            if observation is None:
                session.wait_for_stop(.1)
                continue
            board, last_color, opponent_move = observation
            waiting = False
            if opponent_move is not None:
                wx, wy = opponent_move
                moves.append([opponent_player, coordinate(wx, wy, len(board))])
                previous = None
            occupied = any(v for row in board for v in row)
            our_turn = last_color == opponent if occupied else color == 1
            if previous is not None or not our_turn:
                waiting = True
                session.wait_for_stop(.1)
                continue
            if initial is None:
                initial = [row.copy() for row in board]
            if engine is None:
                diagnostic['stage'] = 'engine_start'
                engine = resources.enter_context(start_engine())
            diagnostic['stage'] = 'engine_analysis'
            move = analyze(session, engine, initial, moves, initial_player=player, rules=rules, komi=komi)
            diagnostic['move'] = move
            if move.lower() == 'pass':
                session.emit_finished('已完成', '建议虚手', 'KataGo 建议虚手，交由用户决定。')
                return
            x, y = parse_coordinate(move, len(board))
            if board[y][x] != 0:
                raise ValueError(f'KataGo 落点与棋盘不符：{move}')
            if session.stop_requested:
                break
            manual_move = None
            while not session.stop_requested:
                session.wait_if_paused()
                if session.stop_requested:
                    break
                diagnostic.update(stage='before_click_recheck', before_board=board,
                                  before_source='observed_before_analysis', expected_board=None)

                def validate_unchanged(current, current_last_color):
                    if current != board or current_last_color != last_color:
                        raise ObservationPending('分析或模式切换期间棋盘或回合已变化，已停止')

                observe(validate=validate_unchanged)
                mode = session.apply_parameter_updates().get('mode', 'auto')
                if mode == 'auto':
                    break
                manual_move = _wait_for_manual_move(
                    session, observe, point, board, x, y, move, color, diagnostic)
                if manual_move is not None:
                    break
            if session.stop_requested:
                break
            if manual_move is not None:
                move, previous = manual_move
                moves.append([player, move])
                moves_played += 1
                session.emit_status('对弈中', f'已确认{color_name}', f'第 {moves_played} 手：{move}；等待{opponent_name}')
                continue
            diagnostic['stage'] = 'click'
            click(x, y)
            expected_own = move_result(board, x, y, color)
            diagnostic.update(stage='confirm_own_move', before_board=board,
                              before_source='observed_before_own_move', expected_board=expected_own)

            def validate_own(after, after_last_color):
                if after == expected_own:
                    return
                if after_last_color == opponent:
                    try:
                        single_new_stone(expected_own, after, opponent)
                    except ValueError:
                        pass
                    else:
                        return
                raise ObservationPending(f'{move} 点击后未见完整合法落子，已停止')

            if session.wait_for_stop(.2):
                raise InterruptedError('用户已停止')
            # Confirmation is read-only; the submitted click is never repeated.
            after, _, _ = observe(validate=validate_own, retries=24, timeout=5)
            if after == expected_own:
                waiting = True
            moves.append([player, move])
            previous = expected_own
            moves_played += 1
            session.emit_status('对弈中', f'已落{color_name}', f'第 {moves_played} 手：{move}')
        session.emit_finished('已停止', name, f'已按请求停止，本次落 {moves_played} 手', stopped_by_request=True)
    except InterruptedError:
        session.emit_finished('已停止', name, f'已按请求停止，本次落 {moves_played} 手', stopped_by_request=True)
    except Exception:
        diagnostic.update(initial_board=initial, moves=moves, moves_played=moves_played,
                          options=dict(session.options))
        raise
    finally:
        resources.close()
