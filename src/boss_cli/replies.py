"""Single-conversation draft/review/send workflow, using the existing worker lock."""

import asyncio
import uuid

from .browser import BossAdapter, LayoutChanged, clean_error
from .control import Controller, StopRequested, task_lock


class ReplyWorkflow:
    def __init__(self, store, directory, bridge, factory):
        self.store, self.directory, self.bridge, self.factory = store, directory, bridge, factory
        self.state = {"status": "idle", "note": "选择一个已经沟通过的职位，读取近期对话"}
        self.chat = None
        self.job_id = None
        self.binding = None

    def validate(self, action, params):
        if action not in {"read", "generate", "send"}:
            raise ValueError("无效的回复操作")
        if action == "send":
            record = self.store.reply(params.get("replyId", ""))
            if record["status"] != "draft":
                raise ValueError("这条回复已处理，未重复发送")
            message = params.get("message")
            if not isinstance(message, str) or not message.strip() or len(message.strip()) > 1000:
                raise ValueError("回复需为 1–1000 字")
            return self.store.reply_job(record["job_id"])
        job = self.store.reply_job(params.get("jobId", ""))
        if action == "generate" and (
            self.state.get("job", {}).get("job_id") != job.job_id or not self.state.get("context")
        ):
            raise ValueError("请先读取这个职位的对话")
        return job

    async def run(self, config, action, params, run_id):
        reserved = None
        try:
            with task_lock(self.directory):
                self.store.recover()
                job = self.validate(action, params)
                self.store.create_run(run_id, "reply_" + action)
                control = Controller(self.store, run_id)
                await control.checkpoint()
                if action == "read":
                    self.state = {"job": job.as_dict()}
                    self.binding = None
                self.state.update(
                    status=action,
                    note={
                        "read": "正在读取并核对会话…",
                        "generate": "DeepSeek 正在生成回复…",
                        "send": "正在核对会话并发送回复…",
                    }[action],
                )
                retained = self.chat if self.job_id == job.job_id else None
                async with self.factory(config, self.directory, retained) as session:
                    adapter = BossAdapter(session, config, control)
                    if self.chat is None or self.chat.is_closed() or self.job_id != job.job_id:
                        await adapter.inspect_job(job)
                        self.chat = await adapter.open_full_chat(job)
                        self.job_id = job.job_id
                    else:
                        adapter.watch_page(self.chat)
                        session.owned_pages.append(self.chat)
                        if self.binding:
                            adapter.chat_bindings[self.chat] = self.binding
                    context = await adapter.read_conversation(self.chat, job)
                    self.binding = adapter.chat_bindings.get(self.chat)
                    self.state.update(context=context, job=job.as_dict())
                    if action == "read":
                        self.state.update(context=context, job=job.as_dict(), draft=None)
                        old = self.store.db.execute(
                            "SELECT id FROM replies WHERE job_id=? AND context_hash=? AND status='draft'",
                            (job.job_id, context["fingerprint"]),
                        ).fetchone()
                        if old:
                            self.state["draft"] = self.store.reply(old["id"])
                        self.state.update(
                            status="ready", note=context["reason"] or "对话已读取，可生成回复"
                        )
                    elif action == "generate":
                        self.store.check_reply_pending(job.job_id)
                        if not context["canReply"]:
                            raise ValueError(context["reason"])
                        self.state.update(context=context, draft=None, job=job.as_dict())
                        result = await self.bridge.request(
                            "generate",
                            transport="llm",
                            config=config.llm.model_dump(),
                            messages=context["messages"],
                            job={"title": job.title, "company": job.company},
                        )
                        await control.checkpoint()
                        current = await adapter.read_conversation(self.chat, job)
                        if (
                            current["fingerprint"] != context["fingerprint"]
                            or not current["canReply"]
                        ):
                            self.state["context"] = current
                            raise LayoutChanged("生成期间会话已变化，请重新生成回复")
                        message = result.get("message")
                        if (
                            not isinstance(message, str)
                            or not message.strip()
                            or len(message) > 1000
                        ):
                            raise ValueError("模型未返回 1–1000 字的有效回复")
                        self.state["draft"] = self.store.save_reply(
                            uuid.uuid4().hex, job.job_id, context, config.llm.model, message
                        )
                        self.state.update(status="draft", note="草稿已生成，请编辑确认后发送")
                    else:
                        record = self.store.reply(params["replyId"])
                        if (
                            context["fingerprint"] != record["context_hash"]
                            or not context["canReply"]
                        ):
                            self.state.update(context=context, draft=None)
                            raise LayoutChanged("草稿对应的会话已变化，请重新读取并生成回复")
                        await control.checkpoint()
                        self.store.reserve_reply(
                            record["id"], run_id, params["message"].strip(), config.run.daily_limit
                        )
                        reserved = record["id"]
                        self.store.update_run(run_id, attempts=1, target=1)
                        result = await adapter.send_custom(
                            self.chat,
                            job,
                            params["message"].strip(),
                            expected_context=record["context_hash"],
                        )
                        status = "sent" if result.status == "sent" else "unknown"
                        self.store.finish_reply(reserved, status, result.note)
                        reserved = None
                        self.state.update(status=status, note=result.note, draft=None)
                        self.store.update_run(run_id, sent=int(status == "sent"))
                        if status != "sent":
                            raise LayoutChanged(result.note)
                        self.state["context"] = {
                            **context,
                            "messages": (
                                context["messages"]
                                + [
                                    {
                                        "role": "assistant",
                                        "content": params["message"].strip(),
                                        "supported": True,
                                        "id": "",
                                    }
                                ]
                            )[-config.llm.context_messages :],
                            "canReply": False,
                            "reason": "等待招聘方新消息，再读取对话回复",
                        }
                self.store.update_run(run_id, status="completed", note=self.state["note"])
                self.store.event(run_id, "INFO", "reply_" + action, self.state["note"], job.job_id)
        except (asyncio.CancelledError, StopRequested):
            self.state.update(status="stopped", note="回复操作已停止；发送中的结果请在记录里核对")
            if self.store.run(run_id):
                self.store.update_run(run_id, status="stopped", note=self.state["note"])
        except Exception as error:
            self.state.update(status="error", note=clean_error(error))
            if self.store.run(run_id):
                self.store.update_run(
                    run_id, status="needs_attention", errors=1, note=self.state["note"]
                )
                self.store.event(run_id, "WARNING", "reply_error", self.state["note"])
        finally:
            if reserved:
                self.store.finish_reply(
                    reserved, "unknown", "回复期间中断或异常，请到网站核对；不会自动重发"
                )
                self.state["draft"] = None
        return self.store.run(run_id)
