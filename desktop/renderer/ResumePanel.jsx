import React, { useEffect, useState } from "react";
import { FileText, Upload, Sparkles, Save, Square, Trash2 } from "lucide-react";

const fields = [
  ["summary", "个人概况"],
  ["skills", "核心技能"],
  ["experiences", "代表经历与成果"],
  ["strengths", "岗位优势"],
];
const editable = (profile) =>
  Object.fromEntries(
    fields.map(([key]) => [
      key,
      key === "summary"
        ? profile?.[key] || ""
        : (profile?.[key] || []).join("\n"),
    ]),
  );

export default function ResumePanel({
  state,
  locked,
  act,
  saveModel,
  onModels,
  onGreeting,
}) {
  const document = state.resume?.document;
  const status = state.resume?.state || {};
  const [text, setText] = useState(document?.text || "");
  const [profile, setProfile] = useState(editable(document?.profile));
  const [remove, setRemove] = useState(false);
  useEffect(() => {
    setText(document?.text || "");
    setProfile(editable(document?.profile));
  }, [document?.revision]);
  const textDirty = text !== (document?.text || "");
  const profileDirty =
    JSON.stringify(profile) !== JSON.stringify(editable(document?.profile));
  const analyzing = state.active && status.status === "analyzing";
  const run = (fn, success) => () => act(fn, success).catch(() => {});
  return (
    <>
      <div className="page-heading">
        <h1>个人简历</h1>
        <button
          className="btn primary"
          disabled={locked}
          onClick={run(() => window.desk.resume({ action: "import" }))}
        >
          <Upload size={16} /> 上传简历
        </button>
      </div>
      {!state.llm?.configured && (
        <div className="notice">
          <span>分析简历前，请先配置 DeepSeek API Key。</span>
          <button className="text-button" onClick={onModels}>
            前往配置
          </button>
        </div>
      )}
      <div className="resume-grid">
        <section className="panel resume-source">
          <div className="ai-section-title">
            <FileText size={19} />
            <h2>简历正文</h2>
            {document && (
              <span className="badge">
                {document.profile ? "已分析" : "待分析"}
              </span>
            )}
          </div>
          <p className="resume-file">
            {document?.source_name || "上传文件或粘贴正文"}
          </p>
          <p className="ai-hint">
            PDF、DOCX、TXT · 最大 10 MB · PDF 需包含可复制文字
          </p>
          <label className="field">
            <span>正文</span>
            <textarea
              className="resume-text"
              aria-label="简历正文"
              value={text}
              onChange={(event) => setText(event.target.value)}
              disabled={locked}
              maxLength={60000}
              placeholder="粘贴简历正文，也可上传文件后校对"
            />
            <small>{text.length.toLocaleString()} / 60,000 字</small>
          </label>
          <div className="ai-actions">
            <button
              className="btn"
              disabled={locked || !text.trim() || !textDirty}
              onClick={run(() =>
                window.desk.resume({ action: "saveText", text }),
              )}
            >
              <Save size={15} /> 保存正文
            </button>
            <button
              className="btn primary"
              disabled={locked || !text.trim() || !state.llm?.configured}
              onClick={run(async () => {
                let current = state.resume;
                if (textDirty || !document)
                  current = await window.desk.resume({
                    action: "saveText",
                    text,
                  });
                await saveModel();
                return window.desk.resume({
                  action: "analyze",
                  revision: current.document.revision,
                });
              })}
            >
              <Sparkles size={15} />{" "}
              {analyzing
                ? "正在分析…"
                : document?.profile
                  ? "重新分析"
                  : "分析简历"}
            </button>
            {analyzing && (
              <button
                className="btn"
                onClick={run(() => window.desk.control({ action: "stop" }))}
              >
                <Square size={14} />
                停止分析
              </button>
            )}
          </div>
          <p className="ai-hint">
            分析时，简历正文会发送至
            DeepSeek。简历和分析结果保存在本机，不随配置导出。
          </p>
          {document && (
            <button
              className="text-button"
              disabled={locked}
              onClick={() => setRemove(true)}
            >
              <Trash2 size={14} /> 移除简历
            </button>
          )}
        </section>
        <section className="panel resume-profile">
          <div className="ai-section-title">
            <Sparkles size={19} />
            <h2>简历特点</h2>
          </div>
          {status.note && (
            <p
              role="status"
              className={status.status === "error" ? "ai-warning" : "ai-hint"}
            >
              {status.note}
            </p>
          )}
          {document?.profile ? (
            <>
              <p className="ai-hint">
                {document.profile_model} ·{" "}
                {new Date(document.analyzed_at).toLocaleString()} · 每行一项
              </p>
              {fields.map(([key, label]) => (
                <label className="field" key={key}>
                  <span>{label}</span>
                  <textarea
                    aria-label={label}
                    value={profile[key]}
                    rows={key === "summary" ? 3 : 4}
                    maxLength={key === "summary" ? 4000 : 20000}
                    disabled={locked || textDirty}
                    onChange={(event) =>
                      setProfile((old) => ({
                        ...old,
                        [key]: event.target.value,
                      }))
                    }
                  />
                </label>
              ))}
              {textDirty && (
                <p className="ai-warning">正文已修改，保存后需重新分析。</p>
              )}
              <div className="ai-actions">
                <button
                  className="btn"
                  disabled={
                    locked ||
                    textDirty ||
                    !profileDirty ||
                    !profile.summary.trim()
                  }
                  onClick={run(() =>
                    window.desk.resume({
                      action: "saveProfile",
                      revision: document.revision,
                      profile: Object.fromEntries(
                        fields.map(([key]) => [
                          key,
                          key === "summary"
                            ? profile[key].trim()
                            : profile[key]
                                .split("\n")
                                .map((value) => value.trim())
                                .filter(Boolean),
                        ]),
                      ),
                    }),
                  )}
                >
                  <Save size={15} /> 保存特点
                </button>
                <button
                  className="btn primary"
                  disabled={locked || textDirty || profileDirty}
                  onClick={onGreeting}
                >
                  使用 AI 岗位招呼
                </button>
              </div>
            </>
          ) : (
            <div className="empty resume-empty">
              <FileText size={28} />
              <h3>{analyzing ? "正在分析简历…" : "暂无分析结果"}</h3>
            </div>
          )}
        </section>
      </div>
      {remove && (
        <div className="modal-backdrop">
          <section
            className="modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="remove-resume-title"
          >
            <h2 id="remove-resume-title">移除本地简历？</h2>
            <p>将移除导入的正文和分析结果；原文件与投递记录保留。</p>
            <div className="modal-actions">
              <button className="btn" onClick={() => setRemove(false)}>
                取消
              </button>
              <button
                className="btn danger"
                disabled={locked}
                onClick={run(async () => {
                  await window.desk.resume({
                    action: "clear",
                    revision: document.revision,
                  });
                  setRemove(false);
                })}
              >
                确认移除
              </button>
            </div>
          </section>
        </div>
      )}
    </>
  );
}
