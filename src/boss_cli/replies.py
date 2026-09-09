"""Single-conversation draft/review/send workflow, using the existing worker lock."""

import asyncio
import uuid

from .browser import BossAdapter, ConversationChanged, LayoutChanged, clean_error
from .control import Controller, StopRequested, task_lock


async def generate_reply(
    store, bridge, adapter, chat, job, context, config, control, run_id, *, source="manual"
):
    store.check_reply_pending(job.job_id)
    if not context["canReply"]:
        raise ValueError(context["reason"])
    store.event(run_id, "INFO", "reply_generate", f"开始生成回复 · {config.llm.model}", job.job_id)
    result = await bridge.request(
        "generate",
        transport="llm",
        config=config.llm.model_dump(),
        messages=context["messages"],
        job={"title": job.title, "company": job.company},
    )
    await control.checkpoint()
    current = await adapter.read_conversation(chat, job)
    if current["fingerprint"] != context["fingerprint"] or not current["canReply"]:
        raise ConversationChanged("生成期间会话已变化，已丢弃草稿；重新读取后再生成")
    message = result.get("message")
    if not isinstance(message, str) or not message.strip():
        raise ValueError("模型未返回有效的完整回复")
    record = store.save_reply(
        uuid.uuid4().hex,
        job.job_id,
        context,
        config.llm.model,
        message.strip(),
        source=source,
        usage=result.get("usage"),
    )
    token_note = (
        f"，输出 {result['usage']['completion_tokens']} Token"
        if isinstance(result.get("usage"), dict) and "completion_tokens" in result["usage"]
        else ""
    )
    store.event(
        run_id,
        "INFO",
        "reply_generated",
        f"已完整生成 {len(message.strip())} 字{token_note}；正文和对话已保存在回复记录",
        job.job_id,
    )
    return record


async def send_reply(store, adapter, chat, job, record, message, config, control, run_id):
    reserved = False
    try:
        context = await adapter.read_conversation(chat, job)
        if context["fingerprint"] != record["context_hash"] or not context["canReply"]:
            raise ConversationChanged("草稿对应的会话已变化，未发送；请重新读取并生成回复")
        await control.checkpoint()
        store.reserve_reply(record["id"], run_id, message, config.run.daily_limit)
        reserved = True
        store.event(
            run_id, "INFO", "reply_send", "已登记发送，正在核对目标并提交完整正文", job.job_id
        )
        result = await adapter.send_custom(
            chat, job, message, expected_context=record["context_hash"]
        )
        status = "sent" if result.status == "sent" else "unknown"
        store.finish_reply(record["id"], status, result.note)
        reserved = False
        store.event(
            run_id,
            "INFO" if status == "sent" else "WARNING",
            "reply_" + status,
            result.note,
            job.job_id,
        )
        if status != "sent":
            raise LayoutChanged(result.note)
        return result
    except ConversationChanged as error:
        if reserved:
            store.finish_reply(record["id"], "not_sent", str(error))
            store.event(run_id, "INFO", "reply_superseded", str(error), job.job_id)
            reserved = False
        raise
    finally:
        if reserved:
            note = "回复期间中断或异常，请到网站核对；不会自动重发"
            store.finish_reply(record["id"], "unknown", note)
            store.event(run_id, "WARNING", "reply_unknown", note, job.job_id)


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
            if not isinstance(message, str) or not message.strip():
                raise ValueError("回复正文不能为空")
            return self.store.reply_job(record["job_id"])
        job = self.store.reply_job(params.get("jobId", ""))
        if action == "generate" and (
            self.state.get("job", {}).get("job_id") != job.job_id or not self.state.get("context")
        ):
            raise ValueError("请先读取这个职位的对话")
        return job

    async def run(self, config, action, params, run_id):
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
                        self.state.update(context=context, draft=None, job=job.as_dict())
                        self.state["draft"] = await generate_reply(
                            self.store,
                            self.bridge,
                            adapter,
                            self.chat,
                            job,
                            context,
                            config,
                            control,
                            run_id,
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
                        self.store.update_run(run_id, attempts=1, target=1)
                        result = await send_reply(
                            self.store,
                            adapter,
                            self.chat,
                            job,
                            record,
                            params["message"].strip(),
                            config,
                            control,
                            run_id,
                        )
                        self.state.update(status="sent", note=result.note, draft=None)
                        self.store.update_run(run_id, sent=1)
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
            if action == "send":
                self.state["draft"] = None
        return self.store.run(run_id)
