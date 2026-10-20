# 共享游戏数据与调用示例

公共代码位于 `src/huanzhizhi/games/`，与宿主一起提供。按需导入，不启动服务、不自动下载组件。技能可以在任意草稿目录运行，不依赖另一个技能目录。当前提供扫雷、围棋和象棋模块，不需要注册表、工厂或适配器基类。

新技能先完成“截图 → 棋盘数据”的适配，再调用下面的公共逻辑。窗口选择、坐标、界面规则与回合识别、实际输入及网站特有的结束条件由技能负责；围棋和象棋还可复用下述对弈流程，处理各自已支持的回合推进和落子核对。先用静态数据验证识别，再通过现有任务运行链验证实际效果；公共求解正确不等于新界面已适配。

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

### 复用对弈流程

`go.play(session, *, observe, click, point, diagnostic, rules='chinese', komi=7.5)` 提供执黑/执白、自动/提示模式、回合推进、实际棋谱记录、KataGo 分析及落子核对。直接传三个普通函数或绑定方法，不需要继承接口、注册适配器或创建工厂：

```python
go.play(session, observe=site.observe, click=site.click, point=site.point,
        diagnostic=diagnostic, rules="chinese", komi=7.5)
```

`site` 是技能自身保存窗口、最新截图、坐标和采样状态的对象，也可以使用普通函数保存这些状态。三个回调的约定：

- `observe(validate=None, *, changed_only=False, retries=3, timeout=None)`：获取当前画面，识别并返回 `(board, last_color, result)`。`last_color` 为最后确认落子的颜色：`0` 不确定、`1` 黑、`2` 白；不能用棋子数量猜测。传入 `validate` 时，对当前识别调用 `validate(board, last_color)`，把其返回值作为 `result`。复用 SDK `confirm_observation`，只对 `ObservationPending` 重新截图复检，保持同一参考局面，并遵守传入的重试次数、超时和停止请求。窗口、权限和输入等明确错误直接抛出。
- 只有 `changed_only=True` 且画面未变、窗口与坐标仍有效时，`observe` 才能返回 `None` 来跳过完整识别；否则必须确认当前棋盘。每次成功观察更新轻量变化检测的参考，分析后、模式切换后或暂停恢复后的强制观察不得使用这个捷径。失败复检必须获取新画面，不能把旧帧重复当作新证据。
- `point(x, y)`：将棋盘索引转换为当前屏幕坐标，供公共流程显示提示。`click(x, y)`：使用最近确认的窗口和客户区坐标，通过宿主发送一次点击；保留窗口有效性及停止检查，不自行重复点击。

`session.options` 沿用 `color`（`B`/`W`，启动时确定）、`mode`（`auto`/`hint`，运行时切换）与 `max_visits`。模式切换、暂停恢复后先重新确认局面；提示模式允许用户选择其他合法落点，没有手动落子的等待超时。自动落子只发送一次，随后只读确认结果，兼容已包含对手快速应手的画面；己方结果确认最多 25 次、约 5 秒，普通确认最多 4 次。分析期间发生的模式切换在下次点击前应用，已发出的点击先完成结果确认。

`diagnostic` 是技能维护的普通字典：公共流程填写阶段、参考棋盘、预期结果、实际走法与运行参数；适配函数补充窗口、截图和识别证据。异常向调用方抛出，由技能保存已有画面与诊断，不为保存证据重新截图或操作。公共流程负责正常停止、虚手建议的完成通知，以及引擎清理；KataGo 建议虚手时结束，不自动虚手、重开或认输。

这一流程仍需要网站可靠识别棋盘及末手颜色，不自动恢复接手前的完整棋谱，也不通用识别终局页面。使用默认规则和贴目前仍须确认目标网站设定。

## 象棋

`from huanzhizhi.games import xiangqi`

`START` 是标准开局的 90 格列表，从左上角逐行排列，每行 9 格：`.` 为空，`rnbakcp` 表示黑方车、马、象、士、将、炮、卒，大写为红方。走法用皮卡鱼坐标，例如 `b2e2`。`apply_move(board, move)` 返回移动或吃子后的副本，检查起点非空、目标不是己方；它不是完整规则引擎，调用前必须用 `Engine.legal(moves)` 核对合法性。

`xiangqi.check_engine()` 复用共享组件的引擎、模型和实际搜索检查，用于 `describe(__file__, environment_checks={"皮卡鱼": xiangqi.check_engine})`。`Engine(session, record)` 沿用共享依赖路径，整局复用一个皮卡鱼进程与 NNUE；`legal(moves)` 查询合法走法，`search(moves)` 搜索 500 毫秒并返回走法或 `None`，`close()` 回收线程和管道。请求按顺序发送，等待可停止、单次响应有 12 秒上限；必须在 `finally` 中关闭。

`Game(session, view, *, checkpoint_file)` 从已确认的标准开局历史继续自动执红，负责历史重放与持久化、暂停恢复核对、回合推进、搜索、选中确认、落子及快速应手核对、超时和停止。当前没有增加执黑、提示模式、任意 FEN 接管或自动重开功能。`view` 是技能自己的具体对象，不继承公共基类，不注册到框架：

```python
state = session.runtime_dir / "state"
state.mkdir(parents=True, exist_ok=True)
game = xiangqi.Game(session, view, checkpoint_file=state / "checkpoint.json")
try:
    game.run()
except xiangqi.GameEnded as result:
    session.emit_finished("棋局结束", "本局对弈已结束", str(result))
finally:
    game.close()
    view.close()  # 技能自己的日志等资源；停止与异常通知沿用任务 SDK。
```

适配对象只负责以下网站和证据操作，方法详情见 `Game` 文档字符串：

- `observe(after=None)` / `stable()` 返回 `(evidence, board, layout, selected)`：证据对象不由公共模块解析，棋盘须归一为上述标准棋子；`layout` 是可比较的输入映射及相关状态快照，坐标或影响点击的网页标记变化时必须不同；`selected` 为 90 个选中标记。`observe(after=...)` 必须使用该时间戳之后的画面。不要通过棋子归一化隐藏网页终局标记等输入状态变化。
- `initial_history(observation)` 返回可见走法、总半回合数、是否可见开局起始标记、诊断依据；先核实页面方向、玩家颜色及开局设置。`history(observation)` 返回可见走法和总半回合数。完整历史验证、检查点续接与合法性重放由公共模块执行；缺失或冲突时停止，不编造历史。
- `click(layout, square, *, ply)` 把棋盘坐标转换为客户区像素，检查停止与窗口有效性后调用宿主输入。`finish_reason(observation, moves, legal)` 核实网页终局标记与引擎结果一致，再返回网站特有的结束原因或 `None`。四次重复、无进攻子力等当前网页规则留在网站适配中，不作为通用象棋规则。
- `record(event, **data)`、`record_frame(observation)`、`mark_move(observation, move, ply)` 保留技能自己的日志和截图留证。预标注在点选之前，正常落子留证在确认之后；点选到落点之间不保存截图。

启动和暂停恢复时核对网页棋谱；正常回合由完整棋盘及唯一合法走法推进，不反复读取文字棋谱。点选后一次新鲜、明确的观察同时核对棋盘、输入映射和唯一选中棋子；随后检查停止再点击落点，不额外重复全盘识别。模糊反馈只观察、限时等待，不能误点；确认超时不重复点击。网站的识别阈值、截图区域、窗口规则和错误截图仍在独立技能中。

## 分发与修改范围

公共模块更新随宿主完整包发布；仅安装新技能不能为旧宿主补上这些 API。完整包会复制 `src/`，源码包通过 Python 包发现包含游戏代码，知识文档作为包数据提供。新增或修改公共模块后需要重新构建完整包；已有发行包不会随源码更新而自动变化。宿主不预装技能；公共模块供独立技能复用。

创造技能时只读公共源码并调用，不修改宿主；若公共模块不支持所需规则，明确说明缺口，再按任务范围处理。不要为了一个界面增加公共配置项、复制整个引擎或引入通用插件框架。
