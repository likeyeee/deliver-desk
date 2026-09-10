const { spawn } = require("node:child_process");
const { createInterface } = require("node:readline");
const { EventEmitter } = require("node:events");
const path = require("node:path");
const fs = require("node:fs");

class Backend extends EventEmitter {
  constructor({ root, resources, packaged, dataDir, config, browser, llm }) {
    super();
    this.nextId = 1;
    this.pending = new Map();
    this.browser = browser;
    this.llm = llm;
    this.closed = false;
    let executable, args;
    if (packaged) {
      const embedded = path.join(resources, "backend", "python.exe");
      if (process.platform === "win32" && fs.existsSync(embedded)) {
        executable = embedded;
        args = ["-m", "boss_cli.desktop_service"];
      } else {
        executable = path.join(
          resources,
          "backend",
          process.platform === "win32" ? "boss-desktop.exe" : "boss-desktop",
        );
        args = [];
      }
    } else {
      executable = path.join(
        root,
        ".venv",
        process.platform === "win32" ? "Scripts/python.exe" : "bin/python",
      );
      args = ["-m", "boss_cli.desktop_service"];
    }
    args.push("--data-dir", dataDir, "--config", config);
    this.child = spawn(executable, args, {
      cwd: packaged ? dataDir : root,
      stdio: ["pipe", "pipe", "pipe"],
      windowsHide: true,
      env: { ...process.env, PYTHONUNBUFFERED: "1", PYTHONIOENCODING: "utf-8" },
    });
    this.ready = new Promise((resolve, reject) => {
      this.readyResolve = resolve;
      this.readyReject = reject;
    });
    this.child.on("error", (error) => this.fail(error));
    this.child.on("exit", (code) => {
      this.closed = true;
      this.fail(Error(`任务服务已退出（${code ?? "中断"}）`));
    });
    this.child.stderr.on("data", (data) =>
      this.emit("diagnostic", data.toString().slice(0, 2000)),
    );
    createInterface({ input: this.child.stdout }).on("line", (line) =>
      this.receive(line),
    );
  }
  send(data) {
    if (!this.closed && this.child.stdin.writable)
      this.child.stdin.write(JSON.stringify(data) + "\n");
  }
  async receive(line) {
    let data;
    try {
      data = JSON.parse(line);
    } catch {
      return this.emit("diagnostic", "任务服务返回了无效数据");
    }
    if (data.kind === "ready") return this.readyResolve(data);
    if (data.kind === "reply") {
      const pending = this.pending.get(data.id);
      if (!pending) return;
      clearTimeout(pending.timer);
      this.pending.delete(data.id);
      data.error
        ? pending.reject(Error(data.error))
        : pending.resolve(data.result);
    } else if (data.kind === "browser") {
      try {
        this.send({
          kind: "browserReply",
          id: data.id,
          result: await this.browser.command(data.method, data.params),
        });
      } catch (error) {
        this.send({
          kind: "browserReply",
          id: data.id,
          error: String(error.message).replace(
            /(https?:\/\/[^\s?]+)\?\S+/g,
            "$1?[隐藏参数]",
          ),
        });
      }
    } else if (data.kind === "llm") {
      try {
        if (
          !["generate", "analyzeResume", "generateGreeting"].includes(
            data.method,
          ) ||
          !this.llm
        )
          throw Error("模型服务不可用");
        this.send({
          kind: "llmReply",
          id: data.id,
          result: await this.llm[data.method](data.id, data.params),
        });
      } catch (error) {
        this.send({ kind: "llmReply", id: data.id, error: error.message });
      }
    } else if (data.kind === "llmCancel") this.llm?.cancel(data.id);
    else if (data.kind === "event") this.emit("event", data);
  }
  async request(method, params = {}) {
    await this.ready;
    if (this.closed) throw Error("任务服务已断开，请重新打开应用");
    const id = String(this.nextId++);
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(Error("任务服务响应超时"));
      }, 45000);
      this.pending.set(id, { resolve, reject, timer });
      this.send({ id, method, params });
    });
  }
  fail(error) {
    this.readyReject(error);
    for (const p of this.pending.values()) {
      clearTimeout(p.timer);
      p.reject(error);
    }
    this.pending.clear();
    this.emit("unavailable", error.message);
  }
  async close() {
    this.llm?.close();
    if (this.closed) return;
    this.child.stdin.end();
    await new Promise((resolve) => {
      this.child.once("exit", resolve);
      setTimeout(resolve, 5000).unref();
    });
    if (!this.closed) this.child.kill();
  }
}
module.exports = { Backend };
