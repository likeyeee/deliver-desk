"""One serialized inbox scan. The desktop service owns the opt-in timer."""

import asyncio

from .browser import BossAdapter, ConversationChanged, LayoutChanged, NeedsAttention, clean_error
from .control import Controller, StopRequested, task_lock
from .inbox import InboxReader
from .replies import generate_reply, send_reply
from .storage import now


class AutoReplyWorkflow:
    def __init__(self, store, directory, bridge, factory):
        self.store, self.directory, self.bridge, self.factory = store, directory, bridge, factory
        self.page = None
        self.layout_skips = {}
        self.state = {
            "status": "off",
            "note": "未开启",
            "lastScanAt": None,
            "nextScanAt": None,
            "scanned": 0,
            "pending": 0,
            "sent": 0,
            "errors": 0,
            "totalSent": 0,
            "job": None,
        }

    def prepare(self):
        self.layout_skips.clear()
        self.store.reset_auto_failures()
        self.state.update(totalSent=0, errors=0, job=None)

    def record(self, run_id, job, context, status, note, *, level="INFO"):
        key = context.get("replyKey", context["fingerprint"])
        old = self.store.auto_reply_check(job.job_id, key)
        self.store.record_auto_check(job.job_id, context, status, note)
        if not old or (old["status"], old["note"]) != (status, note):
            self.store.event(run_id, level, "auto_" + status, note, job.job_id)

    async def cycle(self, config, run_id, *, scan_only=False):
        current = None
        counts = {"scanned": 0, "matched": 0, "sent": 0, "skipped": 0, "errors": 0, "attempts": 0}
        try:
            with task_lock(self.directory):
                self.store.recover()
                self.store.create_run(run_id, "reply_scan" if scan_only else "reply_auto")
                control = Controller(self.store, run_id)
                await control.checkpoint()
                self.state.update(
                    status="scanning",
                    note="正在扫描网站会话…",
                    nextScanAt=None,
                    scanned=0,
                    pending=0,
                    sent=0,
                    errors=0,
                    job=None,
                )
                self.store.event(
                    run_id,
                    "INFO",
                    "auto_scan",
                    "开始只读扫描待回复会话" if scan_only else "开始检查待回复会话",
                )
                async with self.factory(config, self.directory, self.page) as session:
                    adapter = BossAdapter(session, config, control)
                    reader = InboxReader(adapter)
                    await reader.open()
                    self.page = reader.page
                    visited = set()
                    stagnant = 0
                    while True:
                        await control.checkpoint()
                        entries = await reader.rows()
                        unseen = [
                            entry
                            for entry in entries
                            if (entry["token"], entry["signature"]) not in visited
                        ]
                        stagnant = 0 if unseen else stagnant + 1
                        for entry in unseen:
                            await control.checkpoint()
                            entry_key = (entry["token"], entry["signature"])
                            visited.add(entry_key)
                            counts["scanned"] += 1
                            self.state.update(
                                scanned=counts["scanned"],
                                note=f"正在检查 {entry['company']} · {entry['name']}",
                            )
                            self.store.update_run(run_id, **counts, note=self.state["note"])
                            if entry_key in self.layout_skips:
                                counts["skipped"] += 1
                                continue
                            if entry["hasDraft"]:
                                note = f"{entry['company']} · {entry['name']}：网页已有草稿，保留给本人处理"
                                self.layout_skips[entry_key] = note
                                self.store.event(run_id, "INFO", "auto_skipped", note)
                                counts["skipped"] += 1
                                continue
                            try:
                                job, context = await reader.select(entry)
                            except NeedsAttention:
                                raise
                            except LayoutChanged as error:
                                note = f"{entry['company']} · {entry['name']}：{clean_error(error)}"
                                if "列表已更新" not in str(error):
                                    self.layout_skips[entry_key] = note
                                self.store.event(run_id, "WARNING", "auto_skipped", note)
                                counts["errors"] += 1
                                counts["skipped"] += 1
                                continue
                            self.store.mark_reply_contact(job)
                            current = (job, context)
                            self.state["job"] = job.as_dict()
                            if not context["canReply"]:
                                self.record(run_id, job, context, "skipped", context["reason"])
                                counts["skipped"] += 1
                                current = None
                                continue
                            try:
                                self.store.check_reply_pending(job.job_id)
                            except ValueError as error:
                                self.record(
                                    run_id, job, context, "skipped", str(error), level="WARNING"
                                )
                                counts["skipped"] += 1
                                current = None
                                continue
                            old = self.store.auto_reply_check(
                                job.job_id, context.get("replyKey", context["fingerprint"])
                            )
                            if old and old["status"] in {"generating", "sent", "failed"}:
                                counts["skipped"] += 1
                                current = None
                                continue
                            counts["matched"] += 1
                            self.state["pending"] = counts["matched"]
                            self.record(
                                run_id,
                                job,
                                context,
                                "pending",
                                "发现招聘方发来、本人尚未回复的消息",
                            )
                            if scan_only:
                                current = None
                                continue
                            if self.store.attempts_today() >= config.run.daily_limit:
                                raise NeedsAttention(
                                    "今日发送上限（含投递和回复）已达到，自动回复已停止"
                                )
                            await control.sleep(config.auto_reply.settle_seconds)
                            stable = await adapter.read_conversation(self.page, job)
                            if stable["fingerprint"] != context["fingerprint"]:
                                # Do not mark this inbound turn processed: several messages
                                # may arrive together and must be read as one complete turn.
                                self.store.event(
                                    run_id,
                                    "INFO",
                                    "auto_waiting_messages",
                                    "收到连续新消息，留待下轮稳定后生成",
                                    job.job_id,
                                )
                                current = None
                                continue
                            self.record(run_id, job, context, "generating", "正在生成完整回复")
                            self.state.update(
                                status="generating", note=f"正在为 {job.company} 生成回复"
                            )
                            try:
                                record = await generate_reply(
                                    self.store,
                                    self.bridge,
                                    adapter,
                                    self.page,
                                    job,
                                    context,
                                    config,
                                    control,
                                    run_id,
                                    source="auto",
                                )
                            except ConversationChanged as error:
                                self.record(run_id, job, context, "superseded", str(error))
                                current = None
                                continue
                            except (asyncio.CancelledError, StopRequested):
                                raise
                            except Exception as error:
                                self.record(
                                    run_id,
                                    job,
                                    context,
                                    "failed",
                                    clean_error(error),
                                    level="WARNING",
                                )
                                current = None
                                raise NeedsAttention(clean_error(error)) from error
                            await control.checkpoint()
                            self.state.update(
                                status="sending", note=f"正在核对并回复 {job.company}"
                            )
                            counts["attempts"] += 1
                            try:
                                await send_reply(
                                    self.store,
                                    adapter,
                                    self.page,
                                    job,
                                    record,
                                    record["message"],
                                    config,
                                    control,
                                    run_id,
                                )
                            except ConversationChanged as error:
                                self.record(run_id, job, context, "superseded", str(error))
                                current = None
                                continue
                            except NeedsAttention:
                                raise
                            except LayoutChanged as error:
                                self.record(
                                    run_id,
                                    job,
                                    context,
                                    "failed",
                                    clean_error(error),
                                    level="WARNING",
                                )
                                counts["errors"] += 1
                                current = None
                                continue
                            self.record(run_id, job, context, "sent", "完整回复已确认送达")
                            counts["sent"] += 1
                            self.state.update(
                                sent=counts["sent"], totalSent=self.state["totalSent"] + 1
                            )
                            current = None
                            await control.delay(config.run.action_delay)
                        self.state["errors"] = counts["errors"]
                        self.store.update_run(run_id, **counts)
                        if not await reader.next_page():
                            break
                        if stagnant >= 3:
                            self.store.event(
                                run_id,
                                "WARNING",
                                "auto_list_wait",
                                "列表暂未加载更多会话，下轮将继续检查",
                            )
                            break
                note = (
                    f"本轮检查 {counts['scanned']} 个会话，发现 {counts['matched']} 条待回复消息"
                    + ("；只读扫描已完成" if scan_only else f"，已送达 {counts['sent']} 条")
                )
                self.state.update(
                    status="ready" if scan_only else "waiting",
                    note=note,
                    lastScanAt=now(),
                    job=None,
                )
                self.store.update_run(run_id, status="completed", note=note, **counts)
                self.store.event(run_id, "INFO", "auto_scan_complete", note)
        except (asyncio.CancelledError, StopRequested):
            note = "自动回复操作已停止；已提交但回执不明的消息不会自动重发"
            self.state.update(status="off", note=note, nextScanAt=None)
            if current:
                self.record(run_id, *current, "failed", "操作已停止，后续未继续发送")
            if self.store.run(run_id):
                self.store.update_run(run_id, status="stopped", note=note, **counts)
        except Exception as error:
            note = clean_error(error)
            counts["errors"] += 1
            self.state.update(
                status="needs_attention", note=note, errors=counts["errors"], nextScanAt=None
            )
            if current:
                self.record(run_id, *current, "failed", note, level="WARNING")
            if self.store.run(run_id):
                self.store.update_run(run_id, status="needs_attention", note=note, **counts)
            self.store.event(run_id, "WARNING", "auto_error", note)
        return self.store.run(run_id)
