import asyncio
import json
import os
import subprocess
import sys

import pytest

from boss_cli.config import Config, load_config
from boss_cli.desktop_service import DesktopService


@pytest.fixture
def service(tmp_path):
    item = DesktopService(tmp_path / "state", tmp_path / "config.yaml")
    yield item
    item.store.close()


async def test_desktop_import_cannot_change_profile_or_attach_browser(service, tmp_path):
    config = Config().model_dump(mode="json")
    config["state_dir"] = str(tmp_path / "elsewhere")
    config["browser"]["cdp_url"] = "http://localhost:9222"
    config["browser"]["headless"] = True
    await service.request("saveConfig", {"config": config})
    persisted = load_config(service.config_path)
    assert persisted.state_dir == str(service.directory)
    assert persisted.browser.cdp_url is None
    assert not persisted.browser.headless
    assert not (tmp_path / "elsewhere").exists()


async def test_invalid_config_does_not_replace_saved_config(service):
    before = service.config_path.read_bytes()
    config = service.config.model_dump(mode="json")
    config["run"]["max_sends"] = 0
    with pytest.raises(ValueError):
        await service.request("saveConfig", {"config": config})
    assert service.config_path.read_bytes() == before


async def test_unknown_commands_and_modes_are_rejected(service):
    with pytest.raises(ValueError):
        await service.request("exec", {"code": "anything"})
    with pytest.raises(ValueError):
        await service.request("start", {"mode": "arbitrary"})


async def test_inflight_start_cannot_be_repeated_or_reconfigured(service):
    service.task = asyncio.create_task(asyncio.sleep(60))
    try:
        with pytest.raises(ValueError, match="已有任务"):
            await service.request("start", {"mode": "preview"})
        with pytest.raises(ValueError, match="停止"):
            await service.request("saveConfig", {"config": Config().model_dump(mode="json")})
        with pytest.raises(ValueError, match="停止"):
            await service.request("reply", {"action": "read", "jobId": "abc123"})
    finally:
        service.task.cancel()
        await asyncio.gather(service.task, return_exceptions=True)


async def test_stopping_model_request_releases_worker_and_emits_cancellation(
    service, job, monkeypatch
):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace

    from boss_cli.control import is_active
    from boss_cli.conversation import conversation

    service.store.create_run("contact", "send")
    service.store.save_job(job)
    service.store.mark_contacted(job, "contact")
    service.store.update_run("contact", status="completed")

    class Chat:
        def is_closed(self):
            return False

    service.replies.chat = Chat()
    service.replies.job_id = job.job_id

    class Adapter:
        def __init__(self, *_args):
            self.chat_bindings = {}

        def watch_page(self, _page):
            pass

        async def read_conversation(self, _page, _job):
            return conversation(
                [{"role": "user", "content": "可以介绍项目吗？", "supported": True}], 20
            )

    @asynccontextmanager
    async def factory(*_args):
        yield SimpleNamespace(owned_pages=[])

    service.replies.factory = factory
    monkeypatch.setattr("boss_cli.replies.BossAdapter", Adapter)
    emitted = []
    requested = asyncio.Event()

    def emit(value):
        emitted.append(value)
        if value.get("kind") == "llm":
            requested.set()

    monkeypatch.setattr("boss_cli.desktop_service.emit", emit)
    await service.request("reply", {"action": "read", "jobId": job.job_id})
    await service.task
    await service.request("reply", {"action": "generate", "jobId": job.job_id})
    await asyncio.wait_for(requested.wait(), 2)
    assert is_active(service.directory)
    assert len(service.bridge.pending) == 1
    await service.request("control", {"action": "stop"})
    await service.task
    assert not is_active(service.directory)
    assert not service.bridge.pending
    assert service.snapshot()["replyState"]["status"] == "stopped"
    assert not service.snapshot()["replies"]
    assert service.store.attempts_today() == 0
    assert any(value.get("kind") == "llmCancel" for value in emitted)


async def test_auto_monitor_waits_for_other_work_is_idempotent_and_cancels(service, monkeypatch):
    released, entered, cancelled = asyncio.Event(), asyncio.Event(), asyncio.Event()
    calls = []

    async def cycle(_config, _run_id):
        calls.append(_run_id)
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(service.auto_replies, "cycle", cycle)
    service.task = asyncio.create_task(released.wait())
    other = service.task
    try:
        assert not service.snapshot()["autoReply"]["enabled"]
        await service.request("autoReply", {"action": "enable"})
        monitor = service.monitor_task
        await service.request("autoReply", {"action": "enable"})
        assert service.monitor_task is monitor
        await asyncio.sleep(0)
        assert service.snapshot()["autoReply"]["status"] == "waiting_task"
        assert not calls
        released.set()
        await other
        await asyncio.wait_for(entered.wait(), 2)
        assert len(calls) == 1
        await service.request("autoReply", {"action": "disable"})
        await asyncio.wait_for(monitor, 2)
        assert cancelled.is_set()
        assert not service.snapshot()["autoReply"]["enabled"]
        assert not service.snapshot()["active"]
        assert "enabled" not in load_config(service.config_path).auto_reply.model_dump()
    finally:
        released.set()
        service.stop_monitor()
        await asyncio.gather(other, service.monitor_task, return_exceptions=True)


async def test_disabling_auto_during_generation_cancels_api_and_never_sends(
    service, job, monkeypatch
):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace

    from boss_cli.control import is_active
    from boss_cli.conversation import conversation

    context = conversation([{"role": "user", "content": "可以介绍项目吗？", "supported": True}], 20)

    class Adapter:
        def __init__(self, *_args):
            pass

        async def read_conversation(self, *_args):
            return context

    class Reader:
        def __init__(self, *_args):
            self.page = object()

        async def open(self):
            pass

        async def rows(self):
            return [
                {
                    "name": "示例招聘者",
                    "company": job.company,
                    "token": "entry1",
                    "signature": "first",
                    "hasDraft": False,
                }
            ]

        async def select(self, _entry):
            return job, context

        async def next_page(self):
            return False

    @asynccontextmanager
    async def factory(*_args):
        yield SimpleNamespace()

    monkeypatch.setattr("boss_cli.auto_replies.BossAdapter", Adapter)
    monkeypatch.setattr("boss_cli.auto_replies.InboxReader", Reader)
    service.auto_replies.factory = factory
    service.config.auto_reply.settle_seconds = 0
    requested, emitted = asyncio.Event(), []

    def emit(value):
        emitted.append(value)
        if value.get("kind") == "llm":
            requested.set()

    monkeypatch.setattr("boss_cli.desktop_service.emit", emit)
    await service.request("autoReply", {"action": "enable"})
    try:
        await asyncio.wait_for(requested.wait(), 2)
        assert is_active(service.directory)
        await service.request("autoReply", {"action": "disable"})
        await asyncio.wait_for(service.monitor_task, 2)
        assert not is_active(service.directory)
        assert not service.bridge.pending
        assert not service.store.reply_history()
        assert service.store.attempts_today() == 0
        assert any(value.get("kind") == "llmCancel" for value in emitted)
        assert any(event["kind"] == "auto_disabled" for event in service.store.reply_events())
    finally:
        service.stop_monitor()
        await asyncio.gather(service.monitor_task, return_exceptions=True)


async def test_monitor_failure_stops_and_restart_never_reenables(tmp_path, monkeypatch):
    item = DesktopService(tmp_path / "state", tmp_path / "config.yaml")

    async def cycle(_config, _run_id):
        item.auto_replies.state.update(status="needs_attention", note="模型连接失败")
        return {"status": "needs_attention"}

    monkeypatch.setattr(item.auto_replies, "cycle", cycle)
    await item.request("autoReply", {"action": "enable"})
    await asyncio.wait_for(item.monitor_task, 2)
    assert not item.monitor_enabled
    assert item.snapshot()["autoReply"]["status"] == "needs_attention"
    await item.shutdown()
    restarted = DesktopService(tmp_path / "state", tmp_path / "config.yaml")
    try:
        assert not restarted.monitor_enabled
        assert restarted.monitor_task is None
        assert any(event["kind"] == "auto_disabled" for event in restarted.store.reply_events())
    finally:
        await restarted.shutdown()


async def test_windows_style_event_loop_does_not_fail_during_cleanup(monkeypatch, tmp_path):
    from boss_cli.browser import NeedsAttention
    from boss_cli.runner import run_task

    loop = asyncio.get_running_loop()

    def unavailable(*args):
        raise NotImplementedError

    monkeypatch.setattr(loop, "add_signal_handler", unavailable)
    monkeypatch.setattr(loop, "remove_signal_handler", unavailable)

    class UnavailableBrowser:
        def __init__(self, *_args):
            pass

        async def __aenter__(self):
            raise NeedsAttention("synthetic browser unavailable")

        async def __aexit__(self, *_args):
            pass

    run = await run_task(Config(), tmp_path / "windows", factory=UnavailableBrowser)
    assert run["status"] == "needs_attention"
    assert run["note"] == "synthetic browser unavailable"


def test_service_round_trips_unicode_when_host_uses_legacy_encoding(tmp_path):
    config = Config().model_dump(mode="json")
    config["search"]["keywords"] = ["数据分析"]
    config["message"]["template"] = "您好，期待交流 👋"
    commands = [
        {"id": "save", "method": "saveConfig", "params": {"config": config}},
        {"id": "read", "method": "snapshot"},
    ]
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "boss_cli.desktop_service",
            "--data-dir",
            str(tmp_path / "状态"),
            "--config",
            str(tmp_path / "配置.yaml"),
        ],
        input="".join(json.dumps(command, ensure_ascii=False) + "\n" for command in commands),
        text=True,
        encoding="utf-8",
        capture_output=True,
        env={**os.environ, "PYTHONUTF8": "0", "PYTHONIOENCODING": "cp1252"},
        timeout=10,
        check=True,
    )
    replies = {
        data["id"]: data
        for line in result.stdout.splitlines()
        if (data := json.loads(line)).get("kind") == "reply"
    }
    assert "error" not in replies["save"]
    persisted = replies["read"]["result"]["config"]
    assert persisted["search"]["keywords"] == ["数据分析"]
    assert persisted["message"]["template"] == "您好，期待交流 👋"
