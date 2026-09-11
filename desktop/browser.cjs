const { WebContentsView, session } = require("electron");
const fs = require("node:fs/promises");
const path = require("node:path");
const { platforms, platformForURL } = require("./platforms.cjs");

function allowedURL(value, platform) {
  const found = platformForURL(value);
  return Boolean(found && (!platform || found === platform));
}

class BrowserManager {
  constructor(dataDir, emit, options = {}) {
    this.dataDir = dataDir;
    this.emit = emit;
    this.views = new Map();
    this.fits = new Map();
    this.nextId = 1;
    this.partition = options.partition || "persist:boss";
    this.platform = "boss";
    this.pagePlatforms = new Map();
    this.platformTabs = new Map();
    this.sessions = new Map();
    this.host = null;
    this.visible = false;
    this.insets = { left: 220, top: 220, right: 16, bottom: 16 };
    this.errors = new Map();
    this.taskPages = new Set();
    this.backgroundPopups = new Set();
    this.session = this.platformSession("boss");
    this.lastId = null;
    if (options.host) this.attach(options.host, options.shellView);
  }
  platformSession(platform) {
    if (!platforms[platform]) throw Error("不支持的招聘平台");
    if (this.sessions.has(platform)) return this.sessions.get(platform);
    const partition =
      platform === "boss"
        ? this.partition
        : this.partition === "persist:boss"
          ? "persist:zhaopin"
          : `${this.partition}-${platform}`;
    const browserSession = session.fromPartition(partition);
    browserSession.setPermissionRequestHandler((_, __, callback) =>
      callback(false),
    );
    browserSession.setPermissionCheckHandler(() => false);
    browserSession.on("will-download", (event) => event.preventDefault());
    this.sessions.set(platform, browserSession);
    return browserSession;
  }
  selectPlatform(platform) {
    if (!platforms[platform]) throw Error("不支持的招聘平台");
    this.platform = platform;
    this.lastId =
      this.platformTabs.get(platform) ||
      [...this.views.keys()].find(
        (id) => this.pagePlatforms.get(id) === platform,
      ) ||
      null;
    this.layout();
    return platform;
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
      if (id === this.lastId) this.fitToWidth(id);
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
    this.platform = this.pagePlatforms.get(id);
    this.platformTabs.set(this.platform, id);
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
  fitToWidth(id, reset = false) {
    const view = this.views.get(id),
      state = this.fits.get(id);
    if (!view || !state || view.webContents.isDestroyed())
      return Promise.resolve();
    if (reset) {
      state.minimumWidth = 0;
      state.revision++;
    }
    const wc = view.webContents;
    // Electron queues script execution until the main frame finishes loading.
    // Wait for did-stop-loading instead of accumulating sizing scripts on blank
    // or navigating tabs; hidden tabs are measured when they become active.
    if (
      !wc.getURL() ||
      wc.getURL() === "about:blank" ||
      wc.isLoadingMainFrame()
    )
      return state.pending || Promise.resolve();
    state.dirty = true;
    if (state.pending) return state.pending;
    state.pending = (async () => {
      // Cache only observed horizontal overflow, not the enlarged CSS viewport
      // at a reduced zoom. This lets a wider window return to normal text size.
      for (let pass = 0; state.dirty && pass < 4; pass++) {
        state.dirty = false;
        const width = view.getBounds().width,
          revision = state.revision;
        if (width <= 0) break;
        const factor = Math.max(
          0.25,
          Math.min(
            1,
            Math.floor((1000 * width) / (state.minimumWidth || width)) / 1000,
          ),
        );
        if (Math.abs(wc.getZoomFactor() - factor) > 0.0001)
          wc.setZoomFactor(factor);
        let timer;
        let size;
        try {
          size = await Promise.race([
            wc.executeJavaScript(`new Promise(resolve => {
          let timer;
          const read = () => {
            clearTimeout(timer);
            const root = document.documentElement;
            resolve(root ? { viewport: root.clientWidth,
              width: Math.max(root.scrollWidth, document.body?.scrollWidth || 0),
              gutter: innerWidth - root.clientWidth } : null);
          };
          timer = setTimeout(read, 150);
          requestAnimationFrame(() => requestAnimationFrame(read));
        })`),
            // A replaced or covered renderer can leave an in-flight JS promise
            // unresolved. Sizing must never hold up the task's next operation.
            new Promise((resolve) => {
              timer = setTimeout(() => resolve(null), 500);
            }),
          ]);
        } finally {
          clearTimeout(timer);
        }
        if (revision !== state.revision || width !== view.getBounds().width) {
          state.dirty = true;
          continue;
        }
        if (size && size.width > size.viewport + 1) {
          const minimum = size.width + size.gutter + 1;
          if (minimum > state.minimumWidth) {
            state.minimumWidth = minimum;
            state.dirty = true;
          }
        }
      }
    })()
      .catch(() => {
        // Navigation/destruction can invalidate a measurement. The next DOM or
        // resize event measures again; native clicks still recheck their target.
      })
      .finally(() => {
        state.pending = null;
        if (state.dirty && this.views.has(id)) this.fitToWidth(id);
      });
    return state.pending;
  }
  preferences(platform = this.platform) {
    return {
      session: this.platformSession(platform),
      contextIsolation: true,
      sandbox: true,
      nodeIntegration: false,
      webSecurity: true,
      backgroundThrottling: false,
    };
  }
  register(view, parent, platform = this.platform) {
    if (parent) platform = this.pagePlatforms.get(parent);
    const id = String(this.nextId++);
    this.views.set(id, view);
    this.pagePlatforms.set(id, platform);
    this.fits.set(id, {
      minimumWidth: 0,
      revision: 0,
      dirty: false,
      pending: null,
    });
    view.webContents.setZoomMode("isolated");
    if (!parent || !this.backgroundPopups.has(parent)) {
      this.platform = platform;
      this.lastId = id;
      this.platformTabs.set(platform, id);
    }
    this.host.contentView.addChildView(view);
    this.layout();
    const wc = view.webContents;
    // A navigation can replace the renderer while this view is covered. Apply
    // the background policy to the new renderer so native input remains usable.
    wc.on("dom-ready", () => {
      wc.setBackgroundThrottling(false);
      this.fitToWidth(id, true);
    });
    wc.on("did-stop-loading", () => this.fitToWidth(id));
    const navigation = (url) =>
      this.emit({ kind: "browserEvent", event: "navigation", page: id, url });
    wc.on("did-navigate", (_, url) => navigation(url));
    wc.on("did-navigate-in-page", (_, url, main) => {
      if (main) {
        navigation(url);
        this.fitToWidth(id, true);
      }
    });
    wc.on("will-navigate", (event, url) => {
      if (url !== "about:blank" && !allowedURL(url, platform)) {
        event.preventDefault();
        const secure = url.replace(/^http:/, "https:");
        if (
          platform === "zhaopin" &&
          secure !== url &&
          allowedURL(secure, platform)
        )
          wc.loadURL(secure).catch(() => {});
      }
    });
    wc.on("will-redirect", (event, url) => {
      if (url !== "about:blank" && !allowedURL(url, platform))
        event.preventDefault();
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
      const secure = details.url.replace(/^http:/, "https:");
      if (
        platform === "zhaopin" &&
        secure !== details.url &&
        allowedURL(secure, platform)
      ) {
        const child = new WebContentsView({
          webPreferences: this.preferences(platform),
        });
        const created = this.register(child, id);
        if (this.taskPages.has(id)) this.taskPages.add(created.id);
        child.webContents.loadURL(secure).catch(() => {});
        return { action: "deny" };
      }
      if (details.url !== "about:blank" && !allowedURL(details.url, platform))
        return { action: "deny" };
      return {
        action: "allow",
        overrideBrowserWindowOptions: {
          webPreferences: this.preferences(platform),
        },
        createWindow: (options) => {
          const child = new WebContentsView({
            ...options,
            webPreferences: {
              ...options.webPreferences,
              ...this.preferences(platform),
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
      this.pagePlatforms.delete(id);
      if (this.platformTabs.get(platform) === id)
        this.platformTabs.delete(platform);
      this.fits.delete(id);
      this.taskPages.delete(id);
      this.backgroundPopups.delete(id);
      this.errors.delete(id);
      if (this.lastId === id) this.selectPlatform(platform);
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
  create(platform = this.platform) {
    if (!this.host || this.host.isDestroyed())
      throw Error("工作台窗口尚未就绪");
    const view = new WebContentsView({
      webPreferences: this.preferences(platform),
    });
    return this.register(view, undefined, platform);
  }
  get(id) {
    const view = this.views.get(id);
    if (!view || view.webContents.isDestroyed())
      throw Error("浏览器页面已关闭");
    return view;
  }
  async showOrOpen(url, platform = platformForURL(url) || this.platform) {
    if (url && !allowedURL(url, platform)) throw Error("无效的招聘网站地址");
    if (platform !== this.platform) this.selectPlatform(platform);
    let id = this.views.has(this.lastId)
      ? this.lastId
      : [...this.views.keys()].find(
          (id) => this.pagePlatforms.get(id) === platform,
        );
    if (!id) id = this.create(platform).id;
    this.activate(id, true);
    this.host.show();
    this.host.focus();
    if (url || !this.get(id).webContents.getURL()) {
      const targetURL = url || platforms[platform].login;
      await this.command("goto", {
        page: id,
        url: targetURL,
      });
      if (platform === "zhaopin" && targetURL === platforms.zhaopin.login)
        await this.prepareLogin(id);
    }
    return id;
  }
  async prepareLogin(id) {
    const wc = this.get(id).webContents;
    for (let pass = 0; pass < 12; pass++) {
      if (!wc.getURL().startsWith(platforms.zhaopin.login)) return;
      const point = await wc.executeJavaScript(`(() => {
        const e=document.querySelector('.zppp-panel-normal-bar__img');
        if(!e?.getClientRects().length)return null;
        const r=e.getBoundingClientRect(),x=r.x+r.width/2,y=r.y+r.height/2;
        const hit=document.elementFromPoint(x,y);
        if(hit!==e && !e.contains(hit))return null;
        const target=crypto.randomUUID();
        e[Symbol.for('deliverdesk.clickTarget')]=target;
        return {x,y,target};
      })()`);
      if (point) {
        await this.command("click", { page: id, ...point });
        return;
      }
      await new Promise((resolve) => setTimeout(resolve, 250));
    }
  }
  async command(method, params) {
    if (method === "primary") {
      const platform = params.platform || "boss";
      this.selectPlatform(platform);
      const matches = (id) =>
        this.views.has(id) && this.pagePlatforms.get(id) === platform;
      const retained = matches(params.keep) ? params.keep : this.lastId;
      const id =
        params.preferCurrent && this.views.has(retained)
          ? retained
          : [...this.views.keys()].find(matches);
      for (const managedId of [...this.taskPages]) {
        if (
          !params.preferCurrent &&
          this.pagePlatforms.get(managedId) === platform &&
          managedId !== id &&
          managedId !== params.keep &&
          this.views.has(managedId)
        )
          this.get(managedId).webContents.close({ waitForBeforeUnload: false });
      }
      if (id) this.activate(matches(params.keep) ? params.keep : id);
      return id
        ? { id, url: this.get(id).webContents.getURL() }
        : this.create(platform);
    }
    if (method === "new") {
      const created = this.create(params.platform || this.platform);
      this.taskPages.add(created.id);
      return created;
    }
    const wc = this.get(params.page).webContents;
    switch (method) {
      case "goto": {
        if (!allowedURL(params.url, this.pagePlatforms.get(params.page)))
          throw Error("只能在对应平台的会话中打开招聘网页");
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
        await this.fitToWidth(params.page);
        return { url: wc.getURL() };
      }
      case "evaluate": {
        await this.fits.get(params.page)?.pending;
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
        await this.fits.get(params.page)?.pending;
        // DOM-ready and isFocused() can precede the new renderer's first frame.
        // Sending input before it renders can silently drop the click on macOS.
        let timer;
        try {
          await Promise.race([
            wc.executeJavaScript(
              "new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve(true))))",
            ),
            new Promise((_, reject) => {
              timer = setTimeout(
                () => reject(Error("网页尚未准备好接收点击，请检查浏览器")),
                2000,
              );
            }),
          ]);
        } finally {
          clearTimeout(timer);
        }
        if (
          this.lastId !== params.page ||
          !this.host.isFocused() ||
          !wc.isFocused()
        )
          throw Error("等待点击期间浏览器焦点发生变化，未点击");
        const cssPoint = { x: Math.round(params.x), y: Math.round(params.y) };
        if (params.target) {
          const unchanged = await wc.executeJavaScript(`(() => {
            let e=document.elementFromPoint(${cssPoint.x},${cssPoint.y});
            while(e) {
              if(e[Symbol.for('deliverdesk.clickTarget')]===${JSON.stringify(params.target)})
                return !e.disabled && e.getAttribute('aria-disabled')!=='true' && (!e[Symbol.for('deliverdesk.actionGuard')] || e[Symbol.for('deliverdesk.actionGuard')]());
              e=e.parentElement;
            }
            return false;
          })()`);
          if (!unchanged)
            throw Error("等待点击期间目标控件位置发生变化，未点击");
        }
        // Adapter rectangles use CSS pixels; native input uses view pixels.
        // Use the current page zoom after the final hit test, without a DPR multiplier.
        const zoom = wc.getZoomFactor();
        const point = {
          x: Math.round(cssPoint.x * zoom),
          y: Math.round(cssPoint.y * zoom),
        };
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
        await this.fitToWidth(params.page);
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
    const checkedPlatform = this.platform;
    const loginChecks = [...this.views]
      .filter(([id]) => this.pagePlatforms.get(id) === checkedPlatform)
      .map(async ([, view]) => {
        try {
          if (checkedPlatform === "zhaopin") {
            if (
              new URL(view.webContents.getURL()).hostname !== "www.zhaopin.com"
            )
              return false;
            return await view.webContents.executeJavaScript(
              `Array.from(document.querySelectorAll('.c-login__top__name')).some(e=>e.getClientRects().length && e.innerText.trim())`,
            );
          }
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
    this.logins ||= {};
    if (checks) this.logins[checkedPlatform] = checks.some(Boolean);
    const loggedIn = this.logins[this.platform] || false;
    return {
      platform: this.platform,
      platforms: Object.entries(platforms).map(([id, item]) => ({
        id,
        name: item.name,
      })),
      open: this.views.size,
      loggedIn,
      activeId: this.lastId,
      tabs: [...this.views]
        .filter(([id]) => this.pagePlatforms.get(id) === this.platform)
        .map(([id, view]) => {
          const wc = view.webContents;
          return {
            id,
            platform: this.pagePlatforms.get(id),
            title: wc.getTitle() || platforms[this.platform].name,
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
      return this.showOrOpen(platforms[this.platform][action]);
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
