"""Pure visual-board solver: every straight drag must eliminate its starting tile."""
from __future__ import annotations

from dataclasses import dataclass
from time import monotonic
from collections.abc import Callable

WIDTH, HEIGHT = 10, 14
DIRECTIONS = ((-1, 0), (0, 1), (1, 0), (0, -1))
NEIGHBORS = tuple(tuple((r+dy)*WIDTH+c+dx if 0 <= r+dy < HEIGHT and 0 <= c+dx < WIDTH else -1
                       for dy, dx in DIRECTIONS) for r in range(HEIGHT) for c in range(WIDTH))

@dataclass(frozen=True)
class Move:
    start: int
    direction: int
    distance: int
    match: int

    @property
    def target(self) -> int:
        if not self.distance:
            return self.start
        dy, dx = DIRECTIONS[self.direction]
        return self.start + (dy * WIDTH + dx) * self.distance

def matches(board: bytes, index: int) -> tuple[int, ...]:
    if not board[index]:
        return ()
    result = []
    for direction in range(4):
        other = NEIGHBORS[index][direction]
        while other >= 0 and not board[other]:
            other = NEIGHBORS[other][direction]
        if other >= 0 and board[other] == board[index]:
            result.append(other)
    return tuple(result)

def preview(board: bytes, move: Move) -> bytes:
    if not 0 <= move.start < 140 or not board[move.start] or move.distance < 0:
        raise ValueError('无效起点或拖动距离')
    if not move.distance:
        return board
    if move.direction not in range(4):
        raise ValueError('拖动必须沿一个方向')
    if matches(board, move.start):
        raise ValueError('起始砖已经可以直接配对，应点击消除，不能拖动')
    chain, current = [], move.start
    while current >= 0 and board[current]:
        chain.append(current)
        current = NEIGHBORS[current][move.direction]
    for _ in range(move.distance):
        if current < 0 or board[current]:
            raise ValueError('连续空位不足')
        current = NEIGHBORS[current][move.direction]
    result = bytearray(board)
    offset = move.target - move.start
    for i in chain:
        result[i] = 0
    for i in chain:
        result[i + offset] = board[i]
    return bytes(result)

def apply(board: bytes, move: Move) -> bytes:
    result = preview(board, move)
    if move.match not in matches(result, move.target):
        raise ValueError('起始砖在落点不能配对，必须回弹')
    cleared = bytearray(result)
    cleared[move.target] = cleared[move.match] = 0
    return bytes(cleared)

def candidates(board: bytes):
    seen = set()
    for index, tile in enumerate(board):
        if not tile:
            continue
        direct_matches = matches(board, index)
        for match in direct_matches:
            if index < match:
                move = Move(index, -1, 0, match)
                yield move, apply(board, move)
        if direct_matches:
            continue
        for direction, (dy, dx) in enumerate(DIRECTIONS):
            chain, current = [], index
            while current >= 0 and board[current]:
                chain.append(current)
                current = NEIGHBORS[current][direction]
            distance = 1
            while current >= 0 and not board[current]:
                offset = (dy * WIDTH + dx) * distance
                shifted = bytearray(board)
                for i in chain:
                    shifted[i] = 0
                for i in chain:
                    shifted[i + offset] = board[i]
                target = index + offset
                for match in matches(shifted, target):
                    result = shifted.copy()
                    result[target] = result[match] = 0
                    result = bytes(result)
                    if result not in seen:
                        seen.add(result)
                        yield Move(index, direction, distance, match), result
                distance += 1
                current = NEIGHBORS[current][direction]

def canonicalize(board: bytes) -> bytes:
    """Compare layouts independently of temporary visual group numbers."""
    labels = {0: 0}
    return bytes(labels.setdefault(tile, len(labels)) for tile in board)


def solve(board: bytes, stopped: Callable[[], bool] = lambda: False,
          *, seconds: float = 8.0, max_nodes: int = 60_000) -> tuple[Move, ...] | None:
    if len(board) != 140 or any(not isinstance(tile, int) or not 0 <= tile <= 255 for tile in board):
        raise ValueError('棋盘必须包含140个有效格子')
    if any(board.count(tile) % 2 for tile in set(board) - {0}):
        raise ValueError('识别结果存在奇数张图案')
    started = monotonic()
    deadline = started + min(1.0, seconds/4)
    node_limit = max(1, max_nodes//4)
    prefer_pairs = True
    seen: set[bytes] = set()
    nodes = 0
    def dfs(current: bytes, remaining: int):
        nonlocal nodes
        if stopped():
            raise InterruptedError('用户停止提示')
        if not remaining:
            return ()
        nodes += 1
        if nodes > node_limit or monotonic() > deadline:
            raise TimeoutError('本次搜索未找到完整解法，不代表无解')
        if current in seen:
            return None
        seen.add(current)
        actions = candidates(current)
        if prefer_pairs:
            # Open interior space with direct pairs first. This is a search
            # preference only: every generated move remains available.
            actions = sorted(actions, key=lambda item: (bool(item[0].distance),
                sum(NEIGHBORS[i].count(-1) for i in (item[0].target,item[0].match))))
        for move, result in actions:
            suffix = dfs(result, remaining - 2)
            if suffix is not None:
                return (move,) + suffix
        return None
    remaining = sum(bool(tile) for tile in board)
    try:
        return dfs(bytes(board), remaining)
    except TimeoutError:
        # No single ordering works well on every layout. Give the heuristic
        # a small slice, then retry the existing order within the SAME total
        # time/node budget; partial searches must not leave states pruned.
        seen.clear()
        prefer_pairs = False
        deadline = started + seconds
        node_limit = max_nodes
        return dfs(bytes(board), remaining)
