"""智联招聘可见网页适配；依据 2026-09-11 的内嵌浏览器实测。"""

from __future__ import annotations

import json
import re
import time
from urllib.parse import parse_qs, urlsplit

from .browser import BossAdapter, LayoutChanged, NeedsAttention, SendResult
from .models import Job, canonical_job_url
from .zhaopin_greetings import ZhaopinGreetings

JOBS_URL = "https://www.zhaopin.com/jobs/?pageMode=search"
LOGIN_URL = "https://passport.zhaopin.com/login"
FILTERS = {
    "薪资待遇": "薪资",
    "学历要求": "学历",
    "工作经验": "经验",
    "公司性质": "公司性质",
    "融资阶段": "融资阶段",
    "公司规模": "公司人数",
    "求职类型": "工作性质",
    "职位类型": "职位类别",
    "公司行业": "公司行业",
}

CARD_STATE = r"""nodes => nodes.filter(e=>e.getClientRects().length).map(e=>{
  const text=s=>(e.querySelector(s)?.innerText||'').trim();
  const token=e.getAttribute('data-deliverdesk-zhaopin-card')||crypto.randomUUID();
  e.setAttribute('data-deliverdesk-zhaopin-card',token);
  return {token, title:text('.job-card__title-main'),company:text('.job-card__company-name'),
    salary:text('.job-card__salary'),location:text('.job-card__location'),
    signature:e.innerText.trim(),active:e.classList.contains('job-card--active')};
})"""

PANE_STATE = r"""e => {
  const text=s=>(e.querySelector(s)?.innerText||'').trim();
  return {title:text('.job-detail-summary__title-text'),
    company:text('.job-detail-summary__company-name'),
    url:e.querySelector('.job-company-info__view-all')?.href||'',
    loading:!!e.querySelector('.job-detail-modules__scroll--loading'),
    description:text('.job-detail-description'),
    token:document.querySelector('.job-card--active')?.getAttribute('data-deliverdesk-zhaopin-card')||''};
}"""

DETAIL_STATE = r"""e => {
  const text=s=>{const n=e.querySelector(s);return n?.getClientRects().length?(n.innerText||'').trim():'';};
  return {url:location.href,title:text('.summary-planes__title'),company:text('.company-info__name'),
    salary:text('.summary-planes__salary'),description:text('.describtion-card'),
    summary:text('.summary-planes__left'),
    action:text('.summary-planes__action button'),
    receipt:text('.deliver-greeting-modal__title'),greeting:text('.deliver-greeting-modal__content-text')};
}"""


class ZhaopinAdapter(BossAdapter):
    jobs_url = JOBS_URL

    def __init__(self, session, config, control):
        super().__init__(session, config, control)
        self.card_cache = {}
        self.keyword = None
        self.greetings = ZhaopinGreetings(self)
        self.selectors = {
            "profile": ".c-login__top__name",
            "search_input": ".query-sug__input",
            "cards": ".job-list-panel .job-card",
            "description": ".describtion-card",
            "apply": ".summary-planes__action button",
            "receipt": ".deliver-greeting-modal__title",
        } | config.selectors

    async def gate(self, page, *, allow_login=False):
        if page.is_closed():
            raise NeedsAttention("智联页面已关闭")
        url = urlsplit(page.url)
        if url.hostname and not (
            url.hostname == "zhaopin.com" or url.hostname.endswith(".zhaopin.com")
        ):
            raise NeedsAttention("页面已离开智联招聘，请检查浏览器")
        text = await page.locator("body").inner_text(timeout=3000)
        if not allow_login and url.hostname == "passport.zhaopin.com":
            raise NeedsAttention("智联登录已失效，请在求职浏览器重新登录")
        if not allow_login:
            challenges = page.locator('iframe[src*="captcha"], iframe[src*="verify"], .nc_wrapper')
            if any([await e.is_visible() for e in await challenges.all()]):
                raise NeedsAttention("智联要求安全验证，请在浏览器手动处理后重新运行")
            if re.search(
                r"(?m)^(?:安全验证|请完成验证|访问过于频繁.*|操作过于频繁.*|.*今日.*投递.*上限.*|.*投递次数.*用完.*)$",
                text,
            ):
                raise NeedsAttention("智联要求验证或已达到操作限制，请检查浏览器")

    async def logged_in(self, page):
        if urlsplit(page.url).hostname != "www.zhaopin.com":
            return False
        for item in await page.locator(self.selectors["profile"]).all():
            if await item.is_visible() and (await item.inner_text()).strip():
                return True
        return False

    async def wait_for(self, read, what, timeout=None):
        deadline = time.monotonic() + (timeout or self.config.browser.timeout_seconds)
        while time.monotonic() < deadline:
            await self.control.checkpoint()
            result = await read()
            if result:
                return result
            await self.control.sleep(0.25)
        raise LayoutChanged(what)

    async def login(self, timeout):
        if await self.logged_in(self.page):
            await self.gate(self.page)
            return
        await self.page.goto(JOBS_URL, wait_until="domcontentloaded")
        # Give a saved session time to hydrate before opening a login form.
        for _ in range(12):
            await self.control.checkpoint()
            if await self.logged_in(self.page):
                return
            await self.control.sleep(0.25)
        await self.page.goto(LOGIN_URL, wait_until="domcontentloaded")
        await self.page.bring_to_front()
        deadline = time.monotonic() + timeout
        switched = False
        while time.monotonic() < deadline:
            await self.control.checkpoint()
            await self.gate(self.page, allow_login=True)
            if await self.logged_in(self.page):
                return
            toggle = self.page.locator(".zppp-panel-normal-bar__img")
            if not switched and await toggle.is_visible():
                await toggle.click()
                switched = True
            await self.control.sleep(0.5)
        raise NeedsAttention("等待智联扫码登录超时，请在求职浏览器中完成登录")

    async def dismiss_search_tip(self):
        close = self.page.locator(".intention-tabs__tips-close")
        if await close.is_visible():
            await close.click()

    async def wait_ready(self):
        async def ready():
            await self.gate(self.page)
            return (
                await self.logged_in(self.page)
                and await self.page.locator(self.selectors["search_input"]).is_visible()
            )

        await self.wait_for(ready, "未检测到智联登录后的职位搜索页")

    async def begin_result_change(self):
        await self.page.locator("body").evaluate("""body=>{
          const key=Symbol.for('deliverdesk.zhaopinResults');
          body[key]?.observer.disconnect();
          const state={changed:false,at:performance.now()};
          state.observer=new MutationObserver(records=>{
            if(records.some(r=>r.type!=='attributes' &&
              (r.target.parentElement?.closest('.job-list-panel') || r.target.matches?.('.job-list-panel') ||
               [...r.addedNodes,...r.removedNodes].some(n=>n.matches?.('.job-list-panel') || n.querySelector?.('.job-list-panel'))))) {
              state.changed=true;state.at=performance.now();
            }
          });
          state.observer.observe(body,{subtree:true,childList:true,characterData:true});
          body[key]=state;
        }""")

    async def wait_results(self, *, changed=False):
        last = None
        stable = time.monotonic()

        async def ready():
            nonlocal last, stable
            await self.gate(self.page)
            cards = await self.card_states()
            signatures = [r["signature"] for r in cards]
            if signatures != last:
                last, stable = signatures, time.monotonic()
            if time.monotonic() - stable < 0.4:
                return False
            if changed and not await self.page.locator("body").evaluate("""e=>{
              const state=e[Symbol.for('deliverdesk.zhaopinResults')];
              return !!state?.changed && performance.now()-state.at>400;
            }"""):
                return False
            if cards and all(row["title"] and row["company"] for row in cards):
                return True
            text = await self.page.locator("body").inner_text()
            return bool(
                re.search(
                    r"暂无相关职位|没有找到相关职位|没有找到符合条件|暂无搜索结果|暂无职位|没有更多了",
                    text,
                )
            )

        await self.wait_for(ready, "智联列表未加载，未将超时当作空结果")

    async def search(self, keyword):
        self.card_cache.clear()
        await self.page.goto(JOBS_URL, wait_until="domcontentloaded")
        await self.wait_ready()
        await self.page.locator(self.selectors["search_input"]).fill(keyword)
        await self.begin_result_change()
        await (
            await self.unique_visible(self.page.locator(".query-sug__button"), "智联搜索按钮")
        ).click()

        async def searched():
            await self.gate(self.page)
            return (
                parse_qs(urlsplit(self.page.url).query).get("kw") == [keyword]
                and await self.page.locator(self.selectors["search_input"]).input_value() == keyword
            )

        await self.wait_for(searched, "未确认智联搜索词生效")
        self.keyword = keyword
        await self.wait_results(changed=True)
        await self.dismiss_search_tip()
        await self.select_city(self.config.search.city)
        for label, value in self.config.search.filters.items():
            if label == "工作区域":
                await self.select_city(value)
            else:
                await self.select_filter(label, value)
        await self.wait_results()

    async def select_city(self, city):
        if city == "全国":
            raise NeedsAttention("智联当前城市选择器不提供“全国”，请在工作台选择具体城市")
        await self.dismiss_search_tip()
        root = self.page.locator(".filter-region-box")
        current = (await root.locator(".filter-select-box__label").inner_text()).strip()
        if current == city:
            return
        await root.locator(".filter-select-box__trigger").click()
        dialog = await self.wait_for(lambda: self.visible_dialog(), "智联地区选择器未打开")
        await dialog.locator('input[placeholder="搜索城市名/区县"]').fill(city)

        async def option():
            matches = []
            for item in await dialog.locator(".s-option__label").all():
                text = (await item.inner_text()).strip()
                if await item.is_visible() and text.split("-")[-1] == city:
                    matches.append(item)
            if len(matches) > 1:
                raise LayoutChanged("智联存在多个同名地区，请选择更明确的城市或区县")
            return matches[0] if matches else None

        selected = await self.wait_for(option, f"智联未提供唯一的地区：{city}")
        await self.begin_result_change()
        await selected.click()

        async def applied():
            await self.gate(self.page)
            return (
                await root.locator(".filter-select-box__label").inner_text()
            ).strip() == city and not await dialog.is_visible()

        await self.wait_for(applied, f"未确认智联地区 {city} 生效")
        await self.wait_results(changed=True)

    async def visible_dialog(self):
        matches = [e for e in await self.page.locator(".s-dialog").all() if await e.is_visible()]
        if len(matches) > 1:
            raise LayoutChanged("智联出现多个地区弹窗")
        return matches[0] if matches else None

    async def select_filter(self, label, value):
        name = FILTERS.get(label)
        if not name:
            raise NeedsAttention(f"智联暂不支持筛选：{label}")
        roots = self.page.locator(".filter-select-box").filter(
            has=self.page.locator(".filter-select-box__label").filter(
                has_text=re.compile(f"^{re.escape(name)}$")
            )
        )
        root = await self.unique_visible(roots, f"智联{name}筛选")
        token = await root.evaluate(
            "e=>{const token=crypto.randomUUID();e.setAttribute('data-deliverdesk-filter',token);return token;}"
        )
        root = self.page.locator(f'[data-deliverdesk-filter="{token}"]')
        await root.locator(".filter-select-box__trigger").click()
        value = "全部" if name == "经验" and value == "不限" else value
        path = [part.strip() for part in re.split(r"[>＞]", value)]
        if not all(path):
            raise NeedsAttention(f"智联{name}请按“大类 > 子类”填写完整路径")
        for index, part in enumerate(path):
            scope = root.locator(".filter-select-box__column").nth(index) if len(path) > 1 else root
            await self.wait_for(
                lambda scope=scope: scope.is_visible(), f"智联{name}第 {index + 1} 级菜单未加载"
            )
            option = scope.locator(".filter-select-box__item").filter(
                has=self.page.locator(".filter-select-box__item-text").filter(
                    has_text=re.compile(f"^{re.escape(part)}$")
                )
            )
            selected = await self.unique_visible(option, f"智联{name}第 {index + 1} 级选项“{part}”")
            has_child = "filter-select-box__item--has-child" in (
                await selected.get_attribute("class") or ""
            )
            if index < len(path) - 1:
                if not has_child:
                    raise NeedsAttention(f"智联{name}的“{part}”没有下一级选项")
                await selected.click()
            elif has_child:
                raise NeedsAttention(f"智联{name}请填写完整路径，如“大类 > 子类”，或“大类 > 不限”")
        if "filter-select-box__item--selected" in (await selected.get_attribute("class") or ""):
            await root.locator(".filter-select-box__trigger").click()
            return
        await self.begin_result_change()
        await selected.click()
        # Re-open and read the site's selected class, rather than infer success
        # from a click or a matching word elsewhere in a job card.
        await root.locator(".filter-select-box__trigger").click()

        async def applied():
            return "filter-select-box__item--selected" in (
                await selected.get_attribute("class") or ""
            )

        await self.wait_for(applied, f"未确认智联{name}筛选生效")
        await root.locator(".filter-select-box__trigger").click()
        await self.wait_results(changed=True)

    async def card_states(self):
        return await self.page.locator(self.selectors["cards"]).evaluate_all(CARD_STATE)

    async def collect(self):
        await self.gate(self.page)
        results = []
        rows = await self.card_states()
        for row in rows[: self.config.search.max_jobs]:
            await self.control.checkpoint()
            if not row["title"] or not row["company"]:
                raise LayoutChanged("智联职位卡片缺少岗位或公司，未猜测职位身份")
            cached = self.card_cache.get(row["token"])
            if cached and cached[0] == row["signature"]:
                results.append(cached[1])
                continue
            card = self.page.locator(f'.job-card[data-deliverdesk-zhaopin-card="{row["token"]}"]')
            if (await card.inner_text()).strip() != row["signature"]:
                raise LayoutChanged("智联职位列表在读取期间变化，请重新预览")
            pane = self.page.locator(".job-detail-modules")
            before = await pane.evaluate(PANE_STATE) if await pane.is_visible() else {}
            if before.get("token") != row["token"]:
                await card.locator(".job-card__title-main").click()

            async def selected(pane=pane, row=row, before=before):
                await self.gate(self.page)
                if not await pane.is_visible():
                    return None
                state = await pane.evaluate(PANE_STATE)
                if state["loading"]:
                    return None
                if (
                    state["token"] != row["token"]
                    or state["title"] != row["title"]
                    or state["company"] != row["company"]
                ):
                    return None
                if before.get("token") != row["token"] and state["url"] == before.get("url"):
                    return None
                return state if state["url"] else None

            state = await self.wait_for(selected, "智联选中卡片与详情未一致刷新，未使用旧职位链接")
            job_id, url = canonical_job_url(state["url"])
            if not job_id.startswith("zhaopin:"):
                raise LayoutChanged("智联详情链接的平台不一致")
            job = Job(
                job_id=job_id,
                url=url,
                platform="zhaopin",
                **{key: row[key] for key in ("title", "company", "salary", "location")},
            )
            self.live_urls[job_id] = url
            self.card_cache[row["token"]] = (row["signature"], job)
            results.append(job)
        return results

    async def more(self, previous):
        await self.control.checkpoint()
        await self.gate(self.page)
        before = await self.card_states()
        status = self.page.locator(".job-list-panel__status")
        if await status.is_visible() and "没有更多了" in await status.inner_text():
            return False
        if not before:
            return False
        last = self.page.locator(
            f'.job-card[data-deliverdesk-zhaopin-card="{before[-1]["token"]}"]'
        )
        await last.scroll_into_view_if_needed()
        box = await last.bounding_box()
        if box:
            await self.page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        await self.page.mouse.wheel(0, 750)
        tokens = {row["token"] for row in before}
        deadline = time.monotonic() + min(8, self.config.browser.timeout_seconds)
        while time.monotonic() < deadline:
            await self.control.checkpoint()
            await self.gate(self.page)
            if any(row["token"] not in tokens for row in await self.card_states()):
                return True
            if await status.is_visible() and "没有更多了" in await status.inner_text():
                return False
            await self.control.sleep(0.3)
        return False

    def check_identity(self, state, job):
        try:
            job_id, _ = canonical_job_url(state["url"])
        except ValueError as error:
            raise LayoutChanged("智联详情跳转到了其他页面") from error
        if job_id != job.job_id or state["title"] != job.title or state["company"] != job.company:
            raise LayoutChanged("智联职位 ID、公司或标题与目标不一致")

    async def detail_state(self, job):
        await self.gate(self.detail)
        state = await self.detail.locator("body").evaluate(DETAIL_STATE)
        self.check_identity(state, job)
        return state

    async def inspect_job(self, job):
        if job.platform != "zhaopin" or not job.job_id.startswith("zhaopin:"):
            raise LayoutChanged("目标不是智联职位")
        if not self.detail or self.detail.is_closed():
            self.detail = await self.session.context.new_page()
            self.session.owned_pages.append(self.detail)
        await self.detail.goto(job.url, wait_until="domcontentloaded")

        async def ready():
            await self.gate(self.detail)
            state = await self.detail.locator("body").evaluate(DETAIL_STATE)
            return state if state["title"] and state["company"] and state["description"] else None

        state = await self.wait_for(ready, "智联独立详情未显示完整的职位与公司")
        self.check_identity(state, job)
        job.salary = state["salary"]
        job.description = state["description"]
        job.contacted = state["action"] in {"继续沟通", "已投递", "已申请"}
        lines = state["summary"].splitlines()
        job.experience = next((s for s in lines if re.search(r"经验不限|\d.*年", s)), "")
        job.education = next(
            (
                s
                for s in lines
                if re.fullmatch(r"初中及以下|高中|中专/中技|大专|本科|硕士|MBA/EMBA|博士", s)
            ),
            "",
        )
        return job

    async def preflight(self, job):
        await self.control.checkpoint()
        state = await self.detail_state(job)
        if state["action"] in {"继续沟通", "已投递", "已申请"}:
            return False
        if state["action"] != "立即投递":
            raise LayoutChanged("智联没有唯一可用的立即投递按钮")
        if state["receipt"]:
            raise LayoutChanged("智联仍有上一次投递结果弹窗，请先处理")
        await self.unique_visible(self.detail.locator(self.selectors["apply"]), "智联立即投递")
        return True

    async def greet(self, job, message):
        custom = self.config.message.mode != "platform"
        if custom:
            await self.greetings.verify(job, message)
        if not await self.preflight(job):
            return SendResult("contacted", "智联显示已投递或已沟通，未重复提交")
        button = await self.unique_visible(
            self.detail.locator(self.selectors["apply"]), "智联立即投递"
        )
        expected = json.dumps(
            {"url": job.url, "title": job.title, "company": job.company}, ensure_ascii=False
        )
        await button.evaluate(
            """e=>{
          const expected="""
            + expected
            + """;
          const title=document.querySelector('.summary-planes__title'),company=document.querySelector('.company-info__name');
          e[Symbol.for('deliverdesk.actionGuard')]=()=>
            location.origin+location.pathname===expected.url &&
            title===document.querySelector('.summary-planes__title') && company===document.querySelector('.company-info__name') &&
            title?.innerText.trim()===expected.title && company?.innerText.trim()===expected.company && e.innerText.trim()==='立即投递';
          e.addEventListener('click',event=>{
            if(!e[Symbol.for('deliverdesk.actionGuard')]()) {
              event.preventDefault();event.stopImmediatePropagation();
            }
          },{capture:true,once:true});
        }"""
        )
        await button.click()
        deadline = time.monotonic() + self.config.browser.timeout_seconds
        while time.monotonic() < deadline:
            await self.control.checkpoint()
            state = await self.detail_state(job)
            if (
                state["receipt"] == "已向对方发送简历和打招呼语"
                and state["action"] == "继续沟通"
                and state["greeting"]
            ):
                if custom and state["greeting"] != message:
                    return SendResult(
                        "partial",
                        "智联已投递简历，但实际招呼与本岗位原文不一致；请到网站核对，不会补发。实际招呼："
                        + state["greeting"],
                    )
                result = SendResult(
                    "sent",
                    "智联确认已向该职位发送在线简历，招呼原文核对一致"
                    if custom
                    else "智联确认已向该职位发送在线简历和平台招呼",
                    state["greeting"],
                )
                close = self.detail.locator(".deliver-greeting-modal__btn--secondary")
                if await close.is_visible():
                    await close.click()
                return result
            await self.control.sleep(0.3)
        return SendResult(
            "unknown", "已点击智联投递，但未取得该职位的明确成功回执；停止且不会自动重投"
        )

    async def verify_delivery(self, job, expected_message=None):
        state = await self.detail_state(job)
        if expected_message is not None:
            # Resume feedback alone cannot prove that a particular greeting was sent.
            return bool(
                state["receipt"] == "已向对方发送简历和打招呼语"
                and state["action"] == "继续沟通"
                and state["greeting"] == expected_message
            )
        if state["action"] in {"已投递", "已申请"} or (
            state["receipt"] == "已向对方发送简历和打招呼语" and state["greeting"]
        ):
            return True
        # A persistent "continue chat" button alone does not prove resume
        # delivery. Check the site's own application history by public job ID.
        feedback = await self.session.context.new_page()
        self.session.owned_pages.append(feedback)
        await feedback.goto(
            "https://i.zhaopin.com/schedule?status=viewed", wait_until="domcontentloaded"
        )

        async def ready():
            await self.gate(feedback)
            tabs = feedback.locator(".st-nav").get_by_text("投递成功", exact=True)
            return tabs if await tabs.is_visible() else None

        tab = await self.wait_for(ready, "智联求职反馈未加载，请在网页核对投递记录")
        await tab.click()

        async def receipt():
            await self.gate(feedback)
            if (
                not await feedback.locator(".st-nav li.on")
                .get_by_text("投递成功", exact=True)
                .is_visible()
            ):
                return False
            rows = await feedback.locator(".ji-item").evaluate_all("""nodes=>nodes.filter(e=>e.getClientRects().length).map(e=>({
              url:e.querySelector('.ji-item-info-jobName')?.href||'',
              title:e.querySelector('.ji-item-info-jobName')?.innerText.trim()||'',
              company:e.querySelector('.ji-item-info-companyName')?.innerText.trim()||'',
              status:e.querySelector('.ji-item-status p')?.innerText.trim()||''
            }))""")
            for row in rows:
                try:
                    job_id, _ = canonical_job_url(row["url"])
                except ValueError:
                    continue
                if (
                    job_id == job.job_id
                    and row["title"] == job.title
                    and row["company"] == job.company
                    and row["status"] == "成功投递"
                ):
                    return True
            return False

        try:
            return await self.wait_for(receipt, "智联当前投递记录中未找到该职位的成功回执")
        except LayoutChanged:
            return False
