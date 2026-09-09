# 贡献指南

## 本地开发

需要 Node.js 24、[uv](https://docs.astral.sh/uv/getting-started/installation/) 和 Google Chrome。Python 由 uv 安装。

```sh
git clone https://github.com/likeyeee/deliver-desk.git
cd deliver-desk
uv sync --locked
npm ci
npm start
```

界面在 `desktop/renderer/`，Electron 主进程在 `desktop/`，任务核心在 `src/boss_cli/`。开发数据位于 `.boss-cli/`；不要使用或提交真实登录数据作为测试夹具。

## 提交检查

```sh
npm run lint
npm run test:python
npm run build:ui
npm run test:desktop
npm run test:ui
```

Python 浏览器测试需要 Chrome，CI 覆盖 Linux、macOS 和 Windows。桌面集成测试在发行目标 macOS / Windows 上执行，使用临时目录和合成网页，拦截全部网站请求，不会联系真实招聘方。UI 测试使用当前 Python Playwright 包自带的 Node 驱动启动真实应用，覆盖扫码入口、数量设置、自动切换、后台批量投递和窗口尺寸同步。

修改打包、IPC 或任务服务时，还需验证打包后的核心：

```sh
npm run build:backend
npm run test:desktop -- --packaged-backend
```

## Pull request

- Git 提交信息、Pull request 标题与正文、发布说明统一使用中文。
- 一次解决一个明确问题，写清触发条件、结果和验证方法。
- 行为修复附最小回归测试；界面改动附使用示例数据的截图。
- 用户可感知的变化合并到 [当天发布记录](docs/releases/release-notes.md)，并更新 README 的最新更新。
- 依赖变更同时提交 `uv.lock` 或 `package-lock.json`。

消息发送相关改动必须保留：发送前登记、同一职位去重、目标职位核对、明确回执、不确定结果禁止自动重试。页面适配依据见 [浏览器适配](docs/browser-adapter.md)。

## 报告问题

在 [Issues](https://github.com/likeyeee/deliver-desk/issues/new/choose) 提供版本、系统、复现步骤和脱敏日志。不要附浏览器目录、Cookie、二维码、简历或完整聊天记录。安全问题请按 [SECURITY.md](SECURITY.md) 私下报告。
