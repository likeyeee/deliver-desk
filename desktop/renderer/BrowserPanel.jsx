import React, { useLayoutEffect, useRef } from "react";
import {
  ArrowLeft,
  ArrowRight,
  RotateCw,
  Search,
  QrCode,
  Play,
  Pause,
  Square,
  X,
  Monitor,
} from "lucide-react";

// The rectangle is a native WebContentsView owned by the main process.
// The main process places the app above it when a React dialog or another page is open.
export default function BrowserPanel({
  state,
  busy,
  obscured,
  action,
  onWorkspace,
  onLogs,
}) {
  const viewport = useRef(null);
  const browser = state.browser;
  const tabs = browser.tabs || [];
  const current = tabs.find((tab) => tab.id === browser.activeId);
  const run = state.run;
  const hasProgress = run?.mode === "send" && run.target > 0;
  const event = state.events
    .filter((item) => item.run_id === run?.run_id)
    .at(-1);
  const phase =
    event?.kind === "start"
      ? "正在准备浏览器…"
      : event?.kind === "login"
        ? "正在检查登录，出现二维码时请扫码。"
        : event?.message.split("\n")[0];
  const navigate = (actionName) =>
    action(() => window.desk.browserNavigate({ action: actionName }));
  useLayoutEffect(() => {
    const element = viewport.current;
    const update = () => {
      const rect = element.getBoundingClientRect();
      window.desk
        .browserViewport({
          visible: !obscured,
          bounds: {
            x: rect.x,
            y: rect.y,
            width: rect.width,
            height: rect.height,
          },
        })
        .catch(() => {});
    };
    const observer = new ResizeObserver(update);
    observer.observe(element);
    window.addEventListener("resize", update);
    update();
    return () => {
      observer.disconnect();
      window.removeEventListener("resize", update);
      window.desk.browserViewport({ visible: false }).catch(() => {});
    };
  }, [obscured]);
  return (
    <section className="browser-panel" aria-label="BOSS 内置浏览器">
      <div className="browser-runbar">
        <div className="browser-progress">
          <strong>
            {state.active
              ? run?.status === "paused"
                ? "任务已暂停"
                : {
                    send: "正在投递",
                    preview: "正在预览职位",
                    login: "正在检查登录",
                    verify: "正在核对送达",
                    diagnose: "正在检查网页",
                  }[run?.mode] || "准备中"
              : "BOSS 浏览器"}
          </strong>
          <span>
            {hasProgress
              ? `已尝试 ${run.attempts || 0} / ${run.target} 次 · 确认送达 ${run.sent} 次 · 跳过 ${run.skipped} 个`
              : browser.loggedIn
                ? "登录已保存，可回到工作台设置投递"
                : "使用 BOSS 直聘 App 扫码登录，登录后自动继续"}
          </span>
          {hasProgress && (
            <progress
              aria-label="本次投递进度"
              max={run.target}
              value={run.attempts || 0}
            />
          )}
        </div>
        <div className="run-buttons">
          {state.active && (
            <>
              <button
                className="btn"
                disabled={busy}
                onClick={action(() =>
                  window.desk.control({
                    action: run?.status === "paused" ? "run" : "pause",
                  }),
                )}
              >
                {run?.status === "paused" ? (
                  <Play size={15} />
                ) : (
                  <Pause size={15} />
                )}
                {run?.status === "paused" ? "继续" : "暂停"}
              </button>
              <button
                className="btn danger"
                disabled={busy}
                onClick={action(() => window.desk.control({ action: "stop" }))}
              >
                <Square size={15} />
                停止任务
              </button>
            </>
          )}
          <button className="btn" onClick={onLogs}>
            运行日志
          </button>
          <button className="btn" onClick={onWorkspace}>
            返回工作台
          </button>
        </div>
      </div>
      <div className="browser-step" aria-live="polite">
        <i className={state.active ? "pulsing" : ""} />
        <span>
          {state.active
            ? run?.status === "paused"
              ? "后续操作已暂停，点击继续恢复。"
              : phase || "准备浏览器…"
            : run?.note || "扫码登录后，可在工作台预览职位并开始投递。"}
        </span>
      </div>
      <div className="browser-tabs" role="tablist" aria-label="浏览器页面">
        {tabs.map((tab) => (
          <div
            key={tab.id}
            className={
              "browser-tab " + (tab.id === browser.activeId ? "selected" : "")
            }
          >
            <button
              role="tab"
              aria-selected={tab.id === browser.activeId}
              onClick={action(() => window.desk.browserTab({ id: tab.id }))}
              title={tab.title}
            >
              <Monitor size={13} />
              <span>{tab.title}</span>
            </button>
            <button
              className="tab-close"
              aria-label={`关闭网页 ${tab.title}`}
              disabled={busy || state.active}
              onClick={action(() =>
                window.desk.browserTab({ id: tab.id, close: true }),
              )}
            >
              <X size={12} />
            </button>
          </div>
        ))}
      </div>
      <div className="browser-toolbar">
        <button
          className="icon-button"
          aria-label="后退"
          disabled={busy || state.active || !current?.canGoBack}
          onClick={navigate("back")}
        >
          <ArrowLeft size={17} />
        </button>
        <button
          className="icon-button"
          aria-label="前进"
          disabled={busy || state.active || !current?.canGoForward}
          onClick={navigate("forward")}
        >
          <ArrowRight size={17} />
        </button>
        <button
          className="icon-button"
          aria-label="刷新网页"
          disabled={busy || state.active || !current}
          onClick={navigate("reload")}
        >
          <RotateCw size={16} className={current?.loading ? "spinning" : ""} />
        </button>
        <div className="browser-address" title={current?.url}>
          {current?.url || "BOSS 直聘"}
        </div>
        <button
          className="btn"
          disabled={busy || state.active}
          onClick={navigate("login")}
        >
          <QrCode size={15} />
          扫码登录
        </button>
        <button
          className="btn"
          disabled={busy || state.active}
          onClick={navigate("jobs")}
        >
          <Search size={15} />
          职位搜索
        </button>
      </div>
      {current?.error && (
        <div className="browser-error" role="alert">
          {current.error}
        </div>
      )}
      <div className="browser-viewport" ref={viewport}>
        {!tabs.length && (
          <div className="empty">
            <Monitor size={32} />
            <h3>在这里登录并查看投递过程</h3>
            <p>点击上方“扫码登录”打开 BOSS 直聘。</p>
          </div>
        )}
      </div>
    </section>
  );
}
