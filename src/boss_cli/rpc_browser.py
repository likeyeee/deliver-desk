"""DOM-only transport to the app-owned Electron browser, over private stdio.

No remote debugging connection, Chrome profile import, or site script overrides.
The desktop renderer cannot access this interface.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import re
import time

from .browser import BrowserSession, LayoutChanged, NeedsAttention

DOM_QUERY = r"""
const norm = v => String(v ?? '').replace(/\s+/g, ' ').trim();
const visible = e => !!(e && e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden' && getComputedStyle(e).display !== 'none');
const match = (v, p) => p.regex ? new RegExp(p.value, p.flags || '').test(norm(v)) : (p.exact ? norm(v) === norm(p.value) : norm(v).includes(norm(p.value)));
const unique = list => [...new Set(list)];
function query(q) {
  if (!q) return [document];
  if (q.kind === 'or') return unique([...query(q.left), ...query(q.right)]).sort((a,b) => a===b ? 0 : a.compareDocumentPosition(b)&Node.DOCUMENT_POSITION_FOLLOWING ? -1 : 1);
  if (q.kind === 'nth') return query(q.parent).slice(q.index, q.index + 1);
  if (q.kind === 'filter') {
    const has = q.has ? query(q.has) : null;
    return query(q.parent).filter(e => (!q.text || match(e.innerText, q.text)) && (!has || has.some(n => e.contains(n))));
  }
  const roots = query(q.parent);
  if (q.kind === 'css') {
    if (q.selector === '..') return unique(roots.map(e => e.parentElement).filter(Boolean));
    if (q.selector.startsWith('xpath=')) {
      return unique(roots.map(e => document.evaluate(q.selector.slice(6), e, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue).filter(Boolean));
    }
    const requireVisible = q.selector.includes(':visible');
    const selector = q.selector.replaceAll(':visible', '');
    return unique(roots.flatMap(e => [...e.querySelectorAll(selector)])).filter(e => !requireVisible || visible(e));
  }
  const all = unique(roots.flatMap(e => [...e.querySelectorAll('*')])).filter(e => !['SCRIPT','STYLE','NOSCRIPT'].includes(e.tagName));
  if (q.kind === 'text') return all.filter(e => match(e.innerText || e.textContent, q.text) && ![...e.children].some(c => match(c.innerText || c.textContent, q.text)));
  if (q.kind === 'role') {
    const roles = {link:'a[href], [role="link"]', button:'button, input[type="button"], input[type="submit"], [role="button"]', heading:'h1,h2,h3,h4,h5,h6,[role="heading"]'};
    return all.filter(e => e.matches(roles[q.role] || `[role="${q.role}"]`) && (!q.name || match(e.getAttribute('aria-label') || e.innerText || e.value || '', q.name)));
  }
  throw Error('不支持的网页定位类型');
}
"""

CLICK_POINT = """
if(!visible(e) || e.disabled || e.getAttribute('aria-disabled')==='true')
  throw Error('目标控件不可操作');
// A fixed toolbar can cover the center while the rest of the button remains
// usable. Only use a point that actually hits this control inside the viewport.
for(const rect of e.getClientRects()) {
  const left=Math.max(0,rect.left), top=Math.max(0,rect.top);
  const right=Math.min(innerWidth,rect.right), bottom=Math.min(innerHeight,rect.bottom);
  if(right-left<2 || bottom-top<2) continue;
  for(const fy of [0.5,0.25,0.75,0.1,0.9]) {
    for(const fx of [0.5,0.25,0.75,0.1,0.9]) {
      const x=Math.round(left+(right-left-1)*fx), y=Math.round(top+(bottom-top-1)*fy);
      const hit=document.elementFromPoint(x,y);
      if(hit && (hit===e || e.contains(hit))) return {x,y};
    }
  }
}
throw Error('目标控件被其他内容遮挡');
"""


def text_pattern(value, exact=False):
    if isinstance(value, re.Pattern):
        return {"value": value.pattern, "regex": True, "flags": "i" if value.flags & re.I else ""}
    return {"value": value, "exact": exact}


class RpcLocator:
    def __init__(self, page: RpcPage, spec: dict):
        self.page, self.spec = page, spec

    def locator(self, selector):
        return RpcLocator(self.page, {"kind": "css", "selector": selector, "parent": self.spec})

    def get_by_text(self, value, *, exact=False):
        return RpcLocator(
            self.page, {"kind": "text", "text": text_pattern(value, exact), "parent": self.spec}
        )

    def get_by_role(self, role, *, name=None, exact=False):
        return RpcLocator(
            self.page,
            {
                "kind": "role",
                "role": role,
                "name": text_pattern(name, exact) if name is not None else None,
                "parent": self.spec,
            },
        )

    def filter(self, *, has=None, has_text=None):
        return RpcLocator(
            self.page,
            {
                "kind": "filter",
                "parent": self.spec,
                "has": has.spec if has else None,
                "text": text_pattern(has_text) if has_text is not None else None,
            },
        )

    def or_(self, other):
        if self.page is not other.page:
            raise LayoutChanged("不能组合来自不同页面的定位器")
        return RpcLocator(self.page, {"kind": "or", "left": self.spec, "right": other.spec})

    @property
    def first(self):
        return self.nth(0)

    def nth(self, index):
        return RpcLocator(self.page, {"kind": "nth", "parent": self.spec, "index": index})

    async def _read(self, expression):
        return await self.page.run_js(
            DOM_QUERY
            + f"\nconst nodes=query({json.dumps(self.spec, ensure_ascii=False)});\n"
            + expression
        )

    async def count(self):
        return await self._read("return nodes.length;")

    async def all(self):
        return [self.nth(i) for i in range(await self.count())]

    async def _one(self, expression, timeout=None):
        deadline = time.monotonic() + (timeout / 1000 if timeout is not None else self.page.timeout)
        while True:
            # Query and use the element in one renderer turn. SPA hydration can
            # replace the tree between separate count/read IPC requests.
            try:
                result = await self._read(
                    "if(nodes.length!==1)return {count:nodes.length}; const e=nodes[0];"
                    + "const value=(()=>{"
                    + expression
                    + "})(); return {count:1,value};"
                )
            except LayoutChanged as error:
                error.locator_spec = self.spec
                raise
            count = result["count"]
            if count > 1:
                error = LayoutChanged(f"网页操作匹配到 {count} 个元素，已停止")
                error.locator_spec = self.spec
                raise error
            if count == 1:
                return result.get("value")
            if time.monotonic() >= deadline:
                error = LayoutChanged("网页元素未出现，请检查当前浏览器页面")
                error.locator_spec = self.spec
                raise error
            await asyncio.sleep(0.1)

    async def inner_text(self, timeout=None):
        return await self._one("return e.innerText ?? '';", timeout)

    async def input_value(self):
        return await self._one("return e.value ?? ''; ")

    async def get_attribute(self, name):
        return await self._one(f"return e.getAttribute({json.dumps(name)});")

    async def is_visible(self):
        return await self._read("return nodes.length===1 && visible(nodes[0]);")

    async def is_enabled(self):
        return await self._one("return !e.disabled && e.getAttribute('aria-disabled')!=='true';")

    async def evaluate(self, source):
        return await self._one(f"return ({source})(e);")

    async def evaluate_all(self, source):
        return await self._read(f"return ({source})(nodes);")

    async def scroll_into_view_if_needed(self):
        await self._one("e.scrollIntoView({block:'nearest',inline:'nearest'}); return true;")
        await asyncio.sleep(0.08)

    async def bounding_box(self):
        return await self._one(
            "if(!visible(e))return null; const r=e.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height};"
        )

    async def click(self):
        await self.page.bring_to_front()
        await self.scroll_into_view_if_needed()
        point = await self._one(CLICK_POINT)
        await self.page.manager.bridge.request("click", page=self.page.id, **point)
        await asyncio.sleep(0.08)

    async def fill(self, value):
        encoded = json.dumps(value, ensure_ascii=False)
        await self._one(f"""
            if(!visible(e))throw Error('输入框不可见');
            e.focus(); const value={encoded};
            if(e.isContentEditable) e.textContent=value;
            else {{const proto=e.tagName==='TEXTAREA'?HTMLTextAreaElement.prototype:HTMLInputElement.prototype; const setter=Object.getOwnPropertyDescriptor(proto,'value').set; setter.call(e,value);}}
            e.dispatchEvent(new InputEvent('input',{{bubbles:true,inputType:'insertText',data:value}}));
            e.dispatchEvent(new Event('change',{{bubbles:true}})); return true;
        """)


class PopupExpectation:
    def __init__(self, page, timeout, background=False):
        self.page, self.timeout = page, timeout / 1000
        self.background = background
        self.value = asyncio.get_running_loop().create_future()

    def receive(self, popup):
        if not self.value.done():
            self.value.set_result(popup)

    async def __aenter__(self):
        self.page.on("popup", self.receive)
        if self.background:
            await self.page.manager.bridge.request(
                "backgroundPopup", page=self.page.id, enabled=True
            )
        return self

    async def __aexit__(self, kind, error, tb):
        try:
            if error is None:
                await asyncio.wait_for(asyncio.shield(self.value), self.timeout)
        finally:
            self.page.handlers["popup"].remove(self.receive)
            if self.background and not self.page.is_closed():
                await self.page.manager.bridge.request(
                    "backgroundPopup", page=self.page.id, enabled=False
                )


class RpcMouse:
    def __init__(self, page):
        self.page, self.x, self.y = page, 10, 10

    async def move(self, x, y):
        self.x, self.y = x, y

    async def wheel(self, dx, dy):
        await self.page.run_js(f"""let e=document.elementFromPoint({self.x},{self.y});
            while(e && e!==document.body) {{const s=getComputedStyle(e);if(/auto|scroll/.test(s.overflowY)&&e.scrollHeight>e.clientHeight)break;e=e.parentElement;}}
            (e||document.scrollingElement).scrollBy({float(dx)},{float(dy)}); return true;""")


class RpcPage:
    def __init__(self, manager, page_id):
        self.manager, self.id = manager, page_id
        self.timeout = 20
        self.closed = False
        self.url = "about:blank"
        self.handlers = {"framenavigated": [], "popup": [], "response": []}
        self.main_frame = self
        self.mouse = RpcMouse(self)

    def is_closed(self):
        return self.closed

    def on(self, kind, callback):
        self.handlers.setdefault(kind, []).append(callback)

    def emit(self, kind, value):
        for callback in list(self.handlers.get(kind, [])):
            result = callback(value)
            if inspect.isawaitable(result):
                asyncio.get_running_loop().create_task(result)

    async def run_js(self, body):
        if self.closed:
            raise NeedsAttention("浏览器窗口已关闭，请重新打开")
        return await self.manager.bridge.request("evaluate", page=self.id, body=body)

    def locator(self, selector):
        return RpcLocator(self, {"kind": "css", "selector": selector})

    def get_by_text(self, value, *, exact=False):
        return RpcLocator(self, {"kind": "text", "text": text_pattern(value, exact)})

    def get_by_role(self, role, *, name=None, exact=False):
        return RpcLocator(
            self,
            {
                "kind": "role",
                "role": role,
                "name": text_pattern(name, exact) if name is not None else None,
            },
        )

    async def goto(self, url, wait_until="domcontentloaded"):
        data = await self.manager.bridge.request(
            "goto", page=self.id, url=url, timeout=self.timeout * 1000
        )
        self.url = data["url"]

    async def wait_for_load_state(self, state="domcontentloaded"):
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            if await self.run_js("return document.readyState !== 'loading';"):
                return
            await asyncio.sleep(0.1)
        raise LayoutChanged("网页加载超时，请检查当前浏览器")

    async def wait_for_url(self, pattern):
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            if pattern.search(self.url) if isinstance(pattern, re.Pattern) else self.url == pattern:
                return
            await asyncio.sleep(0.1)
        raise LayoutChanged("网页未跳转到预期地址")

    def expect_popup(self, timeout=20000):
        return PopupExpectation(self, timeout)

    def expect_background_popup(self, timeout=20000):
        return PopupExpectation(self, timeout, background=True)

    async def bring_to_front(self):
        await self.manager.bridge.request("show", page=self.id)

    async def screenshot(self, *, path, timeout=3000):
        await self.manager.bridge.request("screenshot", page=self.id, path=path)

    async def close(self):
        await self.manager.bridge.request("close", page=self.id)
        self.closed = True


class RpcBrowser:
    def __init__(self, bridge):
        self.bridge = bridge
        self.pages = {}

    def ensure(self, page_id):
        if page_id not in self.pages:
            self.pages[page_id] = RpcPage(self, page_id)
        return self.pages[page_id]

    def event(self, data):
        page = self.ensure(data["page"])
        kind = data["event"]
        if kind == "navigation":
            page.url = data["url"]
            page.emit("framenavigated", page)
        elif kind == "closed":
            page.closed = True
        elif kind == "popup":
            page.url = data.get("url", "about:blank")
            self.ensure(data["parent"]).emit("popup", page)

    async def new_page(self):
        data = await self.bridge.request("new")
        page = self.ensure(data["id"])
        page.url = data["url"]
        return page


class RpcSession(BrowserSession):
    def __init__(self, config, directory, manager):
        super().__init__(config, directory)
        self.manager = manager

    async def __aenter__(self):
        self.context = self.manager
        data = await self.manager.bridge.request("primary")
        self.page = self.manager.ensure(data["id"])
        self.page.url = data["url"]
        self.page.timeout = self.config.browser.timeout_seconds
        self.owned_pages.append(self.page)
        return self

    async def __aexit__(self, kind, error, traceback):
        # Keep the actual login / verification / chat window available for the user.
        if isinstance(error, (LayoutChanged, NeedsAttention)):
            try:
                await self.capture_failure(error)
            except (OSError, LayoutChanged, NeedsAttention):
                pass
        for page in self.owned_pages:
            for callbacks in page.handlers.values():
                callbacks.clear()
