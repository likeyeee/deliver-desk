import React, { useState } from "react";
import { KeyRound, Save, Sparkles, ShieldCheck, RefreshCw } from "lucide-react";

export default function AISettings({
  config,
  status,
  locked,
  dirty,
  update,
  save,
  act,
  onReplies,
}) {
  const [key, setKey] = useState("");
  const [models, setModels] = useState([
    "deepseek-v4-flash",
    "deepseek-v4-pro",
  ]);
  const [connection, setConnection] = useState("");
  const run = (fn) => () => act(fn).catch(() => {});
  return (
    <>
      <div className="page-heading">
        <div className="simple-heading">
          <h1>模型与人格</h1>
          <p>让回复符合你的经历、表达习惯和求职目标。</p>
        </div>
        <button
          className="btn primary"
          disabled={locked || !dirty}
          onClick={run(save)}
        >
          <Save size={16} />
          {dirty ? "保存模型配置" : "模型配置已保存"}
        </button>
      </div>
      <div className="ai-config-grid">
        <section className="panel ai-settings">
          <div className="ai-section-title">
            <KeyRound size={20} />
            <h2>连接 DeepSeek</h2>
            <span
              className={"badge " + (status?.configured ? "sent" : "partial")}
            >
              {status?.configured ? "已保存密钥" : "尚未配置"}
            </span>
          </div>
          <label className="field">
            <span>模型提供商</span>
            <select value="deepseek" disabled>
              <option value="deepseek">DeepSeek · 官方 API</option>
            </select>
            <small>请求地址：api.deepseek.com</small>
          </label>
          <label className="field">
            <span>API Key</span>
            <input
              type="password"
              autoComplete="off"
              spellCheck={false}
              value={key}
              maxLength={512}
              disabled={locked}
              placeholder={
                status?.configured
                  ? "输入新密钥可替换已有配置"
                  : "粘贴你的 DeepSeek API Key"
              }
              onChange={(event) => setKey(event.target.value)}
            />
            <small>
              密钥使用系统加密存储，不会放进导出的配置或发送给招聘方。
            </small>
          </label>
          <div className="ai-actions">
            <button
              className="btn"
              disabled={locked || !key.trim() || !status?.secureStorage}
              onClick={run(async () => {
                await window.desk.llm({ action: "saveKey", key });
                setKey("");
                setConnection("密钥已加密保存，可以测试连接。");
              })}
            >
              保存密钥
            </button>
            <button
              className="text-button"
              disabled={locked || !status?.configured}
              onClick={run(async () => {
                await window.desk.llm({ action: "removeKey" });
                setConnection("密钥已移除");
              })}
            >
              移除密钥
            </button>
          </div>
          {!status?.secureStorage && (
            <p className="ai-warning">
              系统密钥存储不可用，请解锁系统钥匙串后重试。
            </p>
          )}
          <label className="field">
            <span>模型名称</span>
            <input
              list="deepseek-models"
              value={config.model}
              maxLength={100}
              disabled={locked}
              onChange={(event) => update("model", event.target.value)}
            />
            <datalist id="deepseek-models">
              {models.map((model) => (
                <option key={model} value={model} />
              ))}
            </datalist>
            <small>可选择推荐模型，或填写账户支持的模型 ID。</small>
          </label>
          <button
            className="btn"
            disabled={locked || !status?.configured}
            onClick={run(async () => {
              const result = await window.desk.llm({ action: "test" });
              setModels(result.models);
              setConnection(
                `连接成功，已获取 ${result.models.length} 个可用模型。${result.models.includes(config.model) ? "" : "当前模型不在列表中，请重新选择。"}`,
              );
            })}
          >
            <RefreshCw size={15} />
            测试连接并刷新模型
          </button>
          {connection && (
            <p role="status" className="ai-hint">
              {connection}
            </p>
          )}
        </section>
        <section className="panel ai-settings">
          <div className="ai-section-title">
            <Sparkles size={20} />
            <h2>我的求职人格</h2>
          </div>
          <label className="field">
            <span>系统提示词</span>
            <textarea
              className="persona-editor"
              value={config.system_prompt}
              disabled={locked}
              maxLength={20000}
              onChange={(event) => update("system_prompt", event.target.value)}
              placeholder="例如：我是一名有三年经验的产品经理，正在寻找 AI 产品岗位。我的表达简洁、真诚……"
            />
            <small>
              {config.system_prompt.length.toLocaleString()} / 20,000
              字。可填写个人背景、求职偏好、语言风格和回答边界。
            </small>
          </label>
          <div className="ai-options">
            <label className="field">
              <span>表达灵活度</span>
              <input
                type="number"
                min="0"
                max="2"
                step="0.1"
                value={config.temperature}
                disabled={locked}
                onChange={(event) =>
                  update("temperature", Number(event.target.value))
                }
              />
              <small>0 更稳定 · 2 更多变化</small>
            </label>
            <label className="field">
              <span>近期消息条数</span>
              <input
                type="number"
                min="2"
                max="50"
                value={config.context_messages}
                disabled={locked}
                onChange={(event) =>
                  update("context_messages", Number(event.target.value))
                }
              />
              <small>最多读取 50 条上下文</small>
            </label>
          </div>
          <p className="ai-hint ai-output-policy">
            不设置输出 Token
            或回复字数上限。完整生成后才会发送；模型服务上限或网络中断导致的残缺回复会被拦下并记录日志。
          </p>
          <div className="ai-privacy">
            <ShieldCheck size={18} />
            <p>
              手动生成或开启自动回复后，系统提示词、目标职位名称与公司、待回复会话的近期消息会发送到
              DeepSeek。请只填入你希望用于回复的个人资料。
            </p>
          </div>
          <button className="btn primary" onClick={onReplies}>
            <Sparkles size={16} />
            去生成回复
          </button>
        </section>
      </div>
    </>
  );
}
