from __future__ import annotations

import string
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_default=True)


class BrowserConfig(StrictModel):
    channel: Literal["chrome", "chromium"] = "chrome"
    cdp_url: str | None = None
    headless: bool = False
    timeout_seconds: int = Field(default=20, ge=3, le=120)
    login_timeout_seconds: int = Field(default=300, ge=10, le=1800)

    @field_validator("cdp_url")
    @classmethod
    def local_cdp(cls, value: str | None) -> str | None:
        if value:
            parsed = urlsplit(value)
            if (
                parsed.scheme not in {"http", "ws"}
                or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
                or parsed.username
                or parsed.password
            ):
                raise ValueError("CDP 仅支持本机 localhost / 127.0.0.1 / ::1 地址")
        return value


class SearchConfig(StrictModel):
    keywords: list[str] = Field(default_factory=lambda: ["AI应用", "AI产品经理", "大模型"])
    city: str = "全国"
    # Use exact visible UI labels. No undocumented API parameters or hidden endpoints.
    filters: dict[str, str] = Field(default_factory=dict)
    max_jobs: int = Field(default=30, ge=1, le=2000)
    max_scrolls: int = Field(default=8, ge=0, le=100)

    @field_validator("keywords")
    @classmethod
    def keywords_valid(cls, values: list[str]) -> list[str]:
        values = list(dict.fromkeys(s.strip() for s in values if s.strip()))
        if not values or len(values) > 20 or any(len(s) > 100 for s in values):
            raise ValueError("填写 1–20 个非空职位关键词，每个不超过 100 字")
        return values

    @field_validator("city")
    @classmethod
    def city_valid(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("city 不能为空")
        return value.strip()

    @field_validator("filters")
    @classmethod
    def filters_valid(cls, values: dict[str, str]) -> dict[str, str]:
        allowed = {
            "工作区域",
            "职位类型",
            "求职类型",
            "薪资待遇",
            "工作经验",
            "学历要求",
            "公司行业",
            "公司规模",
            "融资阶段",
        }
        if set(values) - allowed or any(not v.strip() for v in values.values()):
            raise ValueError("filters 需使用网页筛选名称及非空选项文字")
        return values


class MatchConfig(StrictModel):
    title_any: list[str] = Field(
        default_factory=lambda: ["AI", "人工智能", "大模型", "AIGC", "智能体"]
    )
    exclude: list[str] = Field(default_factory=list)
    exclude_companies: list[str] = Field(default_factory=list)
    description_all: list[str] = Field(default_factory=list)
    salary_min: int | None = Field(default=None, ge=0)
    salary_max: int | None = Field(default=None, ge=0)
    unknown_salary: Literal["skip", "allow"] = "skip"

    @model_validator(mode="after")
    def salary_valid(self):
        if (
            self.salary_min is not None
            and self.salary_max is not None
            and self.salary_min > self.salary_max
        ):
            raise ValueError("salary_min 不得高于 salary_max")
        return self


class MessageConfig(StrictModel):
    mode: Literal["platform", "custom", "ai"] = "custom"
    template: str = (
        "您好，我对贵公司的{title}岗位很感兴趣，希望进一步了解岗位要求和团队情况，方便交流吗？"
    )
    instructions: str = Field(
        default="结合这个岗位最相关的真实经历或技能，简洁说明匹配点，表达应聘意愿并邀请交流。",
        max_length=4000,
    )

    @field_validator("template")
    @classmethod
    def template_valid(cls, value: str) -> str:
        if not value.strip() or len(value) > 1000:
            raise ValueError("打招呼模板需为 1–1000 字")
        allowed = {"title", "company", "city", "recruiter"}
        for _, field, spec, conv in string.Formatter().parse(value):
            if field is not None and (field not in allowed or spec or conv):
                raise ValueError(
                    "模板仅支持 {title} {company} {city} {recruiter}，不支持格式表达式"
                )
        return value


class RunConfig(StrictModel):
    max_sends: int = Field(default=5, ge=1, le=200)
    daily_limit: int = Field(default=20, ge=1, le=500)
    action_delay: tuple[float, float] = (1.5, 3.5)
    job_delay: tuple[float, float] = (15, 30)
    cooldown_every: int = Field(default=5, ge=1, le=100)
    cooldown_seconds: tuple[float, float] = (60, 120)
    max_errors: int = Field(default=3, ge=1, le=20)

    @field_validator("action_delay", "job_delay", "cooldown_seconds")
    @classmethod
    def delay_valid(cls, values: tuple[float, float]) -> tuple[float, float]:
        import math

        if not all(math.isfinite(v) for v in values) or not 0 <= values[0] <= values[1] <= 3600:
            raise ValueError("延时需为 [最小秒数, 最大秒数]，范围 0–3600")
        return values


class LLMConfig(StrictModel):
    provider: Literal["deepseek"] = "deepseek"
    model: str = Field(
        default="deepseek-v4-flash", min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9._-]+$"
    )
    system_prompt: str = Field(
        default=(
            "你以我的身份与招聘者交流，使用自然、礼貌、简洁的中文。"
            "只依据我提供的个人背景和对话事实回答，不编造工作经历、技能、薪资、"
            "联系方式或可面试时间；信息不足时向对方澄清或保留待我补充。"
            "只输出可以直接发送的回复正文，不附解释、分析、标题或引号。"
            "优先完整回答实际问题，长度随内容决定，不为凑字数重复，也不为缩短而遗漏要点。"
        ),
        min_length=1,
        max_length=20000,
    )
    temperature: float = Field(default=0.7, ge=0, le=2, allow_inf_nan=False)
    context_messages: int = Field(default=20, ge=2, le=50)

    @model_validator(mode="before")
    @classmethod
    def remove_legacy_token_limit(cls, value):
        # Accept older exports without continuing to apply their artificial output cap.
        if isinstance(value, dict):
            value = {key: item for key, item in value.items() if key != "max_tokens"}
        return value

    @field_validator("model", "system_prompt")
    @classmethod
    def nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("模型和系统提示词不能为空")
        return value.strip()


class AutoReplyConfig(StrictModel):
    interval_seconds: int = Field(default=30, ge=10, le=3600)
    settle_seconds: float = Field(default=2, ge=0, le=10, allow_inf_nan=False)


class Config(StrictModel):
    state_dir: str = ".boss-cli"
    browser: BrowserConfig = Field(default_factory=BrowserConfig)
    search: SearchConfig = Field(default_factory=SearchConfig)
    match: MatchConfig = Field(default_factory=MatchConfig)
    message: MessageConfig = Field(default_factory=MessageConfig)
    run: RunConfig = Field(default_factory=RunConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    auto_reply: AutoReplyConfig = Field(default_factory=AutoReplyConfig)
    selectors: dict[str, str] = Field(default_factory=dict)

    def directory(self, config_path: Path) -> Path:
        path = Path(self.state_dir).expanduser()
        return (path if path.is_absolute() else config_path.resolve().parent / path).resolve()


def load_config(path: Path) -> Config:
    with path.open(encoding="utf-8") as file:
        data = yaml.safe_load(file)
    if not isinstance(data, dict):
        raise ValueError("配置必须是 YAML 对象")
    return Config.model_validate(data)


def dump_config(config: Config) -> str:
    return yaml.safe_dump(config.model_dump(mode="json"), allow_unicode=True, sort_keys=False)


def render_message(config: MessageConfig, job) -> str:
    value = config.template.format(
        title=job.title, company=job.company, city=job.location, recruiter=job.recruiter
    )
    if not value.strip() or len(value) > 1000:
        raise ValueError("生成的消息为空或超过 1000 字")
    return value
