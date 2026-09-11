"""通过智联可见设置页逐岗更新招呼；只编辑本应用创建且内容未被改动的条目。"""

from __future__ import annotations

import asyncio
import json
import re
import time

from .browser import LayoutChanged, NeedsAttention

SETTINGS_URL = "https://i.zhaopin.com/im/greeting/setting"
ROWS = """nodes=>nodes.filter(e=>e.getClientRects().length).map(e=>({
  id:e.id,text:e.querySelector('.greeting-list__content')?.innerText.trim()||'',
  selected:!!e.querySelector('.greeting-list__radio-icon[src*="/radio-active-icon."]'),
  editable:!!e.querySelector('.greeting-list__action-icon[src*="/edit-icon."]')
}))"""


def validate_greeting(message):
    if not isinstance(message, str) or not message.strip():
        raise NeedsAttention("智联招呼内容为空，未发起投递")
    # Match the website textarea's UTF-16 maxlength, including emoji.
    if len(message.encode("utf-16-le")) // 2 > 500:
        raise NeedsAttention(
            "智联招呼超过网站 500 字上限，请缩短表达要求或模板后重试；未截断或投递"
        )
    return message.strip()


class ZhaopinGreetings:
    def __init__(self, adapter):
        self.adapter = adapter
        self.store = adapter.control.store
        self.page = None
        self.state = self.store.greeting_settings("zhaopin")
        self.started = False
        self.restoring = False
        self.prepared = None

    def save(self):
        self.store.save_greeting_settings("zhaopin", self.state)

    async def checkpoint(self):
        if not self.restoring:
            await self.adapter.control.checkpoint()

    async def wait(self, read, note, *, settle=False):
        deadline = time.monotonic() + min(15, self.adapter.config.browser.timeout_seconds)
        while time.monotonic() < deadline:
            # Finish reading back an already-started settings write before honoring
            # stop/pause, so cleanup does not race its in-flight website response.
            if not settle:
                await self.checkpoint()
            await self.adapter.gate(self.page)
            result = await read()
            if result:
                return result
            await asyncio.sleep(0.2)
        raise NeedsAttention(note)

    async def open(self):
        await self.checkpoint()
        if not self.page or self.page.is_closed():
            self.page = await self.adapter.session.context.new_page()
            self.adapter.session.owned_pages.append(self.page)
        await self.page.goto(SETTINGS_URL, wait_until="domcontentloaded")

        async def nav():
            return (
                await self.page.locator(".greeting-left-nav__item")
                .filter(has_text=re.compile(r"^\s*招呼语\s*$"))
                .is_visible()
            )

        await self.wait(nav, "智联聊天设置未加载，请检查登录状态")
        await self.click(
            self.page.locator(".greeting-left-nav__item").filter(
                has_text=re.compile(r"^\s*招呼语\s*$")
            )
        )

        async def ready():
            return await self.page.locator(".greeting-panel__tab--active").is_visible()

        await self.wait(ready, "智联招呼语设置未加载")

    async def rows(self):
        return await self.page.locator(".greeting-list__item").evaluate_all(ROWS)

    async def tab(self, category):
        await self.checkpoint()
        target = self.page.locator(".greeting-panel__tab").filter(
            has_text=re.compile(r"^\s*" + re.escape(category) + r"\s*$")
        )
        if await target.count() != 1:
            raise LayoutChanged("智联招呼语分类变化，请检查设置页")
        await self.click(target)

        async def active():
            return await self.page.locator(".greeting-panel__tab--active").inner_text() == category

        await self.wait(active, "智联招呼语分类未切换")

    async def selected(self):
        async def read():
            rows = [row for row in await self.rows() if row["selected"]]
            if len(rows) == 1:
                category = await self.page.locator(".greeting-panel__tab--active").inner_text()
                return {**rows[0], "category": category}
            return None

        return await self.wait(read, "未确认智联当前默认招呼，未修改设置或投递")

    def row(self, row_id):
        if not re.fullmatch(r"greeting-item-[a-zA-Z0-9_-]+", row_id):
            raise LayoutChanged("智联招呼条目标识无效")
        return self.page.locator(f".greeting-list__item[id='{row_id}']")

    async def click(self, button, *, row=None, value=None):
        """Bind settings writes to the observed row and editor through the actual click."""
        expected = json.dumps({"row": row, "value": value}, ensure_ascii=False)
        await button.evaluate(
            """e=>{
          const expected="""
            + expected
            + """;
          const row=expected.row?document.getElementById(expected.row.id):null;
          const editor=expected.value===null?null:document.querySelector('.phrase-modal textarea');
          e[Symbol.for('deliverdesk.actionGuard')]=()=>
            location.origin+location.pathname==='https://i.zhaopin.com/im/greeting/setting' &&
            (!expected.row || (row===document.getElementById(expected.row.id) && row?.querySelector('.greeting-list__content')?.innerText.trim()===expected.row.text)) &&
            (expected.value===null || (editor===document.querySelector('.phrase-modal textarea') && editor?.value===expected.value));
          e.addEventListener('click',event=>{
            if(!e[Symbol.for('deliverdesk.actionGuard')]()) {event.preventDefault();event.stopImmediatePropagation();}
          },{capture:true,once:true});
        }"""
        )
        # Selecting the saved default scrolls the whole settings page, leaving
        # its category tabs beneath the fixed site header. Nearest scrolling
        # keeps them covered; center the real control before the guarded click.
        await button.evaluate(
            "e=>{e.scrollIntoView({block:'center',inline:'nearest',behavior:'instant'});return true;}"
        )
        previous, stable = None, 0

        async def settled():
            nonlocal previous, stable
            state = await button.evaluate("""e=>{
              const r=e.getBoundingClientRect(),hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);
              return {box:[r.x,r.y,r.width,r.height],ready:!!(r.width&&r.height&&!e.disabled&&(hit===e||e.contains(hit)))};
            }""")
            if not state["ready"]:
                previous, stable = None, 0
                await button.evaluate(
                    "e=>{e.scrollIntoView({block:'center',inline:'nearest',behavior:'instant'});return true;}"
                )
                return False
            stable = stable + 1 if previous == state["box"] else 0
            previous = state["box"]
            return stable >= 2

        # The website can scroll to its default after loading the category.
        # Require a stable, unobstructed control before the single native click.
        await self.wait(settled, "智联设置控件仍在移动或被遮挡，未点击；请检查设置页")
        await button.click()

    async def prepare(self, job, message):
        message = validate_greeting(message)
        self.prepared = None
        if not self.started:
            # Recover a default left by an interrupted earlier run before changing it again.
            if self.state.get("restore"):
                await self.restore()
            await self.open()
            self.state["restore"] = await self.selected()
            self.save()
            self.started = True
        else:
            await self.open()
        await self.tab("自定义")
        rows = await self.rows()
        managed = self.state.get("managed")
        target = next((row for row in rows if managed and row["id"] == managed["id"]), None)
        if managed and not target:
            raise NeedsAttention("应用创建的智联招呼条目已消失，请检查登录账号和招呼设置；未投递")
        if target and (not target["editable"] or target["text"] != managed["text"]):
            raise NeedsAttention("应用创建的智联招呼已被修改，已停止以保留你的设置；未投递")
        if not target or target["text"] != message:
            await self.checkpoint()
            if target:
                await self.click(
                    self.row(target["id"]).locator(
                        '.greeting-list__action-icon[src*="/edit-icon."]'
                    ),
                    row=target,
                )
            else:
                await self.click(self.page.locator(".greeting-panel__add-btn"))
            editor = self.page.locator(".phrase-modal textarea")
            await editor.fill(message)
            if await editor.input_value() != message:
                raise NeedsAttention("智联招呼输入框内容与生成原文不一致，未保存或投递")
            await self.checkpoint()
            # Retain the pending text before the external save, so interrupted edits remain owned.
            self.state["pending"] = {
                "id": target["id"] if target else "",
                "text": message,
                "previous_ids": [row["id"] for row in rows],
            }
            self.save()
            await self.click(self.page.locator(".phrase-modal__btn--ok"), row=target, value=message)
            previous_ids = {row["id"] for row in rows}

            async def saved():
                if await editor.is_visible():
                    return None
                matches = [
                    row
                    for row in await self.rows()
                    if row["text"] == message
                    and row["editable"]
                    and (row["id"] == target["id"] if target else row["id"] not in previous_ids)
                ]
                return matches[0] if len(matches) == 1 else None

            target = await self.wait(saved, "未确认智联自定义招呼保存成功，未投递", settle=True)
            self.state["managed"] = {"id": target["id"], "text": message}
            self.state.pop("pending", None)
            self.save()
        await self.checkpoint()
        if not target["selected"]:
            await self.click(self.row(target["id"]).locator(".greeting-list__default"), row=target)

            async def activated():
                return any(
                    row["id"] == target["id"] and row["selected"] for row in await self.rows()
                )

            await self.wait(activated, "智联自定义招呼未设为默认，未投递", settle=True)
        self.prepared = (job.job_id, message, target["id"])
        await self.verify(job, message)
        # The detail page can cache its initial greeting; reload it only after the saved default is read back.
        description = job.description
        await self.adapter.inspect_job(job)
        if job.description != description:
            raise NeedsAttention("智联岗位描述在生成招呼后发生变化，未投递；请重新预览")

    async def verify(self, job, message):
        if not self.prepared or self.prepared[:2] != (job.job_id, message):
            raise NeedsAttention("当前岗位的智联招呼尚未保存核对，未投递")
        await self.open()
        selected = await self.selected()
        if (selected["id"], selected["text"], selected["category"]) != (
            self.prepared[2],
            message,
            "自定义",
        ):
            raise NeedsAttention("智联当前默认招呼与本岗位原文不一致，未投递")

    async def restore(self):
        original = self.state.get("restore")
        if not original:
            return
        self.restoring = True
        try:
            await self.open()
            pending = self.state.get("pending")
            if pending:
                await self.tab("自定义")
                matches = [
                    row
                    for row in await self.rows()
                    if row["editable"]
                    and row["text"] == pending["text"]
                    and (
                        row["id"] == pending["id"]
                        if pending["id"]
                        else row["id"] not in pending["previous_ids"]
                    )
                ]
                if len(matches) == 1:
                    self.state["managed"] = {"id": matches[0]["id"], "text": matches[0]["text"]}
                elif len(matches) > 1:
                    raise NeedsAttention("中断后无法唯一确认应用创建的智联招呼，请检查聊天设置")
                self.state.pop("pending", None)
                self.save()
                await self.open()
            current = await self.selected()
            managed = self.state.get("managed")
            restored_default = True
            if current["id"] == original["id"] and current["text"] == original["text"]:
                pass
            elif managed and current["id"] == managed["id"] and current["text"] == managed["text"]:
                await self.tab(original["category"])
                if original["id"] == managed["id"]:
                    await self.click(
                        self.row(managed["id"]).locator(
                            '.greeting-list__action-icon[src*="/edit-icon."]'
                        ),
                        row=managed,
                    )
                    await self.page.locator(".phrase-modal textarea").fill(original["text"])
                    await self.click(
                        self.page.locator(".phrase-modal__btn--ok"),
                        row=managed,
                        value=original["text"],
                    )

                    async def reverted():
                        return any(
                            row["id"] == original["id"] and row["text"] == original["text"]
                            for row in await self.rows()
                        )

                    await self.wait(reverted, "未确认运行前智联招呼原文已恢复，请检查聊天设置")
                    self.state["managed"] = {"id": original["id"], "text": original["text"]}
                    self.save()
                target = [
                    row
                    for row in await self.rows()
                    if row["id"] == original["id"] and row["text"] == original["text"]
                ]
                if len(target) != 1:
                    raise NeedsAttention("运行前的智联默认招呼已变化，请在聊天设置中手动恢复")
                if not target[0]["selected"]:
                    await self.click(
                        self.row(original["id"]).locator(".greeting-list__default"), row=target[0]
                    )
                await self.open()
                restored = await self.selected()
                if (restored["id"], restored["text"]) != (original["id"], original["text"]):
                    raise NeedsAttention("未确认智联默认招呼恢复成功，请检查聊天设置")
            else:
                restored_default = False
            # A different default chosen by the user is never overwritten.
            self.state.pop("restore", None)
            self.save()
            return (
                "已恢复运行前的智联默认招呼"
                if restored_default
                else "默认招呼已由你修改，保留当前网站设置"
            )
        finally:
            self.restoring = False
