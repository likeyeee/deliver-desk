import asyncio
import copy
import io
import os
import zipfile
from functools import partial
from types import SimpleNamespace
from xml.sax.saxutils import escape

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from boss_cli.browser import NeedsAttention, SendResult
from boss_cli.config import Config, dump_config
from boss_cli.control import Controller, StopRequested, is_active
from boss_cli.desktop_service import DesktopService
from boss_cli.resume import (
    ResumeDocument,
    ResumeProfile,
    ResumeWorkflow,
    extract_resume,
    generate_greeting,
)
from boss_cli.runner import Runner
from boss_cli.storage import Store, now

TEXT = "示例候选人\n2023—2025 年任产品经理，负责知识库问答项目，需求分析与效果评估。\n技能：Python、SQL。"
PROFILE = {
    "summary": "有知识库问答项目经验的产品经理",
    "skills": ["Python", "SQL"],
    "experiences": ["2023—2025 年负责知识库问答项目的需求分析与效果评估"],
    "strengths": ["通过知识库问答项目积累需求分析与效果评估经验"],
}


def docx_bytes(text):
    body = "".join(
        "<w:p><w:r><w:t>" + escape(line) + "</w:t></w:r></w:p>" for line in text.splitlines()
    )
    result = io.BytesIO()
    with zipfile.ZipFile(result, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "word/document.xml",
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
            + body
            + "</w:body></w:document>",
        )
    return result.getvalue()


@pytest.mark.parametrize("encoding", ["utf-8-sig", "utf-16", "gb18030"])
def test_text_resume_preserves_chinese(tmp_path, encoding):
    file = tmp_path / "简历.TXT"
    file.write_bytes(TEXT.encode(encoding))
    assert extract_resume(file) == TEXT


def test_docx_extracts_tables_and_unicode_without_extracting_files(tmp_path):
    file = tmp_path / "简历.docx"
    file.write_bytes(docx_bytes(TEXT))
    assert extract_resume(file) == TEXT
    assert sorted(path.name for path in tmp_path.iterdir()) == ["简历.docx"]


def test_pdf_extracts_text_and_rejects_scans_and_encryption(tmp_path):
    writer = PdfWriter()
    page = writer.add_blank_page(600, 800)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)}),
        }
    )
    stream = DecodedStreamObject()
    stream.set_data(
        b"BT /F1 12 Tf 50 700 Td (Product manager with Python and SQL experience.) Tj ET"
    )
    page[NameObject("/Contents")] = writer._add_object(stream)
    file = tmp_path / "resume.pdf"
    writer.write(file)
    assert "Python and SQL" in extract_resume(file)
    blank = PdfWriter()
    blank.add_blank_page(600, 800)
    blank.write(file)
    with pytest.raises(ValueError, match="未提取"):
        extract_resume(file)
    writer.encrypt("example-only-password")
    writer.write(file)
    with pytest.raises(ValueError, match="加密"):
        extract_resume(file)


def test_failed_import_preserves_existing_resume_and_never_exports_it(store, tmp_path):
    workflow = ResumeWorkflow(store, store.directory, None)
    original = workflow.save_text(TEXT)["document"]
    assert original["profile"] is None
    before = workflow.file.read_bytes()
    bad = tmp_path / "bad.docx"
    bad.write_bytes(b"not-a-document")
    with pytest.raises(ValueError, match="无法解析"):
        workflow.import_file(bad)
    assert workflow.file.read_bytes() == before
    assert TEXT not in dump_config(Config())
    assert "resume" not in Config().model_dump()
    if os.name != "nt":
        assert workflow.file.stat().st_mode & 0o777 == 0o600


def test_analysis_revision_edits_reload_and_clear(store):
    workflow = ResumeWorkflow(store, store.directory, None)
    document = workflow.save_text(TEXT)["document"]
    result = workflow.save_profile(PROFILE, document["revision"])
    assert result["document"]["profile"] == PROFILE
    revision = result["document"]["revision"]
    assert revision != document["revision"]
    reloaded = ResumeWorkflow(store, store.directory, None)
    assert reloaded.snapshot()["document"]["revision"] == revision
    with pytest.raises(ValueError, match="已变化"):
        workflow.save_profile(PROFILE, document["revision"])
    updated = workflow.save_text(TEXT + "\n新增项目：客服知识库。")
    assert updated["document"]["profile"] is None
    workflow.clear()
    assert not workflow.file.exists()
    assert workflow.snapshot()["document"] is None


@pytest.mark.parametrize(
    "filename,content,match",
    [
        ("old.doc", b"test", "支持"),
        ("empty.txt", b"", "未提取"),
        ("large.txt", b"x" * (60001), "60,000"),
        ("huge.txt", b"x" * (10 * 1024 * 1024 + 1), "10 MB"),
    ],
    ids=["legacy-doc", "empty-text", "text-limit", "file-limit"],
)
def test_unsupported_empty_and_large_resume_are_rejected(tmp_path, filename, content, match):
    file = tmp_path / filename
    file.write_bytes(content)
    with pytest.raises(ValueError, match=match):
        extract_resume(file)


async def test_analysis_uses_saved_resume_without_contacting_website(store, config):
    calls = []

    async def request(method, **params):
        calls.append((method, params))
        return (
            {"profile": PROFILE}
            if method == "analyzeResume"
            else {"message": "您好，我负责过知识库问答项目，希望交流这一岗位。"}
        )

    workflow = ResumeWorkflow(store, store.directory, SimpleNamespace(request=request))
    workflow.save_text(TEXT)
    result = await workflow.run("analyze", config, "resume-test")
    assert result["status"] == "completed"
    assert calls[0][1]["text"] == TEXT
    assert calls[0][1]["transport"] == "llm"
    assert workflow.document.profile.model_dump() == PROFILE
    assert len(calls) == 1
    assert store.history() == []
    assert store.attempts_today() == 0


async def test_invalid_analysis_never_becomes_a_sendable_profile(store, config):
    async def request(*_args, **_kwargs):
        return {"profile": {"summary": ""}}

    workflow = ResumeWorkflow(store, store.directory, SimpleNamespace(request=request))
    workflow.save_text(TEXT)
    result = await workflow.run("analyze", config, "bad-analysis")
    assert result["status"] == "failed"
    assert workflow.document.profile is None
    assert TEXT not in result["note"]


async def test_stop_cancels_resume_model_request_and_releases_worker(tmp_path, monkeypatch):
    service = DesktopService(tmp_path / "state", tmp_path / "config.yaml")
    requested = asyncio.Event()
    emitted = []

    def emit(value):
        emitted.append(value)
        if value.get("kind") == "llm":
            requested.set()

    monkeypatch.setattr("boss_cli.desktop_service.emit", emit)
    try:
        document = (await service.request("resume", {"action": "saveText", "text": TEXT}))[
            "document"
        ]
        await service.request("resume", {"action": "analyze", "revision": document["revision"]})
        await asyncio.wait_for(requested.wait(), 2)
        assert is_active(service.directory)
        with pytest.raises(ValueError, match="停止"):
            await service.request("resume", {"action": "saveText", "text": "replacement"})
        await service.request("control", {"action": "stop"})
        await asyncio.wait_for(service.task, 2)
        assert not is_active(service.directory)
        assert not service.bridge.pending
        assert service.resume.document.profile is None
        assert any(value.get("kind") == "llmCancel" for value in emitted)
        assert service.store.attempts_today() == 0
    finally:
        await service.shutdown()


class GreetingAdapter:
    def __init__(self, store):
        self.store = store
        self.greeted = []

    async def inspect_job(self, job):
        job.description = "负责岗位对应的知识库项目及 Python 开发"
        return job

    async def preflight(self, job):
        return True

    async def greet(self, job, message):
        record = next(row for row in self.store.history() if row["job_id"] == job.job_id)
        assert record["status"] == "sending" and record["message"] == message
        self.greeted.append((job.job_id, message))
        return SendResult("sent", "明确回执")


async def test_ai_greetings_use_each_job_detail_and_existing_reservation_and_dedup(
    config, store, job
):
    config.message.mode = "ai"
    calls = []

    async def greeting(*, job, control):
        assert job.description.startswith("负责岗位")
        calls.append(job.job_id)
        return "您好，我希望应聘" + job.title + "，我有知识库项目经验。"

    runner = Runner(config, store, "test-run", send=True, greeting=greeting)
    adapter = GreetingAdapter(store)
    second = copy.deepcopy(job)
    second.job_id = "second"
    second.title = "AI产品经理"
    second.url = "https://www.zhipin.com/job_detail/second.html"
    await runner.process(adapter, job)
    await runner.process(adapter, second)
    await runner.process(adapter, job)
    assert calls == [job.job_id, second.job_id]
    assert len(adapter.greeted) == 2
    assert adapter.greeted[0][1] != adapter.greeted[1][1]
    assert all(row["status"] == "sent" for row in store.history())


async def test_model_failure_and_stop_never_reserve_or_contact(config, store, job):
    config.message.mode = "ai"
    adapter = GreetingAdapter(store)
    resume = ResumeDocument(
        source_name="resume.txt", text=TEXT, imported_at=now(), profile=ResumeProfile(**PROFILE)
    )

    async def request(*_args, **_kwargs):
        raise RuntimeError("DeepSeek 服务异常")

    async def greeting(*, job, control):
        return await generate_greeting(
            store=store,
            bridge=SimpleNamespace(request=request),
            job=job,
            config=config,
            resume=resume,
            control=control,
            run_id="test-run",
        )

    runner = Runner(config, store, "test-run", send=True, greeting=greeting)
    with pytest.raises(NeedsAttention, match="未发起沟通"):
        await runner.process(adapter, job)
    assert adapter.greeted == [] and store.history() == []
    assert store.attempts_today() == 0
    failed = store.greeting(store.greeting_history()["items"][0]["id"])
    assert failed["status"] == "failed" and "DeepSeek 服务异常" in failed["note"]
    store.update_run("test-run", control="stop")
    with pytest.raises(StopRequested):
        await runner.process(adapter, job)
    assert adapter.greeted == []


async def test_discovery_automatically_generates_and_send_reuses_durable_greetings(
    config, store, job
):
    config.message.mode = "ai"
    resume = ResumeDocument(
        source_name="resume.txt", text=TEXT, imported_at=now(), profile=ResumeProfile(**PROFILE)
    )
    calls = []
    message = "我有知识库项目的需求分析经验。" * 100

    async def request(method, **params):
        assert method == "generateGreeting"
        assert params["resume"] == {"text": TEXT, "profile": PROFILE}
        assert params["job"]["description"].startswith("负责岗位")
        calls.append(params)
        return {"message": message, "usage": {"total_tokens": 1200}}

    bridge = SimpleNamespace(request=request)
    callback = partial(generate_greeting, store=store, bridge=bridge, config=config, resume=resume)
    preview = Runner(
        config,
        store,
        "test-run",
        send=False,
        greeting=partial(callback, run_id="test-run", mode="preview"),
    )
    adapter = GreetingAdapter(store)
    await preview.process(adapter, job)
    assert store.history() == [] and adapter.greeted == []
    assert store.attempts_today() == 0
    original = store.greeting(store.greeting_history()["items"][0]["id"])
    assert original["message"] == message
    assert original["usage"] == {"total_tokens": 1200}
    assert original["resume_revision"] == resume.revision()
    assert original["delivery_status"] == "" and original["mode"] == "preview"
    assert original["profile_json"] == PROFILE
    assert "text" not in original["profile_json"]
    store.create_run("send-auto", "send")

    class AuditedAdapter(GreetingAdapter):
        async def greet(self, job, body):
            record = store.greeting(store.greeting_history()["items"][0]["id"])
            assert record["delivery_status"] == "sending" and record["message"] == body
            return await super().greet(job, body)

    sender = Runner(
        config, store, "send-auto", send=True, greeting=partial(callback, run_id="send-auto")
    )
    await sender.process(AuditedAdapter(store), job)
    assert len(calls) == 1
    sent = store.greeting(store.greeting_history()["items"][0]["id"])
    assert sent["delivery_status"] == "sent" and sent["reused_from"] == original["id"]
    assert sent["message"] == message and sent["usage"] == {}
    await sender.process(adapter, job)
    assert len(calls) == 1 and store.greetings_overview()["total"] == 2
    store.delivery(job.job_id, "unknown", "待核对")
    assert store.greeting(sent["id"])["delivery_status"] == "unknown"
    store.resolve(job.job_id, "sent", "人工核对已送达")
    assert store.greeting(sent["id"])["delivery_note"] == "人工核实：人工核对已送达"
    reloaded = Store(store.directory)
    assert reloaded.greeting(original["id"])["message"] == message
    assert reloaded.greeting(sent["id"])["delivery_status"] == "sent"
    reloaded.close()


@pytest.mark.parametrize(
    "changed", ["jd", "resume", "instructions", "model", "persona", "temperature"]
)
async def test_changed_generation_inputs_refresh_message_and_preserve_original_snapshot(
    config, store, job, changed
):
    resume = ResumeDocument(
        source_name="resume.txt", text=TEXT, imported_at=now(), profile=ResumeProfile(**PROFILE)
    )
    calls = []

    async def request(_method, **params):
        calls.append(params)
        return {"message": f"完整招呼 {len(calls)}"}

    bridge = SimpleNamespace(request=request)
    await generate_greeting(
        store=store,
        bridge=bridge,
        config=config,
        resume=resume,
        job=job,
        control=Controller(store, "test-run"),
        run_id="test-run",
        mode="preview",
    )
    old = store.greeting_history()["items"][0]["id"]
    description = job.description
    if changed == "jd":
        job.description += "，新增 SQL 指标分析职责"
    elif changed == "resume":
        resume = resume.model_copy(update={"text": TEXT + "\n有客服知识库经验"})
    elif changed == "instructions":
        config.message.instructions += "突出需求分析经验"
    elif changed == "model":
        config.llm.model = "deepseek-v4-pro"
    elif changed == "persona":
        config.llm.system_prompt += "表达友好自然"
    else:
        config.llm.temperature = 0.1
    store.create_run("changed", "preview")
    await generate_greeting(
        store=store,
        bridge=bridge,
        config=config,
        resume=resume,
        job=job,
        control=Controller(store, "changed"),
        run_id="changed",
        mode="preview",
    )
    assert len(calls) == 2
    assert store.greeting(old)["job_json"]["description"] == description
    assert store.greeting(old)["message"] == "完整招呼 1"
    latest = store.greeting(store.greeting_history()["items"][0]["id"])
    assert latest["message"] == "完整招呼 2" and latest["reused_from"] == ""


async def test_missing_jd_and_stopped_generation_leave_audit_before_any_contact(config, store, job):
    resume = ResumeDocument(
        source_name="resume.txt", text=TEXT, imported_at=now(), profile=ResumeProfile(**PROFILE)
    )
    entered = asyncio.Event()

    async def request(*_args, **_kwargs):
        entered.set()
        await asyncio.Event().wait()

    job.description = ""
    kwargs = dict(
        store=store, bridge=SimpleNamespace(request=request), config=config, resume=resume, job=job
    )
    with pytest.raises(NeedsAttention, match="岗位 JD"):
        await generate_greeting(**kwargs, control=Controller(store, "test-run"), run_id="test-run")
    assert not entered.is_set()
    first = store.greeting(store.greeting_history()["items"][0]["id"])
    assert first["status"] == "failed" and first["message"] == ""
    store.create_run("stop-generation", "send")
    job.description = "知识库应用开发"
    task = asyncio.create_task(
        generate_greeting(
            **kwargs, control=Controller(store, "stop-generation"), run_id="stop-generation"
        )
    )
    await asyncio.wait_for(entered.wait(), 2)
    assert store.greeting_history()["items"][0]["status"] == "generating"
    store.update_run("stop-generation", control="stop")
    with pytest.raises(StopRequested):
        await asyncio.wait_for(task, 2)
    assert store.greeting_history()["items"][0]["status"] == "stopped"
    assert store.attempts_today() == 0 and store.history() == []


def test_greeting_recovery_pagination_and_readonly_service(config, store, job):
    resume = ResumeDocument(
        source_name="resume.txt", text=TEXT, imported_at=now(), profile=ResumeProfile(**PROFILE)
    )
    for index in range(53):
        run_id = f"audit-{index}"
        store.create_run(run_id, "send")
        store.begin_greeting(str(index), run_id, job, "send", resume, config, str(index))
        if index < 52:
            store.finish_greeting(str(index), "generated", message="完整正文")
    store.reserve(job, "audit-51", "完整正文", 100)
    store.recover()
    assert store.greeting("52")["status"] == "stopped"
    assert store.greeting("51")["delivery_status"] == "unknown"
    assert store.greeting("50")["delivery_status"] == "not_sent"
    first = store.greeting_history()
    second = store.greeting_history(first["nextCursor"])
    assert len(first["items"]) == 50 and len(second["items"]) == 3
    assert second["nextCursor"] is None
    assert len({row["id"] for row in first["items"] + second["items"]}) == 53
    assert store.db.execute("PRAGMA user_version").fetchone()[0] == 6


def test_legacy_database_upgrade_keeps_delivery_history_and_adds_greeting_audit(tmp_path, job):
    directory = tmp_path / "legacy"
    legacy = Store(directory)
    legacy.create_run("old-run", "send")
    legacy.save_job(job)
    legacy.reserve(job, "old-run", "原有招呼", 100)
    legacy.delivery(job.job_id, "sent", "已送达")
    legacy.db.executescript("""
        DROP TRIGGER greeting_delivery_insert;
        DROP TRIGGER greeting_delivery_update;
        DROP TABLE greetings;
        PRAGMA user_version=4;
    """)
    legacy.close()
    upgraded = Store(directory)
    assert upgraded.history()[0]["message"] == "原有招呼"
    assert upgraded.blocked(job.job_id) == "sent"
    assert upgraded.greeting_history()["items"] == []
    assert upgraded.db.execute("PRAGMA user_version").fetchone()[0] == 6
    upgraded.close()


async def test_greeting_history_remains_readable_during_active_task_and_ai_preview_requires_resume(
    tmp_path,
):
    service = DesktopService(tmp_path / "state", tmp_path / "config.yaml")
    try:
        service.config.message.mode = "ai"
        with pytest.raises(ValueError, match="上传并分析简历"):
            await service.request("start", {"mode": "preview"})
        service.task = asyncio.create_task(asyncio.Event().wait())
        assert (await service.request("greetings", {}))["items"] == []
    finally:
        await service.shutdown()
