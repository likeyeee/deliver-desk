# DeliverDesk · 投递工作台

[![CI](https://github.com/likeyeee/deliver-desk/actions/workflows/ci.yml/badge.svg)](https://github.com/likeyeee/deliver-desk/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/likeyeee/deliver-desk?include_prereleases)](https://github.com/likeyeee/deliver-desk/releases)

本地运行的 BOSS 直聘求职工作台：筛选职位、发起沟通、核对送达、管理历史，也可用 DeepSeek 自动监控未回复消息并按个人提示词回复。

![投递工作台界面，使用示例数据](docs/assets/workspace.png)

## 下载

| 系统                  | 安装包                                                                                                          |
| --------------------- | --------------------------------------------------------------------------------------------------------------- |
| macOS · Apple Silicon | [下载 DMG](https://github.com/likeyeee/deliver-desk/releases/download/v0.5.0/DeliverDesk-0.5.0-mac-arm64.dmg)   |
| Windows · x64         | [下载安装程序](https://github.com/likeyeee/deliver-desk/releases/download/v0.5.0/DeliverDesk-0.5.0-win-x64.exe) |

安装包自带运行环境。当前为未签名测试版；[安装说明与验证范围](docs/desktop-guide.md)。

## 使用

1. 点击 **扫码登录**，在主窗口的 **BOSS 浏览器** 中完成登录。
2. 设置关键词、城市、筛选条件和招呼语，点击 **预览职位**。
3. 设置 **本次投递次数** 和节奏，点击 **开始投递**；自动切到浏览器，可暂停、继续或停止。
4. 在 **投递记录** 查看结果、只读核对送达，或导出 CSV。
5. 在 **模型与人格** 配置 DeepSeek 和系统提示词，再到 **消息回复** 开启 **自动回复**，或读取会话、编辑草稿后手动确认发送。[使用说明](docs/llm-replies.md)

历史去重跨重启保留；无法确认的发送不会自动重试。登录和记录保存在本机；手动生成或开启自动回复后，需要回复的会话近期消息和人格提示词会发送给 DeepSeek。自动回复默认关闭，退出应用后停止。“投递”指发起沟通，不自动上传简历附件。

## 开发

需要 Node.js 24 和 [uv](https://docs.astral.sh/uv/getting-started/installation/)。

```sh
git clone https://github.com/likeyeee/deliver-desk.git
cd deliver-desk
uv sync --locked
npm ci
npm start
```

```sh
npm run lint          # Python、前端和文档格式
npm run test:python   # 需要 Google Chrome
npm run test:desktop  # Electron + Python 隔离集成测试
```

```text
desktop/        Electron 主进程与 React 界面
src/boss_cli/   Python 任务核心与 CLI
tests/          匹配、去重、浏览器和进程测试
scripts/        构建、验证、发布工具
examples/       示例配置
docs/           使用、架构、适配与发布记录
```

[使用文档](docs/README.md) · [CLI](docs/cli.md) · [构建与发布](docs/releasing.md) · [参与贡献](CONTRIBUTING.md) · [问题反馈](https://github.com/likeyeee/deliver-desk/issues/new/choose)

## 最新更新

### 2026-09-09

- 消息回复页新增自动回复开关：检查招聘方发来且尚未回复的消息，包含已读会话，并保留发现、生成、发送和失败日志。
- 移除回复的 Token 参数和 1,000 字上限，保留完整正文；模型返回截断内容时停止发送。
- 新增 DeepSeek 模型配置、自定义人格提示词和可编辑回复草稿，确认后发送并核对送达。
- 修复同公司多招聘者导致未发送文字的问题；浏览器铺满窗口，网页宽度自动适配。
- 修复有未读消息时停在简易聊天弹窗的问题，可继续进入完整会话并核对送达。
- 修复发送按钮遮挡、聊天页反复闪跳，以及切到日志后偶发点击无响应的问题。
- 浏览器嵌入主窗口，扫码、职位和聊天页面统一显示；开始任务后自动切换。
- 自定义每轮 1–200 次投递，显示目标进度、每日余额和提前结束原因。

[完整发布记录](docs/releases/release-notes.md)
