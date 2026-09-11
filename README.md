# DeliverDesk · 投递工作台

[![CI](https://github.com/likeyeee/deliver-desk/actions/workflows/ci.yml/badge.svg)](https://github.com/likeyeee/deliver-desk/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/likeyeee/deliver-desk?include_prereleases)](https://github.com/likeyeee/deliver-desk/releases)

本地运行的求职工作台，支持 BOSS 直聘和智联招聘：筛选职位、自动沟通或投递在线简历、核对结果与管理历史。两个平台均支持根据个人简历与岗位描述生成定制招呼，BOSS 还支持用 DeepSeek 自动回复招聘方消息。

![投递工作台界面，使用示例数据](docs/assets/workspace.png)

## 下载

| 系统                  | 安装包                                                                                                          |
| --------------------- | --------------------------------------------------------------------------------------------------------------- |
| macOS · Apple Silicon | [下载 DMG](https://github.com/likeyeee/deliver-desk/releases/download/v0.9.0/DeliverDesk-0.9.0-mac-arm64.dmg)   |
| Windows · x64         | [下载安装程序](https://github.com/likeyeee/deliver-desk/releases/download/v0.9.0/DeliverDesk-0.9.0-win-x64.exe) |

安装包自带运行环境。当前为未签名测试版；[安装说明与验证范围](docs/desktop-guide.md)。

## 使用

1. 选择 **本次投递平台**，点击 **扫码登录**，在主窗口的 **求职浏览器** 中完成登录；浏览器内也可切换平台，两个账号的登录分别保存。
2. 设置关键词，依次选择 **省份 / 地区 → 工作城市**，再设置筛选条件，点击 **预览职位**。两个平台均可选择招呼方式；智联需选择具体城市，并使用智联账户内的在线简历。
3. 设置 **本次投递次数** 和节奏，点击 **开始投递**；自动切到浏览器，可暂停、继续或停止。
4. 在 **投递记录** 查看结果、只读核对送达，或导出 CSV。
5. BOSS 后续回复：在 **模型与人格** 配置 DeepSeek 和系统提示词，再到 **消息回复** 开启 **自动回复**，或读取会话、编辑草稿后手动确认发送。[使用说明](docs/llm-replies.md)

个性化招呼（BOSS / 智联）：在 **个人简历** 上传 PDF、DOCX、TXT 或粘贴正文，点击 **分析简历**，核对并保存个人特点；回到工作台选择 **AI 岗位招呼**，发现符合条件的新岗位时会结合 JD 和你的经历自动生成，投递时自动使用，完整内容与结果保存在 **招呼记录**。[简历与岗位招呼](docs/resume-greetings.md)

历史按平台区分，去重跨重启保留；无法确认的发送不会自动重试。智联的自定义与 AI 模式会逐岗保存并设为网站默认招呼，核对后与在线简历一同投递，任务结束恢复原默认招呼；选择“使用网站当前招呼”则不调用 AI。BOSS 的“投递”指发起沟通，本地导入的简历用于生成招呼，不自动作为附件发给招聘方。简历导入仅在本机解析，分析和生成岗位招呼时会将正文及相关资料发送给 DeepSeek；生成回复时会发送近期会话和人格提示词。自动回复默认关闭，退出应用后停止。

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

### 2026-09-11

- 新增智联招聘平台，支持在应用内扫码登录、搜索职位、选择城市与筛选条件、预览和自动投递在线简历。
- “BOSS 浏览器”改名为“求职浏览器”；工作台与浏览器均可选择平台，分别保存登录状态与筛选条件，切换时保存当前偏好。
- 智联新增 AI 岗位招呼：逐岗结合 JD 与上传的简历生成定制内容，保存为网站自定义默认招呼，重新核对后与在线简历一同投递，再处理下一岗。
- 智联 AI 招呼保存可核对的简历原句，在“招呼记录”展示依据；找不到对应出处或出现简历未提供的英文技能词时停止，避免照搬岗位要求。
- 支持自定义模板与网站当前招呼模式；预览只生成内容，不改智联设置。智联招呼遵守网站 500 字限制，超长或生成、保存失败时停止，不截断发送。
- 连续投递复用一个应用创建的招呼条目，任务结束或停止后恢复原默认招呼，保留用户已有模板；实际发送原文不一致时停止并保留待核对记录。
- 智联始终使用账户中保存的在线简历；网站当前招呼模式无需 AI 或本地简历，后续聊天可在智联网页处理。
- 逐岗核对职位 ID、岗位和公司，确认网站成功回执后记录实际招呼并继续；已投递、已沟通及结果待核对的职位自动跳过。
- 投递记录显示所属平台、支持按平台筛选；智联平台模式可通过求职反馈只读核验，定制招呼还须核对原文，不重新发送简历。
- 保留原有 BOSS 登录、历史、AI 岗位招呼和消息回复；两平台共享本机每日尝试上限，运行期间不能切换平台。
- 智联需选择具体城市；遇到验证、网站限制、列表未刷新或目标不一致时停止，保留现场供处理。

[完整发布记录](docs/releases/release-notes.md)
