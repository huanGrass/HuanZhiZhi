# 共享外部组件

这里存放用户自行下载的外部程序和模型，供多个技能复用。检测不会自行下载文件；用户点击“一键安装”后，桌宠从支持清单的官方来源下载缺失组件，校验 SHA-256 后安装，保留许可证并重新检测。也可手动准备；不会自动升级或删除其他组件。幻之之维护的[支持清单](../src/huanzhizhi/shared_components.json)随宿主源码发布，设置页面和创建技能的 AI 读取同一份清单；列入清单不表示已经安装。

目前支持 KataGo 1.18.2 的 Windows x64 CUDA 版（围棋），以及皮卡鱼 Pikafish 2026-09-06 的 Windows x64 通用 CPU 版（中国象棋）。皮卡鱼会自动选择适合 CPU 的实现，无需 NVIDIA GPU、CUDA 或 cuDNN。各组件的依赖、配套模型及官方来源见清单，文件路径相对于本目录。

## 如何准备

1. 在“设置 → 外部组件”查看支持的共享组件及其依赖，点击“开始检测”确认本机状态；选中条目可打开官方来源。即使尚未创建技能，也可以检测。
2. 检测未通过时，可点击“一键安装”，页面显示下载大小和进度，支持取消；安装失败可复制报告查看原因并重试。GPU 硬件与驱动不能通过此按钮补齐，选中对应条目打开 NVIDIA 官方入口。也可按清单手动下载，保留配套文件、版权声明和许可证。
3. 一键安装后会自动重新检测；手动放置后点击“重新检测”。仅有文件不代表组件可用；KataGo 的最终检查会短暂使用 GPU，加载模型并实际分析一次棋局。皮卡鱼会检查版本和 UCI 握手、校验 NNUE 模型 SHA-256，再用一个 CPU 线程完成象棋初始局面的深度 4 搜索。技能自身仍需验证业务要求。

目录按组件组织，需要区分版本、平台或后端时使用子目录。例如（仅为存放示例，不表示已安装）：

```text
dependencies/
  README.md
  katago/
    <版本及后端>/
      katago.exe
      ……官方包中的配套文件和许可证
    models/
      ……模型文件和许可证
  pikafish/
    2026-09-06-windows-x64/
      Pikafish-Windows-x86-64-universal.exe
      pikafish.nnue
      Copying.txt
      NNUE-License.md
      README.md
      AUTHORS
```

CUDA 运行库优先使用清单指定的共享目录，随后从环境变量、标准安装目录和 PATH 定位。只给组件子进程设置搜索路径，不修改系统 PATH。

皮卡鱼从[官方 2026-09-06 发布包](https://github.com/official-pikafish/Pikafish/releases/tag/Pikafish-2026-09-06)安装，引擎和模型共用一个约 51 MB 的下载。一键安装使用 Windows 自带的 `tar.exe` 读取 7z，仅提取清单指定的 Windows 引擎、模型和说明文件；若系统缺少支持 7z 的 tar，可手动解压上述文件到指定目录。引擎与模型分别保留 `Copying.txt` 和 `NNUE-License.md`，使用及分发时遵循对应条款。当前仅提供共享组件，象棋页面识别与操作尚未接入。

具体版本和文件名以技能说明为准，不应任意下载最新版替换旧版本。同一份兼容组件可以被多个技能引用；不同版本可以并存。删除某个技能时保留这里的文件，确认没有其他技能使用后再自行清理。

技能自己的配置、日志和业务数据保留在技能约定的位置，不写入共享组件目录。不要将下载的组件复制进技能目录、提交到 Git 或随技能更新包分发。

## 技能开发约定

创建和修改技能只使用已具备的资源，不擅自下载或安装额外依赖；缺失组件时引导用户到“设置 → 外部组件”检测和安装。用户指定的必要组件缺失时，说明阻碍和准备方法，保留草稿；不能擅自替换用户指定的实现方案，或将未完成验证的技能交付。

草稿与已保存技能的目录深度不同，不要根据当前工作目录或技能文件的父目录层数猜测本目录。复用项目已有的解释器定位函数：

```python
from huanzhizhi.python_runtime import python_executable

dependencies_dir = python_executable().parent.parent / "dependencies"
```

技能通过 `describe(..., environment_checks={"组件名称": 检查函数})` 检查自己的实际要求，运行前仍会自动检查。设置中的组件检测不替代技能自身的检查。检查应只读且有合理超时；失败说明应包含具体原因、适用版本、官方来源及放置位置，成功说明应标明实际使用的组件。详细准备步骤放在技能 README 中。若技能允许用户指定已有安装位置，应使用该技能明确提供的方式，不依赖开发者机器上的固定绝对路径。

皮卡鱼检测可复用 `huanzhizhi.shared_components.check_pikafish(component, root)`，从 `supported_components()` 取 `id == 'pikafish'` 的条目，消费全部检测结果并检查每项 `satisfied`。引擎和模型位置分别为 `root / 'dependencies' / component['engine']`、`root / 'dependencies' / component['model']`；通信协议见清单的 `protocol` 官方文档。
