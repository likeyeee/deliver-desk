"""Local desktop backend. The only transport is the parent process's stdio."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import threading
import uuid
from pathlib import Path

from filelock import Timeout

from .browser import LayoutChanged, clean_error
from .config import Config, dump_config, load_config
from .control import is_active, task_lock
from .rpc_browser import RpcBrowser, RpcSession
from .runner import run_task
from .storage import Store, private_dir


def emit(data):
    print(json.dumps(data, ensure_ascii=False), flush=True)


class Bridge:
    def __init__(self):
        self.pending = {}

    async def request(self, method, **params):
        key = uuid.uuid4().hex
        future = asyncio.get_running_loop().create_future()
        self.pending[key] = future
        emit({"kind": "browser", "id": key, "method": method, "params": params})
        try:
            return await asyncio.wait_for(future, 40)
        except TimeoutError as error:
            raise LayoutChanged("浏览器操作超时，任务已保留现场") from error
        finally:
            self.pending.pop(key, None)

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
            "directory": str(self.directory),
        }

    async def request(self, method, params):
        if method == "snapshot":
            return self.snapshot()
        if method == "saveConfig":
            if is_active(self.directory) or (self.task and not self.task.done()):
                raise ValueError("请先停止当前任务，再修改配置")
            return self.save(params["config"])
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
            if run and is_active(self.directory):
                self.store.update_run(run["run_id"], control=action)
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

    def finished(self, task):
        try:
            emit({"kind": "event", "event": "finished", "result": task.result()})
        except (Exception, asyncio.CancelledError) as error:
            emit({"kind": "event", "event": "error", "error": clean_error(error)})

    async def shutdown(self):
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
    emit({"kind": "ready", "version": "0.2.0"})
    try:
        while (line := await queue.get()) is not None:
            data = {}
            try:
                data = json.loads(line)
                if data.get("kind") == "browserReply":
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
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(serve(args.data_dir.resolve(), args.config.resolve()))


if __name__ == "__main__":
    main()
