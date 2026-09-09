"""Local desktop backend. The only transport is the parent process's stdio."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from filelock import Timeout

from . import __version__
from .auto_replies import AutoReplyWorkflow
from .browser import LayoutChanged, clean_error
from .config import Config, dump_config, load_config
from .control import is_active, task_lock
from .replies import ReplyWorkflow
from .rpc_browser import RpcBrowser, RpcSession
from .runner import run_task
from .storage import Store, private_dir


def emit(data):
    print(json.dumps(data, ensure_ascii=False), flush=True)


class Bridge:
    def __init__(self):
        self.pending = {}

    async def request(self, method, *, transport="browser", **params):
        key = uuid.uuid4().hex
        future = asyncio.get_running_loop().create_future()
        self.pending[key] = future
        emit({"kind": transport, "id": key, "method": method, "params": params})
        try:
            return await asyncio.wait_for(future, 660 if transport == "llm" else 40)
        except TimeoutError as error:
            raise LayoutChanged(
                "模型请求超时，请手动重试"
                if transport == "llm"
                else "浏览器操作超时，任务已保留现场"
            ) from error
        finally:
            self.pending.pop(key, None)
            if transport == "llm":
                emit({"kind": "llmCancel", "id": key})

    def reply(self, data):
        future = self.pending.get(data["id"])
        if future and not future.done():
            if data.get("error"):
                future.set_exception(LayoutChanged(data["error"]))
            else:
                future.set_result(data.get("result"))


class DesktopService:
    def __init__(self, directory: Path, config_path: Path):
        self.directory = private_dir(directory)
        self.config_path = config_path
        self.store = Store(directory)
        if not is_active(directory):
            with task_lock(directory):
                self.store.recover()
        self.config = load_config(config_path) if config_path.exists() else Config()
        self.config.state_dir = str(directory)
        self.bridge = Bridge()
        self.browser = RpcBrowser(self.bridge)
        self.replies = ReplyWorkflow(
            self.store,
            directory,
            self.bridge,
            lambda c, d, page: RpcSession(c, d, self.browser, retain_page=page),
        )
        self.auto_replies = AutoReplyWorkflow(
            self.store,
            directory,
            self.bridge,
            lambda c, d, page: RpcSession(
                c, d, self.browser, retain_page=page, prefer_current=True
            ),
        )
        # Enabling a monitor is a session action, never an imported preference or
        # an unattended restart after a crash. Its audit trail remains in SQLite.
        self.monitor_enabled = False
        self.monitor_task = None
        self.auto_task = None
        self.task = None
        self.run_id = None
        if not config_path.exists():
            self.save(self.config.model_dump(mode="json"))

    def save(self, data):
        config = Config.model_validate(data)
        # Desktop storage/profile location is owned by the application, never by a config import.
        config.state_dir = str(self.directory)
        config.browser.cdp_url = None
        config.browser.headless = False
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.config_path.with_suffix(".tmp")
        temporary.write_text(dump_config(config), encoding="utf-8")
        temporary.chmod(0o600)
        os.replace(temporary, self.config_path)
        self.config = config
        return config.model_dump(mode="json")

    def snapshot(self):
        active = is_active(self.directory)
        return {
            "config": self.config.model_dump(mode="json"),
            "active": active or bool(self.task and not self.task.done()),
            "run": self.store.latest_run(),
            "history": self.store.history(limit=500),
            "jobs": self.store.jobs(limit=500),
            "events": self.store.recent_events(150),
            "attemptsToday": self.store.attempts_today(),
            "replyState": self.replies.state,
            "replies": self.store.reply_history(),
            "replyContacts": self.store.reply_contacts(),
            "replyEvents": self.store.reply_events(),
            "autoReply": {
                **self.auto_replies.state,
                "enabled": self.monitor_enabled,
                "intervalSeconds": self.config.auto_reply.interval_seconds,
            },
            "directory": str(self.directory),
        }

    async def request(self, method, params):
        if method == "snapshot":
            return self.snapshot()
        if method == "saveConfig":
            if is_active(self.directory) or (self.task and not self.task.done()):
                raise ValueError("请先停止当前任务，再修改配置")
            return self.save(params["config"])
        if method == "autoReply":
            action = params.get("action")
            if action == "disable":
                self.stop_monitor()
                return self.snapshot()
            if action == "enable":
                if self.monitor_enabled:
                    return self.snapshot()
                if self.monitor_task and not self.monitor_task.done():
                    raise ValueError("自动回复正在停止，请稍后再开启")
                self.auto_replies.prepare()
                self.monitor_enabled = True
                self.auto_replies.state.update(
                    status="waiting", note="自动回复已开启，准备检查会话", nextScanAt=None
                )
                self.store.event(None, "INFO", "auto_enabled", "已开启自动回复；应用退出后停止")
                self.monitor_task = asyncio.create_task(self.monitor())
                return self.snapshot()
            if action == "scan":
                if (
                    self.monitor_enabled
                    or is_active(self.directory)
                    or (self.task and not self.task.done())
                ):
                    raise ValueError("请先停止当前操作，再扫描会话")
                self.auto_replies.layout_skips.clear()
                self.run_id = uuid.uuid4().hex[:12]
                self.task = asyncio.create_task(
                    self.auto_replies.cycle(
                        self.config.model_copy(deep=True), self.run_id, scan_only=True
                    )
                )
                self.task.add_done_callback(self.finished)
                return {"runId": self.run_id}
            raise ValueError("无效的自动回复操作")
        if method == "reply":
            if is_active(self.directory) or (self.task and not self.task.done()):
                raise ValueError("请先停止当前任务，再处理回复")
            action = params.get("action")
            self.replies.validate(action, params)
            self.run_id = uuid.uuid4().hex[:12]
            self.task = asyncio.create_task(
                self.replies.run(self.config.model_copy(deep=True), action, params, self.run_id)
            )
            self.task.add_done_callback(self.finished)
            return {"runId": self.run_id}
        if method == "resolveReply":
            if self.task and not self.task.done():
                raise ValueError("请先停止任务，再核对回复")
            try:
                with task_lock(self.directory):
                    self.store.resolve_reply(
                        params["replyId"], params["status"], params.get("note", "")
                    )
            except Timeout as error:
                raise ValueError("任务运行时不能修改回复结果") from error
            return self.snapshot()
        if method == "start":
            mode = params.get("mode")
            if mode not in {"login", "preview", "send", "diagnose", "verify"}:
                raise ValueError("无效的运行模式")
            if is_active(self.directory) or (self.task and not self.task.done()):
                raise ValueError("已有任务在运行")
            if mode == "verify":
                self.store.pending_message(params.get("jobId", ""))
            config = self.config.model_copy(deep=True)
            self.run_id = uuid.uuid4().hex[:12]
            self.task = asyncio.create_task(
                run_task(
                    config,
                    self.directory,
                    mode=mode,
                    verify_job_id=params.get("jobId") if mode == "verify" else None,
                    run_id=self.run_id,
                    factory=lambda c, d: RpcSession(c, d, self.browser),
                )
            )
            self.task.add_done_callback(self.finished)
            return {"runId": self.run_id}
        if method == "control":
            action = params.get("action")
            if action not in {"pause", "run", "stop"}:
                raise ValueError("无效的任务操作")
            run = self.store.latest_run()
            if action == "stop" and self.auto_task and not self.auto_task.done():
                self.stop_monitor()
                return self.snapshot()
            if run and is_active(self.directory):
                self.store.update_run(run["run_id"], control=action)
                if action == "stop" and run["mode"].startswith("reply_") and self.task:
                    self.task.cancel()
            return self.snapshot()
        if method == "resolve":
            if not params.get("note", "").strip():
                raise ValueError("请填写人工核对说明")
            try:
                with task_lock(self.directory):
                    self.store.resolve(params["jobId"], params["status"], params["note"])
            except Timeout as error:
                raise ValueError("任务运行时不能修改发送结果") from error
            return self.snapshot()
        if method == "export":
            return self.store.history(limit=100000)
        raise ValueError("不支持的桌面命令")

    def stop_monitor(self):
        was_enabled = self.monitor_enabled
        self.monitor_enabled = False
        running = bool(self.auto_task and not self.auto_task.done())
        if self.monitor_task and not self.monitor_task.done():
            self.monitor_task.cancel()
        if running:
            self.auto_task.cancel()
        self.auto_replies.state.update(
            status="stopping" if running else "off",
            nextScanAt=None,
            note="正在停止后续回复操作…" if running else "自动回复已关闭",
        )
        if was_enabled:
            self.store.event(None, "INFO", "auto_disabled", "已关闭自动回复，停止后续检查和发送")

    async def monitor(self):
        try:
            while self.monitor_enabled:
                if is_active(self.directory) or (self.task and not self.task.done()):
                    self.auto_replies.state.update(
                        status="waiting_task",
                        nextScanAt=None,
                        note="等待当前投递或手动操作结束后继续检查",
                    )
                    await asyncio.sleep(1)
                    continue
                self.run_id = uuid.uuid4().hex[:12]
                self.auto_task = asyncio.create_task(
                    self.auto_replies.cycle(self.config.model_copy(deep=True), self.run_id)
                )
                self.task = self.auto_task
                self.task.add_done_callback(self.finished)
                run = await self.auto_task
                if (
                    not self.monitor_enabled
                    or not run
                    or run["status"] in {"needs_attention", "stopped"}
                ):
                    if self.monitor_enabled:
                        self.store.event(
                            None,
                            "WARNING",
                            "auto_disabled",
                            "自动回复已停止：" + self.auto_replies.state["note"],
                        )
                    break
                interval = self.config.auto_reply.interval_seconds
                next_scan = (datetime.now().astimezone() + timedelta(seconds=interval)).isoformat(
                    timespec="seconds"
                )
                self.auto_replies.state.update(status="waiting", nextScanAt=next_scan)
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            pass
        finally:
            self.monitor_enabled = False
            if self.auto_task and not self.auto_task.done():
                self.auto_task.cancel()
                await asyncio.gather(self.auto_task, return_exceptions=True)
            self.auto_replies.state["nextScanAt"] = None
            if self.auto_replies.state["status"] != "needs_attention":
                self.auto_replies.state.update(status="off", note="自动回复已关闭")

    def finished(self, task):
        try:
            emit({"kind": "event", "event": "finished", "result": task.result()})
        except (Exception, asyncio.CancelledError) as error:
            emit({"kind": "event", "event": "error", "error": clean_error(error)})

    async def shutdown(self):
        self.stop_monitor()
        if self.monitor_task:
            await asyncio.gather(self.monitor_task, return_exceptions=True)
        if self.task and not self.task.done():
            if self.run_id and self.store.run(self.run_id):
                self.store.update_run(self.run_id, control="stop")
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        self.store.close()


async def serve(directory, config_path):
    service = DesktopService(directory, config_path)
    queue = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def reader():
        for line in sys.stdin:
            loop.call_soon_threadsafe(queue.put_nowait, line)
        loop.call_soon_threadsafe(queue.put_nowait, None)

    threading.Thread(target=reader, daemon=True).start()
    emit({"kind": "ready", "version": __version__})
    try:
        while (line := await queue.get()) is not None:
            data = {}
            try:
                data = json.loads(line)
                if data.get("kind") in {"browserReply", "llmReply"}:
                    service.bridge.reply(data)
                elif data.get("kind") == "browserEvent":
                    service.browser.event(data)
                else:
                    result = await service.request(data["method"], data.get("params", {}))
                    emit({"kind": "reply", "id": data["id"], "result": result})
            except Exception as error:
                emit({"kind": "reply", "id": data.get("id"), "error": clean_error(error)})
    finally:
        await service.shutdown()


def main():
    # Frozen Windows executables may ignore Python encoding environment variables.
    # The Electron parent always sends and expects UTF-8 JSON Lines.
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(serve(args.data_dir.resolve(), args.config.resolve()))


if __name__ == "__main__":
    main()
