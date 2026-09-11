import React, { useEffect, useState } from "react";
import {
  MessageSquare,
  RefreshCw,
  Sparkles,
  Send,
  Square,
  X,
  Radio,
  ScrollText,
} from "lucide-react";

const statuses = {
  draft: "草稿",
  sent: "已送达",
  unknown: "待核对",
  sending: "发送中",
  not_sent: "已核实未发送",
};
export default function RepliesPanel({
  state,
  locked,
  act,
  saveModel,
  onSettings,
  onBrowser,
}) {
  const reply = state.replyState || {};
  const monitor = state.autoReply || {};
  const [jobId, setJobId] = useState(reply.job?.job_id || "");
  const [filter, setFilter] = useState("");
  const [text, setText] = useState("");
  const [confirm, setConfirm] = useState(null);
  const [resolve, setResolve] = useState(null);
  const [note, setNote] = useState("");
  const [resolution, setResolution] = useState("sent");
  const loaded = reply.job?.job_id === jobId;
  const draft = loaded ? reply.draft : null;
  const jobs =
    state.replyContacts ||
    state.history.filter(
      (row) =>
        (row.platform || "boss") === "boss" &&
        ["sent", "contacted", "partial", "unknown"].includes(row.status),
    );
  const selected = jobs.find((row) => row.job_id === jobId);
  const pending = state.replies?.some(
    (row) =>
      row.job_id === jobId && ["unknown", "sending"].includes(row.status),
  );
  const run = (fn) => () => act(fn).catch(() => {});
  useEffect(() => {
    setText(draft?.message || "");
    setConfirm(null);
  }, [draft?.id, draft?.message, jobId]);
  return (
    <>
      <div className="page-heading">
        <h1>消息回复</h1>
        <button className="btn" onClick={onSettings}>
          <Sparkles size={16} />
          模型与人格
        </button>
      </div>
      {!state.llm?.configured && (
        <div className="notice">
          <span>请先配置 DeepSeek API Key。</span>
          <button className="text-button" onClick={onSettings}>
            前往配置
          </button>
        </div>
      )}
      <section
        className={
          "panel auto-reply-panel " + (monitor.enabled ? "enabled" : "")
        }
        aria-label="自动回复设置"
      >
        <div className="auto-reply-heading">
          <div>
            <h2>
              <Radio size={19} />
              自动回复
            </h2>
            <p>
              每轮间隔 {monitor.intervalSeconds || 30}{" "}
              秒，自动回复招聘方的文字消息（含已读未回复）。
            </p>
          </div>
          <button
            className="auto-reply-switch"
            type="button"
            role="switch"
            aria-label="自动回复"
            aria-checked={!!monitor.enabled}
            disabled={
              monitor.status === "stopping" ||
              (!monitor.enabled && (locked || !state.llm?.configured))
            }
            onClick={run(async () => {
              if (!monitor.enabled) await saveModel();
              return window.desk.autoReply({
                action: monitor.enabled ? "disable" : "enable",
              });
            })}
          >
            <span className="switch-track">
              <span />
            </span>
            {monitor.enabled ? "已开启" : "已关闭"}
          </button>
        </div>
        <p
          className={
            "auto-reply-note " +
            (monitor.status === "needs_attention" ? "ai-warning" : "")
          }
          role="status"
        >
          {monitor.note || "未开启"}
        </p>
        <div className="auto-reply-stats">
          <span>
            本轮检查 <b>{monitor.scanned || 0}</b> 个会话
          </span>
          <span>
            发现待回复 <b>{monitor.pending || 0}</b> 条
          </span>
          <span>
            本次开启已送达 <b>{monitor.totalSent || 0}</b> 条
          </span>
          {!!monitor.errors && (
            <span className="ai-warning">
              需查看日志 <b>{monitor.errors}</b> 项
            </span>
          )}
        </div>
        <div className="auto-reply-footer">
          <button
            className="btn"
            disabled={locked || monitor.enabled}
            onClick={run(() => window.desk.autoReply({ action: "scan" }))}
          >
            <RefreshCw size={15} />
            扫描待回复
          </button>
          <small>
            {monitor.lastScanAt && (
              <>
                上次检查{" "}
                {new Date(monitor.lastScanAt).toLocaleTimeString("zh-CN", {
                  hour12: false,
                })}
                {monitor.nextScanAt ? " · " : ""}
              </>
            )}
            {monitor.nextScanAt && (
              <>
                下次检查{" "}
                {new Date(monitor.nextScanAt).toLocaleTimeString("zh-CN", {
                  hour12: false,
                })}
              </>
            )}
          </small>
        </div>
        <p className="ai-hint">
          使用已保存的模型与人格；其他任务期间等待，退出后停止。扫描仅读取。
        </p>
      </section>
      <div className="reply-grid">
        <section className="panel reply-contacts">
          <div className="ai-section-title">
            <MessageSquare size={19} />
            <h2>已沟通职位</h2>
            <small>{jobs.length}</small>
          </div>
          <input
            aria-label="搜索已沟通职位"
            placeholder="搜索职位或公司"
            value={filter}
            onChange={(event) => setFilter(event.target.value)}
          />
          <div className="reply-contact-list">
            {jobs
              .filter((row) =>
                `${row.title} ${row.company} ${row.recruiter || ""}`.includes(
                  filter,
                ),
              )
              .map((row) => (
                <button
                  key={row.job_id}
                  disabled={locked}
                  className={
                    "reply-contact " + (row.job_id === jobId ? "selected" : "")
                  }
                  onClick={() => setJobId(row.job_id)}
                >
                  <strong>{row.title}</strong>
                  <span>{row.company}</span>
                  <small>
                    {row.recruiter?.split("\n")[0] || "招聘方"} ·{" "}
                    {new Date(row.updated_at).toLocaleDateString("zh-CN")}
                  </small>
                </button>
              ))}
          </div>
          {!jobs.length && (
            <p className="ai-hint">暂无会话，点击“扫描待回复”读取。</p>
          )}
        </section>
        <section className="panel reply-compose">
          <div className="reply-target">
            <div>
              <h2>{selected?.title || "选择会话"}</h2>
              <p>{selected?.company || "从左侧选择后读取对话。"}</p>
            </div>
            <button
              className="btn"
              disabled={locked || !selected}
              onClick={run(() => window.desk.reply({ action: "read", jobId }))}
            >
              <RefreshCw size={15} />
              读取对话
            </button>
          </div>
          {loaded && reply.context && (
            <>
              <div className="reply-transcript" aria-label="近期聊天记录">
                {reply.context.messages.map((message, index) => (
                  <div key={index} className={"reply-bubble " + message.role}>
                    <small>{message.role === "user" ? "招聘方" : "我"}</small>
                    <p>{message.content}</p>
                  </div>
                ))}
                {!reply.context.messages.length && (
                  <p className="ai-hint">当前会话暂无可读取消息。</p>
                )}
              </div>
              <p
                className={
                  "ai-hint " + (reply.status === "error" ? "ai-warning" : "")
                }
                role="status"
              >
                {reply.note}
              </p>
              {pending && (
                <p className="ai-warning">
                  有待核对的回复，请先在回复记录中核实。
                </p>
              )}
              <div className="ai-actions">
                <button
                  className="btn"
                  disabled={
                    locked ||
                    !state.llm?.configured ||
                    !reply.context.canReply ||
                    pending
                  }
                  onClick={run(async () => {
                    await saveModel();
                    return window.desk.reply({ action: "generate", jobId });
                  })}
                >
                  <Sparkles size={16} />
                  {draft ? "重新生成" : "生成回复草稿"}
                </button>
                <button
                  className="text-button"
                  disabled={locked}
                  onClick={onBrowser}
                >
                  查看浏览器
                </button>
              </div>
              <p className="ai-hint">系统提示词和近期对话将发送至 DeepSeek。</p>
            </>
          )}
          {loaded && !reply.context && (
            <p
              className={
                "ai-hint " + (reply.status === "error" ? "ai-warning" : "")
              }
              role="status"
            >
              {reply.note}
            </p>
          )}
          {draft && (
            <div className="reply-draft">
              <label className="field">
                <span>回复草稿 · {draft.model}</span>
                <textarea
                  aria-label="回复草稿"
                  value={text}
                  disabled={locked}
                  onChange={(event) => setText(event.target.value)}
                />
                <small>
                  {[...text.trim()].length.toLocaleString()} 字 · 发送前可编辑
                </small>
              </label>
              <button
                className="btn primary"
                disabled={locked || !text.trim() || pending}
                onClick={() =>
                  setConfirm({
                    message: text.trim(),
                    replyId: draft.id,
                    title: selected.title,
                    company: selected.company,
                  })
                }
              >
                <Send size={16} />
                检查并发送
              </button>
            </div>
          )}
          {state.active && state.run?.mode.startsWith("reply_") && (
            <button
              className="btn danger"
              onClick={run(() => window.desk.control({ action: "stop" }))}
            >
              <Square size={14} />
              停止回复操作
            </button>
          )}
        </section>
      </div>
      <section className="panel ai-reply-history">
        <div className="ai-section-title">
          <MessageSquare size={19} />
          <h2>回复记录</h2>
          <small>
            发送上限与投递共用，今日已使用 {state.attemptsToday} /{" "}
            {state.config.run.daily_limit}
          </small>
        </div>
        {state.replies?.length ? (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>职位 / 公司</th>
                  <th>回复正文</th>
                  <th>状态</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {state.replies.map((row) => (
                  <tr key={row.id}>
                    <td>
                      <b>{row.title}</b>
                      <small>{row.company}</small>
                      <small>
                        {row.source === "auto" ? "自动回复" : "手动回复"} ·{" "}
                        {new Date(row.created_at).toLocaleString("zh-CN", {
                          hour12: false,
                        })}
                      </small>
                    </td>
                    <td className="reply-history-message">
                      <p>{row.message}</p>
                      <small>{row.note}</small>
                    </td>
                    <td>
                      <span className={"badge " + row.status}>
                        {statuses[row.status] || row.status}
                      </span>
                    </td>
                    <td>
                      {row.status === "unknown" ? (
                        <button
                          className="text-button"
                          disabled={locked}
                          onClick={() => {
                            setResolve(row);
                            setNote("");
                            setResolution("sent");
                          }}
                        >
                          人工核实
                        </button>
                      ) : (
                        "—"
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="ai-hint">暂无回复记录</p>
        )}
      </section>
      <section className="panel auto-reply-log" aria-label="回复活动日志">
        <div className="ai-section-title">
          <ScrollText size={19} />
          <h2>回复日志</h2>
        </div>
        {state.replyEvents?.length ? (
          <div className="reply-log-list">
            {state.replyEvents.map((event) => (
              <div
                className={"reply-log-row " + event.level.toLowerCase()}
                key={event.id}
              >
                <time>
                  {new Date(event.time).toLocaleString("zh-CN", {
                    hour12: false,
                  })}
                </time>
                <div>
                  {event.company && (
                    <strong>
                      {event.company} · {event.title}
                    </strong>
                  )}
                  <p>{event.message}</p>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <p className="ai-hint">暂无回复日志</p>
        )}
      </section>
      {confirm && (
        <div className="modal-backdrop">
          <section
            className="modal ai-confirm"
            role="dialog"
            aria-modal="true"
            aria-labelledby="reply-confirm-title"
          >
            <button
              className="modal-close"
              aria-label="关闭发送确认"
              onClick={() => setConfirm(null)}
            >
              <X size={20} />
            </button>
            <h2 id="reply-confirm-title">确认发送这条回复</h2>
            <p>
              {confirm.company} · {confirm.title}
            </p>
            <div className="reply-confirm-text">{confirm.message}</div>
            <p className="ai-hint">发送前核对会话，收到回执后记录送达。</p>
            <div className="ai-actions">
              <button className="btn" onClick={() => setConfirm(null)}>
                继续编辑
              </button>
              <button
                className="btn primary"
                disabled={locked}
                onClick={run(async () => {
                  await window.desk.reply({
                    action: "send",
                    replyId: confirm.replyId,
                    message: confirm.message,
                  });
                  setConfirm(null);
                })}
              >
                <Send size={16} />
                确认发送这条回复
              </button>
            </div>
          </section>
        </div>
      )}
      {resolve && (
        <div className="modal-backdrop">
          <section
            className="modal ai-confirm"
            role="dialog"
            aria-modal="true"
            aria-labelledby="reply-resolve-title"
          >
            <button
              className="modal-close"
              aria-label="关闭核实"
              onClick={() => setResolve(null)}
            >
              <X size={20} />
            </button>
            <h2 id="reply-resolve-title">核实回复结果</h2>
            <p>
              {resolve.company} · {resolve.title}
            </p>
            <div className="reply-confirm-text">{resolve.message}</div>
            <p>请先到浏览器核对这条消息的实际发送结果。</p>
            <label className="field">
              <span>核实结果</span>
              <select
                value={resolution}
                onChange={(event) => setResolution(event.target.value)}
              >
                <option value="sent">已发送</option>
                <option value="not_sent">未发送</option>
              </select>
            </label>
            <label className="field">
              <span>核实说明</span>
              <textarea
                value={note}
                maxLength={1000}
                onChange={(event) => setNote(event.target.value)}
              />
            </label>
            <button
              className="btn primary"
              disabled={locked || !note.trim()}
              onClick={run(async () => {
                await window.desk.resolveReply({
                  replyId: resolve.id,
                  status: resolution,
                  note,
                });
                setResolve(null);
              })}
            >
              保存核实结果
            </button>
          </section>
        </div>
      )}
    </>
  );
}
