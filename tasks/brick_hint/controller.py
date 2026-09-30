"""Observe WGC frames and emit hints; this module never injects input."""
from __future__ import annotations

import json
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from time import monotonic

import numpy as np
from PIL import Image

from huanzhizhi.platform import Win32DesktopApi
from huanzhizhi.platform.window_capture import WgcWindowCapture
from recognition import Grid, RecognitionError, Recognizer, empty_cell, locate
from solver import Move, apply, canonicalize, matches, preview, solve

TASK_NAME = '砖了个砖提示'

def instruction(board: bytes, move: Move) -> str:
    position = lambda i: f'{i//10+1}行{i%10+1}列'
    if not move.distance:
        text = f'点击{position(move.start)}的砖'
    else:
        direction = ('上','右','下','左')[move.direction]
        text = f'{position(move.start)}向{direction}拖{move.distance}格'
    if len(matches(preview(board, move), move.target)) > 1:
        text += f'，再选{position(move.match)}'
    return text

def hint_points(grid: Grid, move: Move, left: int, top: int) -> list[tuple[int,int]]:
    indexes = (move.start,move.target,move.match) if move.distance else (move.start,move.match)
    return [(left+grid.center(i)[0],top+grid.center(i)[1]) for i in indexes]

class HintController:
    def __init__(self, session, desktop=None):
        self.session = session
        self.capture = WgcWindowCapture() if desktop is None else None
        self.desktop = desktop if desktop is not None else Win32DesktopApi(capture=self.capture)
        self.recognizer = Recognizer()
        self.last_image = None
        self.last_status = None
        self.last_hint = None
        self.plan: tuple[Move,...] = ()
        self.expected = None
        self.board = None
        self._frame_signature = None
        self._invalid_frames = 0
        self.grid = None
        self._quick_baseline = None
        self._quick_active = False
        self._quick_candidate = None
        self._quick_since = 0.0
        self._verify_at = 0.0
        self._quick_unverified = False

    def clear(self):
        self._quick_baseline = None
        self._quick_candidate = None
        self._quick_active = False
        if self.last_hint is not None:
            self.session.emit_hint([], 'clear', '等待确认画面')
            self.last_hint = None

    def status(self, title, detail):
        value = title, detail
        if value != self.last_status:
            self.session.emit_status(title, TASK_NAME, detail)
            self.last_status = value

    def _pair_signature(self, image, grid):
        array = np.asarray(image)
        move = self.plan[0]
        return np.stack([np.asarray(Image.fromarray(grid.tile(array, index)).resize((16,16)))
                         for index in (move.start,move.match)]).astype(np.int16)

    def _show_plan_hint(self, grid, window):
        move = self.plan[0]
        label = instruction(self.board, move)
        points = hint_points(grid, move, window.left, window.top)
        self.session.emit_hint(points, 'drag' if move.distance else 'click', label)
        self.last_hint = points
        self.status('等待你操作', f'{label}；剩余{sum(bool(v) for v in self.board)}块。我只提示，不会自动点击。')

    def _quick_step(self, image, grid, window, now):
        pair = self._pair_signature(image, grid)
        if self._quick_baseline is None:
            # Show the next hint immediately, but arm its detector only after
            # its two regions settle. The old animation must not skip steps.
            if self._quick_candidate is None or np.abs(pair-self._quick_candidate).mean(axis=(1,2,3)).max() > 1.8:
                self._quick_since = now
            self._quick_candidate = pair
            if now-self._quick_since >= .2:
                self._quick_baseline = pair
            return False
        changed = np.abs(pair-self._quick_baseline).mean(axis=(1,2,3)) > 6
        if changed[0]:
            self._quick_active = True
        if not (self._quick_active and changed[1]):
            return False
        # Clicking shakes every matching tile. Motion alone therefore cannot
        # confirm a pair; reuse the recognizer's local empty-cell check.
        if not empty_cell(np.asarray(image),grid,self.plan[0].match):
            return False
        self.board = self.expected
        self.plan = self.plan[1:]
        self.expected = apply(self.board,self.plan[0]) if self.plan else None
        self._quick_baseline = self._quick_candidate = None
        self._quick_active = False
        self._quick_unverified = True
        self.session.emit_log('快速推进：主动砖已变化，配对格已确认为空；复用下一步，等待完整复核')
        if self.plan:
            self._show_plan_hint(grid,window)
        else:
            self.clear()
            self.status('等待消除确认','最后一对已发生变化，等待确认棋盘清空')
        return True

    def analyze(self, image, window):
        started = monotonic()
        self.last_image = image
        try:
            grid = locate(image, self.grid)
        except RecognitionError:
            self.clear()
            self._frame_signature = None
            self.status('等待棋盘', '请打开砖了个砖的14×10棋盘；当前不显示指向')
            return
        # Re-fitting fewer tiles can round the frame by one pixel. Keep the
        # confirmed coordinates within that rounding noise, avoiding a false
        # geometry change that clears the hint and invalidates cached crops.
        if self.grid is not None and all(abs(getattr(grid,key)-getattr(self.grid,key)) <= 1
                                         for key in ('left','top','width','height')):
            grid = self.grid
        located = monotonic()
        crop = image.crop((grid.left,grid.top,grid.left+grid.width,grid.top+grid.height))
        signature = np.asarray(crop.resize((160,224))).astype(np.int16)
        previous, self._frame_signature = self._frame_signature, signature
        stable = previous is not None and np.abs(signature-previous).reshape(14,16,10,16,3).mean(axis=(1,3,4)).max() <= 1.8
        selecting_match = self.last_status is not None and self.last_status[0] == '选择配对'
        if self.last_hint is not None and self.plan and self.expected is not None and grid == self.grid and not selecting_match:
            if self._quick_step(image,grid,window,started):
                return
            if started < self._verify_at or not stable:
                self._show_plan_hint(grid,window)
                return
        # A two-tile disappearance can be diluted below the threshold when
        # averaged over the whole board. Check each grid-sized region instead
        # so flashes cannot masquerade as a new, valid grouping.
        if not stable:
            self.clear()
            self.status('等待画面稳定', '请完成当前点击或拖动')
            return
        try:
            board, grid = self.recognizer.read(image, grid)
        except RecognitionError as error:
            self.clear()
            self._invalid_frames += 1
            self.status('识别待确认', str(error))
            if self._invalid_frames >= 4:
                raise RecognitionError(f'连续稳定画面无法可靠识别：{error}') from error
            return
        self.grid = grid
        recognized = monotonic()
        self._verify_at = recognized+2
        self._invalid_frames = 0
        # A fresh frame assigns fresh group numbers. Keep the plan's numbering
        # when the observed layout matches a known state, including a pending
        # drag that still needs the user to choose its matching tile.
        observed = canonicalize(board)
        known_states = [self.expected, self.board]
        if self.board is not None and self.plan:
            known_states.append(preview(self.board, self.plan[0]))
        board = next((known for known in known_states
                      if known is not None and canonicalize(known) == observed), observed)
        if self._quick_unverified:
            self.session.emit_log(f'完整复核：{"匹配快速预测" if board == self.board or board == self.expected else "与快速预测不符"}')
            self._quick_unverified = False
        if not any(board):
            self.clear()
            self.status('棋盘已清空', '已识别到空棋盘，等待下一局')
            self.board, self.plan, self.expected = board, (), None
            return
        if self.board is not None and self.plan and board != self.board and board == preview(self.board, self.plan[0]):
            # A pending drag is not a committed board to solve freely.
            target = self.plan[0].match
            x,y = grid.center(target)
            points = [(window.left+x,window.top+y)]
            label = f'点选高亮的{target//10+1}行{target%10+1}列'
            self.session.emit_hint(points,'click',label)
            self.last_hint = points
            self.status('选择配对',label)
            return
        if board != self.board:
            self.clear()
            reuse_plan = board == self.expected and bool(self.plan)
            self.session.emit_log(
                f'棋盘确认：{"命中预期，复用计划" if reuse_plan else "首次求解" if self.board is None else "未命中预期，重新求解"}；'
                f'定位{(located-started)*1000:.0f}ms，识别{(recognized-located)*1000:.0f}ms；'
                f'剩余{sum(bool(v) for v in board)}块；格子定位{grid}')
            if self.board is not None and not reuse_plan:
                self.session.emit_log(
                    f'棋盘差异：上一步={self.plan[0] if self.plan else None}；'
                    f'预期={canonicalize(self.expected).hex() if self.expected is not None else None}；'
                    f'实际={observed.hex()}')
            if reuse_plan:
                self.plan = self.plan[1:]
            else:
                self.status('计算提示', '根据当前画面寻找完整消除顺序')
                solving = monotonic()
                try:
                    self.plan = solve(board, lambda: self.session.stop_requested) or ()
                except TimeoutError as error:
                    # Keep observing, but do not repeatedly run the same
                    # expensive search until the board changes.
                    self.board, self.plan, self.expected = board, (), None
                    self.session.emit_log(f'搜索未完成：{error}；棋盘={observed.hex()}')
                    self.status('搜索未完成','搜索达到上限，不代表无解；可手动调整棋盘后继续，或重新启动任务重试')
                    return
                self.session.emit_log(f'求解结束：耗时{(monotonic()-solving)*1000:.0f}ms；计划{len(self.plan)}步')
            self.board = board
            if not self.plan:
                self.status('没有完整解法', '当前规则下没有完整清盘顺序，请手动调整或洗牌后重试')
                return
            self.expected = apply(board, self.plan[0])
            # Re-observe after solving; do not point at a screenshot that may
            # have changed while the search was running.
            if not reuse_plan:
                self._frame_signature = None
                return
        if not self.plan:
            return
        self._show_plan_hint(grid,window)
        self._quick_baseline = self._pair_signature(image,grid)
        self._quick_active = False

    def run(self):
        try:
            while not self.session.stop_requested:
                started = monotonic()
                windows = [w for w in self.desktop.list_windows() if w.title.strip() == '砖了个砖']
                if len(windows) != 1:
                    self.clear()
                    self.status('等待游戏窗口', '请保持一个标题为“砖了个砖”的游戏窗口打开')
                    self._frame_signature = None
                else:
                    image = self.desktop.capture_client(windows[0]).convert('RGB')
                    self.analyze(image, windows[0])
                if self.session.wait_for_stop(max(0.0, .1-(monotonic()-started))):
                    break
            return '已停止', '已停止画面识别与提示', True
        except InterruptedError:
            return '已停止', '已停止画面识别与提示', True
        finally:
            self.clear()
            # Capture is session-owned; shutting it down does not affect input.
            if self.capture is not None:
                self.capture.close()

    def save_failure(self, error):
        root = Path(sys.executable).parent.parent if getattr(sys, 'frozen', False) else Path(__file__).parents[2]
        directory = root / 'artifacts' / 'brick_hint' / datetime.now().strftime('%Y%m%d-%H%M%S-%f')
        directory.mkdir(parents=True,exist_ok=True)
        if self.last_image is not None:
            self.last_image.save(directory/'frame.png')
        (directory/'failure.json').write_text(json.dumps({'error':str(error),'board':list(self.board) if self.board else None,
            'plan':[asdict(move) for move in self.plan]},ensure_ascii=False,indent=2),encoding='utf-8')
        return directory
