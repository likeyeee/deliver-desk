from pathlib import Path

import pytest

from boss_cli.config import Config
from boss_cli.models import Job
from boss_cli.storage import Store


@pytest.fixture
def config():
    cfg = Config()
    cfg.search.keywords = ["AI"]
    cfg.search.city = "全国"
    cfg.search.max_scrolls = 0
    cfg.run.action_delay = (0, 0)
    cfg.run.job_delay = (0, 0)
    cfg.run.cooldown_seconds = (0, 0)
    cfg.browser.timeout_seconds = 3
    return cfg


@pytest.fixture
def job():
    return Job(
        "abc123",
        "AI应用工程师",
        "示例科技",
        "https://www.zhipin.com/job_detail/abc123.html",
        location="上海",
        salary="20-30K·14薪",
        experience="1-3年",
        education="本科",
        description="Python 大模型应用开发",
    )


@pytest.fixture
def store(tmp_path: Path):
    db = Store(tmp_path / "state")
    db.create_run("test-run", "send")
    yield db
    db.close()
