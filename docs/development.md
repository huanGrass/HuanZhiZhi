# 开发与发布

## 范围和环境

本仓库是唯一宿主开发来源，只包含宿主、文档、宿主测试和构建脚本。`tasks/` 和 `test_tasks/` 仅为本地技能运行目录，均不提交 Git。公开技能在独立仓库维护；内部技能与私有测试素材另行保存。运行环境、共享组件下载、日志和验证产物也不提交 Git。

宿主的 `src/huanzhizhi/games/` 包含扫雷、围棋、象棋公共模块，接口与示例见[共享游戏开发说明](../src/huanzhizhi/game_examples.md)。公共模块不依赖开发目录或其他技能；只同步公共代码及独立测试，不复制具体技能或引擎模型。修改公共代码后需要重新构建完整包，原有 ZIP 不会自动更新。

使用 Windows Python 3.11 x64 准备环境，当前基线是 3.11.9：

```powershell
python scripts/prepare_python.py
.\python\python.exe -m pip check
```

依赖固定在 `requirements.txt`。Qt 使用 PySide6-Essentials，完整包只保留 Widgets、SVG、测试及对应的基础运行模块；不包含 QML、WebEngine、PDF、虚拟键盘或 Qt 开发工具。完整包保留的模块清单在 `scripts/package_support.py` 的 `QT_MODULES`。修改模块清单时，同时检查依赖、许可和真实运行结果。

完整包排除 CPython 自身的 `python/Lib/test/` 回归测试及证书样本，保留 `unittest` 等正常标准库功能；复制与兼容性指纹使用同一过滤规则。

## 验证

宿主测试按模块使用独立进程，避免 Qt 窗口、定时器在测试模块间残留。砖了个砖的截图、标注数据和技能回归测试仅保留在独立内部技能目录，不纳入公开仓库或发行包。整套宿主测试在同一进程中曾出现 Windows 异常退出，尚未定位，不能视为已通过：

```powershell
$env:QT_QPA_PLATFORM = 'offscreen'
foreach ($test in Get-ChildItem tests/test_*.py) {
    .\python\python.exe -X utf8 -m unittest "tests.$($test.BaseName)" -q
    if ($LASTEXITCODE -ne 0) { throw "测试失败：$($test.Name)" }
}
```

Windows 原生窗口恢复测试需要 `QT_QPA_PLATFORM=windows`，无界面测试中会跳过。原生测试只操作自身测试窗口。真实游戏表现、首次 AI 登录/调用以及没有安装 Python 的干净 Windows 仍需单独验证；离线或模拟测试不能替代这些验证。

`verify_capabilities.py` 用于独立测试窗口的 OCR 和输入动作验证，运行前阅读其参数。旧探索流程保留通用协议兼容，不提供依赖内部技能的专用动作。

## 完整包

安装包根目录的 `README.md` 使用 `docs/portable-readme.md`，源码仓库的 README 说明双击启动和首次自动安装。

完整包自动编译 `幻之之.exe` 启动器，源码位于 `scripts/launcher.cs` 和 `scripts/setup_environment.cs`。源码仓库跟踪根目录的启动器；修改启动器后，先用 `python/python.exe scripts/build_launcher.py --output build/幻之之.exe` 生成新文件，验证后替换根目录版本。构建使用 Windows 系统的 `.NET Framework 4.x` C# 编译器（`Framework64/v4.0.30319/csc.exe`），不需要安装 .NET SDK 或 PyInstaller；图标由现有应用 PNG 转换生成。启动器定位自身目录中的 `python/pythonw.exe`，不依赖当前工作目录或系统 Python；单实例仍由宿主负责。

`dist/<版本>/` 只放最终交付的 ZIP、校验值和验证记录。未压缩构建与下载缓存放在 `build/`，验证日志放在 `artifacts/`。构建输出目录必须不存在，重建前先移走旧输出：

```powershell
.\python\python.exe -X utf8 scripts/build_package.py --output build/portable-0.2.0
.\python\python.exe -X utf8 scripts/verify_package.py build/portable-0.2.0 --report-dir artifacts/portable-check
```

完整包默认不附带技能。显式指定技能时，仅打包其运行代码、素材和说明。完整包与单技能更新共用 `task_ignore` 规则，排除技能根目录 `.runtime/`、`tests/` 和 Python 缓存，运行素材正常复制。不复制 `test_tasks/`、本机外部引擎/模型、历史日志或草稿；`dependencies/` 仅附 README。`LICENSE`、`licenses/`、文档和 AI 开发知识随包复制。`build-info.json` 记录版本、依赖、技能及宿主/运行环境校验值。

现有代码仅处理静态截图，发行包排除 OpenCV 的 FFmpeg 视频插件及可选 Mesa 软件 OpenGL 渲染器；不承诺这两项可选能力。

对应的 Qt、PySide/Shiboken 源码附件必须与完整包一同发布。按固定清单下载并校验，压缩为 `HuanZhiZhi-0.2.0-third-party-sources.zip`：

```powershell
.\python\python.exe -X utf8 scripts/fetch_license_sources.py
```

下载缓存位于 `build/third-party-sources/`；清单位于 `licenses/source-archives.json`，包含上游地址和 SHA-256。源码附件保留原始压缩包，不执行其中的代码。更新依赖版本时同时更新附件和声明。

Qt 过滤规则同时用于复制和兼容性指纹，因此未分发的开发工具不会导致单技能更新误判。运行时不执行这些指纹校验，不限制用户替换接口兼容的库；校验仅用于构建更新和验证。

验证覆盖空技能宿主初始化、主题资源解码、公共模块与动态脚本；技能协议使用最小 package_probe 样本验证，不依赖真实游戏，不执行游戏点击。验证会在待测包产生 `logs/`、`artifacts/` 和字节码缓存。正式压缩时使用同一源码和环境重新构建的干净输出，不将验证后的运行数据一并发布。

完整包默认不附带技能；仅在显式传入 `--tasks 技能名称` 时从本地安装目录收录。不要使用内部技能构建公开包。

## 单技能更新

只对已在 `tasks/` 的正式技能打包，不能直接从 `test_tasks/` 读取。基础包、宿主和共享环境必须兼容：

```powershell
.\python\python.exe -X utf8 scripts/build_package.py --task 砖了个砖提示 --base-bundle build/portable-0.2.0 --output build/brick-update-0.2.0
.\python\python.exe -X utf8 scripts/verify_package.py build/portable-0.2.0 --updates build/brick-update-0.2.0 --report-dir artifacts/update-check
```

更新验证在报告目录的副本上执行，不修改基础包。用户先停止技能并备份旧目录，再替换运行文件并保留原技能 `.runtime/`，不要用更新包覆盖本机运行状态；旧 PyInstaller 包需要完整升级。宿主或公共依赖有变化时重新发布完整包。

## 发布前

1. 检查 Git 差异，只包含公开内容；确认没有其他技能、账号文件、开发日志或本机路径。
2. 更新版本号、CHANGELOG，运行依赖检查和测试。
3. 检查实际分发组件与 `licenses/`，保留许可证和版权信息；按第三方声明准备对应源码下载附件。
4. 构建并验证完整包与单技能更新，检查 ZIP 顶层结构和校验值。
5. 在干净 Windows 验证空技能宿主启动、停止/重启，以及安装公开技能、配置 AI 后创建、修改和转正。
6. 确定发布后才提交、推送、打版本标签并上传 Releases。源码仓库不放 `python/`，完整包放 Releases。

## 动态脚本和技能

宿主直接读取 `skill.json` 获取元数据，通过 `--run` 在独立进程启动，不导入技能业务模块。新技能只保存 id，与目录名一致且包含中文；入口使用 `describe(__file__)` 和 `TaskSession(__file__)`。参数必须由技能自行校验。

普通脚本需要导入同目录模块时使用：

```powershell
.\python\python.exe -I -X utf8 -m huanzhizhi.script_runner path/to/script.py
```

`python -m pip` 可用于开发环境；不要使用 `Scripts/*.exe` 启动器，其路径可能指向构建机器。新增依赖时同步更新版本清单与声明，并重新验证完整包。基础发行包只承诺提供清单内的依赖。

## 日常协作

宿主源码在本仓库修改、测试和提交，不再与另一份宿主目录双向同步。公开技能单独提交；内部技能库不配置公开远程。宿主仓库的 `/tasks/` 忽略规则不承担技能备份职责。

测试模块中的 Qt 全局对象会相互影响，完整回归使用独立进程逐模块运行，保留每个退出码；单个模块可运行 `python/python.exe -m unittest tests.test_task_catalog -q`。游戏截图和技能专属测试由对应技能的私有测试目录管理，宿主仅保留协议及公共模块测试。

发布前核对已跟踪文件和构建清单；仅有 `.gitignore` 不会移除已经跟踪的内容。提交、推送和发布是分别进行的步骤。

## 云端技能目录

「技能列表 → 云端」按需读取 [HuanZhiZhi-Skills](https://github.com/huanGrass/HuanZhiZhi-Skills) 的 `main/catalog.json`，仅打开该页或点击刷新时联网。分类由清单提供；本地技能仍可离线使用。

发布技能时，先将以技能名为顶层目录的 ZIP（含运行文件及许可证）提交到技能仓库，再更新清单：`format: 1`，`skills` 条目包含 `id`、`category`、`description`、`version`、`size`、`sha256` 和 `url`。下载 URL 必须指向该仓库固定的 40 位提交下的 `packages/*.zip`，避免包内容随分支变更。公开仓库只放公开技能，不包含内部技能、测试截图或账号资料。

宿主复用组件下载和解压逻辑，检查包大小、SHA-256、路径、清单及 Python 语法后发布到 `tasks/` 并刷新列表。安装不执行技能代码，不覆盖同名目录或旧版可执行技能；运行或开发技能时暂停安装入口。

## 首次启动安装

启动器检查项目内 Python 和必要模块；环境完整时直接启动，缺失时经用户确认后显示原生安装窗口。下载使用官方 Python 3.11.9 x64 嵌入包和 pip 24.0 wheel，并校验固定 SHA-256；依赖按 `requirements.txt` 优先从清华 PyPI 镜像安装，失败后自动改用 PyPI 官方源；仅允许二进制 wheel。取消安装不会触发换源，两个源都失败时保留错误日志并允许重试。开发准备脚本采用相同顺序。无需系统 Python 或管理员权限，项目目录需要可写。下载显示字节进度，依赖安装显示阶段和 pip 日志，支持取消及失败重试。

安装先在项目下的唯一临时目录完成并验证，成功后才移入 `python/`；已有不完整环境保留为 `python.backup-<随机标识>/`，用户确认不再需要后可自行移除。安装不修改系统 PATH。运行时目录结构和隔离路径与 `prepare_python.py` 一致，开发环境仍可使用该脚本。

## 关于页面与更新检查

「设置 → 关于幻之之」直接读取随源码和发行包保留的 `pyproject.toml` 版本。仅点击检查按钮时请求官方仓库 GitHub Releases API，不自动下载或覆盖安装；关闭设置会取消请求。正式发行版标签使用 `v主版本.次版本.修订号`（也支持省略 v），忽略草稿和预发布版本。新版本入口打开对应发行版页面，更新说明按纯文本显示。

无正式发行版、已是最新、发现更新以及网络错误分别显示；限流、超时或响应异常不会报告为“已是最新版”。项目地址与下载页均通过系统浏览器打开，群号可复制，下载失败时提示前往 QQ 群查看群文件。
