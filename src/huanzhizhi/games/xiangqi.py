"""Xiangqi board moves, persistent Pikafish client and automatic red-side play.

The site owns pixels, geometry, selection feedback and its termination rules.
The controller accepts confirmed history from the standard initial position;
it never invents missing history or starts a new game.
"""
import json
from pathlib import Path
import queue
import re
import subprocess
import threading
import time
from huanzhizhi.python_runtime import python_executable
from huanzhizhi.shared_components import supported_components, check_pikafish


def component_paths():
    component = next(c for c in supported_components() if c["id"] == "pikafish")
    root = python_executable().parent.parent
    return component, root, root / "dependencies" / component["engine"], root / "dependencies" / component["model"]


def check_engine():
    component, root, _, _ = component_paths()
    results = list(check_pikafish(component, root))
    failures = [r["detail"] for r in results if not r["satisfied"]]
    if len(results) != 3 or failures:
        raise RuntimeError("；".join(failures) or "皮卡鱼检查不完整")
    return "；".join(r["summary"] for r in results)


class Engine:
    def __init__(self, session, record, *, initial_fen=None):
        self.session, self.record = session, record
        if initial_fen is not None and not re.fullmatch(r'[rnbakcpRNBAKCP1-9/]+ [wb] - - \d+ \d+', initial_fen):
            raise ValueError("初始局面 FEN 无效")
        self.initial_fen = initial_fen
        self.queue = queue.Queue()
        _, _, executable, model = component_paths()
        self.process = subprocess.Popen(
            [str(executable)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW)
        self.reader = threading.Thread(target=self.read, daemon=True)
        self.reader.start()
        try:
            self.send("uci")
            self.until(lambda s:s == "uciok")
            self.send("setoption name EvalFile value " + str(model))
            self.send("setoption name Threads value 2")
            self.send("setoption name Hash value 64")
            self.send("isready")
            self.until(lambda s:s == "readyok")
        except BaseException:
            self.close()
            raise

    def read(self):
        for line in self.process.stdout:
            self.queue.put(line.strip())
        self.queue.put(None)

    def send(self, command):
        if self.session.stop_requested:
            raise InterruptedError("用户停止")
        self.record("uci_in", command=command)
        self.process.stdin.write(command + "\n")
        self.process.stdin.flush()

    def until(self, predicate, timeout=12):
        lines = []
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.session.stop_requested:
                raise InterruptedError("用户停止")
            try:
                line = self.queue.get(timeout=.03)
            except queue.Empty:
                continue
            if line is None:
                raise RuntimeError("皮卡鱼意外退出")
            lines.append(line)
            self.record("uci_out", line=line)
            if predicate(line):
                return lines
        raise TimeoutError("皮卡鱼响应超时")

    def position(self, moves):
        position = "fen " + self.initial_fen if self.initial_fen else "startpos"
        self.send("position " + position + (" moves " + " ".join(moves) if moves else ""))

    def legal(self, moves):
        self.position(moves)
        self.send("go perft 1")
        lines = self.until(lambda s:s.startswith("Nodes searched:"))
        return {m.group(1) for line in lines if (m := re.match(r"^([a-i][0-9][a-i][0-9]):\s+1$", line))}

    def search(self, moves, think_time_ms=500):
        if type(think_time_ms) is not int or not 100 <= think_time_ms <= 60000:
            raise ValueError("每步思考时间必须在 100 到 60000 毫秒之间")
        started = time.perf_counter()
        self.position(moves)
        self.send(f"go movetime {think_time_ms}")
        lines = self.until(lambda s:s.startswith("bestmove "), timeout=max(12, think_time_ms/1000+5))
        move = lines[-1].split()[1]
        self.record("search", move=move, seconds=time.perf_counter()-started, ply=len(moves))
        if move in ("(none)", "0000"):
            return None
        if not re.fullmatch("[a-i][0-9][a-i][0-9]", move):
            raise ValueError("皮卡鱼返回无效走法")
        return move

    def close(self):
        if self.process.poll() is None:
            try:
                self.process.stdin.write("stop\nquit\n")
                self.process.stdin.flush()
                self.process.wait(timeout=.5)
            except (OSError, subprocess.TimeoutExpired):
                self.process.kill()
                self.process.wait(timeout=2)
        self.reader.join(timeout=.5)
        try:
            self.process.stdin.close()
        except OSError:
            # Windows may reject flushing a pipe whose child already exited.
            # Preserve the original engine failure and still close stdout.
            if self.process.poll() is None:
                raise
        finally:
            self.process.stdout.close()


START = list("rnbakabnr" + "."*9 + ".c.....c." + "p.p.p.p.p" + "."*18 +
             "P.P.P.P.P" + ".C.....C." + "."*9 + "RNBAKABNR")


def apply_move(board, move):
    src = (9-int(move[1]))*9 + ord(move[0])-97
    dst = (9-int(move[3]))*9 + ord(move[2])-97
    result = list(board)
    if result[src] == "." or (result[dst] != "." and result[src].isupper() == result[dst].isupper()):
        raise ValueError("走法起终点棋子冲突")
    result[dst], result[src] = result[src], "."
    return result


class GameEnded(Exception):
    pass


class Game:
    def __init__(self, session, view, *, checkpoint_file, think_time_ms=500, mode="auto"):
        """Use one concrete site object, without inheritance or registration.

        view provides observe(after=None), stable(), click(layout, square, ply=...),
        initial_history(observation), history(observation), finish_reason(observation,
        moves, legal), mark_move(observation, move, ply), record and record_frame.
        Observations are (opaque evidence, normalized 90-cell board, layout, selected).
        layout must change when input coordinates become invalid; selected has 90
        booleans. observe(after=...) must acquire pixels newer than that timestamp.
        initial_history returns visible moves, total ply, initial-position visibility,
        and diagnostic evidence; history returns visible moves and total ply. The
        site validates orientation/settings and terminal artwork, not the controller.
        Hint mode also uses view.hint(observation, move) for visual-only guidance.
        """
        if type(think_time_ms) is not int or not 100 <= think_time_ms <= 60000:
            raise ValueError("每步思考时间必须在 100 到 60000 毫秒之间")
        if mode not in ("auto", "hint"):
            raise ValueError("对弈模式无效")
        self.session, self.view = session, view
        self.think_time_ms, self.mode = think_time_ms, mode
        self.checkpoint_file = Path(checkpoint_file)
        self.started = time.perf_counter()
        self.moves = []
        self.expected = list(START)
        self.engine = None

    def check_stop(self):
        if self.session.stop_requested:
            raise InterruptedError("用户停止")

    def apply_parameters(self):
        options = self.session.apply_parameter_updates()
        self.think_time_ms = options.get("think_time_ms", self.think_time_ms)
        self.mode = options.get("mode", self.mode)

    def initialize(self):
        self.engine = Engine(self.session, self.view.record)
        observation = self.view.stable()
        _, board, _, _ = observation
        visible, ply, from_start, evidence = self.view.initial_history(observation)
        if from_start and len(visible) == ply:
            moves = visible
        elif board == START and from_start and not visible:
            moves = []
        elif self.checkpoint_file.is_file():
            saved = json.loads(self.checkpoint_file.read_text(encoding="utf-8"))
            moves = saved["moves"]
            if not isinstance(moves, list) or len(moves) > 1000 or any(
                    not isinstance(move, str) or not re.fullmatch("[a-i][0-9][a-i][0-9]", move) for move in moves):
                raise ValueError("历史记录无效")
            if not visible or visible[-2:] != moves[-min(2, len(moves)):] or ply != len(moves):
                # At most our move and its reply may settle after a stop.
                delta = ply - len(moves) if ply is not None else 0
                if delta not in (1, 2) or len(visible) < delta + 1:
                    raise ValueError("当前棋谱与已核对历史不同")
                proposed = moves + visible[-delta:]
                if visible[-delta-1:] != proposed[-delta-1:]:
                    raise ValueError("停止后的走法不能接续已核对历史")
                moves = proposed
        else:
            raise ValueError("缺少完整可核验历史，未操作棋盘")
        reconstructed = list(START)
        for i, move in enumerate(moves):
            if move not in self.engine.legal(moves[:i]):
                raise ValueError("初始棋谱包含不能核对的走法")
            reconstructed = apply_move(reconstructed, move)
        if reconstructed != board:
            raise ValueError("完整棋谱与当前棋盘不一致")
        self.moves, self.expected = moves, reconstructed
        self.save_checkpoint()
        self.view.record("initial_turn", evidence=evidence, player="red",
                         turn="red" if len(moves) % 2 == 0 else "black", board="".join(board), moves=moves)
        self.view.record_frame(observation)
        self.check_finished(observation)
        self.view.record("initialized", seconds=time.perf_counter()-self.started)
        self.session.emit_status("对弈中", "棋盘及皮卡鱼已确认",
                                 "仅提示红方走法，由你手动落子" if self.mode == "hint" else
                                 "仅操作当前局玩家红方，逐步核对棋盘和合法走法")

    def confirm_history(self, observation):
        rows, ply = self.view.history(observation)
        count = min(2, len(self.moves))
        if len(rows) < count or rows[-count:] != self.moves[-count:] or ply != len(self.moves):
            raise ValueError("网页棋谱与实际走法不能相互核对")

    def save_checkpoint(self):
        temporary=self.checkpoint_file.with_suffix(".tmp")
        temporary.write_text(json.dumps({"moves":self.moves,"board":"".join(self.expected)},ensure_ascii=False),encoding="utf-8")
        temporary.replace(self.checkpoint_file)

    def check_finished(self, observation):
        if observation[1] != self.expected:
            raise ValueError("终局检查与已确认局面不同")
        legal = self.engine.legal(self.moves)
        reason = self.view.finish_reason(observation, self.moves, legal)
        if not legal:
            reason = "玩家获胜：电脑已无合法走法" if len(self.moves) % 2 else "电脑获胜：玩家已无合法走法"
        if reason:
            self.view.record_frame(observation)
            self.view.record("game_ended", reason=reason, ply=len(self.moves))
            raise GameEnded(reason)

    def checkpoint(self):
        resumed = self.session.wait_if_paused()
        self.check_stop()
        self.apply_parameters()
        image,board,origin,selected=self.view.stable()
        if board!=self.expected:
            raise ValueError("局面在安全边界发生变化")
        # Startup establishes the history. During play, unique legal board changes
        # extend it; after a pause, recheck the page history for outside changes.
        if resumed and self.moves:
            self.confirm_history((image,board,origin,selected))
        self.check_finished((image,board,origin,selected))
        return image,board,origin,selected

    def confirm_player(self,move):
        expected=apply_move(self.expected,move)
        next_moves=self.moves+[move]
        legal_reply=self.engine.legal(next_moves)
        deadline=time.monotonic()+10
        while time.monotonic()<deadline:
            image,board,origin,selected=self.view.stable()
            if board==self.expected:
                if self.session.wait_for_stop(.15):
                    raise InterruptedError("用户停止")
                continue
            if board==expected:
                self.moves,self.expected=next_moves,expected
                self.save_checkpoint()
                self.view.record_frame((image,board,origin,selected))
                self.view.record("player_confirmed",move=move,ply=len(self.moves))
                self.session.emit_log("玩家落子已核对："+move)
                self.check_finished((image,board,origin,selected))
                return
            candidates=[reply for reply in legal_reply if apply_move(expected,reply)==board]
            if len(candidates)!=1:
                raise ValueError("玩家落子后棋盘变化不能唯一核对")
            reply=candidates[0]
            self.moves,self.expected=next_moves+[reply],board
            self.save_checkpoint()
            self.view.record_frame((image,board,origin,selected))
            self.view.record("player_confirmed",move=move,ply=len(self.moves)-1,combined=True)
            self.view.record("opponent_confirmed",move=reply,ply=len(self.moves),combined=True)
            self.session.emit_log("双方连续落子均已核对："+move+"、"+reply)
            self.check_finished((image,board,origin,selected))
            return
        raise TimeoutError("落子无响应，未重复点击")

    def wait_opponent(self):
        legal=self.engine.legal(self.moves)
        if not legal:
            raise ValueError("引擎确认无合法应手，但网页终局画面尚待核对")
        deadline=time.monotonic()+60
        self.session.emit_status("等待电脑","玩家落子已确认","电脑回合只观察，不发送点击")
        self.view.record("waiting_opponent",ply=len(self.moves))
        while time.monotonic()<deadline:
            self.check_stop()
            self.apply_parameters()
            image,board,origin,selected=self.view.stable()
            if board==self.expected:
                if self.session.wait_for_stop(.15):
                    raise InterruptedError("用户停止")
                continue
            candidates=[move for move in legal if apply_move(self.expected,move)==board]
            if len(candidates)!=1:
                raise ValueError("电脑应手不能唯一核对")
            move=candidates[0]
            self.moves.append(move)
            self.expected=board
            self.save_checkpoint()
            self.view.record_frame((image,board,origin,selected))
            self.view.record("opponent_confirmed",move=move,ply=len(self.moves))
            self.session.emit_log("电脑应手已核对："+move)
            self.check_finished((image,board,origin,selected))
            return
        raise TimeoutError("等待电脑应手超时，已停止")

    def wait_player(self, move, legal):
        self.session.emit_status("等待你落子", "仅提示", f"建议 {move[:2].upper()} → {move[2:].upper()}；也可以选择其他合法走法")
        try:
            while True:
                self.check_stop()
                if self.session.pause_requested:
                    self.session.emit_hint([], "clear", "已暂停提示")
                resumed = self.session.wait_if_paused()
                self.check_stop()
                self.apply_parameters()
                if self.mode == "auto":
                    return
                observation = self.view.stable()
                board = observation[1]
                if board == self.expected:
                    if resumed and self.moves:
                        self.confirm_history(observation)
                    self.check_finished(observation)
                    self.view.hint(observation, move)
                    if self.session.wait_for_stop(.15):
                        raise InterruptedError("用户停止")
                    continue
                self.session.emit_hint([], "clear", "正在核对手动落子")
                candidates = [candidate for candidate in legal if apply_move(self.expected, candidate) == board]
                if len(candidates) == 1:
                    added = candidates
                else:
                    # A fast computer reply can arrive before the next capture.
                    # Use the visible history to identify both moves, then verify
                    # their legality and resulting full board before committing.
                    rows, ply = self.view.history(observation)
                    count = min(2, len(self.moves))
                    if (ply != len(self.moves)+2 or len(rows) < count+2 or
                            (count and rows[-count-2:-2] != self.moves[-count:])):
                        raise ValueError("手动落子后的棋谱不能接续已核对历史")
                    added = rows[-2:]
                    if (added[0] not in legal or
                            added[1] not in self.engine.legal(self.moves+[added[0]]) or
                            apply_move(apply_move(self.expected, added[0]), added[1]) != board):
                        raise ValueError("手动落子及电脑应手不能与棋盘核对")
                self.moves = self.moves + added
                self.expected = board
                self.save_checkpoint()
                self.view.record_frame(observation)
                self.view.record("manual_confirmed", moves=added, ply=len(self.moves))
                self.session.emit_log("手动落子已核对：" + "、".join(added))
                self.check_finished(observation)
                return
        finally:
            self.session.emit_hint([], "clear", "已结束本手提示")

    def run(self):
        self.initialize()
        while True:
            if len(self.moves)%2:
                self.wait_opponent()
                continue
            self.checkpoint()
            legal=self.engine.legal(self.moves)
            if not legal:
                raise ValueError("玩家无合法走法，网页终局仍需核对")
            self.session.emit_status("选招中","皮卡鱼正在分析","分析期间可停止")
            self.view.record("search_started",ply=len(self.moves))
            move=self.engine.search(self.moves, self.think_time_ms)
            if move is None or move not in legal:
                raise ValueError("引擎未提供已验证的合法走法")
            observation=self.checkpoint()
            _,board,origin,selected=observation
            self.view.mark_move(observation,move,len(self.moves))
            self.check_stop()
            self.apply_parameters()
            if self.mode == "hint":
                self.wait_player(move, legal)
                continue
            self.view.click(origin,move[:2],ply=len(self.moves))
            selected_after=time.perf_counter()
            source=(9-int(move[1]))*9+ord(move[0])-97
            selection_deadline=time.monotonic()+8
            while time.monotonic()<selection_deadline:
                try:
                    image,actual,new_origin,selection=self.view.observe(after=selected_after)
                except ValueError:
                    # An animation may be ambiguous; never act on that frame.
                    if self.session.wait_for_stop(.12):
                        raise InterruptedError("用户停止")
                    continue
                if actual!=board or new_origin!=origin:
                    raise ValueError("选择起点时局面变化")
                if [i for i,v in enumerate(selection) if v] == [source]:
                    break
                if self.session.wait_for_stop(.12):
                    raise InterruptedError("用户停止")
            else:
                raise ValueError("起点选中反馈未确认，未点击终点")
            self.view.record("source_selected",move=move)
            self.session.emit_log("起点选中已核对")
            # The same full-board observation confirms both selection and state.
            # Click immediately, retaining cancellation and the window refresh.
            self.check_stop()
            self.apply_parameters()
            if self.mode == "hint":
                self.wait_player(move, legal)
                continue
            self.view.click(origin,move[2:],ply=len(self.moves))
            self.confirm_player(move)

    def close(self):
        if self.engine:
            self.engine.close()
            self.view.record("engine_closed",returncode=self.engine.process.returncode)
