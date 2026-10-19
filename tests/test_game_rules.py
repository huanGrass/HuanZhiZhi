"""Game logic accepts board data without a skill directory or desktop input."""
from copy import deepcopy
from queue import Queue
from types import SimpleNamespace
import unittest

from huanzhizhi.games import go, minesweeper


class MinesweeperRulesTests(unittest.TestCase):
    def test_rectangular_boards_and_flag_is_not_proof(self):
        for rows, cols in ((1, 3), (9, 9), (16, 16), (16, 30)):
            with self.subTest(rows=rows, cols=cols):
                near = minesweeper.neighbors(rows, cols)
                self.assertEqual(len(near), rows * cols)
                self.assertNotIn(0, near[0])
                self.assertTrue(all(i in near[j] for i, cells in enumerate(near) for j in cells))
                board = [-1] * (rows * cols)
                board[-1] = -2
                safe, bombs, _ = minesweeper.deduce(board, set(), near, 0)
                self.assertEqual(safe, set(range(len(board))))
                self.assertFalse(bombs)

    def test_number_and_total_conflict_is_not_overwritten(self):
        with self.assertRaisesRegex(RuntimeError, '总雷数.*冲突'):
            minesweeper.deduce([1, -1], set(), [{1}, {0}], 0)
        for board, mines, total in (([-1, -1], {0, 1}, 1), ([0, -1], set(), 2)):
            with self.subTest(board=board, mines=mines):
                with self.assertRaises(RuntimeError):
                    minesweeper.deduce(board, mines, [{1}, {0}], total)

    def test_invalid_recognition_and_known_mine_are_rejected(self):
        for board, mines, near, total in (([-3], set(), [set()], 0),
                                          ([9], set(), [set()], 0),
                                          ([-1], set(), [], 0),
                                          ([0], {0}, [set()], 1),
                                          ([-1], {1}, [set()], 1),
                                          ([-1], set(), [{0}], 1),
                                          ([-1], set(), [set()], -1)):
            with self.subTest(board=board, mines=mines, total=total):
                with self.assertRaises(ValueError):
                    minesweeper.deduce(board, mines, near, total)

    def test_deductions_and_risk_do_not_change_input(self):
        board = [1, -1, -1, -1]
        near = minesweeper.neighbors(1, 4)
        mines = set()
        safe, bombs, eq = minesweeper.deduce(board, mines, near, 1)
        self.assertEqual(bombs, {1})
        mines.update(bombs)
        safe, _, eq = minesweeper.deduce(board, mines, near, 1)
        self.assertEqual(safe, {2, 3})
        self.assertEqual(board, [1, -1, -1, -1])
        targets, risk = minesweeper.choose(
            board, mines, eq, SimpleNamespace(stop_requested=False), near, 1)
        self.assertEqual(set(targets), {2, 3})
        self.assertEqual(risk, 0)
        with self.assertRaises(InterruptedError):
            minesweeper.choose(board, mines, eq, SimpleNamespace(stop_requested=True), near, 1)


class GoRulesTests(unittest.TestCase):
    def test_capture_and_observed_transition_on_multiple_sizes(self):
        for size in (9, 13, 19):
            with self.subTest(size=size):
                board = [[0] * size for _ in range(size)]
                board[1][1] = 2
                for x, y in ((0, 1), (1, 0), (2, 1)):
                    board[y][x] = 1
                original = deepcopy(board)
                after = go.move_result(board, 1, 2, 1)
                self.assertEqual(after[1][1], 0)
                self.assertEqual(after[2][1], 1)
                self.assertEqual(go.single_new_stone(board, after, 1), (1, 2))
                self.assertEqual(board, original)
                after[-1][-1] = 1
                with self.assertRaisesRegex(ValueError, '预期一枚'):
                    go.single_new_stone(board, after, 1)

    def test_coordinate_round_trip_and_invalid_points(self):
        for size in (9, 13, 19):
            for x, y in ((0, 0), (size - 1, size - 1), (8, 4)):
                self.assertEqual(go.parse_coordinate(go.coordinate(x, y, size), size), (x, y))
        self.assertEqual(go.coordinate(8, 0, 9), 'J9')
        for vertex in ('I9', 'A0', 'T20', 'pass', 'J10', 'A９'):
            with self.assertRaises(ValueError):
                go.parse_coordinate(vertex, 9)
        board = [[0] * 9 for _ in range(9)]
        for x, y, color in ((-1, 0, 1), (0, 9, 1), (0, 0, 3), (0, 0, True)):
            with self.assertRaises(ValueError):
                go.move_result(board, x, y, color)

    def test_occupied_suicide_and_mismatched_boards_rejected(self):
        board = [[0] * 9 for _ in range(9)]
        board[0][1] = board[1][0] = 2
        with self.assertRaisesRegex(ValueError, '无气'):
            go.move_result(board, 0, 0, 1)
        with self.assertRaisesRegex(ValueError, '占用'):
            go.move_result(board, 0, 1, 1)
        with self.assertRaises(ValueError):
            go.single_new_stone(board, [[0] * 13 for _ in range(13)], 1)
        with self.assertRaises(ValueError):
            go.move_result([[0] * 9, [0]], 0, 0, 1)

    def test_bad_analysis_input_sends_no_request(self):
        requests = Queue()
        engine = (None, requests, Queue(), None)
        session = SimpleNamespace(stop_requested=False, options={})
        board = [[0] * 9 for _ in range(9)]
        for args in ({'initial_player': 'X'}, {'komi': float('nan')}, {'komi': 6.25}, {'rules': ''}):
            with self.subTest(args=args):
                with self.assertRaises(ValueError):
                    go.analyze(session, engine, board, [], **args)
        with self.assertRaises(ValueError):
            go.analyze(session, engine, board, [['W', 'D4']])
        self.assertTrue(requests.empty())


if __name__ == '__main__':
    unittest.main()
