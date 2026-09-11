const {
  app,
  BaseWindow,
  WebContentsView,
  ipcMain,
  dialog,
  shell,
  Menu,
  safeStorage,
} = require("electron");
const fs = require("node:fs/promises");
const path = require("node:path");
const { pathToFileURL } = require("node:url");
const { BrowserManager } = require("./browser.cjs");
const { platformForURL } = require("./platforms.cjs");
const { Backend } = require("./backend.cjs");
const { KeyVault, DeepSeek } = require("./llm.cjs");

const root = path.resolve(__dirname, "..");
if (!app.isPackaged)
  app.setPath(
    "userData",
    process.env.DELIVERDESK_DEV_DATA_DIR
      ? path.resolve(process.env.DELIVERDESK_DEV_DATA_DIR)
      : path.join(root, ".boss-cli", "desktop"),
  );
const dataDir =
  process.env.DELIVERDESK_WORKSPACE === "1" && !app.isPackaged
    ? path.join(root, ".boss-cli")
    : path.join(app.getPath("userData"), "state");
const config = path.join(dataDir, "desktop.yaml");
const uiURL = pathToFileURL(path.join(root, "ui-dist", "index.html")).href;
let window,
  uiView,
  backend,
  browser,
  quitting = false,
  quitPrompt = false;
const serviceCommands = [
  "saveConfig",
  "start",
  "control",
  "resolve",
  "reply",
  "resolveReply",
  "greetings",
];
function assertSender(event) {
  if (
    !window ||
    event.sender !== uiView.webContents ||
    event.senderFrame !== uiView.webContents.mainFrame ||
    event.senderFrame.url !== uiURL
  )
    throw Error("未经授权的窗口请求");
}
function handle(name, fn) {
  ipcMain.handle("desk:" + name, async (event, params) => {
    assertSender(event);
    return fn(params || {});
  });
}
function cleanCSV(value) {
  const text = String(value ?? "");
  return (
    '"' +
    (/^[=+@\-\t\r]/.test(text) ? "'" + text : text).replaceAll('"', '""') +
    '"'
  );
}
async function createWindow() {
  window = new BaseWindow({
    width: 1360,
    height: 940,
    minWidth: 1080,
    minHeight: 760,
    title: "投递工作台",
    backgroundColor: "#f4f6f8",
  });
  uiView = new WebContentsView({
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      sandbox: true,
      nodeIntegration: false,
      backgroundThrottling: false,
    },
  });
  window.contentView.addChildView(uiView);
  browser.attach(window, uiView);
  window.once("closed", () => {
    if (!uiView.webContents.isDestroyed())
      uiView.webContents.close({ waitForBeforeUnload: false });
  });
  uiView.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  uiView.webContents.on("will-navigate", (event, url) => {
    if (url !== uiURL) event.preventDefault();
  });
  await uiView.webContents.loadURL(uiURL);
  window.on("close", (event) => {
    if (!quitting) {
      event.preventDefault();
      app.quit();
    }
  });
}
if (!app.requestSingleInstanceLock()) app.quit();
else {
  app.on("second-instance", () => {
    window?.show();
    window?.focus();
  });
  app
    .whenReady()
    .then(async () => {
      await fs.mkdir(app.getPath("userData"), { recursive: true, mode: 0o700 });
      await fs.chmod(app.getPath("userData"), 0o700);
      await fs.mkdir(dataDir, { recursive: true, mode: 0o700 });
      const vault = await new KeyVault(dataDir, safeStorage).init();
      const llm = new DeepSeek(vault);
      browser = new BrowserManager(dataDir, (data) => backend?.send(data));
      backend = new Backend({
        root,
        resources: process.resourcesPath,
        packaged: app.isPackaged,
        dataDir,
        config,
        browser,
        llm,
      });
      backend.on("diagnostic", (message) => console.error(message));
      backend.on("unavailable", (message) => console.error(message));
      backend.ready.catch((error) =>
        dialog.showErrorBox("任务服务未启动", error.message),
      );
      for (const name of serviceCommands)
        handle(name, async (params) => {
          if (name === "start" && ["preview", "send"].includes(params.mode)) {
            const state = await backend.request("snapshot");
            if (
              state.config.message.mode === "ai" &&
              state.config.platform !== "zhaopin" &&
              !vault.status().configured
            )
              throw Error("请先在模型与人格中保存 DeepSeek API Key");
          }
          return backend.request(name, params);
        });
      handle("snapshot", async () => ({
        ...(await backend.request("snapshot")),
        browser: await browser.status(),
        version: app.getVersion(),
        llm: vault.status(),
      }));
      handle("llm", async ({ action, key }) => {
        const state = await backend.request("snapshot");
        if (state.active || state.autoReply?.enabled)
          throw Error("请先停止任务，再修改或测试模型配置");
        if (action === "saveKey") return vault.save(key);
        if (action === "removeKey") return vault.remove();
        if (action === "test") return llm.test();
        throw Error("无效的模型配置操作");
      });
      handle("resume", async (params) => {
        const state = await backend.request("snapshot");
        if (state.active || state.autoReply?.enabled)
          throw Error("请先停止当前任务和自动回复，再处理简历");
        if (params.action === "import") {
          const { filePaths, canceled } = await dialog.showOpenDialog(window, {
            title: "上传简历",
            properties: ["openFile"],
            filters: [{ name: "简历文件", extensions: ["pdf", "docx", "txt"] }],
          });
          if (canceled || !filePaths.length) return null;
          return backend.request("resume", {
            action: "import",
            path: filePaths[0],
          });
        }
        if (params.action === "analyze" && !vault.status().configured)
          throw Error("请先在模型与人格中保存 DeepSeek API Key");
        return backend.request("resume", params);
      });
      handle("autoReply", async (params) => {
        if (params.action === "enable" && !vault.status().configured)
          throw Error("请先在模型与人格中保存 DeepSeek API Key");
        return backend.request("autoReply", params);
      });
      handle("openBrowser", async () => {
        const state = await backend.request("snapshot");
        if (state.active) {
          // Opening the browser while the first task page is still loading
          // must not replace its navigation with a new login request.
          window.show();
          window.focus();
          return browser.lastId;
        }
        return browser.showOrOpen(undefined, state.config.platform || "boss");
      });
      handle("browserPlatform", async ({ platform }) => {
        const state = await backend.request("snapshot");
        if (state.active || state.autoReply?.enabled)
          throw Error("请先停止当前任务和自动回复，再切换平台");
        const saved = state.config.platform_filters || {};
        const result =
          platform === state.config.platform
            ? state.config
            : await backend.request("saveConfig", {
                config: {
                  ...state.config,
                  platform,
                  platform_filters: {
                    ...saved,
                    [state.config.platform || "boss"]:
                      state.config.search.filters,
                  },
                  search: {
                    ...state.config.search,
                    filters: saved[platform] || {},
                  },
                },
              });
        browser.selectPlatform(platform);
        await browser.showOrOpen();
        return result;
      });
      handle("browserViewport", (params) => browser.setViewport(params));
      handle("browserTab", async ({ id, close }) => {
        if (close) {
          if ((await backend.request("snapshot")).active)
            throw Error("请先停止任务，再关闭网页");
          return browser.command("close", { page: id });
        }
        return browser.activate(id);
      });
      handle("browserNavigate", async ({ action }) => {
        if ((await backend.request("snapshot")).active)
          throw Error("请先停止任务，再手动跳转或刷新网页");
        return browser.navigate(action);
      });
      handle("openJob", async ({ url }) => {
        if (
          !/^https:\/\/(?:www\.zhipin\.com\/job_detail\/[A-Za-z0-9_~\-]+\.html|www\.zhaopin\.com\/jobdetail\/[A-Za-z0-9_-]+\.htm)$/.test(
            url,
          )
        )
          throw Error("无效的职位链接");
        const state = await backend.request("snapshot");
        if (state.active) throw Error("请先暂停并停止任务，再手动打开其他职位");
        return browser.showOrOpen(url, platformForURL(url));
      });
      handle("openData", () => shell.openPath(dataDir));
      handle("exportHistory", async () => {
        const { filePath, canceled } = await dialog.showSaveDialog(window, {
          title: "导出投递历史",
          defaultPath: "投递记录.csv",
          filters: [{ name: "CSV", extensions: ["csv"] }],
        });
        if (canceled) return null;
        const rows = await backend.request("export");
        const keys = [
          "platform",
          "created_at",
          "title",
          "company",
          "status",
          "message",
          "note",
          "url",
        ];
        await fs.writeFile(
          filePath,
          "\uFEFF" +
            [
              keys.map(cleanCSV).join(","),
              ...rows.map((row) => keys.map((k) => cleanCSV(row[k])).join(",")),
            ].join("\r\n"),
          { mode: 0o600 },
        );
        return filePath;
      });
      handle("exportConfig", async () => {
        const { filePath, canceled } = await dialog.showSaveDialog(window, {
          defaultPath: "投递配置.json",
          filters: [{ name: "JSON", extensions: ["json"] }],
        });
        if (canceled) return null;
        const { config } = await backend.request("snapshot");
        config.state_dir = ".boss-cli";
        config.browser.cdp_url = null;
        await fs.writeFile(filePath, JSON.stringify(config, null, 2), {
          mode: 0o600,
        });
        return filePath;
      });
      handle("importConfig", async () => {
        const { filePaths, canceled } = await dialog.showOpenDialog(window, {
          properties: ["openFile"],
          filters: [{ name: "JSON 配置", extensions: ["json"] }],
        });
        if (canceled) return null;
        if ((await fs.stat(filePaths[0])).size > 1024 * 1024)
          throw Error("配置文件过大");
        return backend.request("saveConfig", {
          config: JSON.parse(await fs.readFile(filePaths[0], "utf8")),
        });
      });
      Menu.setApplicationMenu(
        Menu.buildFromTemplate([
          ...(process.platform === "darwin"
            ? [
                {
                  label: "投递工作台",
                  submenu: [
                    { role: "about" },
                    { type: "separator" },
                    { role: "hide" },
                    { role: "quit" },
                  ],
                },
              ]
            : []),
          {
            label: "编辑",
            submenu: [
              { role: "undo" },
              { role: "redo" },
              { type: "separator" },
              { role: "cut" },
              { role: "copy" },
              { role: "paste" },
              { role: "selectAll" },
            ],
          },
          {
            label: "窗口",
            submenu: [
              { role: "minimize" },
              { role: "zoom" },
              {
                label: "显示工作台",
                click: () => {
                  window?.show();
                  window?.focus();
                },
              },
            ],
          },
        ]),
      );
      browser.selectPlatform(
        (await backend.request("snapshot")).config.platform || "boss",
      );
      await createWindow();
    })
    .catch((error) => {
      dialog.showErrorBox("启动失败", error.message);
      app.exit(1);
    });
  app.on("before-quit", (event) => {
    if (quitting) return;
    event.preventDefault();
    if (quitPrompt) return;
    quitPrompt = true;
    (async () => {
      let state;
      try {
        state = await backend?.request("snapshot");
      } catch {}
      if (state?.active) {
        const { response } = await dialog.showMessageBox(window, {
          type: "question",
          buttons: ["返回任务", "停止并退出"],
          defaultId: 0,
          cancelId: 0,
          message: "当前任务仍在运行",
          detail: "退出会停止后续操作。未确认的发送记录会保留，防止重复发送。",
        });
        if (response !== 1) {
          quitPrompt = false;
          return;
        }
      }
      await backend?.close();
      browser?.destroy();
      quitting = true;
      app.quit();
    })();
  });
}
