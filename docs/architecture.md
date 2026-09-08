# 架构

```text
desktop/renderer/       React 界面：配置、任务状态、历史
        │ 白名单 IPC
desktop/main.cjs        Electron 主进程：窗口、文件对话框、数据目录
        │ JSON Lines / stdio
src/boss_cli/           Python 核心：匹配、额度、任务状态、SQLite
        │ 浏览器 RPC
desktop/browser.cjs     Electron 网站窗口：DOM、鼠标、导航
```

CLI 直接复用 Python 核心，通过 Playwright 提供浏览器操作。桌面版通过 `rpc_browser.py` 实现相同的页面接口，无需用户安装 Chrome。

## 模块

| 位置                       | 职责                                  |
| -------------------------- | ------------------------------------- |
| `config.py` / `models.py`  | 配置校验与职位模型                    |
| `matching.py`              | 标题、描述、公司、薪资匹配            |
| `runner.py` / `control.py` | 任务流程、暂停、停止和数量限制        |
| `storage.py`               | SQLite 历史、去重、名额预占与中断恢复 |
| `browser.py`               | 网站元素、搜索、聊天目标和回执识别    |
| `desktop_service.py`       | 桌面请求入口与后台任务                |
| `desktop/backend.cjs`      | Python 子进程、请求关联与生命周期     |
| `desktop/preload.cjs`      | 受限界面 API                          |

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
