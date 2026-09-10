# DeliverDesk · 投递工作台

[![CI](https://github.com/likeyeee/deliver-desk/actions/workflows/ci.yml/badge.svg)](https://github.com/likeyeee/deliver-desk/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/likeyeee/deliver-desk?include_prereleases)](https://github.com/likeyeee/deliver-desk/releases)

本地运行的 BOSS 直聘求职工作台：筛选职位、分析个人简历、按岗位生成招呼、核对送达、管理历史，也可用 DeepSeek 自动回复招聘方消息。

![投递工作台界面，使用示例数据](docs/assets/workspace.png)

## 下载

| 系统                  | 安装包                                                                                                          |
| --------------------- | --------------------------------------------------------------------------------------------------------------- |
| macOS · Apple Silicon | [下载 DMG](https://github.com/likeyeee/deliver-desk/releases/download/v0.7.0/DeliverDesk-0.7.0-mac-arm64.dmg)   |
| Windows · x64         | [下载安装程序](https://github.com/likeyeee/deliver-desk/releases/download/v0.7.0/DeliverDesk-0.7.0-win-x64.exe) |

安装包自带运行环境。当前为未签名测试版；[安装说明与验证范围](docs/desktop-guide.md)。

## 使用

1. 点击 **扫码登录**，在主窗口的 **BOSS 浏览器** 中完成登录。
2. 设置关键词，依次选择 **省份 / 地区 → 工作城市**，再设置筛选条件和招呼语，点击 **预览职位**。
3. 设置 **本次投递次数** 和节奏，点击 **开始投递**；自动切到浏览器，可暂停、继续或停止。
4. 在 **投递记录** 查看结果、只读核对送达，或导出 CSV。
5. 在 **模型与人格** 配置 DeepSeek 和系统提示词，再到 **消息回复** 开启 **自动回复**，或读取会话、编辑草稿后手动确认发送。[使用说明](docs/llm-replies.md)

要使用个性化招呼：在 **个人简历** 上传 PDF、DOCX、TXT 或粘贴正文，点击 **分析简历**，核对并保存个人特点；回到工作台选择 **AI 岗位招呼**，发现符合条件的新岗位时会结合 JD 和你的经历自动生成，投递时自动使用，完整内容与结果保存在 **招呼记录**。[简历与岗位招呼](docs/resume-greetings.md)

历史去重跨重启保留；无法确认的发送不会自动重试。简历导入仅在本机解析，分析和生成岗位招呼时会将正文及相关资料发送给 DeepSeek；生成回复时会发送近期会话和人格提示词。自动回复默认关闭，退出应用后停止。“投递”指发起沟通，导入的简历用于生成招呼，不自动作为附件发给招聘方。

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

### 2026-09-10

- 城市选择支持全国 34 个省级地区、373 个网站城市选项：先选省份，再选城市；原有城市设置自动恢复，切换城市会清除旧的工作区域条件。
- 新增个人简历：上传 PDF、DOCX、TXT 或粘贴正文，使用 DeepSeek 提取个人概况、技能、代表经历和岗位优势，支持校对与编辑。
- AI 岗位招呼改为自动流程：发现符合条件的新岗位时结合 JD 与当前简历生成，投递时自动使用，无需逐岗选择和生成。
- 新增招呼记录：保存完整正文、当时的 JD、简历特点与版本、模型和处理结果；资料与要求未变时沿用已有招呼，变化后重新生成。
- 简历和分析结果保存在本机，不随配置导出；修改正文后需重新分析，也可移除本地简历。
- 保留完整生成正文；生成失败或模型返回截断内容时停止，不发起沟通。

[完整发布记录](docs/releases/release-notes.md)
