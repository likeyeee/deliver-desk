import React from "react";
import { Send, Sparkles, FileText, History } from "lucide-react";

export default function GreetingPanel({
  platform,
  config,
  state,
  locked,
  update,
  template,
  onResume,
  onModels,
  onHistory,
}) {
  const document = state.resume?.document;
  const isZhaopin = platform === "zhaopin";
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
            <option value="platform">
              {isZhaopin ? "使用网站当前招呼" : "仅建立平台沟通"}
            </option>
          </select>
        </label>
        {config.mode === "custom" && (
          <>
            <textarea
              className="message-input"
              value={config.template}
              maxLength={isZhaopin ? 500 : 1000}
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
          </>
        )}
      </fieldset>
      {isZhaopin && (
        <p className="ai-hint">
          投递使用智联账户的在线简历。自定义与 AI 招呼最多 500
          字；逐岗保存到智联设置并设为默认，重新核对后投递。任务结束恢复原默认招呼。预览只生成内容，不改网站设置。
        </p>
      )}
      {config.mode === "ai" ? (
        <>
          <div className="message-preview">
            <span>
              <Sparkles size={13} /> 自动为新岗位写招呼
            </span>
            <p>
              发现符合条件的岗位后，结合 JD
              与当前简历，突出最相关的真实经历和成果。投递时自动使用，完整内容与结果均留存。
            </p>
            <button type="button" className="text-button" onClick={onHistory}>
              <History size={14} /> 查看招呼记录
            </button>
          </div>
          <p className="ai-hint">
            预览职位时自动生成招呼，不发送消息。简历正文、特点和岗位 JD 将发送至
            DeepSeek。
          </p>
        </>
      ) : (
        <div className="message-preview">
          <span>
            <Sparkles size={13} /> 预览
          </span>
          <p>
            {config.mode === "custom"
              ? template
              : isZhaopin
                ? "投递在线简历，并由网站发送当前默认招呼。"
                : "仅建立平台沟通，不发送模板。"}
          </p>
        </div>
      )}
    </section>
  );
}
