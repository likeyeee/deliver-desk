"""Local resume extraction, editable analysis and job-specific greetings."""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import re
import uuid
import zipfile
from pathlib import Path
from typing import Annotated
from xml.etree import ElementTree

from pydantic import Field, ValidationError, field_validator, model_validator
from pypdf import PdfReader

from .browser import NeedsAttention, clean_error
from .config import StrictModel
from .control import Controller, StopRequested, task_lock
from .models import Job
from .storage import now

MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_TEXT = 60000
Fact = Annotated[str, Field(min_length=1, max_length=2000)]


def clean_text(text: str) -> str:
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text).replace("\r\n", "\n")
    text = "\n".join(line.rstrip() for line in text.splitlines()).strip()
    if not text:
        raise ValueError("未提取到简历正文；扫描件请先转成文字，或直接粘贴正文")
    if len(text) > MAX_TEXT:
        raise ValueError("简历正文超过 60,000 字，请精简后上传；原文未截断")
    return text


def extract_resume(file: Path) -> str:
    if file.suffix.lower() not in {".pdf", ".docx", ".txt"}:
        raise ValueError("支持 PDF、DOCX 和 TXT；旧版 DOC 请另存为 DOCX")
    if not file.is_file() or file.stat().st_size > MAX_FILE_BYTES:
        raise ValueError("请选择不超过 10 MB 的简历文件")
    data = file.read_bytes()
    if len(data) > MAX_FILE_BYTES:
        raise ValueError("简历文件超过 10 MB")
    try:
        if file.suffix.lower() == ".pdf":
            reader = PdfReader(io.BytesIO(data))
            if reader.is_encrypted:
                raise ValueError("PDF 已加密，请上传解除密码后的副本")
            if len(reader.pages) > 50:
                raise ValueError("PDF 超过 50 页，请上传简历正文部分")
            pages = []
            for page in reader.pages:
                contents = page.get_contents()
                if contents and len(contents.get_data()) > 4 * 1024 * 1024:
                    raise ValueError("PDF 页面内容过于复杂，请另存为文本版 PDF 或 DOCX")
                pages.append(page.extract_text() or "")
                if sum(map(len, pages)) > MAX_TEXT:
                    raise ValueError("简历正文超过 60,000 字，请精简后上传；原文未截断")
            text = "\n".join(pages)
        elif file.suffix.lower() == ".docx":
            namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
            paragraphs = []
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                if sum(info.file_size for info in archive.infolist()) > 50 * 1024 * 1024:
                    raise ValueError("DOCX 解压后过大，请精简图片或另存为 TXT")
                names = [
                    name
                    for name in archive.namelist()
                    if re.fullmatch(r"word/(document|header\d+|footer\d+)\.xml", name)
                ]
                if "word/document.xml" not in names:
                    raise ValueError("DOCX 缺少文档正文，请重新另存")
                for name in sorted(names, key=lambda n: ("header" not in n, "footer" in n, n)):
                    if archive.getinfo(name).file_size > 4 * 1024 * 1024:
                        raise ValueError("DOCX 正文过大，请精简后上传")
                    xml = archive.read(name)
                    if b"<!DOCTYPE" in xml.upper() or b"<!ENTITY" in xml.upper():
                        raise ValueError("DOCX 包含不支持的 XML 声明，请重新另存")
                    tree = ElementTree.fromstring(xml)
                    for paragraph in tree.iter(namespace + "p"):
                        paragraphs.append(
                            "".join(
                                node.text or ""
                                if node.tag == namespace + "t"
                                else "\n"
                                if node.tag == namespace + "br"
                                else "\t"
                                for node in paragraph.iter()
                                if node.tag
                                in {namespace + "t", namespace + "br", namespace + "tab"}
                            )
                        )
            text = "\n".join(paragraphs)
        else:
            if data.startswith((b"\xff\xfe", b"\xfe\xff")):
                text = data.decode("utf-16")
            else:
                try:
                    text = data.decode("utf-8-sig")
                except UnicodeDecodeError:
                    text = data.decode("gb18030")
    except ValueError:
        raise
    except Exception as error:
        raise ValueError("无法解析简历，请确认文件完整，或另存为 PDF、DOCX、TXT") from error
    return clean_text(text)


class ResumeProfile(StrictModel):
    summary: str = Field(min_length=1, max_length=4000)
    skills: list[Fact] = Field(default_factory=list, max_length=30)
    experiences: list[Fact] = Field(default_factory=list, max_length=30)
    strengths: list[Fact] = Field(default_factory=list, max_length=30)

    @field_validator("summary")
    @classmethod
    def summary_valid(cls, value):
        if not value.strip():
            raise ValueError("个人概况不能为空")
        return value.strip()

    @field_validator("skills", "experiences", "strengths")
    @classmethod
    def facts_valid(cls, values):
        return list(dict.fromkeys(value.strip() for value in values if value.strip()))

    @model_validator(mode="after")
    def profile_size(self):
        if len(json.dumps(self.model_dump(), ensure_ascii=False)) > 20000:
            raise ValueError("简历特点超过 20,000 字")
        return self


class ResumeDocument(StrictModel):
    source_name: str = Field(min_length=1, max_length=255)
    text: str = Field(min_length=1, max_length=MAX_TEXT)
    imported_at: str
    profile: ResumeProfile | None = None
    profile_model: str = ""
    analyzed_at: str = ""

    def revision(self):
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()


async def controlled_request(awaitable, control):
    """Keep pause/stop responsive while a model request is outstanding."""
    task = asyncio.create_task(awaitable)
    try:
        while not task.done():
            await control.checkpoint()
            await asyncio.wait({task}, timeout=0.2)
        await control.checkpoint()
        return await task
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def generate_greeting(*, store, bridge, job, config, resume, control, run_id):
    if not resume or not resume.profile:
        raise NeedsAttention("请先上传并分析简历，再使用 AI 岗位招呼")
    store.event(
        run_id,
        "INFO",
        "greeting_generate",
        f"开始生成岗位招呼 · {config.llm.model} · 简历版本 {resume.revision()[:12]}",
        job.job_id,
    )
    try:
        result = await controlled_request(
            bridge.request(
                "generateGreeting",
                transport="llm",
                config=config.llm.model_dump(),
                job=job.as_dict(),
                resume={"text": resume.text, "profile": resume.profile.model_dump()},
                instructions=config.message.instructions,
            ),
            control,
        )
        message = result.get("message")
        if not isinstance(message, str) or not message.strip():
            raise ValueError("模型未返回有效的完整招呼")
    except (StopRequested, asyncio.CancelledError):
        raise
    except Exception as error:
        raise NeedsAttention("AI 岗位招呼生成失败，未发起沟通：" + clean_error(error)) from error
    message = message.strip()
    store.event(
        run_id,
        "INFO",
        "greeting_generated",
        f"已完整生成 {len(message)} 字；简历版本 {resume.revision()[:12]}",
        job.job_id,
    )
    return message


class ResumeWorkflow:
    def __init__(self, store, directory, bridge):
        self.store, self.directory, self.bridge = store, directory, bridge
        self.file = directory / "resume.json"
        self.document = None
        self.state = {"status": "idle", "note": ""}
        self.greeting = None
        if self.file.exists():
            try:
                if self.file.stat().st_size > 1024 * 1024:
                    raise ValueError("文件过大")
                self.document = ResumeDocument.model_validate_json(self.file.read_bytes())
            except (OSError, ValueError):
                self.state = {"status": "error", "note": "本地简历无法读取，请重新上传"}

    def snapshot(self):
        return {
            "document": (
                self.document.model_dump() | {"revision": self.document.revision()}
                if self.document
                else None
            ),
            "state": self.state,
            "greeting": self.greeting,
        }

    def persist(self, document):
        temporary = self.file.with_suffix("." + uuid.uuid4().hex + ".tmp")
        try:
            temporary.write_text(document.model_dump_json(indent=2) + "\n", encoding="utf-8")
            temporary.chmod(0o600)
            os.replace(temporary, self.file)
        finally:
            temporary.unlink(missing_ok=True)
        self.document = document
        self.greeting = None

    def import_file(self, file):
        path = Path(file)
        text = extract_resume(path)
        self.persist(ResumeDocument(source_name=path.name[:255], text=text, imported_at=now()))
        self.state = {"status": "ready", "note": "简历已导入，可检查正文后分析"}
        self.store.event(None, "INFO", "resume_import", f"已导入简历正文，共 {len(text)} 字")
        return self.snapshot()

    def save_text(self, text):
        if not isinstance(text, str):
            raise ValueError("请填写简历正文")
        text = clean_text(text)
        if self.document and self.document.text == text:
            return self.snapshot()
        self.persist(
            ResumeDocument(
                source_name=self.document.source_name if self.document else "粘贴的简历",
                text=text,
                imported_at=now(),
            )
        )
        self.state = {"status": "ready", "note": "正文已保存，请重新分析简历"}
        return self.snapshot()

    def check_revision(self, revision):
        if not self.document or self.document.revision() != revision:
            raise ValueError("简历已变化，请刷新后再操作")

    def save_profile(self, profile, revision):
        self.check_revision(revision)
        try:
            profile = ResumeProfile.model_validate(profile)
        except ValidationError as error:
            raise ValueError("请填写个人概况；每项特点不超过 2,000 字，每栏不超过 30 项") from error
        self.persist(self.document.model_copy(update={"profile": profile}))
        self.state = {"status": "ready", "note": "简历特点已保存"}
        return self.snapshot()

    def clear(self):
        self.file.unlink(missing_ok=True)
        self.document, self.greeting = None, None
        self.state = {"status": "idle", "note": "简历已移除"}
        self.store.event(None, "INFO", "resume_removed", "已移除本地简历及分析结果")
        return self.snapshot()

    def preview_job(self, job_id):
        row = self.store.db.execute("SELECT data FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        if not row:
            raise ValueError("请先预览职位，再选择要生成招呼的岗位")
        return Job(**json.loads(row["data"]))

    async def run(self, action, config, run_id, job=None):
        try:
            with task_lock(self.directory):
                self.store.recover()
                self.store.create_run(
                    run_id, "resume_analyze" if action == "analyze" else "greeting_preview"
                )
                control = Controller(self.store, run_id)
                await control.checkpoint()
                document = self.document.model_copy(deep=True)
                revision = document.revision()
                self.state = {
                    "status": "analyzing" if action == "analyze" else "generating",
                    "note": "DeepSeek 正在分析简历…"
                    if action == "analyze"
                    else "DeepSeek 正在生成岗位招呼…",
                }
                if action == "analyze":
                    self.store.event(
                        run_id, "INFO", "resume_analyze", f"开始分析简历 · {config.llm.model}"
                    )
                    result = await controlled_request(
                        self.bridge.request(
                            "analyzeResume",
                            transport="llm",
                            config=config.llm.model_dump(),
                            text=document.text,
                        ),
                        control,
                    )
                    try:
                        profile = ResumeProfile.model_validate(result.get("profile"))
                    except ValidationError as error:
                        raise ValueError("DeepSeek 简历分析格式无效，请重新分析") from error
                    self.check_revision(revision)
                    self.persist(
                        document.model_copy(
                            update={
                                "profile": profile,
                                "profile_model": config.llm.model,
                                "analyzed_at": now(),
                            }
                        )
                    )
                    self.state = {
                        "status": "ready",
                        "note": "简历分析完成，可编辑特点后用于岗位招呼",
                    }
                    self.store.event(run_id, "INFO", "resume_analyzed", "简历特点已保存至本机")
                else:
                    self.greeting = None
                    message = await generate_greeting(
                        store=self.store,
                        bridge=self.bridge,
                        job=job,
                        config=config,
                        resume=document,
                        control=control,
                        run_id=run_id,
                    )
                    self.check_revision(revision)
                    self.greeting = {
                        "job": job.as_dict(),
                        "message": message,
                        "model": config.llm.model,
                        "resumeRevision": revision,
                        "createdAt": now(),
                        "instructions": config.message.instructions,
                    }
                    self.state = {"status": "ready", "note": "岗位招呼已生成，未发送"}
                    self.store.event(
                        run_id, "INFO", "greeting_preview", "预览岗位招呼，未发起沟通", job.job_id
                    )
                self.store.update_run(run_id, status="completed", note=self.state["note"])
        except (StopRequested, asyncio.CancelledError):
            self.state = {"status": "stopped", "note": "已停止，未发送消息"}
            self.store.update_run(run_id, status="stopped", note=self.state["note"])
        except Exception as error:
            self.state = {"status": "error", "note": clean_error(error)}
            self.store.update_run(run_id, status="failed", note=self.state["note"])
            self.store.event(run_id, "ERROR", "resume_error", self.state["note"])
        return self.store.run(run_id)
