// Exercise the shipped renderer, preload and main-process IPC in a separate data directory.
// BOSS requests are fulfilled with synthetic pages before any browser tab is opened.
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const os = require("node:os");
const { execFileSync } = require("node:child_process");
const root = path.resolve(__dirname, "..");
const python = path.join(
  root,
  ".venv",
  process.platform === "win32" ? "Scripts/python.exe" : "bin/python",
);
const driver = execFileSync(
  python,
  [
    "-c",
    "import pathlib, playwright; print(pathlib.Path(playwright.__file__).parent / 'driver' / 'package')",
  ],
  { encoding: "utf8", env: { ...process.env, PYTHONIOENCODING: "utf-8" } },
).trim();
const { _electron } = require(driver);
const fixtures = JSON.parse(
  execFileSync(python, [path.join(root, "scripts/export_fixtures.py")], {
    encoding: "utf8",
  }),
);
let desktop, page;
async function until(check, timeout = 20000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) {
    const result = await check();
    if (result) return result;
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw Error("Desktop UI check timed out");
}
async function snapshot() {
  return page.evaluate(() => window.desk.snapshot());
}
async function run() {
  const temp = await fs.mkdtemp(path.join(os.tmpdir(), "deliverdesk-ui-"));
  execFileSync(python, [
    "-c",
    "import sys; from pathlib import Path; from boss_cli.storage import Store; s=Store(Path(sys.argv[1])/'state'); s.create_run('legacy','send'); s.update_run('legacy',status='completed',sent=1); s.db.execute('ALTER TABLE runs DROP COLUMN target'); s.db.execute('ALTER TABLE runs DROP COLUMN attempts'); s.close()",
    temp,
  ]);
  desktop = await _electron.launch({
    executablePath: require("electron"),
    args: [root],
    env: {
      ...process.env,
      DELIVERDESK_DEV_DATA_DIR: temp,
      DELIVERDESK_WORKSPACE: "0",
    },
    timeout: 30000,
  });
  page = await desktop.firstWindow();
  page.setDefaultTimeout(15000);
  await page.getByRole("button", { name: "开始投递", exact: true }).waitFor();
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await desktop.evaluate(async ({ session }, fixtures) => {
    let current = "ui001";
    const card = fixtures.LIST_HTML.match(
      /<li class="new-card">[\s\S]*?<\/li>\n/,
    )[0];
    await session
      .fromPartition("persist:boss")
      .protocol.handle("https", (request) => {
        const route = new URL(request.url).pathname;
        if (route === "/web/user/")
          return new Response(
            `<!doctype html><html><body style="font:16px sans-serif;text-align:center;padding:90px"><h1>扫码登录 · 隔离测试页面</h1><p>这里仅模拟登录流程，没有连接真实招聘网站。</p><button id="demo-login" onclick="location.href='/web/geek/jobs'">模拟扫码完成</button></body></html>`,
            { headers: { "content-type": "text/html;charset=utf-8" } },
          );
        const detail = route.match(/\/job_detail\/([\w-]+)\.html/);
        if (detail) current = detail[1];
        let html = route.includes("/chat")
          ? fixtures.FULL_CHAT_HTML
          : detail
            ? fixtures.DETAIL_COMPOSER_HTML
            : fixtures.LIST_HTML.replace(
                /<ul class="results">[\s\S]*?<\/ul>\s*<aside>/,
                `<ul class="results">${["ui001", "ui002", "ui003", "ui004"].map((id) => card.replaceAll("abc123", id)).join("")}</ul><aside>`,
              );
        if (route.includes("/chat") || detail)
          html = html.replaceAll("abc123", current);
        html = html.replace(
          "</body>",
          "<style>body{font:14px/24px sans-serif;padding:28px}#chat-input{border:1px solid #aaa;min-height:60px;width:420px}button{padding:12px}a{display:inline-block}</style></body>",
        );
        return new Response(html, {
          headers: { "content-type": "text/html;charset=utf-8" },
        });
      });
    session.defaultSession.webRequest.onBeforeRequest((details, callback) =>
      callback({ cancel: !details.url.startsWith("file:") }),
    );
  }, fixtures);
  await page.getByRole("button", { name: "扫码登录", exact: true }).click();
  await page.getByRole("region", { name: "BOSS 内置浏览器" }).waitFor();
  assert.equal(
    await page.getByRole("progressbar", { name: "本次投递进度" }).count(),
    0,
    "Legacy runs have no saved target and must not use the new configuration's target",
  );
  await until(async () =>
    (await snapshot()).browser.tabs?.some((tab) =>
      tab.url.includes("/web/user/"),
    ),
  );
  assert.equal(
    await desktop.evaluate(
      ({ BaseWindow }) => BaseWindow.getAllWindows().length,
    ),
    1,
  );
  await desktop.evaluate(async ({ webContents }) => {
    const site = webContents
      .getAllWebContents()
      .find((wc) => wc.getURL().includes("/web/user/"));
    await site.executeJavaScript(
      "document.getElementById('demo-login').click()",
    );
  });
  await until(async () => (await snapshot()).browser.loggedIn);
  console.log(
    "PASS: real app opens login inside its only window and detects completed login",
  );
  for (const width of [1120, 1360]) {
    await desktop.evaluate(
      ({ BaseWindow }, width) =>
        BaseWindow.getAllWindows()[0].setContentSize(width, 880),
      width,
    );
    await until(async () => {
      const slot = await page.locator(".browser-viewport").boundingBox();
      const bounds = await desktop.evaluate(({ BaseWindow }) =>
        BaseWindow.getAllWindows()[0].contentView.children.at(-1).getBounds(),
      );
      return (
        slot &&
        ["x", "y", "width", "height"].every(
          (key) => Math.abs(Math.round(slot[key]) - bounds[key]) <= 1,
        )
      );
    });
  }
  await page.getByRole("button", { name: "返回工作台", exact: true }).click();
  await page.getByLabel("本次最多浏览", { exact: false }).fill("1");
  await page.getByLabel("本次投递次数", { exact: false }).fill("3");
  assert.equal(
    await page.getByLabel("本次最多浏览", { exact: false }).inputValue(),
    "15",
  );
  await page.getByLabel("操作节奏", { exact: false }).selectOption("流畅");
  await page.getByRole("button", { name: "保存修改", exact: true }).click();
  let state = await snapshot();
  assert.equal(state.config.run.max_sends, 3);
  assert.deepEqual(state.config.run.job_delay, [8, 16]);
  // Shorten fixture-only waits after verifying the user's selected preset was saved.
  state.config.run.action_delay = [0.2, 0.2];
  state.config.run.job_delay = [0.7, 0.7];
  await page.evaluate(
    (config) => window.desk.saveConfig({ config }),
    state.config,
  );
  await page.reload();
  await page.getByRole("button", { name: "开始投递", exact: true }).click();
  await page.getByRole("dialog", { name: "确认本次投递范围" }).waitFor();
  await page.getByRole("button", { name: "确认开始", exact: true }).click();
  await page.getByRole("region", { name: "BOSS 内置浏览器" }).waitFor();
  await until(async () => (await snapshot()).active);
  await page.getByRole("button", { name: "运行日志", exact: true }).click();
  state = await until(async () => {
    const data = await snapshot();
    return !data.active ? data : false;
  }, 60000);
  assert.equal(
    state.run.status,
    "completed",
    JSON.stringify(state.events.slice(-8)),
  );
  assert.equal(state.run.sent, 3);
  assert.equal(state.run.target, 3);
  assert.equal(state.run.attempts, 3);
  assert.ok(!state.history.some((row) => row.job_id === "ui004"));
  assert.equal(
    await desktop.evaluate(
      ({ BaseWindow }) => BaseWindow.getAllWindows().length,
    ),
    1,
  );
  await page.getByRole("button", { name: "BOSS 浏览器", exact: true }).click();
  await until(
    async () =>
      (await page
        .getByRole("progressbar", { name: "本次投递进度" })
        .getAttribute("value")) === "3",
  );
  const output = path.join(root, "artifacts");
  await fs.mkdir(output, { recursive: true });
  await page.screenshot({ path: path.join(output, "desktop-browser-ui.png") });
  await page.getByRole("button", { name: "返回工作台", exact: true }).click();
  await page.screenshot({
    path: path.join(output, "desktop-workspace-ui.png"),
    fullPage: true,
  });
  assert.deepEqual(errors, []);
  await fs.writeFile(
    path.join(output, "desktop-ui-smoke.json"),
    JSON.stringify(
      {
        passed: true,
        externalNetwork: false,
        batchSends: 3,
        windowCount: 1,
        resizeWidths: [1120, 1360],
      },
      null,
      2,
    ),
  );
  console.log(
    "PASS: target editing and scan-budget adjustment, pace preset, automatic browser switch, background batch, progress and responsive view bounds",
  );
  await desktop.close();
  desktop = null;
}
run().catch(async (error) => {
  console.error(error);
  try {
    if (page)
      await page.evaluate(() => window.desk.control({ action: "stop" }));
  } catch {}
  desktop?.process().kill();
  process.exitCode = 1;
});
