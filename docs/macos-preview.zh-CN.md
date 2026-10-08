# 实验性 macOS 原生桌面探针

这不是 macOS MCP 后端，也不是可远程连接的 preview。现有 Linux Docker/X11、OAuth、只读 viewer 和部署流程不变。此阶段只回答：一次性 GitHub macOS runner 能否截到真实 Chrome 窗口，并通过原生输入产生可验证的网页状态变化。

## 手动运行

1. 自行将本次改动提交并推送到子项目对应的 GitHub 仓库；该 workflow 必须存在于默认分支，Actions 页面才会显示手动运行入口。启用 Actions，打开 **Experimental macOS native desktop probe**。
2. 点击 **Run workflow**，选择 `macos-15`（ARM64）或 `macos-15-intel`（Intel）。该 workflow 没有 push/PR 触发；每次最多 15 分钟。
3. 下载 `macos-probe-<runner>-<run_id>` artifact（保留 3 天）。本实现没有自动触发 Actions、提交或发布。

使用 checkout/setup-python 的固定 SHA，Python 3.12，Pillow 12.2.0 / Playwright 1.62.0 与项目当前版本一致。PyObjC core、Cocoa、Quartz 固定为 11.1；Quartz 11.1 的 PyPI 元数据明确列出 Python 3.12，提供 `cp312-macosx_10_13_universal2` wheel，支持 Intel/ARM 两种架构[1](https://pypi.org/pypi/pyobjc-framework-Quartz/11.1/json)。这证明 Python/架构包兼容性，不证明 runner 上 TCC、WindowServer、原生输入必然可用。runner 标签、预装 Chrome 和权限策略可能变化。

依赖只在远端安装，不安装 Playwright 浏览器。检测 `/Applications/Google Chrome.app/Contents/MacOS/Google Chrome`，直接启动 headed Chrome，CDP 绑定 loopback 9222，使用独立临时 profile；端口被占用立即失败，不连接他人的浏览器。不使用 `--no-sandbox`，不使用全局 profile、登录账号或公网隧道。

## 验收 artifact

- `report.json`：按阶段报告成功/失败与原因、权限状态、坐标映射和仅含测试数据的 DOM 结果。失败退出非零；依赖安装失败可能没有 report，不能当作 PASS。
- `before.png` / `after.png`：`/usr/sbin/screencapture` 的全屏主显示截图。脚本识别测试页红/绿/蓝三种专用色标，对照同一 CDP 页的 DOM bounding boxes，建立平移与独立 X/Y 缩放；再用截图像素尺寸与 Quartz 主显示 points bounds 转换原生输入坐标。不硬编码 Chrome 标题栏高度、Retina 倍数或显示分辨率。
- `finder.png`：打开 Finder 后的截图，**必须人工检查**是否真的显示 Finder。生成 PNG 不代表窗口截图已验证；报告不会将 Finder 标记自动 PASS。

先检查 before/after 的真实测试窗口与三块色标，再检查 report 的 `native_click`、`native_chinese_paste`、`native_keyboard_replace`、`native_scroll`、`native_drag` 和两次截图映射阶段。自动成功仍不涵盖 Finder 人工验收。

网页由 `ThreadingHTTPServer` 在 `127.0.0.1` 独立临时端口提供。按钮计数、完整中文输入、Cmd+A 选择/替换、scrollTop 增加、拖动目标状态均由 DOM 断言；鼠标、按键、滚轮和拖动使用 Quartz CGEvent，中文用 UTF-8 pbcopy + Cmd+V。Playwright 只准备/读取页面状态与几何信息，不使用 click/fill/press 替代原生输入。每组动作前激活并检查自己启动的 Chrome 前台状态。

## 权限与失败边界

记录 `CGPreflightScreenCaptureAccess` 与 `AXIsProcessTrusted`（可用时）；不可用记为 null，不假称授权。明确拒绝的权限在 permissions 阶段失败；权限归属通常涉及 runner 会话、执行 Python 的宿主与应用签名，不能仅凭本机设置推断 Actions 授权。未知权限也必须靠后续截图色标和真实输入断言验证。

不请求/自动点击权限弹窗，不篡改 TCC.db，不禁用 SIP。只截壁纸、色标缺失/多匹配、非矩形杂色、反射/不一致的几何、越界坐标均拒绝猜测。此阶段明确只支持一个活动显示器，避免多显示器 capture index 与 Quartz 坐标归属歧义；超出范围失败而非 browser-only 降级。

finally 终止自己启动的 Chrome、停止 HTTP server，移除临时 profile；不杀其他 Chrome/Finder 进程。截图 artifact 只适用于干净、无账号的临时 runner；**不要在有敏感窗口的个人桌面运行**。全屏截图不可保证自动遮蔽外部窗口。artifact 不包含浏览器 profile、账号、cookie、环境变量或凭据。运行取消/超时依赖 runner 的作业清理，artifact 步骤为 always，硬取消仍可能来不及上传。

## 本地回归（不安装依赖）

```sh
python -m unittest discover -s tests -p test_macos_probe.py -v
```

测试仅依赖标准库，覆盖非 macOS 拒绝与非零退出、失败报告、坐标密度/独立轴缩放/边界、色标容差/杂色/缺失/歧义。不得把 Windows 上这些测试通过描述成 macOS 桌面验证通过。需要手动运行两个 runner 的 workflow 并审阅 artifact 后，才能评估下一阶段原生 backend；当前不承诺 MCP、远程 viewer、权限隔离或 session 生命周期适配完成。
