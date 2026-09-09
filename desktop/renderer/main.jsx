import React, { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  ArrowUpRight,
  ArrowRight,
  Play,
  Pause,
  Square,
  Search,
  Send,
  LayoutDashboard,
  History,
  ScrollText,
  Settings,
  Monitor,
  ShieldCheck,
  Download,
  Upload,
  Check,
  CircleHelp,
  X,
  LoaderCircle,
  FolderOpen,
  ChevronRight,
  SlidersHorizontal,
  Sparkles,
  CheckCheck,
  Filter,
  Save,
} from "lucide-react";
import "./style.css";
import BrowserPanel from "./BrowserPanel.jsx";

const api = window.desk;
const pacePresets = {
  流畅: {
    action_delay: [0.6, 1.4],
    job_delay: [8, 16],
    cooldown_every: 5,
    cooldown_seconds: [30, 60],
  },
  均衡: {
    action_delay: [1, 2],
    job_delay: [12, 22],
    cooldown_every: 5,
    cooldown_seconds: [45, 90],
  },
  从容: {
    action_delay: [1.5, 3.5],
    job_delay: [15, 30],
    cooldown_every: 5,
    cooldown_seconds: [60, 120],
  },
};
const labels = {
  starting: "准备中",
  running: "运行中",
  paused: "已暂停",
  stopped: "已停止",
  stopping: "正在停止",
  completed: "已完成",
  needs_attention: "需要处理",
  failed: "运行失败",
  interrupted: "上次运行已中断",
  sent: "已送达",
  contacted: "已建立沟通",
  partial: "待核对",
  unknown: "待核对",
  sending: "发送中",
  not_sent: "已核实未发送",
  preview: "预览",
  send: "正式发送",
  login: "登录",
  diagnose: "诊断",
  cua_assisted: "人工辅助",
  dedup_check: "去重核验",
};
const split = (s) =>
  s
    .split(/[，,\n]/)
    .map((s) => s.trim())
    .filter(Boolean);
const time = (s) =>
  s
    ? new Date(s).toLocaleString("zh-CN", {
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
        hour12: false,
      })
    : "—";
function Badge({ value }) {
  return <span className={"badge " + value}>{labels[value] || value}</span>;
}
function Field({ label, hint, children }) {
  return (
    <label className="field">
      <span>{label}</span>
      {children}
      {hint && <small>{hint}</small>}
    </label>
  );
}
function Button({ children, icon: Icon, className = "", ...props }) {
  return (
    <button className={"btn " + className} {...props}>
      {Icon && <Icon size={16} />} {children}
    </button>
  );
}
function Empty({ icon: Icon = Search, title, body }) {
  return (
    <div className="empty">
      <div className="empty-icon">
        <Icon size={26} />
      </div>
      <h3>{title}</h3>
      <p>{body}</p>
    </div>
  );
}
function App() {
  const [page, setPage] = useState("workspace"),
    [state, setState] = useState(null),
    [draft, setDraft] = useState(null),
    [busy, setBusy] = useState(false),
    [notice, setNotice] = useState(null),
    [confirm, setConfirm] = useState(false),
    [selected, setSelected] = useState(null),
    [search, setSearch] = useState(""),
    [statusFilter, setStatusFilter] = useState(""),
    [resolveNote, setResolveNote] = useState("");
  async function refresh() {
    try {
      const data = await api.snapshot();
      setState(data);
      setDraft((old) => old || data.config);
    } catch (error) {
      setNotice({ error: true, text: error.message });
    }
  }
  useEffect(() => {
    let disposed = false,
      timer;
    async function poll() {
      if (disposed) return;
      await refresh();
      if (!disposed) timer = setTimeout(poll, 1200);
    }
    poll();
    return () => {
      disposed = true;
      clearTimeout(timer);
    };
  }, []);
  async function act(fn, success) {
    setBusy(true);
    setNotice(null);
    try {
      const result = await fn();
      if (success && result !== null) setNotice({ text: success });
      await refresh();
      return result;
    } catch (error) {
      setNotice({ error: true, text: error.message });
      throw error;
    } finally {
      setBusy(false);
    }
  }
  const action = (fn, success) => () => {
    act(fn, success).catch(() => {});
  };
  function update(group, key, value) {
    setDraft((d) => ({ ...d, [group]: { ...d[group], [key]: value } }));
  }
  function setTarget(value) {
    setDraft((d) => ({
      ...d,
      run: { ...d.run, max_sends: value },
      search: {
        ...d.search,
        max_jobs: Math.max(d.search.max_jobs, Math.min(2000, value * 5)),
      },
    }));
  }
  function openBrowser(url) {
    setPage("browser");
    return url ? api.openJob({ url }) : api.openBrowser();
  }
  async function save() {
    const clean = structuredClone(draft);
    clean.search.filters = Object.fromEntries(
      Object.entries(clean.search.filters).filter(([, v]) => v.trim()),
    );
    for (const key of [
      "title_any",
      "exclude",
      "exclude_companies",
      "description_all",
    ])
      clean.match[key] = clean.match[key].map((s) => s.trim()).filter(Boolean);
    const result = await api.saveConfig({ config: clean });
    setDraft(result);
    return result;
  }
  function start(mode) {
    act(async () => {
      await save();
      setConfirm(false);
      setPage("browser");
      return api.start({ mode });
    })
      .then(() => {
        setConfirm(false);
      })
      .catch(() => {});
  }
  const active = state?.active,
    run = state?.run,
    locked = busy || active;
  const dirty =
    draft && state && JSON.stringify(draft) !== JSON.stringify(state.config);
  const nav = [
    ["workspace", LayoutDashboard, "任务工作台"],
    ["browser", Monitor, "BOSS 浏览器"],
    ["history", History, "投递记录"],
    ["logs", ScrollText, "运行日志"],
    ["settings", Settings, "偏好与数据"],
  ];
  const counts = [
    ["已浏览", run?.scanned || 0, "个职位", Search],
    ["符合条件", run?.matched || 0, "个候选", Filter],
    ["确认送达", run?.sent || 0, "条消息", CheckCheck],
    ["已跳过", run?.skipped || 0, "个职位", ShieldCheck],
  ];
  const rows = (state?.history || []).filter(
    (row) =>
      (!statusFilter || row.status === statusFilter) &&
      (!search ||
        [row.title, row.company, row.message].some((v) => v.includes(search))),
  );
  const template = draft?.message.template
    .replaceAll("{title}", draft?.search.keywords[0] || "目标职位")
    .replaceAll("{company}", "目标公司")
    .replaceAll("{city}", draft?.search.city || "目标城市")
    .replaceAll("{recruiter}", "招聘负责人");
  return (
    <div
      className={"app-shell" + (page === "browser" ? " browser-expanded" : "")}
    >
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">
            <Send size={22} />
          </div>
          <div>
            <strong>投递工作台</strong>
            <span>DELIVER DESK</span>
          </div>
        </div>
        <div className="nav-title">工作空间</div>
        <nav>
          {nav.map(([key, Icon, title]) => (
            <button
              key={key}
              className={page === key ? "nav-item active" : "nav-item"}
              onClick={
                key === "browser"
                  ? action(() => openBrowser())
                  : () => setPage(key)
              }
            >
              <Icon size={18} />
              {title}
              {page === key && <i />}
            </button>
          ))}
        </nav>
        <div className="side-bottom">
          <div className="privacy">
            <ShieldCheck size={17} />
            <div>
              <b>数据保存在本机</b>
              <span>登录与投递历史随时可查</span>
            </div>
          </div>
          <span className="version">
            桌面版 {state?.version || "—"} <span>Electron</span>
          </span>
        </div>
      </aside>
      <main>
        <header className="topbar">
          <div className="breadcrumb">
            工作空间 <ChevronRight size={14} />{" "}
            <strong>{nav.find((n) => n[0] === page)[2]}</strong>
          </div>
          <div className="header-right">
            <span
              className={
                "connection " + (state?.browser.loggedIn ? "online" : "")
              }
            >
              <i />
              {state?.browser.loggedIn ? "浏览器已登录" : "浏览器待登录"}
            </span>
            <Button icon={Monitor} onClick={action(() => openBrowser())}>
              {state?.browser.loggedIn ? "查看浏览器" : "扫码登录"}
            </Button>
          </div>
        </header>
        <div
          className={
            "page-content " + (page === "browser" ? "browser-page" : "")
          }
        >
          {notice && (
            <div
              role="alert"
              className={"notice " + (notice.error ? "error" : "")}
            >
              <span>{notice.text}</span>
              <button aria-label="关闭提示" onClick={() => setNotice(null)}>
                <X size={16} />
              </button>
            </div>
          )}
          {!state || !draft ? (
            <Empty
              icon={LoaderCircle}
              title="正在连接本地任务服务"
              body="首次启动需要片刻，请稍候。"
            />
          ) : (
            <>
              {page === "browser" && (
                <BrowserPanel
                  state={state}
                  busy={busy}
                  obscured={!!confirm || !!selected}
                  action={action}
                  onWorkspace={() => setPage("workspace")}
                  onLogs={() => setPage("logs")}
                />
              )}
              {page === "workspace" && (
                <>
                  <div className="page-heading">
                    <div>
                      <div className="eyebrow">YOUR NEXT OPPORTUNITY</div>
                      <h1>让下一次机会，更近一步。</h1>
                      <p>设置求职偏好，预览匹配职位，再开始沟通。</p>
                    </div>
                    <Button
                      icon={Save}
                      disabled={locked || !dirty}
                      onClick={action(save, "配置已保存")}
                    >
                      {dirty ? "保存修改" : "配置已保存"}
                    </Button>
                  </div>
                  <section className="run-strip">
                    <div className="run-intro">
                      <div className={"run-icon " + (active ? "live" : "")}>
                        <Send size={22} />
                      </div>
                      <div>
                        <div className="run-title">
                          {active
                            ? "任务正在执行"
                            : run
                              ? "最近一次任务"
                              : "准备好开始了吗？"}{" "}
                          {run && <Badge value={run.status} />}
                        </div>
                        <p>
                          {active
                            ? `${labels[run?.mode] || "准备"} · ${state.config.search.keywords.join(" / ")}`
                            : run?.note || "第一次使用，请先完成浏览器登录。"}
                        </p>
                      </div>
                    </div>
                    <div className="run-buttons">
                      {active ? (
                        <>
                          <Button
                            icon={run?.status === "paused" ? Play : Pause}
                            disabled={busy}
                            onClick={action(() =>
                              api.control({
                                action:
                                  run?.status === "paused" ? "run" : "pause",
                              }),
                            )}
                          >
                            {run?.status === "paused" ? "继续" : "暂停"}
                          </Button>
                          <Button
                            icon={Square}
                            className="danger"
                            disabled={busy}
                            onClick={action(() =>
                              api.control({ action: "stop" }),
                            )}
                          >
                            停止任务
                          </Button>
                        </>
                      ) : (
                        <>
                          <Button
                            icon={Search}
                            disabled={busy}
                            onClick={() => start("preview")}
                          >
                            预览职位
                          </Button>
                          <Button
                            icon={Play}
                            className="primary"
                            disabled={busy}
                            onClick={() => setConfirm(true)}
                          >
                            开始投递
                          </Button>
                        </>
                      )}
                    </div>
                  </section>
                  <section className="batch-setup" aria-label="本次投递设置">
                    <fieldset disabled={locked}>
                      <div className="batch-target">
                        <Field label="本次投递次数">
                          <div className="unit-input">
                            <input
                              type="number"
                              min="1"
                              max="200"
                              value={draft.run.max_sends}
                              onChange={(e) =>
                                setTarget(Number(e.target.value))
                              }
                            />
                            <span>次</span>
                          </div>
                        </Field>
                        <div
                          className="quick-choices"
                          aria-label="投递次数快捷选项"
                        >
                          {[5, 10, 20, 50].map((n) => (
                            <button
                              key={n}
                              className={
                                draft.run.max_sends === n ? "chosen" : ""
                              }
                              onClick={() => setTarget(n)}
                            >
                              {n} 次
                            </button>
                          ))}
                        </div>
                      </div>
                      <Field
                        label="操作节奏"
                        hint="随机停顿；页面就绪即继续，可在下方微调。"
                      >
                        <select
                          value={
                            Object.entries(pacePresets).find(([, preset]) =>
                              Object.entries(preset).every(
                                ([key, value]) =>
                                  JSON.stringify(draft.run[key]) ===
                                  JSON.stringify(value),
                              ),
                            )?.[0] || "自定义"
                          }
                          onChange={(e) => {
                            const preset = pacePresets[e.target.value];
                            if (preset)
                              setDraft((d) => ({
                                ...d,
                                run: { ...d.run, ...preset },
                              }));
                          }}
                        >
                          <option value="自定义">自定义节奏</option>
                          {Object.keys(pacePresets).map((name) => (
                            <option key={name}>{name}</option>
                          ))}
                        </select>
                      </Field>
                    </fieldset>
                    <p>
                      最多尝试 {draft.run.max_sends} 个新职位 · 浏览上限{" "}
                      {draft.search.max_jobs} 个 · 今日还可尝试{" "}
                      {Math.max(0, draft.run.daily_limit - state.attemptsToday)}{" "}
                      次。重复和不匹配的职位不占投递次数。
                    </p>
                    {draft.run.max_sends >
                      Math.max(
                        0,
                        draft.run.daily_limit - state.attemptsToday,
                      ) && (
                      <p className="quota-note">
                        本次会在今日剩余额度用完时结束，可在“运行节奏”调整每日上限。
                      </p>
                    )}
                    {draft.search.max_jobs < draft.run.max_sends && (
                      <p className="quota-note">
                        浏览上限低于投递目标，请提高下方浏览上限以继续寻找候选职位。
                      </p>
                    )}
                  </section>
                  {run &&
                    ["needs_attention", "failed", "interrupted"].includes(
                      run.status,
                    ) && (
                      <div className="attention">
                        <CircleHelp size={18} />
                        <span>
                          {run.note}
                          。打开浏览器处理后，可重新运行；历史记录会阻止重复发送。
                        </span>
                        <button onClick={action(() => openBrowser())}>
                          查看浏览器 <ArrowRight size={15} />
                        </button>
                      </div>
                    )}
                  <div className="metrics">
                    {counts.map(([label, value, unit, Icon]) => (
                      <div className="metric" key={label}>
                        <div>
                          <span>{label}</span>
                          <Icon size={17} />
                        </div>
                        <strong>
                          {value}
                          <small>{unit}</small>
                        </strong>
                      </div>
                    ))}
                  </div>
                  <div className="workspace-grid">
                    <section className="panel preferences">
                      <div className="panel-heading">
                        <div>
                          <SlidersHorizontal size={18} />
                          <h2>求职偏好</h2>
                        </div>
                        <span>修改后下次运行生效</span>
                      </div>
                      <fieldset disabled={locked}>
                        <Field
                          label="职位关键词"
                          hint="用逗号分隔多个关键词，将依次搜索。"
                        >
                          <input
                            value={draft.search.keywords.join("，")}
                            onChange={(e) =>
                              update(
                                "search",
                                "keywords",
                                e.target.value.split(/[，,]/),
                              )
                            }
                            placeholder="例如：AI 产品经理，大模型应用"
                          />
                        </Field>
                        <div className="form-grid">
                          <Field label="工作城市">
                            <input
                              value={draft.search.city}
                              onChange={(e) =>
                                update("search", "city", e.target.value)
                              }
                              list="cities"
                            />
                            <datalist id="cities">
                              {[
                                "全国",
                                "北京",
                                "上海",
                                "深圳",
                                "广州",
                                "杭州",
                                "成都",
                                "泉州",
                              ].map((v) => (
                                <option key={v} value={v} />
                              ))}
                            </datalist>
                          </Field>
                          <Field label="薪资待遇">
                            <select
                              value={draft.search.filters["薪资待遇"] || ""}
                              onChange={(e) =>
                                update("search", "filters", {
                                  ...draft.search.filters,
                                  薪资待遇: e.target.value,
                                })
                              }
                            >
                              {[
                                "",
                                "不限",
                                "3K以下",
                                "3-5K",
                                "5-10K",
                                "10-20K",
                                "20-50K",
                                "50K以上",
                              ].map((v) => (
                                <option key={v} value={v}>
                                  {v || "使用网站当前条件"}
                                </option>
                              ))}
                            </select>
                          </Field>
                          <Field label="工作经验">
                            <select
                              value={draft.search.filters["工作经验"] || ""}
                              onChange={(e) =>
                                update("search", "filters", {
                                  ...draft.search.filters,
                                  工作经验: e.target.value,
                                })
                              }
                            >
                              {[
                                "",
                                "不限",
                                "在校生",
                                "应届生",
                                "经验不限",
                                "1年以内",
                                "1-3年",
                                "3-5年",
                                "5-10年",
                                "10年以上",
                              ].map((v) => (
                                <option key={v} value={v}>
                                  {v || "使用网站当前条件"}
                                </option>
                              ))}
                            </select>
                          </Field>
                          <Field label="学历要求">
                            <select
                              value={draft.search.filters["学历要求"] || ""}
                              onChange={(e) =>
                                update("search", "filters", {
                                  ...draft.search.filters,
                                  学历要求: e.target.value,
                                })
                              }
                            >
                              {[
                                "",
                                "不限",
                                "初中及以下",
                                "中专/中技",
                                "高中",
                                "大专",
                                "本科",
                                "硕士",
                                "博士",
                              ].map((v) => (
                                <option key={v} value={v}>
                                  {v || "使用网站当前条件"}
                                </option>
                              ))}
                            </select>
                          </Field>
                        </div>
                        <details className="advanced">
                          <summary>
                            更多匹配条件 <ChevronRight size={14} />
                          </summary>
                          <div className="advanced-body">
                            <Field
                              label="职位名至少包含一项"
                              hint="留空表示不增加本地标题限制。"
                            >
                              <input
                                value={draft.match.title_any.join("，")}
                                onChange={(e) =>
                                  update(
                                    "match",
                                    "title_any",
                                    e.target.value.split(/[，,]/),
                                  )
                                }
                              />
                            </Field>
                            <div className="form-grid">
                              <Field label="排除词">
                                <input
                                  value={draft.match.exclude.join("，")}
                                  onChange={(e) =>
                                    update(
                                      "match",
                                      "exclude",
                                      e.target.value.split(/[，,]/),
                                    )
                                  }
                                />
                              </Field>
                              <Field label="排除公司">
                                <input
                                  value={draft.match.exclude_companies.join(
                                    "，",
                                  )}
                                  onChange={(e) =>
                                    update(
                                      "match",
                                      "exclude_companies",
                                      e.target.value.split(/[，,]/),
                                    )
                                  }
                                />
                              </Field>
                            </div>
                            <Field label="职位描述必须包含（全部）">
                              <input
                                value={draft.match.description_all.join("，")}
                                onChange={(e) =>
                                  update(
                                    "match",
                                    "description_all",
                                    e.target.value.split(/[，,]/),
                                  )
                                }
                              />
                            </Field>
                            <div className="form-grid">
                              {[
                                "公司行业",
                                "公司规模",
                                "融资阶段",
                                "求职类型",
                                "职位类型",
                                "工作区域",
                              ].map((label) => (
                                <Field key={label} label={label}>
                                  <input
                                    placeholder="与网页上的选项文字一致"
                                    value={draft.search.filters[label] || ""}
                                    onChange={(e) =>
                                      update("search", "filters", {
                                        ...draft.search.filters,
                                        [label]: e.target.value,
                                      })
                                    }
                                  />
                                </Field>
                              ))}
                            </div>
                          </div>
                        </details>
                      </fieldset>
                    </section>
                    <section className="panel message-panel">
                      <div className="panel-heading">
                        <div>
                          <Send size={18} />
                          <h2>打招呼内容</h2>
                        </div>
                        <span>自定义模板</span>
                      </div>
                      <fieldset disabled={locked}>
                        <Field label="沟通方式">
                          <select
                            value={draft.message.mode}
                            onChange={(e) =>
                              update("message", "mode", e.target.value)
                            }
                          >
                            <option value="custom">发送自定义招呼</option>
                            <option value="platform">仅建立平台沟通</option>
                          </select>
                        </Field>
                        <textarea
                          className="message-input"
                          disabled={locked || draft.message.mode !== "custom"}
                          value={draft.message.template}
                          maxLength={1000}
                          onChange={(e) =>
                            update("message", "template", e.target.value)
                          }
                          aria-label="打招呼模板"
                        />
                        <div className="variables">
                          插入变量{" "}
                          {["title", "company", "city", "recruiter"].map(
                            (v, i) => (
                              <button
                                key={v}
                                type="button"
                                disabled={locked}
                                onClick={() =>
                                  update(
                                    "message",
                                    "template",
                                    draft.message.template + `{${v}}`,
                                  )
                                }
                              >
                                {["职位", "公司", "城市", "招聘者"][i]}
                              </button>
                            ),
                          )}
                        </div>
                      </fieldset>
                      <div className="message-preview">
                        <span>
                          <Sparkles size={13} /> 效果预览
                        </span>
                        <p>
                          {draft.message.mode === "custom"
                            ? template
                            : "仅点击“立即沟通”，不发送上方模板。平台可能自动生成提示。"}
                        </p>
                      </div>
                    </section>
                  </div>
                  <section className="panel limits">
                    <div className="panel-heading">
                      <div>
                        <ShieldCheck size={18} />
                        <h2>运行节奏</h2>
                      </div>
                      <span>
                        今日已尝试 {state.attemptsToday} 次 / 上限{" "}
                        {draft.run.daily_limit} 次
                      </span>
                    </div>
                    <fieldset disabled={locked} className="limit-grid">
                      <Field label="每日尝试上限">
                        <div className="unit-input">
                          <input
                            type="number"
                            min="1"
                            max="500"
                            value={draft.run.daily_limit}
                            onChange={(e) =>
                              update(
                                "run",
                                "daily_limit",
                                Number(e.target.value),
                              )
                            }
                          />
                          <span>次</span>
                        </div>
                      </Field>
                      <Field label="本次最多浏览">
                        <div className="unit-input">
                          <input
                            type="number"
                            min="1"
                            max="2000"
                            value={draft.search.max_jobs}
                            onChange={(e) =>
                              update(
                                "search",
                                "max_jobs",
                                Number(e.target.value),
                              )
                            }
                          />
                          <span>个</span>
                        </div>
                      </Field>
                      <Field label="职位间隔（秒）">
                        <div className="range-input">
                          {draft.run.job_delay.map((v, i) => (
                            <React.Fragment key={i}>
                              {i === 1 && <span>—</span>}
                              <input
                                type="number"
                                min="0"
                                max="3600"
                                value={v}
                                onChange={(e) =>
                                  update(
                                    "run",
                                    "job_delay",
                                    draft.run.job_delay.map((x, n) =>
                                      n === i ? Number(e.target.value) : x,
                                    ),
                                  )
                                }
                              />
                            </React.Fragment>
                          ))}
                        </div>
                      </Field>
                    </fieldset>
                  </section>
                  <section className="panel recent">
                    <div className="panel-heading">
                      <div>
                        <Search size={18} />
                        <h2>最近发现的职位</h2>
                        <span className="count">{state.jobs.length}</span>
                      </div>
                      <button
                        className="text-button"
                        onClick={() => setPage("logs")}
                      >
                        查看运行日志 <ArrowRight size={14} />
                      </button>
                    </div>
                    {state.jobs.length ? (
                      <div className="table-wrap">
                        <table>
                          <thead>
                            <tr>
                              <th>职位 / 公司</th>
                              <th>工作地点</th>
                              <th>薪资</th>
                              <th>发现时间</th>
                              <th />
                            </tr>
                          </thead>
                          <tbody>
                            {state.jobs.slice(0, 8).map((row) => (
                              <tr key={row.job_id}>
                                <td>
                                  <b>{row.title}</b>
                                  <small>{row.company}</small>
                                </td>
                                <td>{row.location || "—"}</td>
                                <td className="salary">
                                  {/[\uE000-\uF8FF]/.test(row.salary)
                                    ? "请查看职位详情"
                                    : row.salary || "—"}
                                </td>
                                <td>{time(row.updated_at)}</td>
                                <td>
                                  <button
                                    className="icon-button"
                                    aria-label={"打开职位 " + row.title}
                                    onClick={action(() => openBrowser(row.url))}
                                  >
                                    <ArrowUpRight size={17} />
                                  </button>
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    ) : (
                      <Empty
                        title="先预览，发现合适的职位"
                        body="配置好求职偏好后，点击“预览职位”。预览不会发起沟通。"
                      />
                    )}
                  </section>
                </>
              )}
              {page === "history" && (
                <>
                  <div className="page-heading">
                    <div className="simple-heading">
                      <h1>每一次沟通，都有记录。</h1>
                      <p>已发送和待核对的职位会自动跳过，避免重复打扰。</p>
                    </div>
                    <Button
                      icon={Download}
                      onClick={action(
                        () => api.exportHistory(),
                        "投递历史已导出",
                      )}
                    >
                      导出 CSV
                    </Button>
                  </div>
                  <section className="panel">
                    <div className="table-toolbar">
                      <div className="search-box">
                        <Search size={17} />
                        <input
                          aria-label="搜索投递记录"
                          placeholder="搜索职位、公司或消息…"
                          value={search}
                          onChange={(e) => setSearch(e.target.value)}
                        />
                      </div>
                      <select
                        aria-label="按发送状态筛选"
                        value={statusFilter}
                        onChange={(e) => setStatusFilter(e.target.value)}
                      >
                        <option value="">全部状态</option>
                        {[
                          "sent",
                          "contacted",
                          "unknown",
                          "partial",
                          "not_sent",
                        ].map((v) => (
                          <option key={v} value={v}>
                            {labels[v]} ({v})
                          </option>
                        ))}
                      </select>
                      <span>{rows.length} 条记录</span>
                    </div>
                    {rows.length ? (
                      <div className="table-wrap">
                        <table>
                          <thead>
                            <tr>
                              <th>职位 / 公司</th>
                              <th>沟通状态</th>
                              <th>时间</th>
                              <th>操作</th>
                            </tr>
                          </thead>
                          <tbody>
                            {rows.map((row) => (
                              <tr key={row.job_id}>
                                <td>
                                  <b>{row.title}</b>
                                  <small>{row.company}</small>
                                </td>
                                <td>
                                  <Badge value={row.status} />
                                </td>
                                <td>{time(row.updated_at)}</td>
                                <td>
                                  <button
                                    className="text-button"
                                    onClick={() => {
                                      setSelected(row);
                                      setResolveNote("");
                                    }}
                                  >
                                    查看详情 <ChevronRight size={14} />
                                  </button>
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    ) : (
                      <Empty
                        icon={History}
                        title={
                          search || statusFilter
                            ? "没有符合条件的记录"
                            : "还没有投递记录"
                        }
                        body="发起沟通后，发送内容和回执会保存在这里。"
                      />
                    )}
                  </section>
                </>
              )}
              {page === "logs" && (
                <>
                  <div className="page-heading">
                    <div className="simple-heading">
                      <h1>运行日志</h1>
                      <p>任务进度自动更新，出现问题时可在这里查看原因。</p>
                    </div>
                    <span className="live-label">
                      <i /> 每秒更新
                    </span>
                  </div>
                  <section className="panel logs-panel">
                    {state.events.length ? (
                      state.events
                        .slice()
                        .reverse()
                        .map((row) => (
                          <div className={"log-row " + row.level} key={row.id}>
                            <time>{time(row.time)}</time>
                            <span className="log-level">{row.level}</span>
                            <div>
                              <strong>{row.kind}</strong>
                              <p>{row.message}</p>
                            </div>
                          </div>
                        ))
                    ) : (
                      <Empty
                        icon={ScrollText}
                        title="等待第一次运行"
                        body="登录、搜索、筛选和发送过程会在这里实时显示。"
                      />
                    )}
                  </section>
                </>
              )}
              {page === "settings" && (
                <>
                  <div className="page-heading">
                    <div className="simple-heading">
                      <h1>让工作台适合你的节奏。</h1>
                      <p>登录状态与历史只保存在这台设备上。</p>
                    </div>
                  </div>
                  <section className="panel settings-panel">
                    <div className="setting-row">
                      <div className="setting-icon">
                        <Monitor size={22} />
                      </div>
                      <div>
                        <h3>BOSS 直聘登录</h3>
                        <p>
                          {state.browser.loggedIn
                            ? "已检测到登录。下次打开应用将复用登录目录。"
                            : "点击登录，在独立浏览器窗口中扫码或完成验证。"}
                        </p>
                      </div>
                      <Button disabled={locked} onClick={() => start("login")}>
                        {state.browser.loggedIn ? "检查登录" : "登录 BOSS 直聘"}
                      </Button>
                    </div>
                    <div className="setting-row">
                      <div className="setting-icon">
                        <FolderOpen size={22} />
                      </div>
                      <div>
                        <h3>本地数据</h3>
                        <p>投递历史、运行日志与错误现场均保存在本机。</p>
                        <code>{state.directory}</code>
                      </div>
                      <Button onClick={action(() => api.openData())}>
                        打开目录
                      </Button>
                    </div>
                    <div className="setting-row">
                      <div className="setting-icon">
                        <SlidersHorizontal size={22} />
                      </div>
                      <div>
                        <h3>配置迁移</h3>
                        <p>导出的配置不包含账号登录状态或投递历史。</p>
                      </div>
                      <Button
                        icon={Upload}
                        disabled={locked}
                        onClick={action(async () => {
                          const result = await api.importConfig();
                          if (result) setDraft(result);
                          return result;
                        }, "配置已导入")}
                      >
                        导入
                      </Button>
                      <Button
                        icon={Download}
                        disabled={locked}
                        onClick={action(async () => {
                          await save();
                          return api.exportConfig();
                        }, "配置已导出")}
                      >
                        导出
                      </Button>
                    </div>
                    <div className="setting-row">
                      <div className="setting-icon">
                        <CircleHelp size={22} />
                      </div>
                      <div>
                        <h3>浏览器诊断</h3>
                        <p>检查当前页面结构，并将结果保存到数据目录。</p>
                      </div>
                      <Button
                        disabled={locked}
                        onClick={() => start("diagnose")}
                      >
                        运行诊断
                      </Button>
                    </div>
                  </section>
                  <section className="panel settings-panel">
                    <div className="panel-heading">
                      <div>
                        <Settings size={18} />
                        <h2>更多运行设置</h2>
                      </div>
                      <Button
                        icon={Save}
                        disabled={locked}
                        onClick={action(save, "配置已保存")}
                      >
                        保存
                      </Button>
                    </div>
                    <fieldset
                      disabled={locked}
                      className="form-grid settings-fields"
                    >
                      {[
                        ["browser", "login_timeout_seconds", "等待登录（秒）"],
                        ["browser", "timeout_seconds", "页面超时（秒）"],
                        ["search", "max_scrolls", "最多滚动次数"],
                        ["run", "cooldown_every", "每几次沟通休息"],
                        ["run", "max_errors", "最多页面错误数"],
                      ].map(([group, key, label]) => (
                        <Field key={key} label={label}>
                          <input
                            type="number"
                            value={draft[group][key]}
                            onChange={(e) =>
                              update(group, key, Number(e.target.value))
                            }
                          />
                        </Field>
                      ))}
                      {[
                        ["action_delay", "操作间隔（秒）"],
                        ["cooldown_seconds", "批次休息（秒）"],
                      ].map(([key, label]) => (
                        <Field key={key} label={label}>
                          <div className="range-input">
                            {draft.run[key].map((v, i) => (
                              <React.Fragment key={i}>
                                {i === 1 && <span>—</span>}
                                <input
                                  type="number"
                                  min="0"
                                  max="3600"
                                  value={v}
                                  onChange={(e) =>
                                    update(
                                      "run",
                                      key,
                                      draft.run[key].map((x, n) =>
                                        n === i ? Number(e.target.value) : x,
                                      ),
                                    )
                                  }
                                />
                              </React.Fragment>
                            ))}
                          </div>
                        </Field>
                      ))}
                    </fieldset>
                  </section>
                </>
              )}
            </>
          )}
          {page !== "browser" && (
            <footer>
              <ShieldCheck size={13} /> 以网站实际送达回执为准 ·
              不确定的发送不会自动重试
            </footer>
          )}
        </div>
      </main>
      {confirm && draft && (
        <div className="modal-backdrop">
          <section
            className="modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="confirm-title"
          >
            <button
              className="modal-close icon-button"
              aria-label="关闭发送确认"
              onClick={() => setConfirm(false)}
            >
              <X size={20} />
            </button>
            <div className="modal-icon">
              <Send size={24} />
            </div>
            <h2 id="confirm-title">确认本次投递范围</h2>
            <p>启动后，将按以下条件查找职位并向匹配的招聘者发起沟通。</p>
            <dl>
              <dt>搜索职位</dt>
              <dd>{draft.search.keywords.join(" / ")}</dd>
              <dt>工作城市</dt>
              <dd>{draft.search.city}</dd>
              <dt>最多尝试</dt>
              <dd>
                {draft.run.max_sends} 次 · 每日上限 {draft.run.daily_limit} 次
              </dd>
              <dt>今日剩余</dt>
              <dd>
                {Math.max(0, draft.run.daily_limit - state.attemptsToday)} 次 ·
                本轮最多浏览 {draft.search.max_jobs} 个
              </dd>
              <dt>沟通方式</dt>
              <dd>
                {draft.message.mode === "custom"
                  ? "自定义招呼"
                  : "仅建立平台沟通"}
              </dd>
            </dl>
            <div className="confirm-message">
              {draft.message.mode === "custom"
                ? template
                : "仅建立沟通，不发送自定义模板。"}
            </div>
            <div className="modal-actions">
              <Button onClick={() => setConfirm(false)}>返回修改</Button>
              <Button
                className="primary"
                icon={Send}
                disabled={busy}
                onClick={() => start("send")}
              >
                {busy ? "正在启动…" : "确认开始"}
              </Button>
            </div>
          </section>
        </div>
      )}
      {selected && (
        <div className="modal-backdrop">
          <section
            className="modal history-detail"
            role="dialog"
            aria-modal="true"
            aria-labelledby="detail-title"
          >
            <button
              className="modal-close icon-button"
              aria-label="关闭记录详情"
              onClick={() => setSelected(null)}
            >
              <X size={20} />
            </button>
            <Badge value={selected.status} />
            <h2 id="detail-title">{selected.title}</h2>
            <p>
              {selected.company} · {time(selected.created_at)}
            </p>
            <div className="confirm-message">
              {selected.message || "未发送自定义消息"}
            </div>
            <h4>核对记录</h4>
            <p className="detail-note">{selected.note}</p>
            {["unknown", "partial"].includes(selected.status) && (
              <Button
                icon={ShieldCheck}
                disabled={locked}
                onClick={action(async () => {
                  await api.start({ mode: "verify", jobId: selected.job_id });
                  setSelected(null);
                  setPage("browser");
                }, "正在只读核对历史消息的送达状态")}
              >
                只读核对送达
              </Button>
            )}
            {["unknown", "partial", "sending"].includes(selected.status) && (
              <div className="resolve-form">
                <Field
                  label="人工核对说明"
                  hint="请先在网站确认实际发送结果，再更改状态。"
                >
                  <input
                    value={resolveNote}
                    onChange={(e) => setResolveNote(e.target.value)}
                    placeholder="填写在网站看到的结果"
                  />
                </Field>
                <div className="modal-actions">
                  <Button
                    disabled={locked || !resolveNote.trim()}
                    onClick={action(async () => {
                      await api.resolve({
                        jobId: selected.job_id,
                        status: "not_sent",
                        note: resolveNote,
                      });
                      setSelected(null);
                    }, "已登记人工核对结果")}
                  >
                    确认未发送
                  </Button>
                  <Button
                    disabled={locked || !resolveNote.trim()}
                    onClick={action(async () => {
                      await api.resolve({
                        jobId: selected.job_id,
                        status: "sent",
                        note: resolveNote,
                      });
                      setSelected(null);
                    }, "已登记人工核对结果")}
                  >
                    确认已发送
                  </Button>
                </div>
              </div>
            )}
            <Button
              icon={ArrowUpRight}
              onClick={action(async () => {
                await openBrowser(selected.url);
                setSelected(null);
              })}
            >
              在浏览器查看职位
            </Button>
          </section>
        </div>
      )}
    </div>
  );
}
createRoot(document.getElementById("root")).render(<App />);
