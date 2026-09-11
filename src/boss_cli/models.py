from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from urllib.parse import urlsplit, urlunsplit


def canonical_job_url(url: str) -> tuple[str, str]:
    """Only accept real job-detail URLs; tracking/security query strings never enter history."""
    parts = urlsplit(url)
    if (
        parts.scheme in {"http", "https"}
        and parts.hostname in {"www.zhaopin.com", "jobs.zhaopin.com"}
        and not parts.port
        and not parts.username
        and not parts.password
    ):
        pattern = (
            r"/([A-Za-z0-9_-]+)\.htm"
            if parts.hostname == "jobs.zhaopin.com"
            else r"/jobdetail/([A-Za-z0-9_-]+)\.htm"
        )
        match = re.fullmatch(pattern, parts.path)
        if not match:
            raise ValueError("无法识别智联职位 ID")
        return "zhaopin:" + match[1], urlunsplit(
            ("https", "www.zhaopin.com", "/jobdetail/" + match[1] + ".htm", "", "")
        )
    if (
        parts.scheme != "https"
        or parts.hostname != "www.zhipin.com"
        or parts.port
        or parts.username
        or parts.password
    ):
        raise ValueError("职位链接必须属于支持的招聘平台")
    match = re.fullmatch(r"/job_detail/([A-Za-z0-9_~\-]+)\.html", parts.path)
    if not match:
        raise ValueError("无法识别职位 ID")
    return match[1], urlunsplit(("https", "www.zhipin.com", parts.path, "", ""))


@dataclass
class Job:
    job_id: str
    title: str
    company: str
    url: str
    location: str = ""
    salary: str = ""
    experience: str = ""
    education: str = ""
    tags: str = ""
    description: str = ""
    recruiter: str = ""
    contacted: bool = False
    platform: str = "boss"

    def as_dict(self) -> dict:
        return asdict(self)


def salary_range(text: str) -> tuple[int, int] | None:
    """Monthly RMB only. Do not guess custom-font glyphs, daily wages or negotiable salaries."""
    if any(0xE000 <= ord(c) <= 0xF8FF for c in text):
        return None
    if re.search(r"[天时日年周]|面议", text):
        return None
    match = re.search(r"(\d+(?:\.\d+)?)\s*[-~–至]\s*(\d+(?:\.\d+)?)\s*([kK千万]|元)", text)
    if not match:
        return None
    factor = {"k": 1000, "千": 1000, "万": 10000, "元": 1}[match[3].lower()]
    low, high = int(float(match[1]) * factor), int(float(match[2]) * factor)
    return (low, high) if 0 <= low <= high else None


def readable_salary(text: str) -> str:
    if any(0xE000 <= ord(c) <= 0xF8FF for c in text):
        return "网页字体编码（请在浏览器查看）"
    return text or "未提供"
