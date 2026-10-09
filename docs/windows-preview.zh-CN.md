# 实验性 Windows MCP preview

**Launch MCP preview** 的 `runner` 可以选择 `windows-2025` 或 `windows-2022`（x64）。服务使用真实 Windows 桌面，不需要 Docker、VNC 或额外下载 Playwright 浏览器。

## 运行

1. 更新默认分支，在 repository Actions secrets 配置 `BRIDGE_OWNER_TOKEN`（8–256 字符）。
2. 打开 **Launch MCP preview → Run workflow**，选择 Windows runner。首次建议用 `mode=verify`：执行实际验收后自动结束。交互预览使用 `mode=preview`，其余 `tunnel`、`minutes`、`public_url` 与 Linux/macOS 相同。
3. 安装 Python 3.12、固定版本依赖，以及校验 SHA-256 的 Windows x64 cloudflared 2026.9.3。使用 runner 预装 Chrome，独立临时 profile，CDP 绑定 loopback 动态端口并从本次 profile 的 `DevToolsActivePort` 获取地址。
4. 检查 Windows 原生后端、上下文文件 IO、OAuth 和 viewer 回归，再运行桌面预检。只有一个显示器且 Default 桌面可交互时才继续；锁屏、服务会话、截图失败或尺寸不一致会失败并输出原因。
5. MCP 服务绑定 `127.0.0.1:8080`，公网隧道通过 OAuth/PKCE 验证截图、浏览器、终端、PNG WebSocket，以及实际 MCP 点击、中文输入、Ctrl+A、滚动和拖动。测试使用本地临时 HTML 色标和 DOM 断言；Playwright 只准备并读取测试页面，输入由 Win32 后端执行。
6. 验收通过后重启服务清除测试授权，Job Summary 显示桌面登录 URL 和 `/mcp`。使用 owner token 登录网页，MCP 客户端走 OAuth。

## 桌面与文件

Windows 现在提供 `desktop_snapshot`：通过系统 UI Automation 读取当前前台窗口，返回控件名称、角色、父子关系、可用/焦点状态与 `[x,y,width,height]` 物理像素边界，以及新的 `observation_id`。此工具只返回文本，不生成 PNG，也不调用 Chromium CDP。脚本使用系统 Windows PowerShell/.NET，无需额外安装依赖。

原生应用的推荐操作顺序为 `desktop_snapshot → desktop_action → desktop_snapshot`。例如打开下载目录时，先寻找文件管理器侧栏的“下载/Downloads/Download”，使用对应控件的边界中心点击，再读取目录列表验证；不要把目录名直接当成搜索词。每个动作都会使旧观察失效，需要新 `observation_id` 和新的 `action_id`。`input_submitted` 只表示输入发送成功，不能当成软件已打开的证据。

快照只覆盖前台窗口，ID 只用于说明本次树结构；动作仍使用坐标，不支持通过 ID 调用控件。读取采用属性缓存，限制遍历时间、节点数和深度，超限返回 `truncated: true`。控件缺失、自绘界面、读取失败或需要视觉信息时使用 `desktop_screenshot`。焦点窗口或屏幕几何在读取期间改变会使快照失败，避免返回错配的坐标。读取进程最长 6 秒后终止；耗时字段 `elapsed_ms` 方便实测。

更新服务后，在 NekoCode 中重新连接该 MCP 并开启新会话，使模型拿到新增工具。工具操作策略直接写入工具描述；当前 NekoCode MCP 接入不会把服务器初始化返回的 `instructions` 自动加入模型提示词。人类 viewer 的 PNG 实时画面仍会独立生成，结构化观察替换的是 AI 的观察链路。

截图使用 Pillow ImageGrab，输入使用 Win32 `SendInput` / `SetCursorPos`。服务和工作线程使用 Per Monitor V2 DPI 上下文，坐标直接对应截图物理像素；屏幕几何改变后需要重新截图。`dy` 正数向下，滚轮单位为 wheel tick。Windows 组合键使用 `ctrl`、`alt`、`shift`、`win`，如 `["ctrl", "a"]`。中文与 emoji 输入使用 UTF-16 Unicode 键盘事件。

owner viewer 支持鼠标、拖动、滚轮、键盘和粘贴；OAuth viewer 始终只读，服务器不会执行其输入。private takeover、撤权、授权过期会关闭相关 viewer。PNG 截图流约每 0.5 秒更新。

原生 viewer 自动显示虚拟光标：紫色箭头和 AI 标记显示 AI 的桌面指针位置，按下时显示点击波纹，右键采用蓝色波纹，滚动采用黄色波纹，按住拖动时显示 Drag。owner 接管后的操作标为 You。光标遵循画面缩放、居中留白和全屏尺寸，断线或接管导致原观看连接结束时清理叠加层；开启“减少动态效果”时减少动画。

Windows 光标事件来自成功的 SetCursorPos/SendInput；拖动中的每个原生移动步骤都会通知 viewer，通过同一 WebSocket 的小型 JSON 消息独立发送，不等待下一张 PNG。事件只包含坐标、按钮状态、显示尺寸和操作者，不传输键盘按键或输入文本。波纹表示鼠标输入已发送，具体操作结果仍需观察应用。用户侧叠加层不会写入 desktop_screenshot 或 desktop_snapshot，也不改变点击坐标或 OAuth viewer 的只读权限。

更新服务并刷新 viewer 即可启用，无需额外调用 MCP 工具。此叠加层用于原生 PNG viewer；VNC/noVNC 路径保持其现有光标行为。macOS 原生后端目前反馈动作完成后的指针终点，Windows 提供拖动过程中的连续位置。browser_action 使用浏览器结构操作，不产生桌面鼠标遥测。

Windows 上个人上下文使用 Win32 文件句柄和文件锁，拒绝 symlink / junction 等 reparse point，持有目录句柄期间禁止其重命名，并在同目录原子替换 JSON。Windows 使用继承的 NTFS ACL，POSIX 权限位仅在 Linux/macOS 检查。文件引用拒绝盘符、路径穿越和 alternate data stream。

可选 MCP 插件配置在 Windows 上检查实际 NTFS DACL；写权限只允许当前用户、文件所有者、SYSTEM 和 Administrators，拒绝其他主体的写入授权。配置仍需放在模型 workspace 外部。

服务、Chrome 和隧道只获得必要环境变量。Windows 保留 `SYSTEMROOT`、`COMSPEC`、`PATHEXT`、临时目录等系统环境，避免 shell 和子进程无法启动。预览结束或失败会按本次 PID 清理进程树，随后清理临时 profile 与数据。需要的文件应在结束前下载；硬取消/超时依靠一次性 runner 清理。

## 证据与限制

`windows-preview-preflight-<runner>-<run_id>` artifact（保留 3 天）包含启动前 `report.json` 和 `desktop.png`。此报告只证明桌面可用和截图成功；只有后续公网 MCP 输入测试成功才表示整个服务就绪。不会上传交互会话截图或 Chrome profile。

GitHub Windows runner 是否有可交互桌面以每次真实运行结果为准。后端不会通过解除锁屏、切换用户、绕过 UAC 或提升权限来处理不可用桌面；Win32 输入受 UIPI 限制，不能保证操作更高权限窗口。[SendInput 官方说明](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendinput)。

原生截图和输入测试仅适用于无个人账号的临时 runner；不要将这个预检流程直接运行在有敏感窗口的个人电脑上。此适配不将 Windows 本机长期部署或 RDP 会话稳定性标记为已验收。
