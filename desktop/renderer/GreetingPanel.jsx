import React, { useState } from "react";
import { Send, Sparkles, FileText, Square } from "lucide-react";

export default function GreetingPanel({
  config,
  state,
  locked,
  update,
  template,
  save,
  act,
  onResume,
  onModels,
}) {
  const [jobId, setJobId] = useState("");
  const document = state.resume?.document;
  const greeting = state.resume?.greeting;
  const ready = !!document?.profile && !!state.llm?.configured;
  const generating =
    state.active && state.resume?.state.status === "generating";
  const run = (fn) => () => act(fn).catch(() => {});
  const currentPreview =
    greeting?.job.job_id === jobId &&
    greeting.resumeRevision === document?.revision &&
    greeting.instructions === config.instructions &&
    greeting.model === state.config.llm.model;
  return (
    <section className="panel message-panel">
      <div className="panel-heading">
        <div>
          <Send size={18} />
          <h2>打招呼内容</h2>
        </div>
      </div>
      <fieldset disabled={locked}>
        <label className="field">
          <span>沟通方式</span>
          <select
            aria-label="沟通方式"
            value={config.mode}
            onChange={(event) => update("mode", event.target.value)}
          >
            <option value="custom">发送自定义招呼</option>
            <option value="ai">AI 岗位招呼</option>
            <option value="platform">仅建立平台沟通</option>
          </select>
        </label>
        {config.mode === "custom" && (
          <>
            <textarea
              className="message-input"
              value={config.template}
              maxLength={1000}
              onChange={(event) => update("template", event.target.value)}
              aria-label="打招呼模板"
            />
            <div className="variables">
              插入变量{" "}
              {["title", "company", "city", "recruiter"].map((value, index) => (
                <button
                  key={value}
                  type="button"
                  onClick={() =>
                    update("template", config.template + "{" + value + "}")
                  }
                >
                  {["职位", "公司", "城市", "招聘者"][index]}
                </button>
              ))}
            </div>
          </>
        )}
        {config.mode === "ai" && (
          <>
            <div className="greeting-resume">
              <FileText size={17} />
              <span>
                {document?.profile
                  ? document.source_name
                  : "请先上传并分析简历"}
              </span>
              <button type="button" className="text-button" onClick={onResume}>
                个人简历
              </button>
            </div>
            {!state.llm?.configured && (
              <p className="ai-warning">
                请先配置 DeepSeek。
                <button
                  type="button"
                  className="text-button"
                  onClick={onModels}
                >
                  模型与人格
                </button>
              </p>
            )}
            <label className="field">
              <span>AI 招呼要求</span>
              <textarea
                aria-label="AI 招呼要求"
                rows={3}
                value={config.instructions || ""}
                maxLength={4000}
                onChange={(event) => update("instructions", event.target.value)}
              />
            </label>
            <label className="field">
              <span>预览岗位</span>
              <select
                aria-label="预览岗位"
                value={jobId}
                onChange={(event) => setJobId(event.target.value)}
              >
                <option value="">选择已发现的职位</option>
                {state.jobs.map((job) => (
                  <option key={job.job_id} value={job.job_id}>
                    {job.title} · {job.company}
                  </option>
                ))}
              </select>
            </label>
            {!state.jobs.length && (
              <p className="ai-hint">先点击“预览职位”获取岗位。</p>
            )}
            <button
              type="button"
              className="btn"
              disabled={locked || !ready || !jobId}
              onClick={run(async () => {
                await save();
                return window.desk.resume({
                  action: "previewGreeting",
                  revision: document.revision,
                  jobId,
                });
              })}
            >
              <Sparkles size={15} /> {generating ? "正在生成…" : "生成招呼预览"}
            </button>
          </>
        )}
      </fieldset>
      {config.mode === "ai" ? (
        <>
          {generating && (
            <button
              className="btn"
              onClick={run(() => window.desk.control({ action: "stop" }))}
            >
              <Square size={14} />
              停止生成
            </button>
          )}
          <p className="ai-hint">
            每个岗位单独生成。简历正文、特点和职位资料将发送至 DeepSeek。
          </p>
          {state.resume?.state.status === "error" && (
            <p role="status" className="ai-warning">
              {state.resume.state.note}
            </p>
          )}
          {greeting && (
            <div className="message-preview greeting-preview">
              <span>
                <Sparkles size={13} /> 招呼预览 · 未发送
              </span>
              {currentPreview ? (
                <>
                  <b>
                    {greeting.job.title} · {greeting.job.company}
                  </b>
                  <p>{greeting.message}</p>
                  <small>投递时会结合最新职位详情重新生成。</small>
                </>
              ) : (
                <p>岗位或配置已变化，请重新生成预览。</p>
              )}
            </div>
          )}
        </>
      ) : (
        <div className="message-preview">
          <span>
            <Sparkles size={13} /> 预览
          </span>
          <p>
            {config.mode === "custom"
              ? template
              : "仅建立平台沟通，不发送模板。"}
          </p>
        </div>
      )}
    </section>
  );
}
