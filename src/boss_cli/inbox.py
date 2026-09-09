"""Walk the website's rendered inbox, then verify each conversation's public job popup."""

import re
import time
from urllib.parse import urljoin, urlsplit

from .browser import JOBS_URL, LayoutChanged, NeedsAttention
from .models import Job

# Selectors come from the public chat page's rendered components. No site stores,
# framework internals, message APIs, cookies or authentication parameters are read.
INBOX_ROWS = r"""rows => {
  const text = e => (e?.innerText || '').replace(/\s+/g, ' ').trim();
  const visible = e => {
    const r=e.getBoundingClientRect(), box=e.closest('.user-list-content')?.getBoundingClientRect();
    return !!e.getClientRects().length && getComputedStyle(e).display !== 'none'
      && (!box || (r.bottom > box.top + 2 && r.top < box.bottom - 2));
  };
  return rows.filter(visible).map(row => {
    if (!row.dataset.deliverdeskInboxId) row.dataset.deliverdeskInboxId = crypto.randomUUID();
    const spans = [...row.querySelectorAll('.title-box .name-box > span')];
    const name = text(row.querySelector('.name-text'));
    const company = text(spans.find(e => !e.matches('.name-text')));
    const preview = text(row.querySelector('.last-msg-text'));
    const hasDraft = !!row.querySelector('.last-msg .draft');
    return {token: row.dataset.deliverdeskInboxId, name, company, preview, hasDraft,
      unread: !!text(row.querySelector('.notice-badge')),
      signature: JSON.stringify([name, company, preview, hasDraft])};
  }).filter(row => row.name && row.company);
}"""


class InboxReader:
    def __init__(self, adapter):
        self.adapter = adapter
        self.page = adapter.page
        self.control = adapter.control
        self.config = adapter.config
        self.selectors = adapter.selectors
        self.list = None

    async def ensure_empty_editor(self):
        for editor in await self.page.locator(self.selectors["chat_editor"]).all():
            if not await editor.is_visible():
                continue
            value = (
                await editor.inner_text()
                if await editor.get_attribute("contenteditable") == "true"
                else await editor.input_value()
            )
            if value.strip():
                raise NeedsAttention("浏览器输入框有未发送草稿，请先处理后再开启自动回复")

    async def open(self):
        await self.control.checkpoint()
        address = urlsplit(self.page.url)
        if address.hostname != "www.zhipin.com" or address.path != "/web/geek/chat":
            # A newly created native view has no loaded document yet. Querying
            # its editor queues JS indefinitely until the first navigation.
            if self.page.url in {"", "about:blank"}:
                await self.page.goto(JOBS_URL, wait_until="domcontentloaded")
            else:
                await self.ensure_empty_editor()
            deadline = time.monotonic() + self.config.browser.timeout_seconds
            while time.monotonic() < deadline:
                await self.control.checkpoint()
                await self.adapter.gate(self.page)
                destinations = set()
                for link in await self.page.locator('a[href*="/web/geek/chat"]').all():
                    if not await link.is_visible():
                        continue
                    target = urljoin(self.page.url, await link.get_attribute("href") or "")
                    parsed = urlsplit(target)
                    if (
                        parsed.scheme == "https"
                        and parsed.netloc == "www.zhipin.com"
                        and parsed.path == "/web/geek/chat"
                    ):
                        destinations.add(parsed._replace(query="", fragment="").geturl())
                if len(destinations) == 1:
                    await self.page.goto(destinations.pop(), wait_until="domcontentloaded")
                    break
                await self.control.sleep(0.25)
            else:
                raise LayoutChanged("无法确认网站的唯一消息入口，请先在浏览器打开消息页")
        await self.adapter.gate(self.page)
        await self.ensure_empty_editor()
        root = self.page.locator(self.selectors["inbox_root"])
        all_label = root.locator(".label-list .label-name").filter(has_text=re.compile(r"^全部$"))
        deadline = time.monotonic() + self.config.browser.timeout_seconds
        while time.monotonic() < deadline:
            await self.control.checkpoint()
            await self.adapter.gate(self.page)
            if (
                await root.count() == 1
                and await root.is_visible()
                and await all_label.count() == 1
                and await all_label.is_visible()
            ):
                break
            await self.control.sleep(0.25)
        else:
            raise LayoutChanged("会话列表区域尚未加载或结构已变化，请查看浏览器；未自动发送")
        search = root.locator('input[placeholder*="联系人"]')
        if await search.count() == 1 and (await search.input_value()).strip():
            await search.fill("")
        label = await self.adapter.unique_visible(all_label, "全部会话选项")
        if not await label.locator("..").evaluate("e => e.classList.contains('selected')"):
            await label.click()
        deadline = time.monotonic() + self.config.browser.timeout_seconds
        while time.monotonic() < deadline:
            await self.control.checkpoint()
            await self.adapter.gate(self.page)
            lists = self.page.locator(self.selectors["inbox_list"])
            if await lists.count() == 1 and await lists.is_visible():
                rows = self.page.locator(self.selectors["inbox_rows"])
                footer = lists.locator(".boss-list-footer .finished")
                finished = await footer.count() == 1 and "没有更多" in await footer.inner_text()
                if await rows.count() or finished:
                    self.list = lists
                    await self.list.evaluate("e => { e.scrollTop = 0; return true; }")
                    await self.control.sleep(0.25)
                    return
            text = await root.inner_text()
            if re.search(r"暂无(?:消息|沟通|联系人)|当前没有", text):
                return
            await self.control.sleep(0.25)
        raise LayoutChanged("会话列表尚未加载或结构已变化，请查看浏览器；未自动发送")

    async def rows(self):
        if self.list is None:
            return []
        await self.control.checkpoint()
        await self.adapter.gate(self.page)
        return await self.page.locator(self.selectors["inbox_rows"]).evaluate_all(INBOX_ROWS)

    async def next_page(self):
        if self.list is None:
            return False
        await self.control.checkpoint()
        before = await self.list.evaluate(
            "e => ({top:e.scrollTop,height:e.scrollHeight,size:e.clientHeight})"
        )
        footer = self.list.locator(".boss-list-footer .finished")
        finished = await footer.count() == 1 and "没有更多" in await footer.inner_text()
        if before["top"] + before["size"] >= before["height"] - 3 and finished:
            return False
        await self.list.evaluate(
            "e => { e.scrollBy(0, Math.max(200, e.clientHeight * .8)); return true; }"
        )
        await self.control.sleep(0.35)
        return True

    async def select(self, entry):
        await self.control.checkpoint()
        await self.adapter.gate(self.page)
        await self.ensure_empty_editor()
        token = entry["token"]
        if not re.fullmatch(r"[A-Za-z0-9-]+", token):
            raise LayoutChanged("会话条目标记无效")
        row = self.page.locator(f'[data-deliverdesk-inbox-id="{token}"]')
        current = await row.evaluate_all(INBOX_ROWS)
        if len(current) != 1 or current[0]["signature"] != entry["signature"]:
            raise LayoutChanged("会话列表已更新，下次检查将重新定位")
        if entry["hasDraft"]:
            raise LayoutChanged("此会话已有网页草稿，保留给本人处理")
        self.adapter.chat_bindings.pop(self.page, None)
        await row.click()
        name = self.page.locator(self.selectors["inbox_name"])
        company = self.page.locator(self.selectors["inbox_company"])
        position = self.page.locator(self.selectors["inbox_position"])
        deadline = time.monotonic() + self.config.browser.timeout_seconds
        while time.monotonic() < deadline:
            await self.control.checkpoint()
            await self.adapter.gate(self.page)
            if await name.count() == 1 and await name.is_visible():
                selected = await row.count() == 1 and await row.evaluate(
                    "e => e.classList.contains('selected')"
                )
                if selected and (await name.inner_text()).strip() == entry["name"]:
                    if (
                        await company.count() == 1
                        and await company.is_visible()
                        and (await company.inner_text()).strip()
                        and await position.count() == 1
                        and await position.is_visible()
                        and (await position.inner_text()).strip()
                    ):
                        break
            await self.control.sleep(0.2)
        else:
            raise LayoutChanged("联系人、公司或职位信息尚未加载完整或与所选会话不一致，未自动回复")
        company = await self.adapter.unique_visible(
            self.page.locator(self.selectors["inbox_company"]), "会话公司"
        )
        position = await self.adapter.unique_visible(
            self.page.locator(self.selectors["inbox_position"]), "会话职位"
        )
        company_text = (await company.inner_text()).strip()
        title = re.sub(r"^(?:兼职|全职|实习)[·•\s]+", "", (await position.inner_text()).strip())
        if not title or not company_text:
            raise LayoutChanged("联系人缺少可核对的公司或职位，未自动回复")
        # A list may visually abbreviate a company. Require its visible prefix and the
        # exact selected row/name, then independently verify the full company/job popup.
        listed_company = re.sub(r"(?:\.{3}|…)+$", "", entry["company"]).strip()
        abbreviated = listed_company != entry["company"]
        same_company = (
            company_text.startswith(listed_company)
            if abbreviated
            else company_text == listed_company
        )
        if not listed_company or not same_company:
            raise LayoutChanged("聊天页公司与选中条目不一致，未自动回复")
        job = Job("", title, company_text, "", recruiter=entry["name"], contacted=True)
        editor = await self.adapter.unique_visible(
            self.page.locator(self.selectors["chat_editor"]), "聊天输入框"
        )
        await self.adapter.chat_scope_via_job_popup(self.page, editor, job, discover=True)
        await self.ensure_empty_editor()
        return job, await self.adapter.read_conversation(self.page, job)
