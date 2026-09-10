import asyncio
import copy
import io
import os
import zipfile
from types import SimpleNamespace
from xml.sax.saxutils import escape

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from boss_cli.browser import NeedsAttention, SendResult
from boss_cli.config import Config, dump_config
from boss_cli.control import StopRequested, is_active
from boss_cli.desktop_service import DesktopService
from boss_cli.resume import (
    ResumeDocument,
    ResumeProfile,
    ResumeWorkflow,
    extract_resume,
    generate_greeting,
)
from boss_cli.runner import Runner
from boss_cli.storage import now

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
    assert updated["greeting"] is None
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
)
def test_unsupported_empty_and_large_resume_are_rejected(tmp_path, filename, content, match):
    file = tmp_path / filename
    file.write_bytes(content)
    with pytest.raises(ValueError, match=match):
        extract_resume(file)


async def test_analysis_and_preview_use_saved_resume_without_contacting_website(store, config, job):
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
    store.save_job(job)
    result = await workflow.run(
        "previewGreeting", config, "greeting-test", workflow.preview_job(job.job_id)
    )
    assert result["status"] == "completed"
    greeting = workflow.snapshot()["greeting"]
    assert greeting["job"]["job_id"] == job.job_id
    assert calls[-1][1]["job"]["description"] == job.description
    assert calls[-1][1]["resume"]["profile"] == PROFILE
    assert calls[-1][1]["resume"]["text"] == TEXT
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
    store.update_run("test-run", control="stop")
    with pytest.raises(StopRequested):
        await runner.process(adapter, job)
    assert adapter.greeted == []


async def test_ai_job_preview_never_calls_model(config, store, job):
    config.message.mode = "ai"

    async def forbidden(**_kwargs):
        raise AssertionError("职位预览不应请求模型")

    runner = Runner(config, store, "test-run", send=False, greeting=forbidden)
    await runner.process(GreetingAdapter(store), job)
    assert store.history() == []
    assert store.attempts_today() == 0
