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
        if (globalThis.inboxTestMode && route === "/web/geek/chat")
          return new Response(fixtures.INBOX_HTML, {
            headers: { "content-type": "text/html;charset=utf-8" },
          });
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
        if (route.includes("/chat") && globalThis.replyTestMode)
          html = html.replace(
            'id="messages">',
            'id="messages"><div class="message-item item-myself"><div class="text-content">您好，我对这个职位很感兴趣。</div></div><div class="message-item"><div class="text-content">请问你做过哪些 AI 项目？</div></div>',
          );
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
          "<style>html{min-width:1280px}body{font:14px/24px sans-serif;padding:28px}#chat-input{border:1px solid #aaa;min-height:60px;width:420px}button{padding:12px}a{display:inline-block}</style></body>",
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
  for (const width of [1080, 1360, 1580, 1080]) {
    let resizeState;
    await desktop.evaluate(
      ({ BaseWindow }, width) =>
        BaseWindow.getAllWindows()[0].setContentSize(
          width,
          width === 1080 ? 734 : 880,
        ),
      width,
    );
    await until(async () => {
      const slot = await page.locator(".browser-viewport").boundingBox();
      const bounds = await desktop.evaluate(({ BaseWindow }) =>
        BaseWindow.getAllWindows()[0].contentView.children.at(-1).getBounds(),
      );
      const website = await desktop.evaluate(async ({ BaseWindow }) => {
        const host = BaseWindow.getAllWindows()[0];
        const view = host.contentView.children.at(-1);
        const size = await view.webContents.executeJavaScript(
          "({ content: document.documentElement.scrollWidth, viewport: document.documentElement.clientWidth })",
        );
        return {
          ...size,
          bounds: view.getBounds(),
          host: host.getContentSize(),
          loading: view.webContents.isLoadingMainFrame(),
          zoom: view.webContents.getZoomFactor(),
        };
      });
      resizeState = { requested: width, slot, bounds, website };
      return (
        slot &&
        website.content <= website.viewport + 1 &&
        website.bounds.width === website.host[0] &&
        Math.abs(slot.y + slot.height - website.host[1]) <= 1 &&
        slot.x === 0 &&
        slot.y <= 185 &&
        ["x", "y", "width", "height"].every(
          (key) => Math.abs(Math.round(slot[key]) - bounds[key]) <= 1,
        )
      );
    }).catch((error) => {
      throw Error(`${error.message}: ${JSON.stringify(resizeState)}`);
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
  // Capture the native website as well as the shell: renderer-only screenshots
  // cannot show a sibling WebContentsView and previously left the browser blank.
  const browserImage = await desktop.evaluate(
    async ({ BaseWindow, nativeImage }) => {
      const host = BaseWindow.getAllWindows()[0];
      const shell = host.contentView.children.find((view) =>
        view.webContents.getURL().startsWith("file:"),
      );
      const site = host.contentView.children.at(-1);
      const shellImage = await shell.webContents.capturePage();
      const siteImage = await site.webContents.capturePage();
      const size = shellImage.getSize(1),
        bounds = site.getBounds(),
        siteSize = siteImage.getSize(1);
      const [hostWidth, hostHeight] = host.getContentSize();
      const offset = {
        x: Math.round((bounds.x * size.width) / hostWidth),
        y: Math.round((bounds.y * size.height) / hostHeight),
      };
      const composite = shellImage.toBitmap({ scaleFactor: 1 });
      const pixels = siteImage.toBitmap({ scaleFactor: 1 });
      if (
        composite.length !== size.width * size.height * 4 ||
        pixels.length !== siteSize.width * siteSize.height * 4
      )
        throw Error("Unexpected screenshot pixel dimensions");
      for (
        let row = 0;
        row < Math.min(siteSize.height, size.height - offset.y);
        row++
      ) {
        const start = row * siteSize.width * 4;
        pixels.copy(
          composite,
          ((row + offset.y) * size.width + offset.x) * 4,
          start,
          start + Math.min(siteSize.width, size.width - offset.x) * 4,
        );
      }
      return nativeImage
        .createFromBitmap(composite, { ...size, scaleFactor: 1 })
        .toPNG()
        .toString("base64");
    },
  );
  await fs.writeFile(
    path.join(output, "desktop-browser-ui.png"),
    Buffer.from(browserImage, "base64"),
  );
  await page.getByRole("button", { name: "返回工作台", exact: true }).click();
  await page.screenshot({
    path: path.join(output, "desktop-workspace-ui.png"),
    fullPage: true,
  });
  // Use only a made-up key and local responses. Node's fetch is mocked independently
  // from Electron's already-intercepted BOSS session; no LLM request leaves the machine.
  await desktop.evaluate(() => {
    globalThis.replyTestMode = true;
    globalThis.llmRequests = [];
    globalThis.fetch = async (url, options) => {
      if (url === "https://api.deepseek.com/models")
        return new Response(
          JSON.stringify({
            data: [{ id: "deepseek-v4-flash" }, { id: "deepseek-v4-pro" }],
          }),
        );
      if (url !== "https://api.deepseek.com/chat/completions")
        throw Error("Unexpected test network request");
      globalThis.llmRequests.push(JSON.parse(options.body));
      return new Response(
        JSON.stringify({
          choices: [
            {
              finish_reason: "stop",
              message: {
                role: "assistant",
                content: globalThis.inboxTestMode
                  ? "我会先明确岗位目标，再结合具体问题说明解决思路。".repeat(
                      65,
                    ) + "以上是完整回复。"
                  : "我做过一个面向内部知识库的 AI 问答项目，可以介绍需求分析和效果评估的过程。",
              },
            },
          ],
        }),
      );
    };
  });
  await page
    .locator("nav")
    .getByRole("button", { name: "模型与人格", exact: true })
    .click();
  const fakeKey = "sk-desktop-ui-test-key-never-real";
  await page.getByLabel("API Key", { exact: false }).fill(fakeKey);
  await page.getByRole("button", { name: "保存密钥", exact: true }).click();
  await until(async () => (await snapshot()).llm.configured);
  await until(
    async () =>
      (await page.getByLabel("API Key", { exact: false }).inputValue()) === "",
  );
  assert.ok(
    !(await fs.readFile(path.join(temp, "state", "deepseek-key.enc"))).includes(
      Buffer.from(fakeKey),
    ),
  );
  await page
    .getByRole("button", { name: "测试连接并刷新模型", exact: true })
    .click();
  await page
    .getByText("连接成功，已获取 2 个可用模型。", { exact: true })
    .waitFor();
  const persona =
    "我是示例候选人，有三年产品经验。表达简洁、真诚；只根据我提供的信息回答，不编造经历。";
  await page.getByLabel("系统提示词", { exact: false }).fill(persona);
  await page.getByRole("button", { name: "保存模型配置", exact: true }).click();
  await until(
    async () => (await snapshot()).config.llm.system_prompt === persona,
  );
  assert.ok(!JSON.stringify(await snapshot()).includes(fakeKey));
  assert.ok(
    !(
      await fs.readFile(path.join(temp, "state", "desktop.yaml"), "utf8")
    ).includes(fakeKey),
  );
  for (const width of [1080, 1360]) {
    await desktop.evaluate(
      ({ BaseWindow }, width) =>
        BaseWindow.getAllWindows()[0].setContentSize(width, 940),
      width,
    );
    await until(() =>
      page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
    );
  }
  await page.screenshot({
    path: path.join(output, "desktop-models-ui.png"),
  });
  await page.getByRole("button", { name: "去生成回复", exact: true }).click();
  await page.locator(".reply-contact").first().click();
  await page.getByRole("button", { name: "读取对话", exact: true }).click();
  await until(async () => {
    const s = await snapshot();
    return !s.active && s.replyState.status === "ready";
  }, 30000);
  assert.equal(
    (await snapshot()).replyState.context.messages.at(-1).content,
    "请问你做过哪些 AI 项目？",
  );
  await page.getByRole("button", { name: "生成回复草稿", exact: true }).click();
  await until(async () => {
    const s = await snapshot();
    return !s.active && s.replyState.status === "draft";
  }, 30000);
  const modelRequests = await desktop.evaluate(() => globalThis.llmRequests);
  assert.equal(modelRequests.length, 1);
  assert.equal(modelRequests[0].messages[0].content, persona);
  assert.equal(modelRequests[0].messages.at(-1).role, "user");
  assert.equal(
    (await snapshot()).attemptsToday,
    3,
    "Generating never reserves or sends a message",
  );
  const editedReply =
    "我做过一个内部知识库问答项目，主要负责需求分析与效果评估。方便的话，我可以进一步介绍。";
  await page
    .getByRole("textbox", { name: "回复草稿", exact: true })
    .fill(editedReply);
  await page.screenshot({
    path: path.join(output, "desktop-replies-ui.png"),
  });
  await page.getByRole("button", { name: "检查并发送", exact: true }).click();
  assert.equal((await snapshot()).attemptsToday, 3);
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "确认发送这条回复", exact: true })
    .click();
  state = await until(async () => {
    const s = await snapshot();
    return !s.active && s.replies[0]?.status === "sent" ? s : false;
  }, 30000);
  assert.equal(state.replies[0].message, editedReply);
  assert.equal(state.history.length, 3);
  assert.equal(state.attemptsToday, 4);
  await assert.rejects(
    () =>
      page.evaluate(
        (replyId) =>
          window.desk.reply({ action: "send", replyId, message: "重复发送" }),
        state.replies[0].id,
      ),
    /已处理/,
  );
  await desktop.evaluate(async ({ webContents }) => {
    globalThis.inboxTestMode = true;
    const site = webContents
      .getAllWebContents()
      .find((wc) => wc.getURL().includes("/web/geek/chat"));
    await site.loadURL("https://www.zhipin.com/web/geek/chat");
  });
  state = await snapshot();
  state.config.auto_reply.settle_seconds = 0;
  await page.evaluate(
    (config) => window.desk.saveConfig({ config }),
    state.config,
  );
  await page.reload();
  await page
    .locator("nav")
    .getByRole("button", { name: "消息回复", exact: true })
    .click();
  const autoSwitch = page.getByRole("switch", {
    name: "自动回复",
    exact: true,
  });
  assert.equal(await autoSwitch.getAttribute("aria-checked"), "false");
  await page.getByRole("button", { name: "扫描待回复", exact: true }).click();
  state = await until(async () => {
    const s = await snapshot();
    return !s.active ? s : false;
  }, 45000);
  assert.equal(state.run.status, "completed", JSON.stringify(state.autoReply));
  assert.equal(state.run.matched, 2);
  assert.equal(state.attemptsToday, 4);
  assert.equal(
    (await desktop.evaluate(() => globalThis.llmRequests)).length,
    1,
  );
  await autoSwitch.click();
  state = await until(async () => {
    const s = await snapshot();
    return !s.active && s.autoReply.totalSent === 2 ? s : false;
  }, 45000);
  assert.equal(await autoSwitch.getAttribute("aria-checked"), "true");
  assert.equal(state.attemptsToday, 6);
  assert.equal(state.history.length, 3);
  assert.ok(
    state.replies
      .filter((row) => row.source === "auto")
      .every(
        (row) =>
          row.status === "sent" &&
          row.message.length > 1000 &&
          row.message.endsWith("以上是完整回复。"),
      ),
  );
  const autoRequests = await desktop.evaluate(() =>
    globalThis.llmRequests.slice(1),
  );
  assert.equal(autoRequests.length, 2);
  assert.ok(autoRequests.every((request) => !("max_tokens" in request)));
  await page
    .getByRole("region", { name: "回复活动日志" })
    .getByText("完整回复已确认送达", { exact: true })
    .first()
    .waitFor();
  await page
    .getByRole("region", { name: "自动回复设置" })
    .getByText(state.autoReply.note, { exact: true })
    .waitFor();
  for (const width of [1080, 1360]) {
    await desktop.evaluate(
      ({ BaseWindow }, width) =>
        BaseWindow.getAllWindows()[0].setContentSize(width, 940),
      width,
    );
    await until(() =>
      page.evaluate(
        (width) =>
          innerWidth === width &&
          document.documentElement.scrollWidth <= innerWidth,
        width,
      ),
    );
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({
      path: path.join(output, `desktop-auto-replies-${width}-ui.png`),
    });
  }
  await page
    .getByRole("region", { name: "回复活动日志" })
    .scrollIntoViewIfNeeded();
  await page.screenshot({
    path: path.join(output, "desktop-reply-log-ui.png"),
  });
  await page.evaluate(() => window.scrollTo(0, 0));
  await autoSwitch.click();
  await until(async () => !(await snapshot()).autoReply.enabled);
  await until(
    async () => (await autoSwitch.getAttribute("aria-checked")) === "false",
  );
  assert.equal(await autoSwitch.getAttribute("aria-checked"), "false");
  console.log(
    "PASS: automatic reply toggle, read-only scan, complete long messages, local activity log and responsive layout",
  );
  await page
    .locator("nav")
    .getByRole("button", { name: "模型与人格", exact: true })
    .click();
  await page.getByRole("button", { name: "移除密钥", exact: true }).click();
  await until(async () => !(await snapshot()).llm.configured);
  console.log(
    "PASS: encrypted DeepSeek key, connection test, persona, read conversation, editable draft, explicit send, receipt and duplicate prevention",
  );
  assert.deepEqual(errors, []);
  await fs.writeFile(
    path.join(output, "desktop-ui-smoke.json"),
    JSON.stringify(
      {
        passed: true,
        externalNetwork: false,
        batchSends: 3,
        windowCount: 1,
        resizeWidths: [1080, 1360, 1580, 1080],
        pageFitsWidth: true,
        llmDraftAndReply: true,
        automaticReplies: 2,
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
