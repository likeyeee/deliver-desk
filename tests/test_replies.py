import json

import pytest

from boss_cli.config import Config
from boss_cli.conversation import conversation


def context(value="请问你有什么项目经验？"):
    return conversation([{"role": "user", "content": value, "supported": True, "id": "m1"}], 20)


def prepare(store, job):
    store.save_job(job)
    store.mark_contacted(job, "test-run")
    return store.save_reply(
        "draft-1", job.job_id, context(), "deepseek-v4-flash", "我可以介绍项目。"
    )


def test_old_config_adds_nonsecret_llm_defaults_and_validates():
    data = Config().model_dump()
    data.pop("llm")
    assert Config.model_validate(data).llm.model == "deepseek-v4-flash"
    for values in [
        {"api_key": "never-export"},
        {"temperature": float("nan")},
        {"system_prompt": " "},
        {"model": "bad model"},
        {"context_messages": 51},
    ]:
        with pytest.raises(ValueError):
            Config.model_validate({"llm": values})


def test_reply_requires_existing_contact_and_keeps_greeting_history(store, job):
    store.save_job(job)
    with pytest.raises(ValueError, match="已经沟通"):
        store.reply_job(job.job_id)
    prepare(store, job)
    before = store.history()
    store.reserve_reply("draft-1", "test-run", "这是修改过的回复", 20)
    assert store.reply("draft-1")["status"] == "sending"
    assert store.attempts_today() == 1
    store.finish_reply("draft-1", "sent", "已送达")
    assert store.history() == before
    assert store.reply_history()[0]["message"] == "这是修改过的回复"
    with pytest.raises(ValueError, match="已处理"):
        store.reserve_reply("draft-1", "test-run", "重复发送", 20)
    with pytest.raises(ValueError, match="已经回复"):
        store.save_reply("draft-2", job.job_id, context(), "deepseek-v4-pro", "另一条")


def test_unknown_reply_blocks_all_contexts_until_explicit_resolution(store, job):
    prepare(store, job)
    store.reserve_reply("draft-1", "test-run", "回复", 20)
    store.recover()
    assert store.reply("draft-1")["status"] == "unknown"
    with pytest.raises(ValueError, match="待核对"):
        store.save_reply("draft-2", job.job_id, context("新问题"), "deepseek-v4-flash", "新回复")
    with pytest.raises(ValueError):
        store.resolve_reply("draft-1", "not_sent", "")
    store.resolve_reply("draft-1", "not_sent", "核对浏览器，消息仍在草稿框中")
    result = store.save_reply("draft-2", job.job_id, context(), "deepseek-v4-pro", "重新生成")
    assert result["id"] == "draft-1"
    assert result["status"] == "draft"
    assert store.attempts_today() == 1


def test_reply_and_initial_greeting_share_daily_limit(store, job):
    prepare(store, job)
    store.event("test-run", "INFO", "send_reserved", "模拟其他职位投递")
    with pytest.raises(ValueError, match="今日发送上限"):
        store.reserve_reply("draft-1", "test-run", "不能发送", 1)
    assert store.reply("draft-1")["status"] == "draft"
    assert store.attempts_today() == 1


def test_fingerprint_ignores_model_context_limit_but_detects_new_message():
    messages = [
        {"role": "user", "content": str(i), "supported": True, "id": str(i)} for i in range(30)
    ]
    assert conversation(messages, 2)["fingerprint"] == conversation(messages, 20)["fingerprint"]
    assert (
        conversation(messages[:-1], 20)["fingerprint"] != conversation(messages, 20)["fingerprint"]
    )
    assert not conversation([], 20)["canReply"]
    assert not conversation([{"role": "assistant", "content": "已经回复", "supported": True}], 20)[
        "canReply"
    ]
    assert not conversation([{"role": "user", "content": "图片", "supported": False}], 20)[
        "canReply"
    ]
    assert "api_key" not in json.dumps(Config().model_dump())
