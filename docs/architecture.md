# 架构

```text
desktop/renderer/       React 界面：配置、任务状态、历史
        │ 白名单 IPC
desktop/main.cjs        Electron 主进程：窗口、文件对话框、数据目录
        │ JSON Lines / stdio
src/boss_cli/           Python 核心：匹配、额度、任务状态、SQLite
        │ 浏览器 RPC
desktop/browser.cjs     主窗口内的网站视图：DOM、鼠标、导航
```

CLI 直接复用 Python 核心，通过 Playwright 提供浏览器操作。桌面版通过 `rpc_browser.py` 实现相同的页面接口，无需用户安装 Chrome。

主窗口使用 `BaseWindow`，应用界面与每个网站页面分别使用 `WebContentsView`。界面通过受限 IPC 提交浏览器占位区域的尺寸；主进程裁剪到窗口范围内，并在窗口缩放时更新。网站弹出页通过 `createWindow` 留在同一窗口，保留原生 opener 和导航关系。

切到日志、设置或打开应用弹窗时，应用视图位于网站视图上方；网站渲染器保持映射。新文档就绪后重新应用后台运行设置，派发鼠标操作前聚焦宿主与目标网页，避免 Windows 在导航或切换视图后丢失点击。每轮复用详情页，结束后保留当前会话供查看，下次任务清理已管理的辅助页。登录分区始终为 `persist:boss`，更新不迁移账号。

## 模块

| 位置                             | 职责                                        |
| -------------------------------- | ------------------------------------------- |
| `config.py` / `models.py`        | 配置校验与职位模型                          |
| `matching.py`                    | 标题、描述、公司、薪资匹配                  |
| `runner.py` / `control.py`       | 任务流程、暂停、停止和数量限制              |
| `storage.py`                     | SQLite 历史、去重、名额预占与中断恢复       |
| `browser.py`                     | 网站元素、搜索、聊天目标和回执识别          |
| `desktop_service.py`             | 桌面请求入口与后台任务                      |
| `desktop/backend.cjs`            | Python 子进程、请求关联与生命周期           |
| `desktop/preload.cjs`            | 受限界面 API                                |
| `replies.py` / `conversation.py` | 单个会话的上下文、草稿、确认发送与变化检测  |
| `desktop/llm.cjs`                | 系统密钥加密、DeepSeek 请求、错误处理与取消 |

## 一次任务

1. 保存并冻结本次配置，持有数据目录独占锁。
2. 校验登录，搜索并确认网站筛选结果。
3. 读取职位 ID、卡片与详情，执行本地匹配。
4. 预览只保存发现记录。发送前在事务中检查历史并登记 `sending`，占用尝试名额。
5. 建立会话，核对职位 ID、公司和聊天目标，再输入消息。
6. 根据己方原文与送达/已读回执登记结果；无法确认时保留 `partial` / `unknown`。

`sending`、`sent`、`contacted`、`partial` 和 `unknown` 都阻止自动重发。进程退出时保留不确定状态；只读核验只更新原记录，不产生新发送尝试。

## 数据目录

发行应用使用 Electron `userData` 下的 `state/`；开发版使用 `.boss-cli/desktop/state/`。浏览器登录目录由应用持有，不随配置导入改变。CLI 默认使用配置旁的 `.boss-cli/`。

每个目录独立计数和去重，建议同一账号固定使用一个目录。SQLite 使用 WAL；失败诊断与截图只保存在本机。

回复草稿与发送记录使用独立的 `replies` 表，原有 `deliveries` 职位去重不变。回复发送同样先登记并计入每日额度；不确定结果阻止该会话继续生成或发送，直到用户人工核实。上下文摘要排除未读数字和回执文字，发送前重新核对，避免回复过期消息。

模型请求由 Python 后台任务通过独立 stdio 消息交给 Electron 主进程，主线程继续接收网页和模型响应。API Key 仅由主进程解密用于官方 HTTPS 请求，不进入 Python 配置或普通界面状态；停止任务会取消待处理请求。读取、生成和发送复用同一个已核对聊天页，其他批量任务仍正常清理辅助页。

旧数据目录会补充任务的目标和尝试数字段，保留原有历史。目标写入本次任务记录，之后修改配置不会改写已完成任务的进度。
