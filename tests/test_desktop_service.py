import asyncio

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
    finally:
        service.task.cancel()
        await asyncio.gather(service.task, return_exceptions=True)


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
