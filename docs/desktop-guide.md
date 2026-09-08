# 投递工作台 0.2

Electron + React 桌面界面，复用 Python 的搜索、匹配、去重、任务控制和 SQLite 历史。安装包自带运行环境，使用者无需安装 Python、Node.js 或命令行工具。

## 使用

1. 打开应用，点击右上角“打开浏览器”，在 BOSS 直聘网站完成登录。扫码过期时点击网站自己的“点击刷新”。登录使用应用自己的浏览器目录；普通 Chrome 的账号不会被复制进来。
2. 填写关键词、城市、薪资、经验和学历。其他条件在“更多匹配条件”里；所有筛选文字必须与网站提供的选项一致。
3. 点击“预览职位”。这一步不会点击立即沟通或发送消息。通过预览确认职位范围。
4. 设置招呼语和数量，点击“开始投递”，核对界面显示的范围后启动。运行中可暂停、继续或停止。
5. 在“投递记录”查看实际文本与结果，导出 CSV；“运行日志”显示进度和失败原因。待核对记录可点击“只读核对送达”，重新检查原消息、目标职位与网站回执，成功后更新历史。这一步不发送新消息；无法确认时保留原状态并继续阻止自动重发。

只建立沟通不会计为“已送达”。自定义消息需同时出现新的本人消息、输入框清空和送达/已读回执才计为发送成功。招聘方已明确拒绝的会话不会继续发消息。

遇到安全验证、网站限制、页面空白或收件人无法核对，任务停止并保留浏览器和现场。请在浏览器手动处理，然后重新运行。程序不绕过验证、不更换账号、不自动重发不确定的消息。

## 安装与数据

- macOS：使用对应芯片版本的 DMG，把应用拖到 Applications。当前本机产物为 Apple Silicon / arm64。Intel Mac 需在 Intel 构建机重建 Python 核心和 Electron x64 包。
- Windows：使用 x64 安装程序，或将 ZIP 完整解压后运行“投递工作台.exe”。不要从 ZIP 内直接运行单个 EXE，也不要移走 resources 目录。
- 应用尚未使用开发者发行证书签名和公证。系统可能要求用户在系统安全设置里确认来源。正式面向大量用户发布前，需配置自己的签名证书和 macOS 公证。
- 每台机器有独立登录与历史。新安装包不包含开发者的个人账号、二维码、聊天、配置或投递记录。
- 在“偏好与数据 → 打开目录”可找到本机数据。配置导出仅带任务设置；CSV 带个人投递内容，请按需要分享。

## 开发

```sh
uv sync --locked --python 3.12
npm ci
npm start
```

开发环境的数据默认位于 `.boss-cli/desktop/state`。显式设置 `DELIVERDESK_WORKSPACE=1` 可在开发时读取当前项目的 `.boss-cli` 历史，但发行应用忽略这个开关。

```sh
uv run pytest -q
npm run test:desktop
npm run build:backend
npx electron scripts/desktop-smoke.cjs --packaged-backend
node scripts/build-icons.cjs
npm run pack:mac
```

Windows 机器执行 `npm run pack:win`，会先用 Windows Python 构建本机服务，再生成 NSIS 安装程序和 ZIP。`.github/workflows/desktop-build.yml` 包含 macOS / Windows 原生构建和隔离测试任务。构建记录见 [GitHub Actions](https://github.com/likeyeee/deliver-desk/actions/workflows/desktop-build.yml)；安装程序及校验值见 [Releases](https://github.com/likeyeee/deliver-desk/releases)。

在 Mac 准备 Windows 包也可使用：

```sh
uv run python scripts/build_windows_backend.py
npx electron-builder --config desktop/windows-cross.cjs --win --x64 --publish never
```

这条流程校验 Python 官网的压缩包 SHA-256，安装锁定版本的 Windows wheel，并自带离线运行时。跨平台生成成功不等于已经在真实 Windows 机器上完成启动验证。打包钩子会拒绝将 Mac Python 核心放进 Windows 包。

## 浏览器问题的处理

原 CLI 使用 Playwright 驱动 Chrome 时，本机出现过 BOSS code=37 与 about:blank。桌面版改用 Electron 自有浏览器，通过主进程提供的 DOM 和鼠标接口执行现有任务流程，不连接普通 Chrome 的调试端口。远程网站窗口不加载应用 preload，Node.js 关闭，沙箱和隔离开启。

2026-09-08 的真实验证已完成：新浏览器恢复登录，自动搜索“AI应用”，选择泉州、经验不限，读取详情并预览；随后通过完整聊天页自动发送一条自定义招呼。首次发送后，旧消息选择器漏识别了网站已经显示的“送达”。修正为新版己方消息结构后，重启应用并执行“只读核对送达”，确认原文和回执仍存在，历史更新为已送达；再次处理同一真实职位时，在浏览器操作前直接跳过，发送尝试数未增加。

修正包括等待城市和结果加载完成、避免页面更新期间的重复定位、从简易沟通弹窗转到完整会话，以及识别新版消息气泡。完整聊天页通过网站的“查看职位”核对实际职位 ID，不能仅凭公司名或联系人列表顺序发送。当前记录保留了首次回执识别失败和后续只读核验的过程。

自动检查覆盖 Python 核心、浏览器和进程测试，以及 Electron 与打包 Python 服务的隔离流程。macOS / Windows 的原生服务均已在 GitHub Actions 执行；结果见 [CI](https://github.com/likeyeee/deliver-desk/actions/workflows/ci.yml) 和 [原生构建](https://github.com/likeyeee/deliver-desk/actions/workflows/desktop-build.yml)。macOS 安装版已实际启动并恢复登录；Windows 安装向导和真实账号登录仍需实机验收。网站后续布局变化可能需要继续适配。

参考依据：[Electron WebContents](https://www.electronjs.org/docs/latest/api/web-contents)、[Electron 安全建议](https://www.electronjs.org/docs/latest/tutorial/security)、[Python Windows 嵌入式发行包](https://docs.python.org/3/using/windows.html#the-embeddable-package)、[electron-builder 跨平台构建](https://www.electron.build/docs/features/multi-platform-build/)。原有 BOSS 页面适配研究见 [GitHub 项目研究](research/github-reference.md)。
