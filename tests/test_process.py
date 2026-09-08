import asyncio
import multiprocessing
import time
from pathlib import Path

from test_browser import CHAT_HTML, DETAIL_HTML, LIST_HTML
from typer.testing import CliRunner

from boss_cli.browser import BrowserSession
from boss_cli.cli import app
from boss_cli.config import Config, dump_config
from boss_cli.control import is_active
from boss_cli.runner import run_task
from boss_cli.storage import Store


class FixtureSession(BrowserSession):
    """A real isolated Chrome with every request fulfilled from local synthetic HTML."""

    async def __aenter__(self):
        session = await super().__aenter__()

        async def handler(route):
            html = (
                CHAT_HTML
                if "/web/geek/chat" in route.request.url
                else DETAIL_HTML.replace(
                    "this.textContent='继续沟通'", "location.href='/web/geek/chat'"
                )
                if "/job_detail/" in route.request.url
                else LIST_HTML
            )
            await route.fulfill(status=200, content_type="text/html; charset=utf-8", body=html)

        await self.context.route("**/*", handler)
        return session


def fixture_worker(directory: str):
    cfg = Config()
    cfg.browser.headless = True
    cfg.browser.timeout_seconds = 3
    cfg.search.keywords = ["AI应用"]
    cfg.run.action_delay = (0, 0)
    cfg.run.job_delay = (30, 30)
    cfg.run.max_sends = 2
    asyncio.run(run_task(cfg, Path(directory), mode="send", factory=FixtureSession))


def wait_until(predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError("timed out waiting for worker state")


def test_separate_process_pause_resume_stop_and_persistent_history(tmp_path):
    directory = tmp_path / "state"
    cfg = Config(state_dir=str(directory))
    config_path = tmp_path / "config.yaml"
    config_path.write_text(dump_config(cfg), encoding="utf-8")
    db = Store(directory)
    process = multiprocessing.get_context("spawn").Process(
        target=fixture_worker, args=(str(directory),)
    )
    process.start()
    cli = CliRunner()
    try:
        wait_until(lambda: db.latest_run() and db.latest_run()["sent"] == 1)
        assert is_active(directory)
        assert cli.invoke(app, ["pause", "-c", str(config_path)]).exit_code == 0
        wait_until(lambda: db.latest_run()["status"] == "paused")
        assert cli.invoke(app, ["resume", "-c", str(config_path)]).exit_code == 0
        wait_until(lambda: db.latest_run()["status"] == "running")
        status = cli.invoke(app, ["status", "-c", str(config_path)])
        assert status.exit_code == 0
        assert cli.invoke(app, ["stop", "-c", str(config_path)]).exit_code == 0
        process.join(timeout=10)
        assert not process.is_alive()
        assert process.exitcode == 0
        assert db.latest_run()["status"] == "stopped"
        assert db.history()[0]["status"] == "sent"
        assert db.attempts_today() == 1
        assert not is_active(directory)
    finally:
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)
        db.close()


async def test_persistent_browser_profile_survives_restart(tmp_path):
    cfg = Config()
    cfg.browser.headless = True
    directory = tmp_path / "profile-state"
    async with FixtureSession(cfg, directory) as session:
        await session.context.add_cookies(
            [
                {
                    "name": "fixture_login",
                    "value": "synthetic-test-only",
                    "domain": "www.zhipin.com",
                    "path": "/",
                    "expires": time.time() + 3600,
                }
            ]
        )
    async with FixtureSession(cfg, directory) as session:
        cookies = await session.context.cookies("https://www.zhipin.com")
        assert any(
            c["name"] == "fixture_login" and c["value"] == "synthetic-test-only" for c in cookies
        )
