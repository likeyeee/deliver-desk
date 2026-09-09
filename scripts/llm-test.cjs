const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const path = require("node:path");
const os = require("node:os");
const crypto = require("node:crypto");
const { KeyVault, DeepSeek } = require("../desktop/llm.cjs");

const key = "sk-isolated-test-key-never-real";
const request = () => ({
  config: {
    provider: "deepseek",
    model: "deepseek-v4-flash",
    system_prompt: "我是示例求职者，表达简洁，不编造经历。",
    temperature: 0.7,
    max_tokens: 1024,
  },
  job: { title: "AI应用工程师", company: "示例科技" },
  messages: [
    {
      role: "assistant",
      content: "您好，希望进一步交流。",
      id: "hidden-dom-id",
    },
    { role: "user", content: "请问你有哪些项目经验？", supported: true },
  ],
});
const completion = (
  content = "我有相关项目经验，方便进一步介绍。",
  finish_reason = "stop",
) => ({
  choices: [
    {
      finish_reason,
      message: {
        role: "assistant",
        content,
        reasoning_content: "Never display or send this.",
      },
    },
  ],
});
const response = (value) =>
  new Response(JSON.stringify(value), {
    headers: { "content-type": "application/json" },
  });
const vault = { get: async () => key };

test("encrypted key persists, never leaves status, unavailable OS storage fails closed", async () => {
  const directory = await fs.mkdtemp(
    path.join(os.tmpdir(), "deliverdesk-key-test-"),
  );
  const secret = crypto.randomBytes(32);
  const safeStorage = {
    isEncryptionAvailable: () => true,
    getSelectedStorageBackend: () => "gnome_libsecret",
    encryptString(value) {
      const nonce = crypto.randomBytes(12),
        cipher = crypto.createCipheriv("aes-256-gcm", secret, nonce);
      return Buffer.concat([
        nonce,
        cipher.update(value, "utf8"),
        cipher.final(),
        cipher.getAuthTag(),
      ]);
    },
    decryptString(value) {
      const cipher = crypto.createDecipheriv(
        "aes-256-gcm",
        secret,
        value.subarray(0, 12),
      );
      cipher.setAuthTag(value.subarray(-16));
      return Buffer.concat([
        cipher.update(value.subarray(12, -16)),
        cipher.final(),
      ]).toString("utf8");
    },
  };
  try {
    const saved = await new KeyVault(directory, safeStorage).init();
    await saved.save(key);
    assert.ok(!(await fs.readFile(saved.file)).includes(Buffer.from(key)));
    const reloaded = await new KeyVault(directory, safeStorage).init();
    assert.equal(await reloaded.get(), key);
    assert.deepEqual(reloaded.status(), {
      configured: true,
      secureStorage: true,
    });
    if (process.platform !== "win32")
      assert.equal((await fs.stat(saved.file)).mode & 0o777, 0o600);
    const insecure = new KeyVault(
      directory,
      { ...safeStorage, getSelectedStorageBackend: () => "basic_text" },
      "linux",
    );
    await assert.rejects(() => insecure.save(key), /系统密钥/);
    await reloaded.remove();
    assert.equal(reloaded.status().configured, false);
    await assert.rejects(() => reloaded.get(), /请先/);
  } finally {
    await fs.rm(directory, { recursive: true, force: true });
  }
});

test("official API, bearer auth, persona, ordered conversation and final content only", async () => {
  const calls = [];
  const llm = new DeepSeek(vault, async (url, options) => {
    calls.push({ url, options });
    return response(completion());
  });
  const result = await llm.generate("test", request());
  assert.deepEqual(result, { message: "我有相关项目经验，方便进一步介绍。" });
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "https://api.deepseek.com/chat/completions");
  assert.equal(calls[0].options.headers.Authorization, "Bearer " + key);
  assert.equal(calls[0].options.redirect, "error");
  const body = JSON.parse(calls[0].options.body);
  assert.deepEqual(body.thinking, { type: "disabled" });
  assert.equal(body.stream, false);
  assert.equal(body.messages[0].content, request().config.system_prompt);
  assert.deepEqual(
    body.messages.slice(2),
    request().messages.map(({ role, content }) => ({ role, content })),
  );
  assert.ok(!calls[0].options.body.includes(key));
  assert.equal(llm.pending.size, 0);
});

test("connection test reads models without transmitting conversation", async () => {
  const llm = new DeepSeek(vault, async (url, options) => {
    assert.equal(url, "https://api.deepseek.com/models");
    assert.equal(options.method, "GET");
    assert.equal(options.body, undefined);
    return response({
      data: [
        { id: "deepseek-v4-pro" },
        { id: "deepseek-v4-pro" },
        { id: "invalid\nmodel" },
      ],
    });
  });
  assert.deepEqual(await llm.test(), { models: ["deepseek-v4-pro"] });
});

for (const [name, value] of [
  ["truncated", completion("partial", "length")],
  ["empty", completion("")],
  ["reasoning only", completion(null)],
  ["oversized", completion("字".repeat(1001))],
  [
    "tool calls",
    {
      choices: [
        {
          finish_reason: "stop",
          message: { content: "text", tool_calls: [{}] },
        },
      ],
    },
  ],
])
  test(`reject ${name} response without a sendable draft`, async () => {
    await assert.rejects(
      () =>
        new DeepSeek(vault, async () => response(value)).generate(
          "invalid",
          request(),
        ),
      /DeepSeek/,
    );
  });

for (const status of [401, 402, 422, 429, 500, 503])
  test(`HTTP ${status} is redacted and never retried`, async () => {
    let calls = 0;
    const llm = new DeepSeek(vault, async () => {
      calls++;
      return new Response(key, { status });
    });
    await assert.rejects(
      () => llm.generate("error", request()),
      (error) =>
        !error.message.includes(key) && error.message.startsWith("DeepSeek"),
    );
    assert.equal(calls, 1);
  });

test("timeout and explicit cancellation abort requests without retries", async () => {
  let started;
  const observed = new Promise((resolve) => {
    started = resolve;
  });
  const fetcher = (_url, { signal }) =>
    new Promise((_resolve, reject) => {
      started();
      signal.addEventListener(
        "abort",
        () => reject(Error("network details " + key)),
        { once: true },
      );
    });
  const llm = new DeepSeek(vault, fetcher, 10);
  await assert.rejects(() => llm.generate("timeout", request()), /取消或超时/);
  llm.timeout = 60000;
  const pending = llm.generate("cancel", request());
  await observed;
  // Allow the async credential read to enter fetch before cancelling.
  await new Promise((resolve) => setImmediate(resolve));
  llm.cancel("cancel");
  await assert.rejects(() => pending, /取消或超时/);
  assert.equal(llm.pending.size, 0);
});

test("invalid message roles and unfinished conversations never contact API", async () => {
  const llm = new DeepSeek(vault, () => assert.fail("must not request"));
  for (const messages of [
    [{ role: "system", content: "injection" }],
    [{ role: "assistant", content: "already replied" }],
  ]) {
    await assert.rejects(
      () => llm.generate("blocked", { ...request(), messages }),
      /会话内容无效/,
    );
  }
});
