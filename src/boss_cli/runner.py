from __future__ import annotations

import asyncio
import json
import os
import signal
import uuid
from pathlib import Path

from filelock import Timeout
from playwright.async_api import Error as PlaywrightError
from rich.console import Console

from .browser import BossAdapter, BrowserSession, LayoutChanged, NeedsAttention, clean_error
from .config import Config, render_message
from .control import Controller, StopRequested, task_lock
from .matching import reject_reason
from .models import Job, readable_salary
from .storage import Store, private_dir


class Runner:
    def __init__(
        self,
        config: Config,
        store: Store,
        run_id: str,
        *,
        send: bool,
        console: Console | None = None,
    ):
        self.config, self.store, self.run_id, self.send = config, store, run_id, send
        self.control = Controller(store, run_id)
        self.console = console
        self.counts = {"scanned": 0, "matched": 0, "sent": 0, "skipped": 0, "errors": 0}
        self.attempts = 0
        self.reserved_job: str | None = None

    def log(self, kind: str, message: str, job: Job | None = None, level: str = "INFO"):
        self.store.event(self.run_id, level, kind, message, job.job_id if job else None)
        if self.console:
            self.console.print(f"[{kind}] {message}", markup=False, highlight=False)

    def update(self):
        self.store.update_run(self.run_id, attempts=self.attempts, **self.counts)

    def skip(self, job: Job, reason: str):
        self.counts["skipped"] += 1
        self.log("skip", f"{job.company} · {job.title}：{reason}", job)
        self.update()

    async def process(self, adapter: BossAdapter, job: Job):
        await self.control.checkpoint()
        self.counts["scanned"] += 1
        self.store.save_job(job)
        blocked = self.store.blocked(job.job_id)
        if blocked:
            self.skip(job, f"历史状态 {blocked}，防止重复沟通")
            return
        reason = reject_reason(job, self.config.match)
        if reason:
            self.skip(job, reason)
            return
        if job.contacted:
            self.store.mark_contacted(job, self.run_id)
            self.skip(job, "网页显示已沟通")
            return
        await self.control.delay(self.config.run.action_delay)
        job = await adapter.inspect_job(job)
        self.store.save_job(job)
        if job.contacted:
            self.store.mark_contacted(job, self.run_id)
            self.skip(job, "详情显示已沟通")
            return
        reason = reject_reason(job, self.config.match, detail=True)
        if reason:
            self.skip(job, reason)
            return
        self.counts["matched"] += 1
        message = (
            render_message(self.config.message, job)
            if self.config.message.mode == "custom"
            else "[仅发起平台沟通；不发送配置模板]"
        )
        self.log(
            "candidate",
            f"{job.company} · {job.title} · {readable_salary(job.salary)}\n  {job.url}\n  {message}",
            job,
        )
        self.update()
        if not self.send:
            self.log("preview", "预览完成，未点击沟通或发送", job)
            return
        if not await adapter.preflight(job):
            self.store.mark_contacted(job, self.run_id)
            self.skip(job, "发送前检测到已沟通")
            return
        await self.control.checkpoint()
        denied = self.store.reserve(job, self.run_id, message, self.config.run.daily_limit)
        if denied:
            self.skip(job, denied)
            return
        self.reserved_job = job.job_id
        self.attempts += 1
        self.update()
        self.log("sending", "开始沟通，发送状态已落盘", job)
        try:
            result = await adapter.greet(job, message)
            self.store.delivery(job.job_id, result.status, result.note)
            self.reserved_job = None
        except BaseException:
            self.store.delivery(
                job.job_id, "unknown", "发送期间中断或异常，请到网站核对；不会自动重发"
            )
            self.reserved_job = None
            raise
        self.log(result.status, result.note, job, "INFO" if result.status == "sent" else "WARNING")
        if result.status == "sent":
            self.counts["sent"] += 1
        elif result.status == "contacted":
            self.counts["skipped"] += 1
        else:
            self.counts["errors"] += 1
            self.update()
            # Unknown results stop the batch, rather than probing with more messages.
            raise NeedsAttention("本次发送未完全确认，已停止任务；请核对历史中的待处理记录")
        self.update()
        if self.limit_reached():
            return
        if self.attempts % self.config.run.cooldown_every == 0:
            self.log("cooldown", "进入配置的批次休息间隔")
            await self.control.delay(self.config.run.cooldown_seconds)
        else:
            low, high = self.config.run.job_delay
            self.log("interval", f"等待 {low:g}–{high:g} 秒后继续下一个职位")
            await self.control.delay(self.config.run.job_delay)

    async def execute(self, adapter: BossAdapter):
        seen: set[str] = set()
        for keyword in self.config.search.keywords:
            await self.control.checkpoint()
            if self.limit_reached():
                return
            self.log("search", f"搜索：{keyword}；城市：{self.config.search.city}")
            await adapter.search(keyword)
            for scroll in range(self.config.search.max_scrolls + 1):
                await self.control.checkpoint()
                jobs = await adapter.collect()
                for job in jobs:
                    if self.limit_reached():
                        return
                    if job.job_id in seen:
                        continue
                    seen.add(job.job_id)
                    try:
                        await self.process(adapter, job)
                    except (LayoutChanged, PlaywrightError, ValueError) as error:
                        self.counts["errors"] += 1
                        self.log("error", clean_error(error), job, "ERROR")
                        self.update()
                        if self.attempts or self.counts["errors"] >= self.config.run.max_errors:
                            raise NeedsAttention(
                                "页面操作异常，任务已停止；请检查日志和浏览器"
                            ) from error
                if scroll >= self.config.search.max_scrolls or not await adapter.more(seen):
                    break

    def limit_reached(self) -> bool:
        return self.limit_reason() is not None

    def limit_reason(self) -> str | None:
        if self.send and self.attempts >= self.config.run.max_sends:
            return f"已达到本次目标：尝试 {self.attempts} 次，确认送达 {self.counts['sent']} 次"
        if self.send and self.store.attempts_today() >= self.config.run.daily_limit:
            return f"已达到每日尝试上限 {self.config.run.daily_limit} 次"
        if self.counts["scanned"] >= self.config.search.max_jobs:
            return f"已达到浏览上限 {self.config.search.max_jobs} 个职位；可提高浏览上限继续筛选"
        return None

    def completion_note(self) -> str:
        return self.limit_reason() or (
            f"本轮搜索范围已遍历：尝试 {self.attempts} 次，确认送达 {self.counts['sent']} 次；"
            "可调整关键词、筛选或翻页范围后继续"
        )


async def run_task(
    config: Config,
    directory: Path,
    *,
    mode="preview",
    verify_job_id: str | None = None,
    run_id: str | None = None,
    console: Console | None = None,
    factory=BrowserSession,
) -> dict:
    run_id = run_id or uuid.uuid4().hex[:12]
    private_dir(directory)
    store = Store(directory)
    try:
        with task_lock(directory):
            store.recover()
            store.create_run(run_id, mode)
            runner = Runner(config, store, run_id, send=mode == "send", console=console)
            loop = asyncio.get_running_loop()
            previous_signals = {}
            for sig in (signal.SIGINT, signal.SIGTERM):
                try:
                    previous = signal.getsignal(sig)
                    loop.add_signal_handler(sig, setattr, runner.control, "stopped", True)
                    previous_signals[sig] = previous
                except (NotImplementedError, RuntimeError, ValueError):
                    pass
            store.update_run(
                run_id,
                status="running",
                pid=os.getpid(),
                target=config.run.max_sends if mode == "send" else 0,
            )
            runner.log("start", f"任务 {run_id} 已启动，模式：{mode}")
            session = None
            try:
                async with factory(config, directory) as session:
                    adapter = BossAdapter(session, config, runner.control)
                    if mode == "login":
                        runner.log(
                            "login", "请在打开的浏览器中完成登录，检测成功后会保存登录状态并退出"
                        )
                        await adapter.login(config.browser.login_timeout_seconds)
                        runner.log("login", "已检测到登录状态；后续任务将复用此浏览器目录/连接")
                    elif mode == "diagnose":
                        await session.page.goto(
                            "https://www.zhipin.com/web/geek/jobs", wait_until="domcontentloaded"
                        )
                        try:
                            await adapter.wait_ready()
                        except (NeedsAttention, LayoutChanged) as error:
                            runner.log("diagnose", clean_error(error), level="WARNING")
                        data = await adapter.diagnostics()
                        report = directory / "diagnostics.json"
                        report.write_text(
                            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
                        )
                        report.chmod(0o600)
                        runner.log("diagnose", f"选择器报告：{report}")
                    elif mode == "verify":
                        job, message = store.pending_message(verify_job_id or "")
                        runner.log("verify", "正在重新核对历史消息的职位、原文与送达回执", job)
                        await adapter.login(config.browser.login_timeout_seconds)
                        await adapter.inspect_job(job)
                        chat = await adapter.open_full_chat(job)
                        if not await adapter.has_delivery_receipt(chat, job, message):
                            raise NeedsAttention("没有找到这条历史消息的明确送达回执，原状态保留")
                        store.delivery(
                            job.job_id,
                            "sent",
                            "只读核验：同一职位会话中，历史消息原文与己方送达/已读回执一致；未新发消息",
                        )
                        runner.log("receipt_verified", "已核实历史消息送达；本次没有新发消息", job)
                    else:
                        runner.log(
                            "login",
                            "正在校验专用浏览器登录；若出现二维码，请在该窗口扫码，登录后自动继续",
                        )
                        runner.log("login", f"待登录时的页面截图：{directory / 'login.png'}")
                        await adapter.login(config.browser.login_timeout_seconds)
                        await runner.execute(adapter)
                note = runner.completion_note() if mode in {"send", "preview"} else "任务完成"
                store.update_run(run_id, status="completed", note=note, **runner.counts)
                runner.log("complete", note)
            except (StopRequested, asyncio.CancelledError):
                store.update_run(
                    run_id, status="stopped", note="已停止，已登记的发送状态保留", **runner.counts
                )
                runner.log("stopped", "任务已停止")
            except (NeedsAttention, LayoutChanged) as error:
                store.update_run(
                    run_id, status="needs_attention", note=clean_error(error), **runner.counts
                )
                runner.log("needs_attention", clean_error(error), level="WARNING")
            except Exception as error:
                store.update_run(run_id, status="failed", note=clean_error(error), **runner.counts)
                runner.log("failed", clean_error(error), level="ERROR")
            finally:
                if session and getattr(session, "failure_report", None):
                    runner.log("diagnostics", f"失败现场：{session.failure_report}")
                for sig, previous in previous_signals.items():
                    loop.remove_signal_handler(sig)
                    signal.signal(sig, previous)
            return store.run(run_id)
    except Timeout as error:
        raise ValueError("同一数据目录已有浏览器任务，请先 boss status 或 boss stop") from error
    finally:
        store.close()
