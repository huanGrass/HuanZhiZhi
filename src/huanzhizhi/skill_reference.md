# 技能接口参考

按[知识索引](skill_creation.md)选读相关章节；本文件不扩大当前阶段的操作范围。

## 接口与组件

路径相对于项目根目录，只读本次需要的条目。

- **技能清单与事件**：每个技能同时交付 `skill.json` 和 `task.py`。清单是 ID、说明、展示方式、参数定义和环境检查名称的唯一来源；宿主直接读 JSON，不执行技能。SDK `src/huanzhizhi/task_sdk.py` 的 `describe(__file__, environment_checks=...)` 处理运行前环境检查及兼容的 `--describe` 输出；`TaskSession(__file__)` 从同一清单读取 ID、参数默认值和运行时可调范围，通过 `session.skill["id"]` 获取名称（旧代码读取 `name` 时由 SDK 返回同一值）。不要在 Python 中重复定义元数据或向 `TaskSession` 传参数定义。技能只持久化 id，不再保存 name，id 与目录名使用完全相同的中文技能名称；手动改目录后宿主同步 id 并迁移设置；名称只用中文、字母、数字、下划线或连字符，以中文、字母或数字开头且必须含中文。修改已有技能时保留原值。
- **入口示例**：SDK 中 `describe` 的文档给出最小接法；优先参考相关已安装技能的 task.py。窗口截图、组件定位与验证脚本见[常用调用示例](skill_examples.md)。
- **共享游戏逻辑**：扫雷、围棋与象棋先读[游戏数据格式与调用示例](game_examples.md)，按需导入 `huanzhizhi.games.minesweeper`、`go` 或 `xiangqi`，不把规则、求解器或引擎通信代码复制进技能。扫雷已有确定性推理与有界概率搜索；围棋已有落子/提子校验、可复用的 KataGo 进程及 `go.play` 对弈流程，统一处理执黑/执白、自动/提示切换、回合推进、落子核对与停止。象棋已有共享皮卡鱼客户端 `xiangqi.Engine` 和自动执红流程 `xiangqi.Game`，保留历史续接、唯一合法走法核对和连续点击间的性能优化。技能提供网站观察、坐标及输入适配、页面规则与终局反馈，不重复实现公共流程；使用普通函数或具体对象，不新增适配器基类。公共模块不是通用图像识别器，也不保证仅凭一张截图恢复完整棋谱；不支持的模式和规则需明确说明。
- **共享外部组件**：[支持清单](shared_components.json)与设置中的“外部组件”共用，包含组件用途、版本、文件位置、依赖和官方调用协议；列入清单不代表本机已经安装。编写业务策略前先读清单，筛选相关组件并阅读说明、完成最小实际调用；适用且可用时优先复用，不直接另写简化策略。清单的文件路径相对于 `dependencies/`；当前 KataGo 的路径与子进程环境可复用 `shared_components.katago_paths`，实际检查可复用 `check_katago`（逐项返回结果，调用者须检查所有结果）。皮卡鱼可复用 `check_pikafish(component, root)`，同样须消费全部结果；引擎和 NNUE 路径直接读取清单的 `engine`、`model` 字段，相对于 `root / "dependencies"`。KataGo 检查短暂使用 GPU，皮卡鱼使用 CPU；只读共享文件，临时配置和日志自动清理。
  用户手动准备或通过“设置 → 外部组件”一键安装的外部程序和模型放在项目或发行包根目录的 `dependencies/`，规则见 [目录说明](../../dependencies/README.md)。用 `huanzhizhi.python_runtime.python_executable().parent.parent / "dependencies"` 定位，不能依赖草稿目录层级、当前工作目录或开发机绝对路径。只使用已具备的资源，不下载、安装或修改共享组件；用户指定的必要组件缺失时说明阻碍、引导用户到“设置 → 外部组件”检测并安装，保留草稿，不擅自替换方案或交付未验证技能。多个技能可引用同一份兼容组件，不复制进技能目录；配置、日志和业务数据不写入共享目录。技能 README 和依赖检查的失败说明应提供适用版本、官方来源及放置位置，供使用已有技能的用户自行准备。
- **额外依赖**：在 `skill.json` 的 `environment_checks` 中仅列出实际需要的检查名称，并在 `describe(__file__, environment_checks={"依赖名称": 检查函数})` 中提供同名函数，名称集合必须完全一致。函数无参数，只读检查实际要求（版本、导入、GPU 运算或引擎启动），满足时返回非空说明，不满足时抛出具体错误；不得安装组件、修改配置、操作窗口或向 stdout 打印日志。额外依赖在函数内导入。SDK 在 `--run` 的业务逻辑前检查，失败即退出；`--check-environment` 单独调用同一组检查。列表读取清单时不运行检查。
- **目标窗口、截图与输入**：`src/huanzhizhi/platform/target_window.py` 提供窗口查找、刷新、客户区截图和输入接口；底层截图见 `src/huanzhizhi/platform/window_capture.py`。点击只使用 `Win32DesktopApi.post_click(info, x, y, action='click')`，坐标为客户区像素；`action` 还支持 `right_click`、`double_click`。接口自动报告桌宠点击动画，不额外调用 `emit_point`，不使用旧的 `post_left_click`。正式运行通过 `TaskSession` 连接桌宠，分析调试时由宿主自动提供点击事件通道；未连接时接口报错，不静默执行无动画点击。依据当前画面计算坐标，并检查实际操作结果。
- **图像识别**：`src/huanzhizhi/vision/`；按需查看 `src/huanzhizhi/vision/__init__.py` 导出的方法和对应实现。[通用能力参考](../../docs/capabilities.md)提供方法选型和用途表，其中旧探索协议章节用于兼容维护。
- **运行约定**：`src/huanzhizhi/tasks/catalog.py`、`src/huanzhizhi/tasks/process.py` 和 `src/huanzhizhi/script_runner.py` 是现成加载运行层，不修改。使用普通 Python 和现有依赖进行识别、计算及调试。元数据校验用 `TaskCatalog(Path("output")).reload()` 直接读取清单，不以执行入口作为元数据来源；技能实机验证统一使用下面的流程。

## skill.json 与入口最小示例

目录 `数独留一格/` 中的 `skill.json`：

```json
{
  "protocol_version": 1,
  "id": "数独留一格",
  "description": "核对并填写数独，保留最后一格。",
  "parameters": []
}
```

同目录的 `task.py`：

```python
from huanzhizhi.task_sdk import describe, TaskSession

if __name__ == "__main__":
    if describe(__file__):
        raise SystemExit(0)
    session = TaskSession(__file__)
    from controller import run
    run(session)
```

清单可选字段：`presentation`（`persistent` 或 `launch_only`）、`debug_steps`、`parameters`、`environment_checks`。参数结构沿用 `src/huanzhizhi/parameters.py` 的整数/选项规则；只有 `runtime: true` 的参数可以运行中调整。缺少清单、重复 JSON 字段、非法值或 ID 与目录不符都会拒绝加载，不从 Python 回退推导。草稿实机验证也需要在入口所在的同名技能目录内准备清单；最终交付前只在 working/草稿目录开发，不提前写入 output。

## 技能内运行数据

`session.runtime_dir` 是技能根目录下 `.runtime/` 的绝对 `Path`，不依赖当前工作目录。辅助脚本可使用 `from huanzhizhi.task_sdk import runtime_directory`，并传入该技能的 `task.py` 路径。接口只定位，不创建目录或读取旧状态；按实际需要创建子目录：

```python
state = session.runtime_dir / 'state'
state.mkdir(parents=True, exist_ok=True)
checkpoint = state / 'checkpoint.json'
```

截图、详细日志和诊断写入 `.runtime/runs/<本次运行>/`；可重建缓存写入 `.runtime/cache/`；必须保留的用户数据、续局状态写入 `.runtime/state/`，不得作为缓存清理。临时文件也应位于 `.runtime/` 内。普通文本日志继续使用 `session.emit_log()`。代码、默认配置、识别模板、许可证等必需资源保留在技能目录其他位置，读取资源仍可相对于 `__file__`。

宿主导出和交付复制仅排除技能根目录的 `.runtime/` 及 Python 字节码，不按图片或 JSON 后缀排除资源。本地完整备份、重命名和转正继续携带 `.runtime/`。修改工作副本不复制原有运行数据；草稿验证独立生成数据，保存时保留原安装技能的 `.runtime/`，不采用草稿数据。不要为了复现旧状态擅自访问安装目录；需要的测试输入由本次授权范围决定。

旧版独立 EXE 共用安装目录时，路径为其旁边的 `.runtime/<EXE文件名去后缀>/`，避免不同程序共用状态。普通 Python 技能始终使用自己目录内的 `.runtime/`。

## 技能实机验证

运行任务需支持暂停：在主工作线程的安全边界调用 `session.wait_if_paused()`，放在上一项操作结果确认之后、下一轮截图或读取状态之前；返回 `True` 表示发生过暂停，须清除过期画面并重新核对后再输入。连点循环可用 `session.pause_requested` 提前结束本批，但必须先确认已发出的操作，再进入暂停点。不要在鼠标按下与松开之间、一次业务操作的中间或 GUI 事件线程中阻塞；任务自己的采集线程可传 `cancel_event=线程退出事件`，保证关闭窗口也能退出暂停等待。暂停等待会继续应用运行参数并响应停止；返回后仍须检查 `session.stop_requested`。长计算期间宿主显示“暂停中”，直到安全点确认；不要伪报“已暂停”。

创建和修改技能均通过 `huanzhizhi.tasks.process.TaskProcessManager` 启动草稿入口，与桌宠正式运行共用解释器、工作目录、start/stop 协议、事件解析和进程清理。不要使用 `tty=true` 的交互式终端直接执行技能 `--run`，不要手写协议或另写进程启动器。截图、识别和求解等不启动完整技能的离线脚本可直接运行。

在本次草稿内复用[验证脚本示例](skill_examples.md#集中验证)，按以下顺序调用现有接口；只有示例未覆盖的接口问题才查阅 `tests/test_task_process.py`：

1. 创建 `QtCore.QCoreApplication([])` 和 `TaskProcessManager()`，连接 `event_received`、`error`、`state_changed`，保留实际事件与错误记录。
2. 调用 `manager.start(技能真实ID, Path(草稿入口绝对路径), 当次参数字典, input_mode=os.environ.get('HUANZHIZHI_INPUT_MODE', 'background'))`，沿用用户本轮选择的输入模式；保留 `HUANZHIZHI_CLICK_EVENTS`，前台点击和拖拽通过宿主动画完成后才返回。未完成验证的新技能使用 `task_draft.py` 等草稿入口，修改技能使用 `working` 内的入口，不提前生成交付入口。
3. 通过 `app.exec()` 驱动事件循环。按本次目标设置合理的验证时限，超时或需要停止时调用 `manager.stop()`。等 `state_changed` 返回 `idle` 后再退出事件循环，不在收到 `FinishedEvent` 时提前退出，以便完整检查进程退出错误。
4. 在 `finally` 中调用 `manager.close()`。结合 `FinishedEvent.error`、`stopped_by_request`、运行错误及实际画面判断结果；超时停止、正常退出或收到 finished 均不能直接当作目标完成。

用项目解释器通过 `huanzhizhi.script_runner` 执行验证脚本的绝对路径，命令工具设置 `tty=false`，沿用宿主提供的点击事件通道。启动失败时保留具体错误并检查真实调用链，不将一次进程启动错误直接解释为用户缺少权限。

支持连续请求的引擎与模型在技能启动时初始化，循环中复用，结束或停止时清理；等待须可中断并有合理超时。自带资源按技能目录相对定位，不依赖临时开发目录或其他技能目录。
