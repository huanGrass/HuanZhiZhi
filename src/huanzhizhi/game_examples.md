# 共享游戏数据与调用示例

公共代码位于 `src/huanzhizhi/games/`，与宿主一起提供。按需导入，不启动服务、不自动下载组件。技能可以在任意草稿目录运行，不依赖另一个技能目录。当前只有两个游戏模块，不需要注册表、工厂或适配器基类。

新技能先完成“截图 → 棋盘数据”的适配，再调用下面的公共逻辑。窗口选择、坐标、规则/回合识别、动画等待、输入、结果确认与结束条件仍由技能负责。先用静态数据验证识别，再通过现有任务运行链验证实际效果；公共求解正确不等于新界面已适配。

## 扫雷

`from huanzhizhi.games import minesweeper`

- 标准矩形、八邻域棋盘。`neighbors(rows, cols)` 生成邻格表，只在尺寸确定后生成一次；不支持六边形等变体。
- `board` 是从左上角开始、逐行排列的整数列表，长度为 `rows * cols`。`-1` 未展开，`-2` 界面旗子，`0..8` 已展开数字。未知颜色必须让识别报错，不能用 `-1` 掩盖。
- `total` 是整盘总雷数，不能传“剩余未标记雷数”。`mines` 是逻辑已确定或独立核实的雷位索引集合；界面旗子不自动作为事实。
- `deduce(board, mines, near, total)` 返回 `(safe, bombs, equations)`。有新雷位时更新集合并继续推理，直到找到安全格或不再增加雷位。
- `choose(board, mines, equations, session, near, total)` 返回 `(targets, estimated_risk)`，使用最后一次推理返回的约束。保留原有分区枚举与总雷数加权，搜索超过节点上限后使用启发式风险估计；不承诺最低风险或必胜。`session` 提供 `stop_requested`，搜索可中断。是否允许猜测由技能需求决定。

一次纯数据推理示例，`targets` 中索引转为 `(row, col)` 使用 `divmod(index, cols)`：

```python
from huanzhizhi.games import minesweeper

rows, cols, total = 1, 4, 1
board = [1, -1, -1, -1]
near = minesweeper.neighbors(rows, cols)
mines = set()
while True:
    safe, bombs, equations = minesweeper.deduce(board, mines, near, total)
    mines.update(bombs)
    if safe or not bombs:
        break
targets = sorted(safe)  # 本例确定雷位 1，安全格 2、3
```

无确定安全格时，允许猜测的技能才调用 `choose(..., session, ...)`；否则提示无确定解并等待用户。不要把第一次点击的安全性当作所有扫雷实现的共同规则。点击旗子前先确认该界面的开格/取消旗子行为。矛盾数据应重新观察并验证，不清空已推导雷位来掩盖冲突；重新开局时才重置局内记录。公共模块只返回数据，不点击、不重开、不决定“已通关”。

## 围棋

`from huanzhizhi.games import go`

`board` 是方形二维整数列表，`board[y][x]`，原点在左上角：`0` 空、`1` 黑、`2` 白。当前公共坐标和棋盘模块支持 2～19 路方阵，包含常见 9、13、19 路。棋盘尺寸支持与具体界面识别需要分别验证；公共模块不附带窗口识别。

- `board_size(board)`：校验数据并返回边长。
- `coordinate(x, y, size=19)` / `parse_coordinate(vertex, size=19)`：棋盘索引与 GTP 坐标互转，列字母跳过 I。`pass` 不对应像素，交由技能单独处理。
- `move_result(board, x, y, color)`：返回落子、提子后的新棋盘，拒绝越界、占用和自杀，不修改输入。
- `single_new_stone(before, after, color)`：确认新截图是一次落子及相应提子，返回 `(x, y)`；多手变化或其他不一致会报错。

这些局部函数不负责劫/超级劫、终局计分，也不支持允许自杀的规则变体。KataGo 根据实际棋谱、规则和贴目做对局分析；历史缺失时不能保证复原此前的劫限制。不要根据棋子遍历顺序编造棋谱，也不要只数黑白子来猜行棋方。

### 复用 KataGo

`go.check_engine()` 复用共享组件检查，可直接用在 `describe(__file__, environment_checks={"KataGo": go.check_engine})（清单中同时声明 environment_checks）`。缺少依赖时按检查提示处理；引擎、模型、CUDA/cuDNN 仍在 `dependencies/`，不放进游戏源码或技能目录。

```python
from huanzhizhi.games import go

# session 来自实际技能的 TaskSession。
# 示例局面轮到白棋；真实技能须从界面确认。
initial = [[0] * 9 for _ in range(9)]
initial[2][2] = 1
initial_player = "W"
moves = []
with go.start_engine() as engine:
    vertex = go.analyze(session, engine, initial, moves,
                        initial_player=initial_player, rules="chinese", komi=7.5)
    if vertex == "pass":
        session.emit_log("引擎建议虚手，交由用户或技能约定处理。")
    else:
        x, y = go.parse_coordinate(vertex, len(initial))
        # 使用当前截图得到的交叉点坐标映射 (x, y)，操作前再核对棋盘。
```

整局只进入一次 `start_engine()`，后续在同一 `with` 内反复调用 `analyze()`。`initial` 是接手时的棋盘快照，不在每轮替换；`moves` 按实际已确认操作追加，例如 `[["W", "D4"], ["B", "F6"]]`。`initial_player` 指快照时、第一手记录之前的行棋方；没有历史时就是当前行棋方。行棋方必须交替，真实虚手记为 `"pass"`。回合标记、落子提示与终局界面由具体技能识别。

`rules` 使用 KataGo 规则名称，`komi` 是实际贴目；不要因默认值方便而忽略界面设定。`session.options["max_visits"]` 沿用现有字符串档位 `"80"`、`"160"`、`"320"`，默认 `"80"`。每次分析有 45 秒上限、可响应停止并核对响应 ID 与行棋方；超时、异常或停止后退出 `with` 清理进程，不继续使用该引擎上下文。接口只支持依次查询，不并发发送。

协议依据：[KataGo 1.18.2 Analysis Engine](https://github.com/lightvector/KataGo/blob/v1.18.2/docs/Analysis_Engine.md)。公共通信代码从已有围棋技能提取，保留复用进程、可中断等待和异常清理。

## 分发与修改范围

公共模块更新随宿主完整包发布；仅安装新技能不能为旧宿主补上这些 API。完整包会复制 `src/`，源码包通过 Python 包发现包含游戏代码，知识文档作为包数据提供。新增或修改公共模块后需要重新构建完整包；已有发行包不会随源码更新而自动变化。宿主不预装技能；公共模块供独立技能复用。

创造技能时只读公共源码并调用，不修改宿主；若公共模块不支持所需规则，明确说明缺口，再按任务范围处理。不要为了一个界面增加公共配置项、复制整个引擎或引入通用插件框架。
