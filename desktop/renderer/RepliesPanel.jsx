import React, { useEffect, useState } from "react";
import {
  MessageSquare,
  RefreshCw,
  Sparkles,
  Send,
  Square,
  X,
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
    state.history.filter((row) =>
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
        <div className="simple-heading">
          <h1>消息回复</h1>
          <p>读取对话，让你的求职人格生成回复，再由你确认发送。</p>
        </div>
        <button className="btn" onClick={onSettings}>
          <Sparkles size={16} />
          模型与人格
        </button>
      </div>
      {!state.llm?.configured && (
        <div className="notice">
          <span>先在“模型与人格”中配置 DeepSeek API Key，即可生成回复。</span>
          <button className="text-button" onClick={onSettings}>
            前往配置
          </button>
        </div>
      )}
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
            <p className="ai-hint">
              完成首次沟通后，职位会出现在这里。只会读取你选中的会话。
            </p>
          )}
        </section>
        <section className="panel reply-compose">
          <div className="reply-target">
            <div>
              <h2>{selected?.title || "选择一个会话"}</h2>
              <p>
                {selected?.company || "从左侧选择已沟通的职位，读取近期对话。"}
              </p>
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
                  这个会话有待核对的回复，请先在下方回复记录中核实。
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
              <p className="ai-hint">
                生成时会将当前系统提示词和近期对话发送给 DeepSeek。
              </p>
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
                  maxLength={2000}
                  onChange={(event) => setText(event.target.value)}
                />
                <small>
                  {[...text.trim()].length} / 1,000 字 · 发送前可编辑
                </small>
              </label>
              <button
                className="btn primary"
                disabled={
                  locked ||
                  !text.trim() ||
                  [...text.trim()].length > 1000 ||
                  pending
                }
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
          <p className="ai-hint">
            生成的草稿、已发送回复和待核对结果会保存在这里。
          </p>
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
            <p className="ai-hint">
              发送前会再次核对会话和新消息。只有出现送达或已读回执才会记为成功。
            </p>
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
