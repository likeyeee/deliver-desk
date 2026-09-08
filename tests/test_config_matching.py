import pytest
from pydantic import ValidationError

from boss_cli.config import (
    Config,
    MessageConfig,
    RunConfig,
    dump_config,
    load_config,
    render_message,
)
from boss_cli.matching import reject_reason
from boss_cli.models import canonical_job_url, readable_salary, salary_range


def test_defaults_roundtrip(tmp_path):
    path = tmp_path / "boss.yaml"
    path.write_text(dump_config(Config()), encoding="utf-8")
    assert load_config(path) == Config()
    assert load_config(path).directory(path) == tmp_path / ".boss-cli"


@pytest.mark.parametrize(
    "template", ["{title.__class__}", "{company[x]}", "{title!r}", "{title:>20}", "{", ""]
)
def test_bad_templates_rejected(template):
    with pytest.raises((ValueError, ValidationError)):
        MessageConfig(template=template)


def test_message_has_no_invented_credentials(job):
    assert "AI应用工程师" in render_message(MessageConfig(), job)


@pytest.mark.parametrize("delay", [(-1, 3), (3, 2), (0, float("inf")), (float("nan"), 10)])
def test_invalid_delays(delay):
    with pytest.raises(ValidationError):
        RunConfig(job_delay=delay)


def test_unknown_fields_and_remote_cdp():
    with pytest.raises(ValidationError):
        Config.model_validate({"serach": {}})
    with pytest.raises(ValidationError):
        Config.model_validate({"browser": {"cdp_url": "http://example.com:9222"}})


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("15-25K·16薪", (15000, 25000)),
        ("1.5-2.5万", (15000, 25000)),
        ("8-12千", (8000, 12000)),
        ("300-500元/天", None),
        ("40-50万/年", None),
        ("面议", None),
        ("\ue032\ue031-\ue032\ue036K", None),
    ],
)
def test_salary(text, value):
    assert salary_range(text) == value


def test_salary_glyphs_fail_closed(config, job):
    config.match.salary_min = 15000
    job.salary = "\ue032-\ue034K"
    assert reject_reason(job, config.match) is None  # read standalone detail first
    assert reject_reason(job, config.match, detail=True) == "月薪无法可靠读取"
    assert "字体编码" in readable_salary(job.salary)
    config.match.unknown_salary = "allow"
    assert reject_reason(job, config.match) is None


def test_detail_and_company_exclusions(config, job):
    config.match.description_all = ["Python", "RAG"]
    assert reject_reason(job, config.match) is None
    assert reject_reason(job, config.match, detail=True)
    config.match.description_all = ["python"]
    assert reject_reason(job, config.match, detail=True) is None
    config.match.exclude_companies = ["示例"]
    assert reject_reason(job, config.match) == "公司在排除列表"


def test_url_canonicalization():
    job_id, url = canonical_job_url(
        "https://www.zhipin.com/job_detail/abc_123~.html?securityId=secret&lid=12"
    )
    assert job_id == "abc_123~"
    assert url == "https://www.zhipin.com/job_detail/abc_123~.html"


@pytest.mark.parametrize(
    "url",
    [
        "https://www.zhipin.com.evil.test/job_detail/abc.html",
        "https://evil.test/job_detail/a.html",
        "http://www.zhipin.com/job_detail/a.html",
        "https://www.zhipin.com/web/geek/chat",
        "javascript:alert(1)",
    ],
)
def test_untrusted_urls_rejected(url):
    with pytest.raises(ValueError):
        canonical_job_url(url)
