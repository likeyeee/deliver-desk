# CLI 使用

需要 Python 3.11+ 和 Google Chrome。建议优先使用桌面版；CLI 的 Playwright 路径仍可能遇到网站 `code=37` 或空白页。

## 安装与开始

使用锁定依赖：

```bash
uv sync --locked
uv run boss init
uv run boss --help
uv run boss config
uv run boss login
uv run boss preview --max-jobs 10
uv run boss jobs
```

仓库提供 [示例配置](../examples/boss.yaml)，可用 `boss init` 在工作目录生成 `boss.yaml`；该本机文件不提交到 Git。没有 uv 时也可以用 `python -m venv .venv`、激活环境后 `pip install -e .`。

`login` 打开专用 Chrome 的 BOSS 登录页，在该窗口中手动完成扫码登录。程序等账号入口和页面稳定后关闭窗口，后续复用 `.boss-cli/profile`。若使用 `browser.channel: chromium`，先运行 `uv run playwright install chromium`。

`preview`、`run` 和 `start` 也会在同一个浏览器会话内先校验登录，再开始搜索。有效登录可直接复用；若登录失效，程序会在登录页等待扫码，成功后自动继续当前任务。

**普通 Chrome 窗口中已登录的账号，不会自动转移到 Playwright 专用目录。** 两个控制通道不同；本工具不读取或复制日常浏览器的 Cookie 数据库。Playwright 官方也不支持直接自动化 Chrome 默认用户目录：[持久化上下文说明](https://playwright.dev/python/docs/api/class-browsertype#browser-type-launch-persistent-context)。

## 常用命令

```bash
# 搜索和文案预览，不点击“立即沟通”
uv run boss preview --max-jobs 10

# 前台真实发送：先展示配置并询问一次
uv run boss run --send --max-sends 1

# 检查过配置后，允许该任务自动发送
uv run boss run --send --yes

# 后台预览 / 后台发送
uv run boss start
uv run boss start --send --yes

# 实时状态与日志；Ctrl+C 退出查看，不影响后台任务
uv run boss status --watch
uv run boss logs --follow

# 控制同一数据目录中的任务
uv run boss pause
uv run boss resume
uv run boss stop

# 已发现的职位 / 实际沟通记录
uv run boss jobs --json
uv run boss history --json
uv run boss history --watch
uv run boss history --status unknown
uv run boss history --export history.csv --limit 10000
uv run boss history --export history.json --limit 10000

# 配置校验 / 页面选择器诊断
uv run boss config
uv run boss diagnose
```

所有命令支持 `--config /path/to/boss.yaml`。后台启动保存一份本次配置快照，启动后修改原配置不会改变正在执行的任务。停止请求在当前浏览器动作完成后处理；随机延时、批次休息和暂停期间，每 0.2 秒检查一次控制请求。前台 `Ctrl+C` 也会安全停止。遇到登录失效、验证、网站上限或无法确认的发送结果，状态变为 `needs_attention`；在浏览器人工处理后重新运行。`resume` 用于仍在运行的已暂停进程。

## 配置

编辑 `boss.yaml`，然后用 `boss config` 校验。相对 `state_dir` 按配置文件所在目录解析。

```yaml
search:
  keywords: [AI应用, AI产品经理, 大模型]
  city: 全国
  filters: {} # 使用网页显示的筛选名称、选项文字
  max_jobs: 30 # 所有关键词合计最多浏览的唯一职位数
  max_scrolls: 8 # 每个关键词最多滚动加载次数

match:
  title_any: [AI, 人工智能, 大模型, AIGC, 智能体]
  exclude: [] # 标题、公司、标签、详情中的排除词
  exclude_companies: []
  description_all: [] # 详情必须同时出现的关键词
  salary_min: null # 月薪人民币；区间有交集即匹配
  salary_max: null
  unknown_salary: skip # 设置金额条件时，无法解析的薪资默认跳过

message:
  mode: custom
  template: 您好，我对贵公司的{title}岗位很感兴趣，希望进一步了解岗位要求和团队情况，方便交流吗？

run:
  max_sends: 5 # 本次最多发起几次沟通，包含不确定尝试
  daily_limit: 20 # 同一数据目录、本机日期下的尝试上限
  action_delay: [1.5, 3.5]
  job_delay: [15, 30]
  cooldown_every: 5
  cooldown_seconds: [60, 120]
  max_errors: 3
```

网站筛选可使用 `求职类型`、`薪资待遇`、`工作经验`、`学历要求`、`公司行业`、`公司规模` 等页面选项，例如 `filters: {工作经验: 经验不限}`。选项文字必须与当前网页一致；程序确认不了选中结果时停止。`match` 是读取卡片/详情之后的本地二次筛选。

部分列表薪资使用自定义字体，DOM 可能只包含私有区字符。程序会继续读取独立详情页的正常薪资数字；详情仍无法读取时展示“网页字体编码”，不会猜测数字。也可在 `search.filters` 中设置网页薪资范围；如果另外设置了 `salary_min/max`，详情仍无法读取的工资遵循 `unknown_salary`。日薪、时薪、年薪和面议不自动换算成月薪。

消息模式：

- `custom`（默认）：发起沟通并核对收件人后发送 `template`，等待己方消息的“送达/已读”回执。模板支持 `{title}`、`{company}`、`{city}`、`{recruiter}`。输入框有既存草稿、收件人无法核对或发送状态不明确时停止。**平台也可能自动发过一条招呼，此时模板是第二条消息。**
- `platform`：仅点击“立即沟通”，不发送 `template`。实测该动作可能只建立会话；只有建立联系的证据时记为 `contacted`，不会算作文字消息已发送。

收件人通过当前会话的职位链接核对；没有直接链接时，可点击该会话的“查看职位”，核对由它打开的详情页 ID、职位及公司，再关闭核对页。无法确认唯一目标时停止。招聘方已经明确回复“不合适”时，不再追加模板；若拒绝在输入后到达，只清空仍与本次模板一致的草稿。

这里的“投递记录”指打招呼/沟通记录。程序没有自动上传或发送简历附件的动作，也不把“已发出”当作“已读”。随机延时用于控制操作节奏；程序不处理验证码、不使用隐身补丁、不轮换账号或代理。

## 连接已开启调试端口的 Chrome

如果你已经有一个通过专用用户目录启动、开放本机 CDP 端口的 Chrome，可在配置中设置：

```yaml
browser:
  channel: chrome
  cdp_url: http://127.0.0.1:9222
  headless: false
  timeout_seconds: 20
  login_timeout_seconds: 300
```

程序在该浏览器内新建自己的标签页，退出时只关闭自己创建的页面，并断开连接。仅接受本机 CDP 地址。[Playwright CDP 文档](https://playwright.dev/python/docs/api/class-browsertype#browser-type-connect-over-cdp)。

## 防重复与中断恢复

职位详情 URL 的职位 ID 是唯一键，追踪参数和 `securityId` 不进入历史。每次真实点击前，在 SQLite 事务中检查重复、占用当日名额并登记 `sending`。数据库启用 WAL，独占锁限制同一数据目录只运行一个浏览器任务。

| 状态        | 含义                                                  | 自动再次发送 |
| ----------- | ----------------------------------------------------- | ------------ |
| `sent`      | 明确的消息发送成功提示，或己方完整消息和送达/已读回执 | 否           |
| `contacted` | 已有沟通、仅建立会话，或招聘方已拒绝而未追加文字      | 否           |
| `sending`   | 已登记，即将发送或正在等待回执                        | 否           |
| `unknown`   | 发送期间异常、进程退出或回执不明确                    | 否           |
| `partial`   | 沟通已建立，自定义消息未确认送达                      | 否           |
| `not_sent`  | 用户在网页核实确实未发送                              | 可以         |

预览登记职位、预览日志以及网页明确显示的既有沟通状态，不占发送名额。跨关键词、跨重启共享去重记录。程序不会自动重试可能已经发送的消息；当日名额按发送尝试计数，人工核实未发送也不退回已经消耗的尝试计数。数据目录之间各自独立，建议一个账号固定使用一个目录。

在网站核实某条不确定记录后：

```bash
uv run boss resolve 职位ID --result sent --note "已在会话中核实招呼已发出"
# 只有确认没有发送时，才允许下次重新尝试：
uv run boss resolve 职位ID --result not_sent --note "已核对会话，确认未发出"
```

`resolve` 要求任务已停止，保留人工核实日志。

## 数据文件

`.boss-cli/history.db` 存储职位、投递状态和结构化事件，`profile/` 保存专用浏览器登录状态，`runs/` 保存后台配置快照，`worker.log` 保存后台标准输出，`diagnostics.json` 保存只读诊断结果。失败现场保存在 `last-error/`（诊断 JSON 及尽力获取的页面截图），日志会显示路径。目录权限为 `0700`，主要数据文件为 `0600`，运行数据已加入 `.gitignore`。不要把整个数据目录上传到代码仓库。CSV 导出会处理公式前缀，导出文件不会覆盖已有文件。

等待扫码时 `login.png` 只代表当前登录页面；离开该页即移除，避免继续展示过期二维码。登录页面持续跳到 `about:blank` 会直接报告环境异常，不再等待完整扫码超时。
