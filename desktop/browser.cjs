const { BrowserWindow, session } = require("electron");
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
    this.windows = new Map();
    this.nextId = 1;
    this.partition = options.partition || "persist:boss";
    this.visible = options.visible !== false;
    this.session = session.fromPartition(this.partition);
    this.session.setPermissionRequestHandler((_, __, callback) =>
      callback(false),
    );
    this.session.setPermissionCheckHandler(() => false);
    this.session.on("will-download", (event) => event.preventDefault());
    this.lastId = null;
  }
  preferences() {
    return {
      partition: this.partition,
      contextIsolation: true,
      sandbox: true,
      nodeIntegration: false,
      webSecurity: true,
    };
  }
  register(win, parent) {
    const id = String(this.nextId++);
    this.windows.set(id, win);
    this.lastId = id;
    win.setMenu(null);
    const wc = win.webContents;
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
    wc.setWindowOpenHandler(({ url }) => ({
      action: url === "about:blank" || allowedURL(url) ? "allow" : "deny",
      overrideBrowserWindowOptions: {
        width: 1280,
        height: 850,
        show: this.visible,
        webPreferences: this.preferences(),
      },
    }));
    wc.on("did-create-window", (child) => this.register(child, id));
    win.on("closed", () => {
      this.windows.delete(id);
      this.emit({ kind: "browserEvent", event: "closed", page: id });
    });
    win.on("focus", () => {
      this.lastId = id;
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
    const win = new BrowserWindow({
      width: 1280,
      height: 850,
      show: this.visible,
      title: "投递工作台 · BOSS 直聘",
      backgroundColor: "#ffffff",
      webPreferences: this.preferences(),
    });
    return this.register(win);
  }
  get(id) {
    const win = this.windows.get(id);
    if (!win || win.isDestroyed()) throw Error("浏览器窗口已关闭");
    return win;
  }
  async showOrOpen(url) {
    let id = this.windows.has(this.lastId)
      ? this.lastId
      : this.windows.keys().next().value;
    if (!id) id = this.create().id;
    const win = this.get(id);
    win.show();
    win.focus();
    if (url || !win.webContents.getURL()) {
      await this.command("goto", {
        page: id,
        url: url || "https://www.zhipin.com/web/user/?ka=header-login",
      });
    }
    return id;
  }
  async command(method, params) {
    if (method === "primary") {
      const id = [...this.windows.keys()][0];
      return id
        ? { id, url: this.get(id).webContents.getURL() }
        : this.create();
    }
    if (method === "new") return this.create();
    const win = this.get(params.page);
    const wc = win.webContents;
    switch (method) {
      case "goto": {
        if (!allowedURL(params.url)) throw Error("只允许打开 BOSS 直聘网页");
        await wc.loadURL(params.url);
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
        if (this.visible) {
          win.show();
          win.focus();
        }
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
        if (this.visible) {
          win.show();
          win.focus();
        }
        return true;
      case "close":
        win.close();
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
    let loggedIn = false;
    for (const win of this.windows.values()) {
      try {
        if (new URL(win.webContents.getURL()).hostname !== "www.zhipin.com")
          continue;
        loggedIn ||= await win.webContents.executeJavaScript(
          `Array.from(document.querySelectorAll('a[href*="/web/geek/recommend"]')).some(e=>e.getClientRects().length && e.innerText.trim() && e.innerText.trim()!=='推荐')`,
        );
      } catch {}
    }
    return { open: this.windows.size, loggedIn };
  }
  destroy() {
    for (const win of this.windows.values())
      if (!win.isDestroyed()) win.destroy();
  }
}
module.exports = { BrowserManager, allowedURL };
