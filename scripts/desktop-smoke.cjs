// Every HTTPS request is fulfilled by synthetic HTML in an isolated, temporary session.
const { app, BaseWindow, WebContentsView } = require("electron");
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
let backend, browser, host, shellView;
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
    const verificationPopups = [];
    let fixtureJob = "abc123";
    let fixtureJobs = [fixtureJob];
    host = new BaseWindow({
      width: 1360,
      height: 940,
      show: true,
    });
    shellView = new WebContentsView({
      webPreferences: {
        sandbox: true,
        contextIsolation: true,
        nodeIntegration: false,
      },
    });
    host.contentView.addChildView(shellView);
    host.once("closed", () => {
      if (!shellView.webContents.isDestroyed())
        shellView.webContents.close({ waitForBeforeUnload: false });
    });
    await shellView.webContents.loadURL("about:blank");
    browser = new BrowserManager(
      temp,
      (data) => {
        if (data.event === "popup")
          verificationPopups.push({
            page: data.page,
            parent: data.parent,
            active: browser.lastId,
          });
        backend?.send(data);
      },
      {
        partition: "desktop-smoke",
        host,
        shellView,
      },
    );
    browser.setViewport({
      visible: true,
      bounds: { x: 220, y: 220, width: 1100, height: 640 },
    });
    await browser.session.protocol.handle("https", (request) => {
      requests.push(request.url);
      const route = new URL(request.url).pathname;
      const detailId = route.match(/\/job_detail\/([\w-]+)\.html/);
      if (detailId) fixtureJob = detailId[1];
      const card = fixtures.LIST_HTML.match(
        /<li class="new-card">[\s\S]*?<\/li>\n/,
      )[0];
      let html = route.includes("/chat")
        ? fixtures.FULL_CHAT_HTML
        : route.includes("/job_detail/")
          ? fixtures.DETAIL_COMPOSER_HTML
          : fixtures.LIST_HTML.replace(
              /<ul class="results">[\s\S]*?<\/ul>\s*<aside>/,
              `<ul class="results">${fixtureJobs.map((id) => card.replaceAll("abc123", id)).join("")}</ul><aside>`,
            );
      if (route.includes("/chat") || detailId)
        html = html.replaceAll("abc123", fixtureJob);
      if (route.includes("/chat"))
        html = html
          .replace(
            '<aside><button id="other-contact">另一位联系人</button></aside>',
            fixtures.CONTACT_LIST_HTML,
          )
          .replace(
            'class="chat-conversation"',
            'class="chat-conversation" hidden',
          );
      html = html.replace(
        "</body>",
        "<style>html{min-width:1280px}body{font:14px/24px sans-serif;padding:20px}#chat-input{border:1px solid #aaa;min-height:60px;width:400px}button{padding:12px}a{display:inline-block}</style></body>",
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
    assert.equal(
      verificationPopups.length,
      1,
      "A conversation is checked once, including delayed receipt polls",
    );
    assert.ok(
      verificationPopups.every((popup) => popup.active === popup.parent),
      "Job verification must keep the chat selected",
    );
    console.log(
      "PASS: unread-badged message entry, covered composer, single background job check, delayed receipt, persistent history",
    );
    assert.equal(
      BaseWindow.getAllWindows().length,
      1,
      "All job and chat popups must stay in one host window",
    );
    assert.ok(
      [...browser.views.values()].every((view) =>
        host.contentView.children.includes(view),
      ),
    );
    const selectedView = browser.get(browser.lastId);
    assert.equal(
      await selectedView.webContents.executeJavaScript(
        "document.body.dataset.selectedContact",
      ),
      "expected",
      "Match the target recruiter among different contacts at the same company",
    );
    assert.equal(
      await selectedView.webContents.executeJavaScript(
        "document.body.dataset.analysisOpened || ''",
      ),
      "",
      "The inline competitor card must not be opened to send a message",
    );
    const detailView = [...browser.views.values()].find((view) =>
      view.webContents.getURL().includes("/job_detail/"),
    );
    assert.equal(
      await detailView.webContents.executeJavaScript(
        "document.querySelector('textarea').value",
      ),
      "",
      "Continue from the masked composer via the unread-badged message entry",
    );
    assert.equal(selectedView.getBounds().x, 220);
    const originalSize = host.getContentSize();
    await selectedView.webContents.executeJavaScript(`(() => {
      window.edgeClicks = [];
      for (const side of ['left', 'right']) {
        const button = document.createElement('button');
        button.id = 'edge-' + side;
        button.style = 'position:fixed;top:0;width:60px;height:60px;z-index:9999;' + side + ':0';
        button.onclick = event => { if (event.isTrusted) window.edgeClicks.push(side); };
        Object.defineProperty(button, Symbol.for('deliverdesk.clickTarget'), {value: side});
        document.body.append(button);
      }
    })()`);
    for (const width of [1080, 1580, 1080]) {
      host.setContentSize(width, 880);
      browser.setViewport({
        visible: true,
        bounds: { x: 0, y: 160, width, height: 720 },
      });
      await browser.fitToWidth(browser.lastId);
      const geometry = await selectedView.webContents.executeJavaScript(`({
        content: document.documentElement.scrollWidth, viewport: document.documentElement.clientWidth,
        points: ['left', 'right'].map(side => {
          const r = document.getElementById('edge-' + side).getBoundingClientRect();
          return {target: side, x: r.x + r.width / 2, y: r.y + r.height / 2};
        })})`);
      assert.ok(
        geometry.content <= geometry.viewport + 1,
        JSON.stringify(geometry),
      );
      assert.equal(selectedView.webContents.getZoomFactor() < 1, width < 1280);
      for (const point of geometry.points)
        await browser.command("click", { page: browser.lastId, ...point });
    }
    await until(
      async () =>
        (await selectedView.webContents.executeJavaScript(
          "window.edgeClicks.length",
        )) === 6,
    );
    assert.deepEqual(
      await selectedView.webContents.executeJavaScript("window.edgeClicks"),
      ["left", "right", "left", "right", "left", "right"],
    );
    await selectedView.webContents.executeJavaScript(
      "document.querySelectorAll('[id^=edge-]').forEach(e=>e.remove())",
    );
    host.setContentSize(...originalSize);
    browser.setViewport({
      visible: true,
      bounds: { x: 220, y: 220, width: 1100, height: 640 },
    });
    await browser.fitToWidth(browser.lastId);
    console.log(
      "PASS: full page width at 1080/1580, restored zoom, trusted clicks at both scaled edges",
    );
    browser.setViewport({ visible: false });
    assert.equal(host.contentView.children.at(-1), browser.shellView);
    for (let trial = 0; trial < 12; trial++) {
      const reload = trial > 0;
      if (reload)
        await browser.command("goto", {
          page: browser.lastId,
          url: selectedView.webContents.getURL(),
        });
      shellView.webContents.focus();
      await selectedView.webContents.executeJavaScript(
        "window.nativeHiddenClick = 0; const probe = document.createElement('button'); probe.id='native-probe'; probe.style='position:fixed;left:0;top:0;width:60px;height:60px;z-index:9999'; probe.onclick=(event)=>{if(event.isTrusted)window.nativeHiddenClick++};document.body.append(probe)",
      );
      await browser.command("click", { page: browser.lastId, x: 25, y: 25 });
      await new Promise((resolve) => setTimeout(resolve, 100));
      assert.equal(
        await selectedView.webContents.executeJavaScript(
          "window.nativeHiddenClick",
        ),
        1,
        `Native input continues behind the workspace, including after navigation (reload=${reload})`,
      );
      await selectedView.webContents.executeJavaScript(
        "document.getElementById('native-probe').remove()",
      );
    }
    for (const change of ["move", "disable"]) {
      await selectedView.webContents.executeJavaScript(`(() => {
        window.nativeHiddenClick=0;
        const probe=document.createElement('button');probe.id='native-probe';
        probe.style='position:fixed;left:0;top:0;width:60px;height:60px;z-index:9999';
        probe.onclick=()=>window.nativeHiddenClick++;
        Object.defineProperty(probe,Symbol.for('deliverdesk.clickTarget'),{value:'guard-probe'});
        document.body.append(probe);
        requestAnimationFrame(()=>{${change === "move" ? "probe.style.left='200px'" : "probe.disabled=true"}});
      })()`);
      await assert.rejects(
        browser.command("click", {
          page: browser.lastId,
          x: 25,
          y: 25,
          target: "guard-probe",
        }),
        /目标控件位置发生变化/,
      );
      assert.equal(
        await selectedView.webContents.executeJavaScript(
          "window.nativeHiddenClick",
        ),
        0,
      );
      await selectedView.webContents.executeJavaScript(
        "document.getElementById('native-probe').remove()",
      );
    }
    browser.setViewport({ visible: true });
    assert.ok(selectedView.getVisible());
    assert.equal(
      await selectedView.webContents.executeJavaScript(
        "typeof require + ':' + typeof window.desk",
      ),
      "undefined:undefined",
    );
    assert.equal(
      await selectedView.webContents.executeJavaScript(
        "window.open('https://example.org/') === null",
      ),
      true,
    );
    assert.equal(BaseWindow.getAllWindows().length, 1);
    console.log(
      "PASS: embedded views, native popup containment, hiding and remote-page isolation",
    );
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
    fixtureJobs = ["abc123", "batch001", "batch002", "batch003", "batch004"];
    config.search.max_jobs = 20;
    config.run.max_sends = 3;
    config.run.action_delay = [0, 0];
    await backend.request("saveConfig", { config });
    const batchStart = Date.now();
    browser.setViewport({ visible: false });
    shellView.webContents.focus();
    await backend.request("start", { mode: "send" });
    state = await until(async () => {
      const s = await backend.request("snapshot");
      return !s.active ? s : false;
    }, 60000);
    assert.equal(
      state.run.status,
      "completed",
      JSON.stringify(state.events.slice(-8)),
    );
    assert.equal(state.run.sent, 3);
    assert.equal(state.run.attempts, 3);
    assert.equal(state.run.target, 3);
    assert.equal(state.run.skipped, 1);
    assert.equal(verificationPopups.length, 4);
    assert.ok(
      verificationPopups.every((popup) => popup.active === popup.parent),
    );
    assert.equal(state.attemptsToday, 4);
    assert.ok(!state.history.some((row) => row.job_id === "batch004"));
    assert.match(state.run.note, /达到本次目标/);
    assert.equal(BaseWindow.getAllWindows().length, 1);
    const batchSeconds = (Date.now() - batchStart) / 1000;
    browser.setViewport({ visible: true });
    console.log(
      `PASS: three consecutive sends, prior-job dedup, exact target stop (${batchSeconds.toFixed(1)}s fixture runtime)`,
    );
    fixtureJobs = ["abc789"];
    config.search.max_jobs = 1;
    config.run.max_sends = 1;
    config.run.action_delay = [0.8, 0.8];
    await backend.request("saveConfig", { config });
    await backend.request("start", { mode: "send" });
    await until(async () =>
      (await backend.request("snapshot")).history.some(
        (row) => row.job_id === "abc789" && row.status === "sending",
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
      state.history.find((row) => row.job_id === "abc789").status,
      "unknown",
    );
    assert.equal(state.attemptsToday, 5);
    console.log(
      "PASS: exit during reserved send preserves uncertainty and prevents retry",
    );
    await backend.close();
    browser.destroy();
    host.destroy();
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
          batchSends: 3,
          batchSeconds,
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
    host?.destroy();
    app.exit(1);
  });
