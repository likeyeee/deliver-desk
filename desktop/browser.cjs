const { WebContentsView, session } = require("electron");
const fs = require("node:fs/promises");
const path = require("node:path");

function allowedURL(value) {
  try {
    const u = new URL(value);
    return (
      u.protocol === "https:" &&
      (u.hostname === "zhipin.com" || u.hostname.endsWith(".zhipin.com"))
    );
  } catch {
    return false;
  }
}

class BrowserManager {
  constructor(dataDir, emit, options = {}) {
    this.dataDir = dataDir;
    this.emit = emit;
    this.views = new Map();
    this.nextId = 1;
    this.partition = options.partition || "persist:boss";
    this.host = null;
    this.visible = false;
    this.insets = { left: 220, top: 220, right: 16, bottom: 16 };
    this.errors = new Map();
    this.taskPages = new Set();
    this.backgroundPopups = new Set();
    this.session = session.fromPartition(this.partition);
    this.session.setPermissionRequestHandler((_, __, callback) =>
      callback(false),
    );
    this.session.setPermissionCheckHandler(() => false);
    this.session.on("will-download", (event) => event.preventDefault());
    this.lastId = null;
    if (options.host) this.attach(options.host, options.shellView);
  }
  attach(host, shellView) {
    this.host = host;
    this.shellView = shellView;
    if (!this.shellView) throw Error("工作台内容视图尚未就绪");
    host.on("resize", () => this.layout());
    host.once("closed", () => this.destroy());
    this.layout();
  }
  setViewport({ visible, bounds }) {
    if (bounds) {
      if (
        !["x", "y", "width", "height"].every((key) =>
          Number.isFinite(bounds[key]),
        )
      )
        throw Error("无效的浏览器尺寸");
      const [width, height] = this.host.getContentSize();
      this.insets = {
        left: Math.max(0, Math.round(bounds.x)),
        top: Math.max(0, Math.round(bounds.y)),
        right: Math.max(0, Math.round(width - bounds.x - bounds.width)),
        bottom: Math.max(0, Math.round(height - bounds.y - bounds.height)),
      };
    }
    this.visible = visible === true;
    this.layout();
    return true;
  }
  layout() {
    if (!this.host || this.host.isDestroyed()) return;
    const [width, height] = this.host.getContentSize();
    this.shellView.setBounds({ x: 0, y: 0, width, height });
    const x = Math.min(width, this.insets.left),
      y = Math.min(height, this.insets.top);
    const bounds = {
      x,
      y,
      width: Math.max(0, width - x - this.insets.right),
      height: Math.max(0, height - y - this.insets.bottom),
    };
    for (const [id, view] of this.views) {
      view.setBounds(bounds);
      // Keep the active native renderer mapped behind the app when the user reads
      // logs. Unmapping it prevents native input from reaching newly opened pages.
      view.setVisible(
        id === this.lastId && bounds.width > 0 && bounds.height > 0,
      );
    }
    const top =
      this.visible && this.views.has(this.lastId)
        ? this.views.get(this.lastId)
        : this.shellView;
    if (this.host.contentView.children.at(-1) !== top)
      this.host.contentView.addChildView(top);
  }
  activate(id, focus = false) {
    const view = this.get(id);
    if (this.lastId !== id) {
      this.lastId = id;
      this.layout();
    }
    // Native input needs the host and target renderer focused even while the
    // workspace covers that renderer. Visibility alone does not establish focus.
    if (focus) {
      this.host.focus();
      view.webContents.focus();
    }
    return id;
  }
  preferences() {
    return {
      partition: this.partition,
      contextIsolation: true,
      sandbox: true,
      nodeIntegration: false,
      webSecurity: true,
      backgroundThrottling: false,
    };
  }
  register(view, parent) {
    const id = String(this.nextId++);
    this.views.set(id, view);
    if (!parent || !this.backgroundPopups.has(parent)) this.lastId = id;
    this.host.contentView.addChildView(view);
    this.layout();
    const wc = view.webContents;
    // A navigation can replace the renderer while this view is covered. Apply
    // the background policy to the new renderer so native input remains usable.
    wc.on("dom-ready", () => wc.setBackgroundThrottling(false));
    const navigation = (url) =>
      this.emit({ kind: "browserEvent", event: "navigation", page: id, url });
    wc.on("did-navigate", (_, url) => navigation(url));
    wc.on("did-navigate-in-page", (_, url, main) => {
      if (main) navigation(url);
    });
    wc.on("will-navigate", (event, url) => {
      if (url !== "about:blank" && !allowedURL(url)) event.preventDefault();
    });
    wc.on("will-redirect", (event, url) => {
      if (url !== "about:blank" && !allowedURL(url)) event.preventDefault();
    });
    wc.on("content-bounds-updated", (event) => event.preventDefault());
    wc.on("did-start-navigation", (_, __, inPlace, main) => {
      if (main && !inPlace) this.errors.delete(id);
    });
    wc.on("did-fail-load", (_, code, description, __, main) => {
      if (main && code !== -3)
        this.errors.set(
          id,
          `网页加载失败（${description}），可重试或检查网络。`,
        );
    });
    wc.on("render-process-gone", () =>
      this.errors.set(id, "网页进程已退出，请停止任务后重新加载。"),
    );
    wc.setWindowOpenHandler((details) => {
      if (details.url !== "about:blank" && !allowedURL(details.url))
        return { action: "deny" };
      return {
        action: "allow",
        overrideBrowserWindowOptions: { webPreferences: this.preferences() },
        createWindow: (options) => {
          const child = new WebContentsView({
            ...options,
            webPreferences: {
              ...options.webPreferences,
              ...this.preferences(),
            },
          });
          const created = this.register(child, id);
          if (this.taskPages.has(id)) this.taskPages.add(created.id);
          if (details.disposition === "background-tab")
            child.webContents.loadURL(details.url).catch(() => {});
          return child.webContents;
        },
      };
    });
    wc.once("destroyed", () => {
      if (this.host && !this.host.isDestroyed())
        this.host.contentView.removeChildView(view);
      this.views.delete(id);
      this.taskPages.delete(id);
      this.backgroundPopups.delete(id);
      this.errors.delete(id);
      if (this.lastId === id)
        this.lastId = [...this.views.keys()].at(-1) || null;
      this.layout();
      this.emit({ kind: "browserEvent", event: "closed", page: id });
    });
    if (parent)
      this.emit({
        kind: "browserEvent",
        event: "popup",
        parent,
        page: id,
        url: wc.getURL() || "about:blank",
      });
    return { id, url: wc.getURL() || "about:blank" };
  }
  create() {
    if (!this.host || this.host.isDestroyed())
      throw Error("工作台窗口尚未就绪");
    const view = new WebContentsView({
      webPreferences: this.preferences(),
    });
    return this.register(view);
  }
  get(id) {
    const view = this.views.get(id);
    if (!view || view.webContents.isDestroyed())
      throw Error("浏览器页面已关闭");
    return view;
  }
  async showOrOpen(url) {
    let id = this.views.has(this.lastId)
      ? this.lastId
      : this.views.keys().next().value;
    if (!id) id = this.create().id;
    this.activate(id, true);
    this.host.show();
    this.host.focus();
    if (url || !this.get(id).webContents.getURL()) {
      await this.command("goto", {
        page: id,
        url: url || "https://www.zhipin.com/web/user/?ka=header-login",
      });
    }
    return id;
  }
  async command(method, params) {
    if (method === "primary") {
      const id = [...this.views.keys()][0];
      for (const managedId of [...this.taskPages]) {
        if (managedId !== id && this.views.has(managedId))
          this.get(managedId).webContents.close({ waitForBeforeUnload: false });
      }
      if (id) this.activate(id);
      return id
        ? { id, url: this.get(id).webContents.getURL() }
        : this.create();
    }
    if (method === "new") {
      const created = this.create();
      this.taskPages.add(created.id);
      return created;
    }
    const wc = this.get(params.page).webContents;
    switch (method) {
      case "goto": {
        if (!allowedURL(params.url)) throw Error("只允许打开 BOSS 直聘网页");
        this.activate(params.page);
        // Resolve when the DOM is ready. Images and long-lived resources do not block the adapter.
        await new Promise((resolve, reject) => {
          const done = (error) => {
            clearTimeout(timer);
            wc.removeListener("dom-ready", ready);
            wc.removeListener("destroyed", closed);
            error ? reject(error) : resolve();
          };
          const ready = () => done();
          const closed = () => done(Error("浏览器页面已关闭"));
          const timer = setTimeout(
            () => done(Error("网页加载超时，请检查网络")),
            params.timeout || 20000,
          );
          wc.once("dom-ready", ready);
          wc.once("destroyed", closed);
          wc.loadURL(params.url).catch(done);
        });
        return { url: wc.getURL() };
      }
      case "evaluate": {
        // Source arrives only from our packaged Python adapter, never from remote content or UI IPC.
        const result = await wc.executeJavaScript(
          `(() => { try { const value = (() => {${params.body}\n})(); return {ok:true,value:value===undefined?null:value}; } catch(e) { return {ok:false,error:String(e.message)}; } })()`,
        );
        if (!result?.ok)
          throw Error(result?.error || "网页跳转，未返回操作结果");
        return result.value;
      }
      case "click": {
        this.activate(params.page, true);
        const point = { x: Math.round(params.x), y: Math.round(params.y) };
        wc.sendInputEvent({ type: "mouseMove", ...point });
        wc.sendInputEvent({
          type: "mouseDown",
          button: "left",
          clickCount: 1,
          ...point,
        });
        wc.sendInputEvent({
          type: "mouseUp",
          button: "left",
          clickCount: 1,
          ...point,
        });
        return true;
      }
      case "show":
        this.activate(params.page, true);
        return true;
      case "backgroundPopup":
        if (params.enabled) this.backgroundPopups.add(params.page);
        else this.backgroundPopups.delete(params.page);
        return true;
      case "close":
        wc.close({ waitForBeforeUnload: false });
        return true;
      case "screenshot": {
        const target = path.resolve(params.path),
          relative = path.relative(this.dataDir, target);
        if (
          relative.startsWith("..") ||
          path.isAbsolute(relative) ||
          !target.endsWith(".png")
        )
          throw Error("无效的截图保存位置");
        await fs.mkdir(path.dirname(target), { recursive: true, mode: 0o700 });
        await fs.writeFile(target, (await wc.capturePage()).toPNG(), {
          mode: 0o600,
        });
        return true;
      }
      default:
        throw Error("不支持的浏览器命令");
    }
  }
  async status() {
    const loginChecks = [...this.views.values()].map(async (view) => {
      try {
        if (new URL(view.webContents.getURL()).hostname !== "www.zhipin.com")
          return false;
        return await view.webContents.executeJavaScript(
          `Array.from(document.querySelectorAll('a[href*="/web/geek/recommend"]')).some(e=>e.getClientRects().length && e.innerText.trim() && e.innerText.trim()!=='推荐')`,
        );
      } catch {
        return false;
      }
    });
    // Loading web content must not hold up pause/stop controls or the UI snapshot.
    let timer;
    const checks = await Promise.race([
      Promise.all(loginChecks),
      new Promise((resolve) => {
        timer = setTimeout(() => resolve(null), 250);
      }),
    ]);
    clearTimeout(timer);
    if (checks) this.loggedIn = checks.some(Boolean);
    const loggedIn = this.loggedIn || false;
    return {
      open: this.views.size,
      loggedIn,
      activeId: this.lastId,
      tabs: [...this.views].map(([id, view]) => {
        const wc = view.webContents;
        return {
          id,
          title: wc.getTitle() || "BOSS 直聘",
          url: wc.getURL().split(/[?#]/)[0],
          loading: wc.isLoadingMainFrame(),
          error: this.errors.get(id) || "",
          canGoBack: wc.navigationHistory.canGoBack(),
          canGoForward: wc.navigationHistory.canGoForward(),
        };
      }),
    };
  }
  async navigate(action) {
    if (["login", "jobs"].includes(action))
      return this.showOrOpen(
        action === "login"
          ? "https://www.zhipin.com/web/user/?ka=header-login"
          : "https://www.zhipin.com/web/geek/jobs",
      );
    const wc = this.get(this.lastId).webContents;
    if (action === "back" && wc.navigationHistory.canGoBack())
      wc.navigationHistory.goBack();
    else if (action === "forward" && wc.navigationHistory.canGoForward())
      wc.navigationHistory.goForward();
    else if (action === "reload") wc.reload();
    else throw Error("当前无法执行此浏览器操作");
    return true;
  }
  destroy() {
    for (const view of [...this.views.values()])
      if (!view.webContents.isDestroyed())
        view.webContents.close({ waitForBeforeUnload: false });
  }
}
module.exports = { BrowserManager, allowedURL };
