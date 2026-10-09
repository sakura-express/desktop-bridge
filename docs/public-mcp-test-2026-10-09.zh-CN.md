# 公网 Windows MCP 实测（2026-10-09）

目标：`https://indianapolis-rebates-letters-informational.trycloudflare.com/mcp`。
经 owner 登录及 OAuth/PKCE 获取测试授权；凭据未写入脚本、报告或截图文件。

## 结论

公网 Windows 原生输入链路可用，已验证连续键盘操作和实际侧栏导航；新加入的结构化观察链路在此部署中不可用，需要继续定位。此测试直接调用 MCP，不是 NekoCode 模型自主完成任务的评测，不能据此保证原模型会选择正确工具。

| 测试 | 结果 | 证据 |
| --- | --- | --- |
| 健康检查 / OAuth / 初始化 / 工具列表 | 通过 | Windows/native，desktop_snapshot 已在列表中 |
| Win+E 打开文件管理器 | 通过 | 出现 Home 窗口 |
| 点击左侧 Downloads | 通过 | 窗口标题和路径切换为 Downloads，侧栏项被选中 |
| 打开已有下载软件 | 未测试 | 此部署的 Downloads 目录为空 |
| 同 action_id、同参数重试 | 通过 | 返回 replayed: true，没有重复执行 |
| 新 action_id 使用旧观察 | 通过 | 返回 STALE_OBSERVATION，拒绝动作 |
| Win+R → notepad → Enter | 通过 | 出现新记事本窗口 |
| 英文输入 / Ctrl+A / 中文替换 | 通过 | 最终显示 MCP 中文输入测试 456 |
| desktop_snapshot | 失败 | Chrome、Explorer、Notepad 共 5 次，均超时后返回 DESKTOP_ERROR |

结构化观察耗时为 6510、6416、6457、6439、6385 ms（包含公网往返）。均与后端 6 秒子进程超时一致；但当前远端错误隐藏了具体异常，不能仅根据耗时确定卡在初始化、辅助代码编译还是 UIA provider 读取。

截图的端到端返回耗时约 0.43–1.41 秒；此数值不包含模型识图和生成下一步的耗时。此部署上“先结构化观察、失败后截图”目前反而额外增加约 6 秒等待。

## 额外发现

1. 输入发送成功不等于界面已经渲染完成。中文替换后紧接着获取的截图只出现前缀 MCP，后续复查才显示完整文本。需要根据目标状态轮询验证，不能只凭第一张图断定失败或重复输入。
2. MCP session_status/session_start 报告固定 1280×800；实际桌面及截图为 1024×768。操作必须以当前观察中的坐标信息为准。这个元数据不一致问题尚未在本次修改中修复。
3. 远端 coding_exec_command 拒绝内联 Python 诊断，返回 PERMISSION_REQUIRED，因此没有运行该脚本，也没有修改远端权限配置。

## 本地补充与后续验证

在 windows_snapshot.ps1 中加入固定阶段标签，在 windows.py 中将子进程超时区分为 UIA_TIMEOUT 并返回最后已进入的阶段。阶段包括 start_powershell、load_assemblies、compile_dpi_helper、read_foreground_root、cache_root_properties、walk_controls、serialize_result；不返回 provider 文本或窗口内容。

此诊断补丁仅在本地，未部署到上述公网服务。更新运行服务后重新执行 desktop_snapshot，依据具体阶段继续定位；在成功读取控件并完成一次 Downloads 导航验证前，不能宣布结构化观察已验收。

新增 scripts/public_probe.py 可通过 stdin JSON 指令重放测试，凭据仅在进程内使用。测试截图放在被 Git 忽略的 probe-output/。本地针对 Windows 后端和结构化快照的 23 项测试及 Ruff 检查通过。

## 截图证据

- 下载目录：../probe-output/downloads-opened.png
- 完整中文输入：../probe-output/notepad-unicode.png

测试使用新打开的 Explorer 和空白记事本；结束时关闭这两个窗口，丢弃仅由测试产生的未保存文本，并停止测试控制。
