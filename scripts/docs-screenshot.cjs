// Render the real UI with synthetic data, an isolated profile and no network access.
const { app, BrowserWindow, ipcMain, session } = require("electron");
const fs = require("node:fs/promises");
const path = require("node:path");
const os = require("node:os");
const { execFileSync } = require("node:child_process");
const root = path.resolve(__dirname, "..");
const temp = require("node:fs").mkdtempSync(
  path.join(os.tmpdir(), "deliverdesk-docs-"),
);
app.setPath("userData", temp);

app
  .whenReady()
  .then(async () => {
    const python = path.join(
      root,
      ".venv",
      process.platform === "win32" ? "Scripts/python.exe" : "bin/python",
    );
    const config = JSON.parse(
      execFileSync(
        python,
        [
          "-c",
          "from boss_cli.config import Config; print(Config().model_dump_json())",
        ],
        { encoding: "utf8" },
      ),
    );
    config.search.keywords = ["AI应用工程师", "AI产品经理"];
    config.search.city = "杭州";
    config.search.filters = {
      薪资待遇: "20-50K",
      工作经验: "1-3年",
      学历要求: "本科",
    };
    const fixtures = JSON.parse(
      execFileSync(python, [path.join(root, "scripts/export_fixtures.py")], {
        encoding: "utf8",
      }),
    );
    const state = {
      config,
      active: false,
      run: {
        mode: "preview",
        status: "completed",
        scanned: 12,
        matched: 8,
        sent: 0,
        skipped: 4,
        note: "预览完成，可以检查职位和招呼语后开始沟通。",
      },
      history: [],
      events: [],
      attemptsToday: 0,
      directory: "示例数据",
      version: require("../package.json").version,
      browser: { loggedIn: true },
      llm: { configured: true, secureStorage: true },
      resume: {
        document: {
          source_name: "示例候选人简历.docx",
          text: fixtures.RESUME_TEXT,
          profile: fixtures.RESUME_PROFILE,
          profile_model: config.llm.model,
          analyzed_at: "2026-09-10T10:00:00+08:00",
          revision: "example-resume",
        },
        state: { status: "ready", note: "简历特点已保存" },
        greeting: null,
      },
      jobs: [
        {
          job_id: "demo1",
          title: "AI 应用工程师",
          company: "示例科技",
          location: "杭州 · 余杭区",
          salary: "20-30K·14薪",
          updated_at: "2026-09-08T10:00:00+08:00",
          url: "",
        },
        {
          job_id: "demo2",
          title: "AI 产品经理",
          company: "示例智能",
          location: "杭州 · 滨江区",
          salary: "25-40K",
          updated_at: "2026-09-08T10:01:00+08:00",
          url: "",
        },
      ],
    };
    ipcMain.handle("desk:snapshot", () => state);
    const isolated = session.fromPartition("docs-screenshot");
    isolated.webRequest.onBeforeRequest((details, callback) => {
      callback({ cancel: !details.url.startsWith("file:") });
    });
    const window = new BrowserWindow({
      width: 1360,
      height: 1420,
      useContentSize: true,
      show: false,
      webPreferences: {
        preload: path.join(root, "desktop/preload.cjs"),
        session: isolated,
        contextIsolation: true,
        sandbox: true,
        nodeIntegration: false,
        backgroundThrottling: false,
      },
    });
    window.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
    await window.loadFile(path.join(root, "ui-dist/index.html"));
    await window.webContents.executeJavaScript(`
    new Promise((resolve, reject) => {
      const limit = Date.now() + 10000;
      const timer = setInterval(() => {
        if (document.querySelector('.recent tbody tr')) {
          clearInterval(timer); document.fonts.ready.then(resolve);
        } else if (Date.now() > limit) {
          clearInterval(timer); reject(Error('Documentation UI failed to render'));
        }
      }, 50);
    });
  `);
    async function capture(name) {
      const height = await window.webContents.executeJavaScript(
        "document.documentElement.scrollHeight",
      );
      window.setContentSize(1360, height);
      await new Promise((resolve) => setTimeout(resolve, 500));
      const screenshot = await window.webContents.capturePage();
      const target = path.join(root, "docs/assets", name + ".png");
      await fs.mkdir(path.dirname(target), { recursive: true });
      await fs.writeFile(target, screenshot.resize({ width: 1360 }).toPNG());
    }
    await capture("workspace");
    window.setContentSize(1360, 940);
    await window.webContents.executeJavaScript(
      "[...document.querySelectorAll('nav button')].find(button => button.textContent === '个人简历').click()",
    );
    await new Promise((resolve) => setTimeout(resolve, 200));
    await capture("resume");
    config.message.mode = "ai";
    state.resume.greeting = {
      job: state.jobs[0],
      message:
        "您好，看到贵公司的 AI 应用工程师岗位。我曾负责知识库问答项目的需求分析与效果评估，掌握 Python、SQL，希望结合这些经历参与岗位工作，期待进一步交流。",
      model: config.llm.model,
      resumeRevision: "example-resume",
      instructions: config.message.instructions,
    };
    window.setContentSize(1360, 940);
    await window.loadFile(path.join(root, "ui-dist/index.html"));
    await window.webContents
      .executeJavaScript(`new Promise((resolve, reject) => {
      const end = Date.now() + 10000;
      const timer = setInterval(() => {
        const select = document.querySelector('select[aria-label="预览岗位"]');
        if (select) { clearInterval(timer); select.value='demo1'; select.dispatchEvent(new Event('change', { bubbles: true })); resolve(); }
        else if (Date.now() > end) { clearInterval(timer); reject(Error('Greeting preview failed to render')); }
      }, 50);
    });`);
    await new Promise((resolve) => setTimeout(resolve, 200));
    await capture("ai-greeting");
    window.destroy();
    console.log(
      "Created workspace, resume and AI greeting screenshots from synthetic data",
    );
    app.quit();
  })
  .catch((error) => {
    console.error(error);
    app.exit(1);
  });
