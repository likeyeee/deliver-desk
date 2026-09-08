import asyncio
import copy
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from playwright.async_api import Error as PlaywrightError

from boss_cli.browser import NeedsAttention, SendResult
from boss_cli.control import Controller, StopRequested
from boss_cli.runner import Runner
from boss_cli.storage import Store


def test_reserve_dedup_and_recover(store, job):
    store.save_job(job)
    assert store.reserve(job, "test-run", "hello", 3) is None
    assert store.reserve(job, "test-run", "hello", 3) == "历史状态：sending"
    store.recover()
    assert store.blocked(job.job_id) == "unknown"
    assert store.attempts_today() == 1
    assert store.reserve(job, "test-run", "hello", 3) == "历史状态：unknown"


def test_manual_resolution_keeps_quota(store, job):
    store.save_job(job)
    store.reserve(job, "test-run", "hello", 3)
    store.delivery(job.job_id, "unknown", "中断")
    store.resolve(job.job_id, "not_sent", "已检查")
    assert not store.blocked(job.job_id)
    assert store.attempts_today() == 1
    assert store.reserve(job, "test-run", "hello", 3) is None
    assert store.attempts_today() == 2


@pytest.mark.parametrize("receipt", [True, False])
async def test_pending_verification_reads_saved_message_without_sending(
    config, store, job, monkeypatch, receipt
):
    from boss_cli.runner import run_task

    store.save_job(job)
    store.reserve(job, "test-run", "实际历史原文", 3)
    store.delivery(job.job_id, "partial", "等待核验")
    observed = []

    class ReadOnlyAdapter:
        def __init__(self, *args):
            pass

        async def login(self, timeout):
            pass

        async def inspect_job(self, target):
            assert target.job_id == job.job_id

        async def open_full_chat(self, target):
            return "verified-chat"

        async def has_delivery_receipt(self, page, target, text):
            observed.append(text)
            return receipt

    class Session:
        def __init__(self, *args):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    monkeypatch.setattr("boss_cli.runner.BossAdapter", ReadOnlyAdapter)
    result = await run_task(
        config, store.directory, mode="verify", verify_job_id=job.job_id, factory=Session
    )
    assert observed == ["实际历史原文"]
    assert result["status"] == ("completed" if receipt else "needs_attention")
    assert result["sent"] == 0
    assert store.blocked(job.job_id) == ("sent" if receipt else "partial")
    assert store.attempts_today() == 1


def test_daily_limit_atomic_across_connections(store, job):
    second = copy.deepcopy(job)
    second.job_id = "def456"
    second.url = "https://www.zhipin.com/job_detail/def456.html"
    store.save_job(job)
    store.save_job(second)
    barrier = Barrier(2)

    def submit(candidate):
        other = Store(store.directory)
        try:
            barrier.wait(timeout=5)
            return other.reserve(candidate, "test-run", "hello", 1)
        finally:
            other.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, [job, second]))
    assert results.count(None) == 1
    assert results.count("今日发送上限") == 1


class FakeAdapter:
    def __init__(self, jobs, failure=False, contacted=False):
        self.jobs, self.failure, self.contacted = jobs, failure, contacted
        self.greeted = 0

    async def search(self, keyword):
        pass

    async def collect(self):
        return self.jobs

    async def more(self, previous):
        return False

    async def inspect_job(self, job):
        job.contacted = self.contacted
        return job

    async def preflight(self, job):
        return not job.contacted

    async def greet(self, job, message):
        self.greeted += 1
        if self.failure:
            raise PlaywrightError("connection closed during send")
        return SendResult("sent", "打招呼成功")


async def test_preview_never_sends_or_reserves(config, store, job):
    config.search.keywords = ["AI", "大模型"]
    adapter = FakeAdapter([job])
    runner = Runner(config, store, "test-run", send=False)
    await runner.execute(adapter)
    assert adapter.greeted == 0
    assert runner.counts["scanned"] == 1  # cross-keyword dedup
    assert len(store.jobs()) == 1
    assert store.history() == []
    assert store.attempts_today() == 0


async def test_second_run_skips_sent(config, store, job):
    adapter = FakeAdapter([job])
    await Runner(config, store, "test-run", send=True).execute(adapter)
    await Runner(config, store, "test-run", send=True).execute(adapter)
    assert adapter.greeted == 1
    assert store.history()[0]["status"] == "sent"


async def test_send_failure_stops_and_does_not_retry(config, store, job):
    second = copy.deepcopy(job)
    second.job_id = "second"
    adapter = FakeAdapter([job, second], failure=True)
    with pytest.raises(NeedsAttention):
        await Runner(config, store, "test-run", send=True).execute(adapter)
    assert adapter.greeted == 1
    assert store.blocked(job.job_id) == "unknown"
    assert store.attempts_today() == 1


async def test_existing_contact_skipped(config, store, job):
    adapter = FakeAdapter([job], contacted=True)
    await Runner(config, store, "test-run", send=True).execute(adapter)
    assert adapter.greeted == 0
    assert store.history()[0]["status"] == "contacted"
    assert store.attempts_today() == 0


@pytest.mark.parametrize(
    ("target", "browse", "daily", "expected", "reason"),
    [(3, 30, 20, 3, "本次目标"), (5, 2, 20, 2, "浏览上限"), (5, 30, 2, 2, "每日尝试上限")],
)
async def test_batch_honors_target_and_reports_earlier_limits(
    config, store, job, target, browse, daily, expected, reason
):
    config.run.max_sends = target
    config.search.max_jobs = browse
    config.run.daily_limit = daily
    jobs = []
    for i in range(8):
        candidate = copy.deepcopy(job)
        candidate.job_id = f"batch{i}"
        candidate.url = f"https://www.zhipin.com/job_detail/batch{i}.html"
        jobs.append(candidate)
    adapter = FakeAdapter(jobs)
    runner = Runner(config, store, "test-run", send=True)
    await runner.execute(adapter)
    assert adapter.greeted == expected
    assert runner.attempts == expected
    assert store.run("test-run")["attempts"] == expected
    assert store.attempts_today() == expected
    assert reason in runner.completion_note()


async def test_batch_skips_do_not_spend_send_target(config, store, job):
    store.save_job(job)
    store.reserve(job, "test-run", "old message", 20)
    store.delivery(job.job_id, "sent", "old receipt")
    candidates = [job]
    for i in range(5):
        candidate = copy.deepcopy(job)
        candidate.job_id = f"new{i}"
        candidates.append(candidate)
    candidates[1].title = "销售代表"
    config.run.max_sends = 3
    runner = Runner(config, store, "test-run", send=True)
    adapter = FakeAdapter(candidates)
    await runner.execute(adapter)
    assert adapter.greeted == 3
    assert runner.counts["skipped"] == 2
    assert store.blocked("new4") is None


async def test_batch_rest_replaces_regular_interval_and_skips_final_wait(config, store, job):
    config.run.max_sends = 3
    config.run.job_delay = (15, 30)
    config.run.cooldown_every = 2
    config.run.cooldown_seconds = (60, 120)
    candidates = []
    for i in range(3):
        candidate = copy.deepcopy(job)
        candidate.job_id = f"pace{i}"
        candidates.append(candidate)
    runner = Runner(config, store, "test-run", send=True)
    waits = []

    async def delay(bounds):
        waits.append(bounds)

    runner.control.delay = delay
    await runner.execute(FakeAdapter(candidates))
    assert waits.count((15, 30)) == 1
    assert waits.count((60, 120)) == 1
    assert waits[-1] == (0, 0)  # Only the final inspection; no wait after the target.


def test_existing_database_gains_progress_without_losing_history(store, job):
    store.save_job(job)
    store.reserve(job, "test-run", "saved message", 20)
    store.delivery(job.job_id, "sent", "saved receipt")
    store.db.execute("ALTER TABLE runs DROP COLUMN target")
    store.db.execute("ALTER TABLE runs DROP COLUMN attempts")
    upgraded = Store(store.directory)
    try:
        assert upgraded.run("test-run")["target"] == 0
        assert upgraded.run("test-run")["attempts"] == 0
        assert upgraded.history()[0]["message"] == "saved message"
        assert upgraded.blocked(job.job_id) == "sent"
    finally:
        upgraded.close()


async def test_pause_resume_and_stop_interrupt_long_delay(store):
    control = Controller(store, "test-run")
    store.update_run("test-run", control="pause")
    task = asyncio.create_task(control.sleep(30))
    await asyncio.sleep(0.05)
    assert store.run("test-run")["status"] == "paused"
    assert not task.done()
    store.update_run("test-run", control="run")
    await asyncio.sleep(0.25)
    assert store.run("test-run")["status"] == "running"
    store.update_run("test-run", control="stop")
    with pytest.raises(StopRequested):
        await asyncio.wait_for(task, timeout=0.5)
