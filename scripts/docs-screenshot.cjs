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
    state.greetings = { total: 2, revision: 1 };
    const greetings = state.jobs.map((job, index) => ({
      id: `example-${index}`,
      ...job,
      mode: index ? "preview" : "send",
      status: "generated",
      delivery_status: index ? "" : "sent",
      created_at: "2026-09-10T15:30:00+08:00",
      updated_at: "2026-09-10T15:30:10+08:00",
      resume_name: state.resume.document.source_name,
      resume_revision: "example-resume",
      model: config.llm.model,
      instructions: config.message.instructions,
      message:
        "您好，我曾负责知识库问答项目的需求分析与效果评估，并掌握 Python、SQL。贵公司的岗位涉及知识库应用，希望结合这些经历参与需求梳理和效果验证，期待进一步交流。",
      job_json: {
        ...job,
        description:
          "负责企业知识库应用的需求分析与效果评估，使用 Python、SQL 分析业务数据。",
      },
      profile_json: fixtures.RESUME_PROFILE,
      usage: { total_tokens: 1680 },
      note: "",
      delivery_note: index ? "" : "已核实完整正文与送达回执",
    }));
    ipcMain.handle("desk:greetings", (_event, params) =>
      params.id
        ? greetings.find((row) => row.id === params.id)
        : { items: greetings, nextCursor: null },
    );
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
    window.setContentSize(1360, 940);
    await window.loadFile(path.join(root, "ui-dist/index.html"));
    await window.webContents
      .executeJavaScript(`new Promise((resolve, reject) => {
      const end = Date.now() + 10000;
      const timer = setInterval(() => {
        const panel = document.querySelector('textarea[aria-label="AI 招呼要求"]');
        if (panel) { clearInterval(timer); resolve(); }
        else if (Date.now() > end) { clearInterval(timer); reject(Error('Automatic greeting settings failed to render')); }
      }, 50);
    });`);
    await new Promise((resolve) => setTimeout(resolve, 200));
    await capture("ai-greeting");
    window.setContentSize(1360, 940);
    await window.webContents.executeJavaScript(
      "[...document.querySelectorAll('nav button')].find(button => button.textContent === '招呼记录').click()",
    );
    await new Promise((resolve) => setTimeout(resolve, 500));
    await capture("greeting-history");
    await window.webContents.executeJavaScript(
      "[...document.querySelectorAll('button')].find(button => button.textContent === '查看记录').click()",
    );
    await new Promise((resolve) => setTimeout(resolve, 500));
    await capture("greeting-detail");
    config.platform = "zhaopin";
    config.search.filters = {
      薪资待遇: "10K-15K",
      工作经验: "1-3年",
      学历要求: "本科",
    };
    state.browser.platform = "zhaopin";
    state.run.platform = "zhaopin";
    state.run.note = "预览完成，请核对职位范围和智联在线简历后开始投递。";
    state.jobs = state.jobs.map((job) => ({
      ...job,
      platform: "zhaopin",
      job_id: "zhaopin:" + job.job_id,
      salary: "10000-15000元",
    }));
    window.setContentSize(1360, 1420);
    await window.loadFile(path.join(root, "ui-dist/index.html"));
    await window.webContents.executeJavaScript(`new Promise((resolve,reject)=>{
      const end=Date.now()+10000;const timer=setInterval(()=>{
        if(document.querySelector('.platform-delivery-info')){clearInterval(timer);resolve();}
        else if(Date.now()>end){clearInterval(timer);reject(Error('Zhaopin settings failed to render'));}
      },50);
    })`);
    await capture("zhaopin-workspace");
    window.destroy();
    console.log(
      "Created BOSS and Zhaopin workspace, resume and AI greeting screenshots from synthetic data",
    );
    app.quit();
  })
  .catch((error) => {
    console.error(error);
    app.exit(1);
  });
