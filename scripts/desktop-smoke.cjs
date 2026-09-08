// Every HTTPS request is fulfilled by synthetic HTML in an isolated, temporary session.
const { app } = require("electron");
const fs = require("node:fs/promises");
const os = require("node:os");
const path = require("node:path");
const assert = require("node:assert/strict");
const { execFileSync } = require("node:child_process");
const { BrowserManager, allowedURL } = require("../desktop/browser.cjs");
const { Backend } = require("../desktop/backend.cjs");
const root = path.resolve(__dirname, "..");
const temp = require("node:fs").mkdtempSync(
  path.join(os.tmpdir(), "deliverdesk-smoke-"),
);
app.setPath("userData", path.join(temp, "profile"));
let backend, browser;
async function until(check, timeout = 25000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) {
    const result = await check();
    if (result) return result;
    await new Promise((r) => setTimeout(r, 100));
  }
  throw Error("Desktop integration timed out");
}
app
  .whenReady()
  .then(async () => {
    const python = path.join(
      root,
      ".venv",
      process.platform === "win32" ? "Scripts/python.exe" : "bin/python",
    );
    const fixtures = JSON.parse(
      execFileSync(python, [path.join(root, "scripts/export_fixtures.py")], {
        encoding: "utf8",
      }),
    );
    const requests = [];
    let fixtureJob = "abc123";
    browser = new BrowserManager(temp, (data) => backend?.send(data), {
      partition: "desktop-smoke",
      // X11 needs a mapped window to receive native mouse events under Xvfb.
      visible: process.platform === "linux",
    });
    await browser.session.protocol.handle("https", (request) => {
      requests.push(request.url);
      const route = new URL(request.url).pathname;
      let html = route.includes("/chat")
        ? fixtures.CHAT_HTML.replace(
            "message-item is-self",
            "message-item item-myself",
          )
            .replace("text.className='text'", "text.className='text-content'")
            .replace(
              '<a href="/job_detail/abc123.html">AI应用工程师</a>',
              "<span>AI应用工程师</span><button onclick=\"window.open('/job_detail/abc123.html')\">查看职位</button>",
            )
        : route.includes("/job_detail/")
          ? fixtures.DETAIL_HTML.replace(
              "this.textContent='继续沟通'",
              "location.href='/web/geek/chat'",
            )
          : fixtures.LIST_HTML;
      html = html
        .replaceAll("abc123", fixtureJob)
        .replace(
          "</body>",
          "<style>body{font:14px/24px sans-serif;padding:20px}#chat-input{border:1px solid #aaa;min-height:60px;width:400px}button{padding:12px}a{display:inline-block}</style></body>",
        );
      return new Response(html, {
        headers: { "content-type": "text/html;charset=utf-8" },
      });
    });
    backend = new Backend({
      root,
      resources: path.join(root, "build"),
      packaged: process.argv.includes("--packaged-backend"),
      dataDir: temp,
      config: path.join(temp, "config.yaml"),
      browser,
    });
    backend.on("diagnostic", (message) => console.error(message));
    await backend.ready;
    let state = await backend.request("snapshot");
    const config = state.config;
    config.search.keywords = ["AI应用"];
    config.search.max_jobs = 1;
    config.search.max_scrolls = 0;
    config.search.filters = { 学历要求: "本科" };
    config.run.action_delay = [0, 0];
    config.run.job_delay = [0, 0];
    config.run.cooldown_seconds = [0, 0];
    config.run.max_sends = 1;
    config.browser.timeout_seconds = 4;
    await backend.request("saveConfig", { config });
    await backend.request("start", { mode: "preview" });
    state = await until(async () => {
      const s = await backend.request("snapshot");
      return !s.active && s.run ? s : false;
    });
    assert.equal(
      state.run.status,
      "completed",
      JSON.stringify(state.events.slice(-8)),
    );
    assert.equal(state.run.matched, 1);
    assert.equal(state.history.length, 0);
    assert.equal(state.attemptsToday, 0);
    console.log(
      "PASS: Electron + Python preview, exact filter selection, zero contact",
    );
    await backend.request("start", { mode: "send" });
    state = await until(async () => {
      const s = await backend.request("snapshot");
      return !s.active && s.run.mode === "send" ? s : false;
    });
    assert.equal(
      state.run.status,
      "completed",
      JSON.stringify(state.events.slice(-8)),
    );
    assert.equal(state.history[0].status, "sent");
    assert.equal(state.run.sent, 1);
    assert.equal(state.attemptsToday, 1);
    assert.ok(requests.some((url) => url.includes("/web/geek/chat")));
    console.log("PASS: custom message, visible receipt, persistent history");
    await backend.request("start", { mode: "send" });
    state = await until(async () => {
      const s = await backend.request("snapshot");
      return !s.active ? s : false;
    });
    assert.equal(state.run.sent, 0);
    assert.equal(state.run.skipped, 1);
    assert.equal(state.attemptsToday, 1);
    console.log("PASS: second run skips already sent job before contact");
    config.run.action_delay = [5, 5];
    await backend.request("saveConfig", { config });
    await backend.request("start", { mode: "preview" });
    await until(async () => (await backend.request("snapshot")).active);
    await backend.request("control", { action: "pause" });
    await until(
      async () => (await backend.request("snapshot")).run.status === "paused",
    );
    await assert.rejects(backend.request("saveConfig", { config }), /停止/);
    await backend.request("control", { action: "run" });
    await until(
      async () => (await backend.request("snapshot")).run.status === "running",
    );
    await backend.request("control", { action: "stop" });
    state = await until(async () => {
      const s = await backend.request("snapshot");
      return !s.active ? s : false;
    });
    assert.equal(state.run.status, "stopped");
    console.log(
      "PASS: responsive pause, resume, stop and frozen running config",
    );
    assert.equal(allowedURL("https://www.zhipin.com.evil.example/"), false);
    const page = browser.create();
    await assert.rejects(
      browser.command("goto", { page: page.id, url: "file:///etc/passwd" }),
    );
    await assert.rejects(
      browser.command("screenshot", {
        page: page.id,
        path: path.join(temp, "..", "outside.png"),
      }),
    );
    console.log("PASS: browser navigation and screenshot path restrictions");
    const history = await backend.request("export");
    assert.equal(history.length, 1);
    await backend.close();
    backend = new Backend({
      root,
      resources: path.join(root, "build"),
      packaged: process.argv.includes("--packaged-backend"),
      dataDir: temp,
      config: path.join(temp, "config.yaml"),
      browser,
    });
    await backend.ready;
    assert.equal((await backend.request("snapshot")).history[0].status, "sent");
    console.log("PASS: backend restart preserves history");
    fixtureJob = "abc789";
    config.run.action_delay = [0.8, 0.8];
    await backend.request("saveConfig", { config });
    await backend.request("start", { mode: "send" });
    await until(async () =>
      (await backend.request("snapshot")).history.some(
        (row) => row.job_id === fixtureJob && row.status === "sending",
      ),
    );
    const beforeClose = Date.now();
    await backend.close();
    assert.ok(
      Date.now() - beforeClose < 4000,
      "Closing an active task should not wait on browser screenshots",
    );
    backend = new Backend({
      root,
      resources: path.join(root, "build"),
      packaged: process.argv.includes("--packaged-backend"),
      dataDir: temp,
      config: path.join(temp, "config.yaml"),
      browser,
    });
    await backend.ready;
    state = await backend.request("snapshot");
    assert.equal(
      state.history.find((row) => row.job_id === fixtureJob).status,
      "unknown",
    );
    assert.equal(state.attemptsToday, 2);
    console.log(
      "PASS: exit during reserved send preserves uncertainty and prevents retry",
    );
    await backend.close();
    browser.destroy();
    await fs.mkdir(path.join(root, "artifacts"), { recursive: true });
    await fs.writeFile(
      path.join(root, "artifacts", "desktop-smoke.json"),
      JSON.stringify(
        {
          passed: true,
          packagedBackend: process.argv.includes("--packaged-backend"),
          date: new Date().toISOString(),
          requests: requests.length,
          externalNetwork: false,
        },
        null,
        2,
      ),
    );
    app.quit();
  })
  .catch(async (error) => {
    console.error(error);
    await backend?.close();
    browser?.destroy();
    app.exit(1);
  });
