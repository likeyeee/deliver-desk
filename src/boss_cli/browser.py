from __future__ import annotations

import json
import re
import secrets
import time
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Locator, Page, Response, async_playwright

from .config import Config
from .control import Controller
from .conversation import MESSAGE_SCRIPT, conversation
from .models import Job, canonical_job_url
from .storage import private_dir

JOBS_URL = "https://www.zhipin.com/web/geek/jobs"
LOGIN_URL = "https://www.zhipin.com/web/user/?ka=header-login"
FILTER_PARAMS = {
    "薪资待遇": ("salary", "sel-job-rec-salary-"),
    "工作经验": ("experience", "sel-job-rec-exp-"),
    "学历要求": ("degree", "sel-job-rec-degree-"),
    "公司规模": ("scale", "sel-job-rec-scale-"),
    "公司行业": ("industry", "sel-industry-"),
}

# Text/role locators are preferred; these fallbacks are configurable via `selectors`.
SELECTORS = {
    "profile": 'a[href*="/web/geek/recommend"]',
    "search_input": 'input[placeholder="搜索职位、公司"]',
    "city_trigger": ".city-label, .city-select-label, .city-name, .city-area-select, .condition-city, .filter-city",
    "city_dialog": ".city-select-dialog",
    "city_current": ".cur-city-label, .city-label",
    "filter_root": ".condition-filter-select",
    "description": ".job-sec-text, .job-description, .job-detail-description",
    "detail_salary": ".name .salary",
    "recruiter": ".job-boss-info h2, .boss-name, .boss-info h2, .recruiter-name",
    "chat_scope": '.dialog-container, .chat-conversation, .chat-content, .chat-window, [role="dialog"]',
    "chat_editor": '#chat-input, .input-area[contenteditable="true"], textarea.input-area, [contenteditable="true"][role="textbox"], textarea[placeholder*="消息"], [contenteditable="true"]',
    "chat_send": ".send-message",
    "outgoing_messages": ".message-item.item-myself .text-content, .message-item.is-self .text, .message-item.is-self .message-text, .message-item.is-me .text, .message-item.is-me .message-text, .message-item.send .text",
    "inbox_root": ".chat-user",
    "inbox_list": ".chat-user .user-list-content",
    "inbox_rows": ".chat-user .user-list .friend-content:not(.drawer)",
    "inbox_name": ".user-info .base-info .name-text",
    "inbox_company": ".user-info .base-info > span:not(.base-title)",
    "inbox_position": ".chat-position-content .position-name",
}

GATE_TEXT = re.compile(
    r"(?m)^(?:安全验证|请完成安全验证|请完成下方验证|请拖动.{0,40}|访问过于频繁.{0,60}|"
    r"操作过于频繁.{0,60}|您的访问.{0,40}异常.{0,40}|账号异常.{0,60}|"
    r".*今日.*沟通.*上限.*|.*今日.*打招呼.*上限.*|.*沟通次数.*已用完.*)$"
)


class NeedsAttention(Exception):
    """A user must handle authentication, validation, or a site limit."""


class LayoutChanged(Exception):
    """Do not guess a consequential click when the DOM no longer matches."""


class ConversationChanged(LayoutChanged):
    """New messages invalidated a draft before its send button was clicked."""


@dataclass
class SendResult:
    status: str
    note: str
    message: str | None = None


def clean_error(error: BaseException) -> str:
    text = str(error).split("Call log:")[0]
    text = re.sub(r"(https?://[^\s?]+)\?[^\s]+", r"\1?[已隐藏参数]", text)
    return re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", text)[:700]


CARD_SCRIPT = r"""anchors => {
  const results = new Map();
  const jobId = a => a.href.match(/\/job_detail\/([A-Za-z0-9_~\-]+)\.html/)[1];
  const visible = e => !!(e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden');
  for (const anchor of anchors) {
    if (!visible(anchor) || !/\/job_detail\/[A-Za-z0-9_~\-]+\.html/.test(anchor.href)) continue;
    const id = jobId(anchor);
    if (results.has(id) || !anchor.innerText.trim() || /查看更多信息/.test(anchor.innerText)) continue;
    let card = anchor.parentElement;
    for (let depth = 0; card && depth < 7; depth++, card = card.parentElement) {
      const links = [...card.querySelectorAll('a[href*="/job_detail/"]')].filter(a => /\/job_detail\/[A-Za-z0-9_~\-]+\.html/.test(a.href));
      if (new Set(links.map(jobId)).size > 1) { card = null; break; }
      if (card.querySelector('a[href*="/gongsi/"]')) break;
    }
    if (!card || card.tagName === 'BODY') continue;
    const txt = card.innerText;
    const company = card.querySelector('a[href*="/gongsi/"]');
    const tags = [...card.querySelectorAll('li, .tag-list span, .job-labels span')].map(e => e.innerText.trim()).filter(Boolean);
    const salaryNode = card.querySelector('[class*="salary"]');
    const salary = salaryNode?.innerText || (txt.match(/[\d\uE000-\uF8FF]+(?:\.[\d\uE000-\uF8FF]+)?\s*[-~至]\s*[\d\uE000-\uF8FF]+(?:\.[\d\uE000-\uF8FF]+)?\s*(?:[kK千万]|元\/天|元\/时)(?:[·\s][\d\uE000-\uF8FF]+薪)?|面议/) || [''])[0];
    const location = card.querySelector('.company-location, .job-area, .job-location, .area-district, [class*="job-area"]')?.innerText.trim() || '';
    results.set(id, {url: anchor.href, title: anchor.innerText.trim(), company: company?.innerText.trim() || '',
      salary, location, experience: tags.find(t=>/经验不限|应届|在校|\d.*年/.test(t)) || '',
      education: tags.find(t=>/学历不限|本科|大专|硕士|博士|高中|初中|中专/.test(t)) || '',
      tags: tags.join(' / '), contacted: /已沟通|继续沟通/.test(txt)});
  }
  return [...results.values()];
}"""

CSS_PATH = """e => {
  const parts=[];
  while(e && e.nodeType===1 && e.tagName!=='HTML') {
    const tag=e.tagName.toLowerCase();
    if(e.id) {parts.unshift('#'+CSS.escape(e.id));break;}
    const siblings=[...e.parentElement.children].filter(n=>n.tagName===e.tagName);
    parts.unshift(tag+':nth-of-type('+(siblings.indexOf(e)+1)+')');e=e.parentElement;
  }
  return parts.join(' > ');
}"""

# Keep a verified identity tied to the actual editor and identity DOM nodes.
# Message/receipt updates are deliberately outside the watched identity roots.
CHAT_BINDING = r"""scope => {
  const options=__OPTIONS__, key=Symbol.for('deliverdesk.chatIdentity');
  const editor=document.querySelector(options.editor), control=document.querySelector(options.control);
  if(!editor || !control || !scope.contains(editor) || !scope.contains(control))
    throw Error('核对期间会话结构发生变化');
  const norm=e=>(e.innerText || '').replace(/\s+/g,' ').trim();
  const history='.chat-record, .chat-message, .im-list, .message-list, #messages';
  const dynamic=e=>e.contains(editor) || e.matches(history) || !!e.querySelector(history);
  let card=control;
  while(card && card!==scope && !norm(card).includes(options.title)) card=card.parentElement;
  if(!card || card===scope || dynamic(card)) throw Error('无法单独核对会话职位卡片');
  const companies=[...scope.querySelectorAll('*')].filter(e=>
    e.getClientRects().length && norm(e).includes(options.company) &&
    ![...e.children].some(child=>norm(child).includes(options.company)) &&
    !e.closest(history) && !editor.contains(e));
  if(!companies.length) throw Error('无法单独核对会话公司');
  let roots=[card,...companies].map(e=>{
    while(e.parentElement && e.parentElement!==scope && !dynamic(e.parentElement)) e=e.parentElement;
    return e;
  });
  roots=[...new Set(roots)].filter(e=>!roots.some(other=>other!==e && other.contains(e)));
  editor[key]?.dispose();
  let valid=true, checking=true;
  const address=location.href;
  const relevant=records=>records.some(r=>r.type!=='attributes' || !['class','style'].includes(r.attributeName));
  const observer=new MutationObserver(records=>{if(relevant(records)) valid=false;});
  roots.forEach(e=>observer.observe(e,{subtree:true,childList:true,characterData:true,attributes:true}));
  const input=event=>{
    const target=event.target instanceof Element ? event.target : event.target?.parentElement;
    const send=target?.closest('button,a,[role="button"],.send-message');
    if(editor.contains(target) || (checking && control.contains(target)) ||
       (send && scope.contains(send) && norm(send)==='发送')) return;
    valid=false;
  };
  document.addEventListener('pointerdown',input,true);
  document.addEventListener('keydown',input,true);
  const binding={
    token:options.token,
    check() {
      if(relevant(observer.takeRecords())) valid=false;
      return valid && location.href===address && scope.isConnected && editor.isConnected &&
        control.isConnected && scope.contains(editor) && scope.contains(control) &&
        roots.every(e=>e.isConnected && scope.contains(e));
    },
    confirm() {checking=false;return this.check();},
    dispose() {
      observer.disconnect();document.removeEventListener('pointerdown',input,true);
      document.removeEventListener('keydown',input,true);
    }
  };
  Object.defineProperty(editor,key,{value:binding,configurable:true});
  return binding.check();
}"""


class BrowserSession:
    def __init__(self, config: Config, directory: Path):
        self.config, self.directory = config, directory
        self.playwright = None
        self.browser = None
        self.context = None
        self.page: Page | None = None
        self.owned_pages: list[Page] = []
        self.failure_report: Path | None = None

    async def __aenter__(self):
        self.playwright = await async_playwright().start()
        try:
            cfg = self.config.browser
            if cfg.cdp_url:
                self.browser = await self.playwright.chromium.connect_over_cdp(
                    cfg.cdp_url, timeout=cfg.timeout_seconds * 1000
                )
                if not self.browser.contexts:
                    raise NeedsAttention("CDP 浏览器没有可用上下文")
                self.context = next(
                    (
                        c
                        for c in self.browser.contexts
                        if any(
                            urlsplit(p.url).hostname
                            == (
                                "www.zhaopin.com"
                                if self.config.platform == "zhaopin"
                                else "www.zhipin.com"
                            )
                            for p in c.pages
                        )
                    ),
                    self.browser.contexts[0],
                )
            else:
                profile = private_dir(
                    self.directory
                    / ("profile-zhaopin" if self.config.platform == "zhaopin" else "profile")
                )
                self.context = await self.playwright.chromium.launch_persistent_context(
                    str(profile),
                    channel="chrome" if cfg.channel == "chrome" else None,
                    headless=cfg.headless,
                    locale="zh-CN",
                    viewport={"width": 1360, "height": 900},
                )
            self.context.set_default_timeout(cfg.timeout_seconds * 1000)
            self.context.set_default_navigation_timeout(cfg.timeout_seconds * 1000)
            self.page = await self.context.new_page()
            self.owned_pages.append(self.page)
            return self
        except BaseException:
            await self.__aexit__(None, None, None)
            raise

    async def __aexit__(self, _kind, error, _traceback):
        if isinstance(error, (NeedsAttention, LayoutChanged, PlaywrightError)):
            with suppress(OSError, PlaywrightError):
                await self.capture_failure(error)
        if self.config.browser.cdp_url:
            for page in self.owned_pages:
                if not page.is_closed():
                    with suppress(PlaywrightError):
                        await page.close()
        elif self.context:
            with suppress(PlaywrightError):
                await self.context.close()
        if self.playwright:
            await self.playwright.stop()

    async def capture_failure(self, error: BaseException):
        """Keep useful evidence before closing our pages; never save cookies or raw HTML."""
        page = next((p for p in reversed(self.owned_pages) if not p.is_closed()), None)
        if page is None:
            return
        directory = private_dir(self.directory / "last-error")
        url = urlsplit(page.url)
        data = {
            "error": clean_error(error),
            "url": f"{url.scheme}://{url.netloc}{url.path}" if url.netloc else page.url,
            "query": {
                k: v for k, v in parse_qs(url.query).items() if k in {"query", "city", "code"}
            },
        }
        if getattr(error, "locator_spec", None):
            data["locator"] = error.locator_spec
        # A bounded structural report makes new chat layouts diagnosable without
        # exporting message text, raw HTML, cookie values, or URL parameters.
        with suppress(PlaywrightError, LayoutChanged):
            data["controls"] = await page.locator(
                'textarea, [contenteditable="true"], [class*="message-item"], [class*="chat-record"]'
            ).evaluate_all("""nodes => nodes.slice(0, 30).map(e => ({
                tag:e.tagName, class:e.className, id:e.id,
                placeholder:e.getAttribute('placeholder'),
                ancestors:[e.parentElement,e.parentElement?.parentElement,e.parentElement?.parentElement?.parentElement]
                    .filter(Boolean).map(p=>({tag:p.tagName,class:p.className})),
                children:[...e.querySelectorAll('*')].slice(0,20).map(n=>({tag:n.tagName,class:n.className}))
            }))""")
        try:
            screenshot = directory / "page.png"
            await page.screenshot(path=str(screenshot), timeout=3000)
            screenshot.chmod(0o600)
            data["screenshot"] = str(screenshot)
        except (PlaywrightError, OSError):
            data["screenshot_error"] = "页面持续跳转或关闭，未能保存截图"
        try:
            report = directory / "diagnostics.json"
            report.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            report.chmod(0o600)
            self.failure_report = report
        except OSError:
            pass


class BossAdapter:
    jobs_url = JOBS_URL

    def __init__(self, session: BrowserSession, config: Config, control: Controller):
        self.session, self.config, self.control = session, config, control
        self.page = session.page
        self.detail: Page | None = None
        self.detail_popups: list[Page] = []
        self.selectors = SELECTORS | config.selectors
        self.live_urls: dict[str, str] = {}
        self.security_errors: dict[Page, str] = {}
        self.navigation_times: dict[Page, float] = {}
        self.chat_bindings: dict[Page, tuple[str, str, str]] = {}
        self.watch_page(self.page)

    def watch_page(self, page: Page):
        # Security redirects can be gone by the next DOM poll. Remember them even if the
        # site returns to /web/geek/jobs or its home page a moment later.
        page.on(
            "framenavigated",
            lambda frame: (
                self.observe_navigation(page, frame.url, changed=True)
                if frame == page.main_frame
                else None
            ),
        )
        page.on("response", lambda response: self.observe_response(page, response))

    def observe_navigation(self, page: Page, address: str, *, changed=False):
        if changed:
            self.navigation_times[page] = time.monotonic()
        url = urlsplit(address)
        if url.hostname == "www.zhipin.com" and url.path.startswith("/web/passport/zp/security"):
            codes = parse_qs(url.query).get("code", [])
            code = codes[0] if codes and codes[0].isdigit() else "未知"
            self.security_errors[page] = (
                f"BOSS 安全检查拦截（code={code}）；页面跳转会清空搜索输入。"
                "请先在浏览器手动处理网站验证，再重新运行；本次不再尝试搜索或发送"
            )

    async def observe_response(self, page: Page, response: Response):
        url = urlsplit(response.url)
        if url.hostname != "www.zhipin.com" or not url.path.startswith(
            ("/wapi/zpgeek/search/", "/wapi/zpgeek/pc/recommend/job/", "/wapi/zpgeek/job/")
        ):
            return
        try:
            data = await response.json()
            code = data.get("code") if isinstance(data, dict) else None
            if code in (36, 37, "36", "37"):
                self.security_errors[page] = (
                    f"BOSS 返回环境/安全检查异常（code={code}）；"
                    "请在浏览器手动处理网站验证，再重新运行；本次不再尝试搜索或发送"
                )
        except (PlaywrightError, ValueError):
            pass

    async def gate(self, page: Page):
        if page.is_closed():
            raise NeedsAttention("浏览器页面已关闭")
        self.observe_navigation(page, page.url)
        if self.security_errors:
            raise NeedsAttention(next(iter(self.security_errors.values())))
        host = urlsplit(page.url).hostname
        if host and host != "www.zhipin.com":
            raise NeedsAttention("页面已离开 BOSS 直聘，请检查浏览器")
        text = await page.locator("body").inner_text(timeout=3000)
        match = GATE_TEXT.search(text)
        challenge = page.locator(
            'iframe[src*="captcha"], iframe[src*="verify"], .geetest_panel:visible'
        )
        for item in await challenge.all():
            if await item.is_visible():
                raise NeedsAttention("网站要求验证，请在浏览器手动完成后重新运行")
        if match:
            raise NeedsAttention(f"网站暂停操作：{match[0][:100]}")
        if re.search(r"扫码登录|短信登录|登录后继续", text) and not await self.logged_in(page):
            raise NeedsAttention("登录已失效，请运行 boss login")

    async def logged_in(self, page: Page) -> bool:
        for locator in await page.locator(self.selectors["profile"]).all():
            if await locator.is_visible() and (await locator.inner_text()).strip() not in {
                "",
                "推荐",
            }:
                return True
        return False

    async def login(self, timeout: float):
        screenshot = self.session.directory / "login.png"
        with suppress(OSError):
            screenshot.unlink(missing_ok=True)
        if urlsplit(self.page.url).hostname == "www.zhipin.com" and await self.logged_in(self.page):
            await self.gate(self.page)
            await self.page.bring_to_front()
            return
        await self.page.goto(LOGIN_URL, wait_until="domcontentloaded")
        await self.page.bring_to_front()
        deadline = time.monotonic() + timeout
        stable_since = None
        blank_since = None
        next_capture = time.monotonic() + 1
        while time.monotonic() < deadline:
            await self.control.checkpoint()
            if self.page.is_closed():
                raise NeedsAttention("登录页面已关闭")
            if urlsplit(self.page.url).path != "/web/user/":
                # A saved QR code becomes misleading as soon as its page is gone.
                with suppress(OSError):
                    screenshot.unlink(missing_ok=True)
            if self.security_errors:
                raise NeedsAttention(next(iter(self.security_errors.values())))
            if self.page.url == "about:blank":
                blank_since = blank_since or time.monotonic()
                if time.monotonic() - blank_since >= 3:
                    raise NeedsAttention(
                        "登录页意外跳到 about:blank，已停止并移除过期二维码截图；"
                        "这不是普通的扫码超时，请检查浏览器环境"
                    )
            else:
                blank_since = None
            # Human login/CAPTCHA is allowed here; never solve or dismiss it automatically.
            if (
                await self.logged_in(self.page)
                and urlsplit(self.page.url).hostname == "www.zhipin.com"
                and urlsplit(self.page.url).path
                in {"/", "/web/user/", "/web/geek/jobs", "/web/geek/recommend"}
            ):
                stable_since = stable_since or time.monotonic()
                stable_since = max(stable_since, self.navigation_times.get(self.page, 0))
                if time.monotonic() - stable_since >= 3:
                    return
            else:
                stable_since = None
                if (
                    time.monotonic() >= next_capture
                    and urlsplit(self.page.url).path == "/web/user/"
                ):
                    with suppress(PlaywrightError, OSError):
                        await self.page.screenshot(path=str(screenshot), timeout=3000)
                        screenshot.chmod(0o600)
                    next_capture = time.monotonic() + 30
            await self.control.sleep(0.5)
        raise NeedsAttention("等待登录超时；请重新运行 boss login")

    async def unique_visible(self, locator: Locator, what: str) -> Locator:
        visible = [item for item in await locator.all() if await item.is_visible()]
        if len(visible) != 1:
            raise LayoutChanged(
                f"{what}匹配到 {len(visible)} 个可见元素，请运行 boss diagnose 检查"
            )
        return visible[0]

    async def wait_ready(self):
        deadline = time.monotonic() + self.config.browser.timeout_seconds
        while time.monotonic() < deadline:
            await self.control.checkpoint()
            await self.gate(self.page)
            if (
                await self.logged_in(self.page)
                and urlsplit(self.page.url).path == "/web/geek/jobs"
                and await self.page.locator(self.selectors["search_input"]).is_visible()
            ):
                return
            await self.control.sleep(0.25)
        raise NeedsAttention(
            "未检测到登录后的搜索页，请先运行 boss login；若已登录可用 boss diagnose 检查"
        )

    async def select_filter(self, label: str, value: str):
        await self.control.checkpoint()
        await self.gate(self.page)
        if label == "城市" and await self.current_city_is(value):
            return
        triggers = self.page.get_by_text(label, exact=True)
        visible = [item for item in await triggers.all() if await item.is_visible()]
        if label == "城市" and not visible:
            triggers = self.page.locator(self.selectors["city_trigger"])
            visible = [item for item in await triggers.all() if await item.is_visible()]
            # Nested city fallbacks can point at the same trigger. Select the smallest DOM element.
            if len(visible) > 1:
                texts = [(len((await item.inner_text()).strip()), item) for item in visible]
                visible = [min(texts, key=lambda pair: pair[0])[1]]
        if not visible:
            raise LayoutChanged(f"找不到筛选入口：{label}")
        trigger = visible[0] if len(visible) == 1 else await self.unique_visible(triggers, label)
        parent_path = await trigger.locator("..").evaluate(CSS_PATH)
        roots = self.page.locator(self.selectors["filter_root"]).filter(has=trigger)
        root_path = await roots.evaluate(CSS_PATH) if await roots.count() == 1 else parent_path
        if value in (await trigger.inner_text()).strip() and label == "城市":
            return
        await trigger.click()
        await self.control.delay(self.config.run.action_delay)
        option_scope = self.page.locator(root_path)
        if label == "城市":
            option_scope = await self.unique_visible(
                self.page.locator(self.selectors["city_dialog"]), "城市选择弹窗"
            )
            # The live dialog initially shows only popular cities. Other cities are
            # behind the visible alphabet groups; inspect those tabs through the UI.
            if not any(
                [
                    await item.is_visible()
                    for item in await option_scope.get_by_text(value, exact=True).all()
                ]
            ):
                for group in ("ABCDE", "FGHJ", "KLMN", "PQRST", "WXYZ"):
                    tabs = option_scope.get_by_text(group, exact=True)
                    visible_tabs = [item for item in await tabs.all() if await item.is_visible()]
                    if len(visible_tabs) != 1:
                        continue
                    await visible_tabs[0].click()
                    await self.control.delay(self.config.run.action_delay)
                    await self.gate(self.page)
                    if any(
                        [
                            await item.is_visible()
                            for item in await option_scope.get_by_text(value, exact=True).all()
                        ]
                    ):
                        break
        option = await self.unique_visible(
            option_scope.get_by_text(value, exact=True), f"{label}选项“{value}”"
        )
        option_ka = await option.evaluate("e => e.closest('[ka]')?.getAttribute('ka') || ''")
        await option.click()
        await self.control.delay(self.config.run.action_delay)
        await self.gate(self.page)
        if label == "城市" and await self.current_city_is(value):
            return
        param, prefix = FILTER_PARAMS.get(label, ("", ""))
        if prefix and option_ka.startswith(prefix) and option_ka[len(prefix) :].isdigit():
            code = option_ka[len(prefix) :]
            deadline = time.monotonic() + self.config.browser.timeout_seconds
            while True:
                selected = parse_qs(urlsplit(self.page.url).query).get(param, [])
                values = set(",".join(selected).split(",")) if selected else set()
                if code in values or (code == "0" and not values):
                    return
                if time.monotonic() >= deadline:
                    raise LayoutChanged(f"无法确认“{label}={value}”生效，任务已停止以避免错筛")
                await self.control.sleep(0.2)
                await self.gate(self.page)
        # Fallback for customized layouts without an option identifier.
        # Modern BOSS keeps the chosen value in a chip beside .current-select.
        if value not in await self.page.locator(root_path).inner_text():
            raise LayoutChanged(f"无法确认“{label}={value}”生效，任务已停止以避免错筛")

    async def current_city_is(self, value: str) -> bool:
        for item in await self.page.locator(self.selectors["city_current"]).all():
            if await item.is_visible():
                text = re.sub(r"[\s\uE000-\uF8FF]", "", await item.inner_text())
                if text == value:
                    return True
        return False

    async def search(self, keyword: str):
        await self.control.checkpoint()
        await self.gate(self.page)
        # BOSS exposes search as a normal bookmarkable page route. Opening that route
        # avoids a redundant reset/search navigation and follows auto-zhipin's approach.
        await self.page.goto(
            f"{JOBS_URL}?{urlencode({'query': keyword})}", wait_until="domcontentloaded"
        )
        await self.wait_ready()
        field = self.page.locator(self.selectors["search_input"])
        deadline = time.monotonic() + self.config.browser.timeout_seconds
        while (
            parse_qs(urlsplit(self.page.url).query).get("query") != [keyword]
            or await field.input_value() != keyword
        ):
            if time.monotonic() >= deadline:
                raise LayoutChanged("无法确认搜索关键词已生效")
            await self.gate(self.page)
            await self.control.sleep(0.25)
        # Initial hydration replaces the city placeholder with the actual city.
        # Wait for real results (or the site's empty state) before locating filters.
        await self.wait_results()
        await self.select_filter("城市", self.config.search.city)
        for label, value in self.config.search.filters.items():
            await self.select_filter(label, value)
        await self.wait_results()

    async def wait_results(self):
        deadline = time.monotonic() + self.config.browser.timeout_seconds
        while time.monotonic() < deadline:
            await self.control.checkpoint()
            await self.gate(self.page)
            if await self.collect():
                return
            text = await self.page.locator("body").inner_text()
            if re.search(r"暂无相关职位|没有找到相关职位|没有找到符合条件|暂无搜索结果", text):
                return
            await self.control.sleep(0.3)
        raise LayoutChanged("职位列表未加载或卡片结构变化；未将超时误判为零条结果")

    async def collect(self) -> list[Job]:
        rows = await self.page.locator('a[href*="/job_detail/"]').evaluate_all(CARD_SCRIPT)
        jobs = []
        for row in rows:
            raw_url = row.pop("url")
            try:
                job_id, url = canonical_job_url(raw_url)
            except ValueError:
                continue
            if not row["title"] or not row["company"]:
                continue
            self.live_urls[job_id] = raw_url
            jobs.append(Job(job_id=job_id, url=url, **row))
        return jobs

    async def more(self, previous: set[str]) -> bool:
        await self.control.checkpoint()
        await self.gate(self.page)
        collected = await self.collect()
        # Wheel over the list so the modern independently scrolling left panel loads more cards.
        if collected:
            last = collected[-1]
            link = (
                self.page.locator(f'a[href*="/job_detail/{last.job_id}.html"]')
                .filter(has_text=last.title)
                .first
            )
            await link.scroll_into_view_if_needed()
            box = await link.bounding_box()
            if box:
                await self.page.mouse.move(
                    box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
                )
        await self.page.mouse.wheel(0, 750)
        await self.control.delay(self.config.run.action_delay)
        deadline = time.monotonic() + min(5, self.config.browser.timeout_seconds)
        while time.monotonic() < deadline:
            await self.gate(self.page)
            jobs = await self.collect()
            if any(job.job_id not in previous for job in jobs):
                return True
            await self.control.sleep(0.25)
        next_button = self.page.get_by_role("button", name="下一页", exact=True).or_(
            self.page.get_by_role("link", name="下一页", exact=True)
        )
        for candidate in await next_button.all():
            if (
                await candidate.is_visible()
                and await candidate.is_enabled()
                and await candidate.get_attribute("aria-disabled") != "true"
            ):
                await candidate.click()
                await self.wait_results()
                return any(job.job_id not in previous for job in await self.collect())
        return False

    async def inspect_job(self, job: Job) -> Job:
        await self.control.checkpoint()
        # Reuse the detail renderer and close only the previous job's auxiliary tabs.
        for page in self.detail_popups:
            if not page.is_closed():
                await page.close()
        self.session.owned_pages[:] = [p for p in self.session.owned_pages if not p.is_closed()]
        if not self.detail or self.detail.is_closed():
            self.detail = await self.session.context.new_page()
            self.watch_page(self.detail)
            self.session.owned_pages.append(self.detail)
            self.detail.on("popup", self.register_popup)
        self.detail_popups = []
        self.chat_bindings.clear()
        # Follow a link observed in the result card; use its temporary parameters only in memory.
        raw_url = self.live_urls.get(job.job_id, job.url)
        canonical_job_url(raw_url)
        await self.detail.goto(raw_url, wait_until="domcontentloaded")
        deadline = time.monotonic() + self.config.browser.timeout_seconds
        while time.monotonic() < deadline:
            await self.control.checkpoint()
            await self.gate(self.detail)
            heading = self.detail.get_by_role("heading", name="职位描述", exact=True)
            if await heading.is_visible():
                break
            await self.control.sleep(0.25)
        else:
            raise LayoutChanged("详情页未显示职位描述")
        try:
            detail_id, _ = canonical_job_url(self.detail.url)
        except ValueError as error:
            raise LayoutChanged("详情页发生意外跳转") from error
        if detail_id != job.job_id:
            raise LayoutChanged("打开的详情页与目标职位 ID 不一致")
        body = await self.detail.locator("body").inner_text()
        if job.title not in body or job.company not in body:
            raise LayoutChanged("详情中的职位或公司与搜索结果不一致")
        desc = self.detail.locator(self.selectors["description"])
        visible_desc = [item for item in await desc.all() if await item.is_visible()]
        job.description = (
            await visible_desc[0].inner_text()
            if visible_desc
            else await heading.locator("..").inner_text()
        )
        # Observed on the live site: list salary glyphs may be encoded while the standalone
        # detail's .name .salary contains ordinary digits. Prefer that visible source.
        salary = self.detail.locator(self.selectors["detail_salary"])
        visible_salary = [item for item in await salary.all() if await item.is_visible()]
        if len(visible_salary) == 1:
            job.salary = (await visible_salary[0].inner_text()).strip()
        recruiter = self.detail.locator(self.selectors["recruiter"])
        for item in await recruiter.all():
            if await item.is_visible():
                job.recruiter = (await item.inner_text()).strip()
                break
        job.contacted = await self.contact_control(self.detail, existing=True) is not None
        return job

    def register_popup(self, page: Page):
        # A popup belongs to the source page. Never claim unrelated tabs the user opens in CDP.
        if page in self.session.owned_pages:
            return
        self.detail_popups.append(page)
        self.session.owned_pages.append(page)
        self.watch_page(page)
        page.on("popup", self.register_popup)

    async def contact_control(self, page: Page, *, existing=False) -> Locator | None:
        labels = ["继续沟通", "已沟通"] if existing else ["立即沟通", "打招呼"]
        matches = []
        for label in labels:
            selector = page.get_by_role("link", name=label, exact=True).or_(
                page.get_by_role("button", name=label, exact=True)
            )
            matches.extend([item for item in await selector.all() if await item.is_visible()])
        if len(matches) > 1:
            raise LayoutChanged("详情中有多个沟通按钮，无法确定发送目标")
        return matches[0] if matches else None

    async def preflight(self, job: Job):
        await self.control.checkpoint()
        await self.gate(self.detail)
        current_id, _ = canonical_job_url(self.detail.url)
        if current_id != job.job_id:
            raise LayoutChanged("发送前页面已切换到其他职位")
        if await self.contact_control(self.detail, existing=True):
            return False
        if not await self.contact_control(self.detail):
            raise LayoutChanged("找不到唯一的立即沟通按钮")
        return True

    async def greet(self, job: Job, message: str) -> SendResult:
        """Caller MUST reserve the delivery before entry. Never retry this method automatically."""
        await self.control.checkpoint()
        await self.gate(self.detail)
        current_id, _ = canonical_job_url(self.detail.url)
        if current_id != job.job_id:
            raise LayoutChanged("沟通前目标职位已变化")
        if await self.contact_control(self.detail, existing=True):
            return SendResult("contacted", "网页已显示沟通过，本次未点击")
        button = await self.contact_control(self.detail)
        if button is None:
            return SendResult("unknown", "沟通入口在发送前变化，请人工核对")
        await button.click()
        deadline = time.monotonic() + self.config.browser.timeout_seconds
        contact_confirmed = False
        message_confirmed = False
        chat_ready = False
        chat_page = self.detail
        while time.monotonic() < deadline:
            await self.control.checkpoint()
            candidates = [self.detail, *self.detail_popups]
            for page in candidates:
                if page.is_closed() or page.url == "about:blank":
                    continue
                await self.gate(page)
                if page == self.detail and "/job_detail/" in page.url:
                    current_id, _ = canonical_job_url(page.url)
                    if current_id != job.job_id:
                        raise LayoutChanged("等待沟通回执时页面已切换到其他职位")
                success = page.get_by_text(re.compile(r"^(?:打招呼成功[！!]?|已成功打招呼)$"))
                if any([await e.is_visible() for e in await success.all()]):
                    contact_confirmed = True
                message_success = page.get_by_text("消息已发送", exact=True)
                if any([await e.is_visible() for e in await message_success.all()]):
                    message_confirmed = True
                if page == self.detail and await self.contact_control(page, existing=True):
                    contact_confirmed = True
                # The current site can open a conversation without sending any text.
                # A verified editor is enough to proceed with the configured message;
                # a changed contact button alone is never proof of a delivered message.
                try:
                    await self.chat_target(page, job)
                except LayoutChanged:
                    pass
                else:
                    contact_confirmed = True
                    chat_ready = True
                    chat_page = page
            if (
                self.config.message.mode != "platform"
                and contact_confirmed
                and not chat_ready
                and "/job_detail/" in self.detail.url
            ):
                # The standalone detail opens a small composer without a job link
                # or delivery receipts. Continue through the site's public Messages
                # page, where both the job identity and outgoing receipt are visible.
                try:
                    chat_page = await self.open_full_chat(job)
                except LayoutChanged as error:
                    return SendResult(
                        "partial", f"沟通已建立，完整会话未确认：{clean_error(error)}"
                    )
                chat_ready = True
            if (
                self.config.message.mode == "platform" and (contact_confirmed or message_confirmed)
            ) or (self.config.message.mode != "platform" and chat_ready):
                break
            await self.control.sleep(0.3)
        if not contact_confirmed and not message_confirmed:
            return SendResult(
                "unknown", "已点击沟通，但未看到明确成功回执；请在网站核对，不会自动重发"
            )
        if self.config.message.mode == "platform":
            if message_confirmed:
                return SendResult("sent", "网页明确提示消息已发送；使用平台当前招呼语")
            return SendResult("contacted", "网页已确认建立沟通；未确认文字消息发送，不计入已发送数")
        if not chat_ready:
            return SendResult("partial", "沟通已建立，但无法核对聊天输入框及收件人；未发送配置文字")
        try:
            return await self.send_custom(chat_page, job, message)
        except (LayoutChanged, PlaywrightError) as error:
            return SendResult("partial", f"沟通已建立，自定义消息未确认：{clean_error(error)}")

    async def open_full_chat(self, job: Job) -> Page:
        # Unread badges change the accessible name (e.g. "消息 7"). Use the
        # visible site's actual destination; never click through the composer
        # overlay or guess a route when that public entry is missing.
        addresses = set()
        for link in await self.detail.locator('a[href*="/web/geek/chat"]').all():
            if not await link.is_visible():
                continue
            address = urljoin(self.detail.url, await link.get_attribute("href") or "")
            parts = urlsplit(address)
            if (
                parts.scheme == "https"
                and parts.netloc == "www.zhipin.com"
                and parts.path == "/web/geek/chat"
            ):
                addresses.add(address)
        if len(addresses) != 1:
            raise LayoutChanged("无法确认唯一的网站消息入口，请检查网页是否加载完整")
        address = addresses.pop()
        chat = await self.session.context.new_page()
        self.session.owned_pages.append(chat)
        self.detail_popups.append(chat)
        self.watch_page(chat)
        chat.on("popup", self.register_popup)
        await chat.goto(address, wait_until="domcontentloaded")
        deadline = time.monotonic() + self.config.browser.timeout_seconds
        selected = False
        last_error = None
        while time.monotonic() < deadline:
            await self.control.checkpoint()
            await self.gate(chat)
            if not selected:
                # The site may have already selected this exact job. Verify it
                # before treating the list entry and chat header as two contacts.
                try:
                    await self.chat_target(chat, job)
                    return chat
                except LayoutChanged:
                    pass
                companies = chat.get_by_text(job.company, exact=True)
                choices = [item for item in await companies.all() if await item.is_visible()]
                if len(choices) > 1 and job.recruiter.strip():
                    name = job.recruiter.splitlines()[0].strip()
                    options = json.dumps({"company": job.company, "name": name})
                    matches = []
                    for item in choices:
                        if await item.evaluate(
                            r"""company => {
                              const {company: text, name} = __OPTIONS__;
                              const visible = e => !!e.getClientRects().length;
                              const exact = (e, value) => visible(e) && e.innerText?.trim() === value
                                && !Array.from(e.children).some(c => c.innerText?.trim() === value);
                              let row = company.parentElement;
                              for (let depth = 0; row && depth < 6; depth++, row = row.parentElement) {
                                if (['BODY', 'HTML'].includes(row.tagName)) break;
                                const nodes = Array.from(row.querySelectorAll('*'));
                                // Never borrow a name from another company row or
                                // from a conversation's message preview/editor.
                                if (nodes.filter(e => exact(e, text)).length > 1) break;
                                if (row.querySelector('textarea, [contenteditable="true"]')) break;
                                if (nodes.some(e => exact(e, name)
                                  && (e.compareDocumentPosition(company) & Node.DOCUMENT_POSITION_FOLLOWING)))
                                  return true;
                              }
                              return false;
                            }""".replace("__OPTIONS__", options)
                        ):
                            matches.append(item)
                    choices = matches
                if len(choices) > 1:
                    raise LayoutChanged("同公司联系人中仍有多个同名招聘者，请人工核对；未发送文字")
                if len(choices) == 1:
                    # Selecting a conversation is read-only. The subsequent public
                    # job popup must still match the exact job ID before typing.
                    await choices[0].click()
                    selected = True
            if selected:
                try:
                    await self.chat_target(chat, job)
                    return chat
                except LayoutChanged as error:
                    last_error = error
            await self.control.sleep(0.3)
        raise LayoutChanged(
            f"完整聊天页无法确认目标：{clean_error(last_error) if last_error else '未找到唯一公司联系人'}"
        )

    async def conversation_declined(self, scope: Locator) -> bool:
        refusal = scope.get_by_text(
            re.compile(r"^(?:感谢您的关注[，,]\s*暂时不合适该职位[。！!]?|对方已拒绝沟通)$")
        )
        return any([await item.is_visible() for item in await refusal.all()])

    async def read_conversation(self, page: Page, job: Job) -> dict:
        await self.control.checkpoint()
        await self.gate(page)
        _, scope = await self.chat_target(page, job)
        result = conversation(
            await scope.evaluate(MESSAGE_SCRIPT), self.config.llm.context_messages
        )
        if await self.conversation_declined(scope):
            result.update(canReply=False, reason="招聘方已拒绝沟通，请人工处理")
        return result

    async def send_custom(
        self, page: Page, job: Job, message: str, *, expected_context: str | None = None
    ) -> SendResult:
        # Sending a template is a second message if BOSS sent its default greeting on contact.
        # Never select a chat by list position or by recruiter name alone.
        editor, scope = await self.chat_target(page, job)
        if await self.conversation_declined(scope):
            return SendResult("contacted", "招聘方已回复不合适/拒绝沟通，未追加自定义消息")
        if (
            await editor.inner_text()
            if await editor.get_attribute("contenteditable") == "true"
            else await editor.input_value()
        ).strip():
            raise LayoutChanged("输入框已有草稿，请先人工处理")
        outgoing = scope.locator(self.selectors["outgoing_messages"]).filter(has_text=message)
        before = len(
            [item for item in await outgoing.all() if (await item.inner_text()).strip() == message]
        )
        await self.control.checkpoint()
        await self.gate(page)
        editor, scope = await self.chat_target(page, job)
        existing = (
            await editor.inner_text()
            if await editor.get_attribute("contenteditable") == "true"
            else await editor.input_value()
        )
        if existing.strip():
            raise LayoutChanged("等待期间出现了新的草稿，未覆盖输入框")
        if await self.conversation_declined(scope):
            return SendResult("contacted", "招聘方已回复不合适/拒绝沟通，未追加自定义消息")
        if expected_context:
            context = await self.read_conversation(page, job)
            if context["fingerprint"] != expected_context or not context["canReply"]:
                raise ConversationChanged("会话出现新消息或已不适合回复，请重新读取后生成")
        await editor.fill(message)
        await self.control.delay(self.config.run.action_delay)
        # The user may switch conversations while a task is paused or waiting.
        editor, scope = await self.chat_target(page, job)
        editor_text = (
            await editor.inner_text()
            if await editor.get_attribute("contenteditable") == "true"
            else await editor.input_value()
        )
        if editor_text.strip() != message:
            raise LayoutChanged("待发送草稿已变化，未点击发送")
        if await self.conversation_declined(scope):
            # Only clear the draft we just wrote, in the still-verified conversation.
            await editor.fill("")
            return SendResult("contacted", "招聘方在等待期间回复不合适，已清空本次草稿，未发送")
        await self.gate(page)
        if expected_context:
            context = await self.read_conversation(page, job)
            if context["fingerprint"] != expected_context or not context["canReply"]:
                await editor.fill("")
                raise ConversationChanged("等待期间会话出现新消息，已清空本次草稿，未发送")
        send = (
            scope.get_by_role("button", name="发送", exact=True)
            .or_(scope.get_by_role("link", name="发送", exact=True))
            .or_(scope.locator(self.selectors["chat_send"]).filter(has_text=re.compile(r"^发送$")))
        )
        await (await self.unique_visible(send, "发送按钮")).click()
        deadline = time.monotonic() + self.config.browser.timeout_seconds
        while time.monotonic() < deadline:
            await self.control.checkpoint()
            await self.gate(page)
            if await self.has_delivery_receipt(page, job, message, after=before):
                return SendResult("sent", "当前会话的己方消息已出现送达/已读回执")
            await self.control.sleep(0.3)
        return SendResult("partial", "沟通已建立，自定义消息送达回执不明确；不会自动补发")

    async def has_delivery_receipt(self, page: Page, job: Job, message: str, *, after=0) -> bool:
        """Read-only confirmation shared by initial sends and pending-history review."""
        editor, scope = await self.chat_target(page, job)
        editor_text = (
            await editor.inner_text()
            if await editor.get_attribute("contenteditable") == "true"
            else await editor.input_value()
        )
        if editor_text.strip():
            return False
        failed = scope.get_by_text("发送失败", exact=True)
        if any([await item.is_visible() for item in await failed.all()]):
            return False
        outgoing = scope.locator(self.selectors["outgoing_messages"]).filter(has_text=message)
        messages = [
            item
            for item in await outgoing.all()
            if await item.is_visible() and (await item.inner_text()).strip() == message
        ]
        for item in messages[after:]:
            bubble = item.locator(
                "xpath=ancestor::*[contains(concat(' ',normalize-space(@class),' '),"
                "' message-item ')][1]"
            )
            receipt = bubble.get_by_text(re.compile(r"^(?:送达|已读|发送成功)$"))
            if any([await marker.is_visible() for marker in await receipt.all()]):
                return True
        return False

    async def chat_target(self, page: Page, job: Job) -> tuple[Locator, Locator]:
        editor = await self.unique_visible(
            page.locator(self.selectors["chat_editor"]), "聊天输入框"
        )
        if page in self.chat_bindings:
            identity, token, scope_path = self.chat_bindings[page]
            if identity != job.job_id or not await self.check_chat_binding(editor, token):
                raise LayoutChanged("已核对的会话发生变化，请重新核对收件人；未继续发送")
            return editor, page.locator(scope_path)
        scopes = page.locator(self.selectors["chat_scope"]).filter(has=editor)
        valid_scopes = []
        observed_identities = set()
        for scope in await scopes.all():
            identity = scope.locator('a[href*="/job_detail/"]')
            identities = set()
            for item in await identity.all():
                if await item.is_visible():
                    try:
                        identity_id, _ = canonical_job_url(
                            urljoin(page.url, await item.get_attribute("href") or "")
                        )
                        identities.add(identity_id)
                    except ValueError:
                        continue
            observed_identities.update(identities)
            if await scope.is_visible() and identities == {job.job_id}:
                valid_scopes.append(scope)
        if not valid_scopes:
            if observed_identities:
                raise LayoutChanged("当前会话中的职位链接与目标不一致")
            scope = await self.chat_scope_via_job_popup(page, editor, job)
            return editor, scope
        scope = valid_scopes[-1]
        if job.company not in await scope.inner_text():
            raise LayoutChanged("当前会话公司不匹配")
        return editor, scope

    async def check_chat_binding(self, editor: Locator, token: str, *, confirm=False) -> bool:
        operation = "confirm" if confirm else "check"
        return await editor.evaluate(
            "e => {const b=e[Symbol.for('deliverdesk.chatIdentity')];return !!b && b.token==="
            + json.dumps(token)
            + f" && b.{operation}();}}"
        )

    async def chat_scope_via_job_popup(
        self, page: Page, editor: Locator, job: Job, *, discover=False
    ) -> Locator:
        """Verify the actual public job link behind the full chat page's '查看职位'."""
        # The live chat's job card is a clickable container, not always an <a href>.
        # Walk from its unique editor to the smallest enclosing conversation, then
        # inspect only the popup created by that conversation's visible control.
        scope = editor
        view_job = None
        for _ in range(8):
            scope = scope.locator("..")
            if await scope.evaluate("e => ['BODY', 'HTML'].includes(e.tagName)"):
                break
            text = await scope.inner_text()
            if job.title not in text or job.company not in text:
                continue
            choices = scope.get_by_text(re.compile(r"^查看职位\s*[\uE000-\uF8FF]*$"))
            visible = [item for item in await choices.all() if await item.is_visible()]
            if len(visible) == 1:
                view_job = visible[0]
                break
        if view_job is None:
            raise LayoutChanged("无法通过当前会话中的职位链接或查看职位入口确认收件人")
        scope_path = await scope.evaluate(CSS_PATH)
        token = secrets.token_hex(16)
        options = {
            "token": token,
            "editor": await editor.evaluate(CSS_PATH),
            "control": await view_job.evaluate(CSS_PATH),
            "title": job.title,
            "company": job.company,
        }
        await scope.evaluate(CHAT_BINDING.replace("__OPTIONS__", json.dumps(options)))
        popup = None
        verified = False
        try:
            expect_popup = getattr(page, "expect_background_popup", page.expect_popup)
            async with expect_popup(timeout=self.config.browser.timeout_seconds * 1000) as opened:
                await view_job.click()
            popup = await opened.value
            self.register_popup(popup)
            await popup.wait_for_url(re.compile(r"^https://www\.zhipin\.com/job_detail/"))
            await popup.wait_for_load_state("domcontentloaded")
            await self.gate(popup)
            identity, address = canonical_job_url(popup.url)
            if not discover and identity != job.job_id:
                raise LayoutChanged("聊天页查看职位打开了其他职位，未发送")
            body = await popup.locator("body").inner_text()
            if job.title not in body or job.company not in body:
                raise LayoutChanged("聊天页打开的职位详情与目标公司/职位不一致")
            if discover:
                title = await self.unique_visible(popup.locator("h1"), "公开职位名称")
                if (await title.inner_text()).strip() != job.title:
                    raise LayoutChanged("会话职位名称与公开职位详情不一致，未自动回复")
                job.job_id, job.url = identity, address
                job.contacted = True
            verified = True
        finally:
            if popup and not popup.is_closed():
                await popup.close()
            if not page.is_closed():
                await page.bring_to_front()
            if not verified and not page.is_closed():
                await editor.evaluate("e => e[Symbol.for('deliverdesk.chatIdentity')]?.dispose()")
        if not await self.check_chat_binding(editor, token, confirm=True):
            raise LayoutChanged("核对职位期间收件人发生变化，请重新核对；未继续发送")
        self.chat_bindings[page] = (job.job_id, token, scope_path)
        return page.locator(scope_path)

    async def diagnostics(self) -> dict:
        page = self.detail if self.detail and not self.detail.is_closed() else self.page
        results = {}
        for name, selector in self.selectors.items():
            try:
                matches = await page.locator(selector).all()
                results[name] = {
                    "selector": selector,
                    "visible": sum([await item.is_visible() for item in matches]),
                }
            except PlaywrightError as error:
                results[name] = {"error": clean_error(error)}
        url = urlsplit(page.url)
        return {
            "url": f"{url.scheme}://{url.netloc}{url.path}" if url.netloc else page.url,
            "security_error": next(iter(self.security_errors.values()), None),
            "selectors": results,
        }
