from types import SimpleNamespace

import pytest
from playwright.async_api import async_playwright

from boss_cli.browser import BossAdapter, LayoutChanged, NeedsAttention
from boss_cli.control import Controller

LIST_HTML = """<!doctype html><html><body>
<a href="/web/geek/recommend">示例用户</a><input placeholder="搜索职位、公司">
<a href="javascript:;" onclick="history.pushState({},'', '/web/geek/jobs?query='+encodeURIComponent(document.querySelector('input').value))">搜索</a>
<div class="filter"><span class="city-name">全国</span></div>
<div class="filter" id="salary-filter"><span id="salary-label" onclick="document.getElementById('salary-options').hidden=false">薪资待遇</span>
<div id="salary-options" hidden><span onclick="document.getElementById('salary-label').textContent=this.textContent;this.parentElement.hidden=true">10-20K</span></div></div>
<div class="condition-filter-select"><div class="current-select"><span id="education-label" onclick="document.getElementById('education-options').hidden=false">学历要求</span></div>
<div id="education-options" hidden><span onclick="document.getElementById('education-label').textContent=this.textContent;this.parentElement.hidden=true">本科</span></div></div>
<ul class="results">
<li class="new-card"><a href="/job_detail/abc123.html?securityId=ephemeral">AI应用工程师</a><span class="salary">20-30K·14薪</span>
<ul><li>1-3年</li><li>本科</li><li>Python</li></ul><a href="/gongsi/company.html">示例科技</a><span class="job-area">上海</span></li>
<li class="new-card"><a href="/job_detail/font456.html">AI产品经理</a><span class="salary">&#xE032;-&#xE034;K</span>
<ul><li>经验不限</li><li>本科</li></ul><a href="/gongsi/other.html">另一家公司</a><span class="job-area">北京</span></li>
</ul>
<aside><a href="/job_detail/abc123.html?securityId=another">查看更多信息</a></aside>
<footer><a href="/job_detail/">职位搜索</a></footer>
<script>document.querySelector('input').value=new URL(location.href).searchParams.get('query') || '';</script>
</body></html>"""

DETAIL_HTML = """<!doctype html><html><body>
<div class="name"><h1>AI应用工程师</h1><span class="salary">20-30K·14薪</span></div><a href="/gongsi/company.html">示例科技</a>
<a id="contact" href="javascript:;" onclick="this.textContent='继续沟通'">立即沟通</a>
<section><h3>职位描述</h3><div class="job-sec-text">Python 大模型应用开发</div></section>
<h2 class="boss-name">示例招聘者</h2>
</body></html>"""

CHAT_HTML = """<!doctype html><html><body>
<div class="chat-window">
<header><a href="/job_detail/abc123.html">AI应用工程师</a><span>示例科技</span></header>
<div id="messages"></div><div id="chat-input" contenteditable="true" role="textbox"></div>
<button onclick="const editor=document.getElementById('chat-input'); const item=document.createElement('div');item.className='message-item is-self'; const text=document.createElement('div');text.className='text';text.textContent=editor.textContent;item.append(text); const receipt=document.createElement('span');receipt.textContent='送达';item.append(receipt); document.getElementById('messages').append(item);editor.textContent='';">发送</button>
</div></body></html>"""


async def test_reuses_logged_in_page_without_login_navigation(web):
    navigations = []
    web.page.on("framenavigated", lambda frame: navigations.append(frame.url))
    await web.login(1)
    assert navigations == []
    assert web.page.url == "https://www.zhipin.com/web/geek/jobs"


async def test_reuses_detail_view_and_closes_previous_job_popups(web, job):
    import copy

    await web.inspect_job(job)
    detail = web.detail
    popup = await web.session.context.new_page()
    web.detail_popups.append(popup)
    web.session.owned_pages.append(popup)
    second = copy.deepcopy(job)
    second.job_id = "new123"
    second.url = "https://www.zhipin.com/job_detail/new123.html"
    await web.inspect_job(second)
    assert web.detail is detail
    assert web.detail.url == second.url
    assert popup.is_closed()
    assert len(web.session.owned_pages) == 2


@pytest.fixture
async def web(config, store):
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(channel="chrome", headless=True)
        context = await browser.new_context()

        async def route_handler(route):
            url = route.request.url
            html = (
                CHAT_HTML if "/chat" in url else DETAIL_HTML if "/job_detail/" in url else LIST_HTML
            )
            await route.fulfill(status=200, content_type="text/html; charset=utf-8", body=html)

        await context.route("**/*", route_handler)
        page = await context.new_page()
        await page.goto("https://www.zhipin.com/web/geek/jobs")
        session = SimpleNamespace(
            page=page, context=context, owned_pages=[page], directory=store.directory
        )
        adapter = BossAdapter(session, config, Controller(store, "test-run"))
        yield adapter
        await browser.close()


async def test_collect_visible_cards_and_canonical_urls(web):
    jobs = await web.collect()
    assert len(jobs) == 2
    assert jobs[0].job_id == "abc123"
    assert jobs[0].company == "示例科技"
    assert jobs[0].experience == "1-3年"
    assert jobs[0].salary == "20-30K·14薪"
    assert jobs[0].url == "https://www.zhipin.com/job_detail/abc123.html"
    assert "securityId=" in web.live_urls[jobs[0].job_id]
    assert "\ue032" in jobs[1].salary


async def test_filter_selection_is_verified(web):
    await web.select_filter("薪资待遇", "10-20K")
    assert await web.page.locator("#salary-label").inner_text() == "10-20K"
    with pytest.raises(LayoutChanged):
        await web.select_filter("不存在的筛选", "10-20K")


async def test_filter_option_is_scoped_away_from_repeated_card_tags(web):
    await web.select_filter("学历要求", "本科")
    assert await web.page.locator("#education-label").inner_text() == "本科"


@pytest.mark.parametrize("updates_query", [True, False])
async def test_real_filter_identifier_requires_selected_url_value(web, updates_query):
    action = "history.pushState({},'', '?experience=101')" if updates_query else ""
    await web.page.set_content(
        '<div class="condition-filter-select"><div class="current-select">'
        '<span>工作经验</span></div><div class="filter-select-dropdown"><ul>'
        f'<li ka="sel-job-rec-exp-101" onclick="{action}">经验不限</li>'
        "</ul></div></div>"
    )
    if updates_query:
        await web.select_filter("工作经验", "经验不限")
        assert "experience=101" in web.page.url
    else:
        with pytest.raises(LayoutChanged, match="无法确认"):
            await web.select_filter("工作经验", "经验不限")


async def test_search_opens_public_route_and_confirms_query(web):
    await web.search("AI应用")
    assert "query=AI" in web.page.url
    assert len(await web.collect()) == 2


async def test_search_waits_for_city_and_results_to_finish_hydrating(web):
    html = LIST_HTML.replace(
        '<span class="city-name">全国</span>',
        '<span class="city-label" onclick="document.body.dataset.wrongClick=1">城市</span>',
    ).replace(
        "</body>",
        """<script>
        const results=document.querySelector('.results'); results.hidden=true;
        document.querySelector('aside').hidden=true;
        setTimeout(()=>{document.querySelector('.city-label').textContent='全国';results.hidden=false},350);
        </script></body>""",
    )

    async def delayed_list(route):
        await route.fulfill(content_type="text/html; charset=utf-8", body=html)

    await web.page.route("**/web/geek/jobs?*", delayed_list)
    await web.search("AI应用")
    assert await web.page.locator("body").get_attribute("data-wrong-click") is None
    assert len(await web.collect()) == 2


async def test_current_city_does_not_open_dialog_via_nested_icon(web):
    await web.page.set_content("""<div class="city-label" onclick="throw Error('must not click')">
      <span class="city-select-label">&#xE111;</span><span class="cur-city-label">泉州</span>
    </div>""")
    await web.select_filter("城市", "泉州")


async def test_city_outside_popular_group_uses_visible_alphabet_tab(web):
    await web.page.set_content("""
    <div class="city-label" onclick="document.querySelector('.city-select-dialog').hidden=false">上海</div>
    <div class="city-select-dialog" hidden>
      <button onclick="document.querySelector('#quanzhou').hidden=false">PQRST</button>
      <span>全国</span>
      <span id="quanzhou" hidden onclick="document.querySelector('.city-label').textContent=this.textContent;this.parentElement.hidden=true">泉州</span>
    </div>""")
    await web.select_filter("城市", "泉州")
    assert await web.page.locator(".city-label").inner_text() == "泉州"


async def test_search_rejects_input_that_disagrees_with_route(web):
    async def wrong_query(route):
        await route.fulfill(
            content_type="text/html; charset=utf-8",
            body=LIST_HTML.replace(
                "new URL(location.href).searchParams.get('query') || ''", "'其他岗位'"
            ),
        )

    await web.page.route("**/web/geek/jobs?*", wrong_query)
    with pytest.raises(LayoutChanged, match="关键词"):
        await web.search("AI应用")


async def test_contact_button_change_is_not_a_sent_message(web, job):
    web.config.message.mode = "platform"
    await web.collect()
    result = await web.inspect_job(job)
    assert result.description == "Python 大模型应用开发"
    assert await web.preflight(job)
    outcome = await web.greet(job, "平台默认")
    assert outcome.status == "contacted"
    assert not await web.preflight(job)


async def test_duplicate_contact_buttons_abort(web, job):
    await web.inspect_job(job)
    await web.detail.set_content(DETAIL_HTML.replace("</body>", "<button>立即沟通</button></body>"))
    with pytest.raises(LayoutChanged):
        await web.preflight(job)


async def test_missing_ack_is_unknown_not_success(web, job):
    await web.inspect_job(job)
    await web.detail.set_content(DETAIL_HTML.replace("onclick=\"this.textContent='继续沟通'\"", ""))
    outcome = await web.greet(job, "平台默认")
    assert outcome.status == "unknown"


async def test_custom_message_correct_chat(web, job):
    await web.page.goto("https://www.zhipin.com/web/geek/chat")
    outcome = await web.send_custom(web.page, job, "您好，希望了解这个岗位。")
    assert outcome.status == "sent"
    assert (
        await web.page.locator(".message-item.is-self .text").inner_text()
        == "您好，希望了解这个岗位。"
    )


async def test_live_message_style_receipt_and_read_only_recheck(web, job):
    html = CHAT_HTML.replace("message-item is-self", "message-item item-myself").replace(
        "text.className='text'", "text.className='text-content'"
    )
    await web.page.goto("https://www.zhipin.com/web/geek/chat")
    await web.page.set_content(html)
    result = await web.send_custom(web.page, job, "实站样式回执")
    assert result.status == "sent"
    assert await web.has_delivery_receipt(web.page, job, "实站样式回执")
    assert not await web.has_delivery_receipt(web.page, job, "另一条内容")
    assert not await web.has_delivery_receipt(web.page, job, "实站样式回执", after=1)
    assert await web.page.locator(".message-item").count() == 1


async def test_custom_greet_sends_after_chat_opens_without_default_greeting(web, job):
    await web.inspect_job(job)
    await web.detail.set_content(
        DETAIL_HTML.replace("this.textContent='继续沟通'", "location.href='/web/geek/chat'")
    )
    result = await web.greet(job, "您好，希望了解这个岗位。")
    assert result.status == "sent"
    assert await web.detail.locator(".message-item.is-self").count() == 1


async def test_detail_composer_continues_in_verified_full_chat(web, job):
    await web.inspect_job(job)
    await web.detail.set_content(
        DETAIL_HTML.replace("</body>", '<a href="/web/geek/chat">消息</a></body>')
    )
    result = await web.greet(job, "完整会话回执验证")
    assert result.status == "sent"
    chats = [page for page in web.session.owned_pages if "/web/geek/chat" in page.url]
    assert len(chats) == 1
    assert await chats[0].locator(".message-item.is-self .text").inner_text() == "完整会话回执验证"


@pytest.mark.parametrize("ambiguous", [True, False])
async def test_full_chat_cannot_send_to_ambiguous_company_or_wrong_job(web, job, ambiguous):
    await web.inspect_job(job)
    await web.detail.set_content(
        DETAIL_HTML.replace("</body>", '<a href="/web/geek/chat">消息</a></body>')
    )
    html = (
        CHAT_HTML.replace("</header>", "<span>示例科技</span></header>")
        if ambiguous
        else CHAT_HTML.replace("abc123.html", "other999.html")
    )

    async def other_chat(route):
        await route.fulfill(content_type="text/html; charset=utf-8", body=html)

    await web.session.context.route("**/web/geek/chat", other_chat)
    result = await web.greet(job, "不能发给其他职位")
    assert result.status == "partial"
    chat = next(page for page in web.session.owned_pages if "/web/geek/chat" in page.url)
    assert await chat.locator(".message-item").count() == 0
    assert await chat.locator("#chat-input").inner_text() == ""


async def test_outgoing_bubble_without_delivery_receipt_is_not_success(web, job):
    await web.page.set_content(
        CHAT_HTML.replace("receipt.textContent='送达'", "receipt.textContent='发送中'")
    )
    result = await web.send_custom(web.page, job, "等待回执的消息")
    assert result.status == "partial"
    assert await web.page.locator(".message-item.is-self").count() == 1


async def test_declined_conversation_does_not_receive_another_message(web, job):
    await web.page.set_content(
        CHAT_HTML.replace(
            '<div id="messages">', '<div id="messages"><p>感谢您的关注，暂时不合适该职位</p>'
        )
    )
    result = await web.send_custom(web.page, job, "不应发送")
    assert result.status == "contacted"
    assert (await web.page.locator("#chat-input").inner_text()).strip() == ""
    assert await web.page.locator(".message-item.is-self").count() == 0


async def test_decline_arrives_after_typing_only_our_draft_is_cleared(web, job):
    await web.page.goto("https://www.zhipin.com/web/geek/chat")

    async def refusal_during_delay(_bounds):
        await web.page.locator("#messages").evaluate(
            "e => e.innerHTML='<p>感谢您的关注，暂时不合适该职位</p>'"
        )

    web.control.delay = refusal_during_delay
    result = await web.send_custom(web.page, job, "不应发送")
    assert result.status == "contacted"
    assert (await web.page.locator("#chat-input").inner_text()).strip() == ""
    assert await web.page.locator(".message-item.is-self").count() == 0


async def test_login_blank_redirect_invalidates_old_qr_image(web):
    image = web.session.directory / "login.png"
    image.write_bytes(b"obsolete QR fixture")
    await web.page.locator('a[href*="/web/geek/recommend"]').evaluate("e => e.remove()")

    async def blank_login(route):
        await route.fulfill(
            content_type="text/html", body="<script>location.href='about:blank'</script>"
        )

    await web.page.route("**/web/user/**", blank_login)
    with pytest.raises(NeedsAttention, match="about:blank"):
        await web.login(5)
    assert not image.exists()


@pytest.mark.parametrize("popup_id", ["abc123", "other456"])
async def test_chat_without_link_verifies_view_job_popup_before_typing(web, job, popup_id):
    html = CHAT_HTML.replace('class="chat-window"', 'class="current-chat-layout"').replace(
        '<a href="/job_detail/abc123.html">AI应用工程师</a>',
        "<span>AI应用工程师</span>"
        f"<div onclick=\"window.open('/job_detail/{popup_id}.html')\">查看职位</div>",
    )
    await web.page.set_content(html)
    if popup_id == job.job_id:
        result = await web.send_custom(web.page, job, "您好，希望进一步了解。")
        assert result.status == "sent"
    else:
        with pytest.raises(LayoutChanged, match="其他职位"):
            await web.send_custom(web.page, job, "不应发送")
        assert await web.page.locator("#chat-input").inner_text() == ""
        assert await web.page.locator("#messages").inner_text() == ""
    assert len(web.session.context.pages) == 1


async def test_custom_message_wrong_recipient_never_types(web, job):
    await web.page.goto("https://www.zhipin.com/web/geek/chat")
    job.job_id = "wrong"
    with pytest.raises(LayoutChanged):
        await web.send_custom(web.page, job, "禁止发出")
    assert await web.page.locator("#chat-input").inner_text() == ""
    assert await web.page.locator("#messages").inner_text() == ""


async def test_existing_draft_not_overwritten(web, job):
    await web.page.goto("https://www.zhipin.com/web/geek/chat")
    await web.page.locator("#chat-input").fill("用户已有草稿")
    with pytest.raises(LayoutChanged):
        await web.send_custom(web.page, job, "新消息")
    assert await web.page.locator("#chat-input").inner_text() == "用户已有草稿"


async def test_salary_uses_readable_detail_after_encoded_card(web, job):
    job.salary = "\ue032-\ue034K"
    await web.inspect_job(job)
    assert job.salary == "20-30K·14薪"


async def test_recipient_switch_during_delay_prevents_send(web, job):
    await web.page.goto("https://www.zhipin.com/web/geek/chat")

    async def switch_conversation(bounds):
        await web.page.locator(".chat-window header a").evaluate(
            "e => e.href='/job_detail/other.html'"
        )

    web.control.delay = switch_conversation
    with pytest.raises(LayoutChanged):
        await web.send_custom(web.page, job, "不应该发出")
    assert await web.page.locator("#messages").inner_text() == ""


async def test_new_draft_during_pause_is_preserved(web, job):
    await web.page.goto("https://www.zhipin.com/web/geek/chat")

    async def new_draft():
        await web.page.locator("#chat-input").fill("等待期间手写草稿")

    web.control.checkpoint = new_draft
    with pytest.raises(LayoutChanged):
        await web.send_custom(web.page, job, "程序消息")
    assert await web.page.locator("#chat-input").inner_text() == "等待期间手写草稿"


async def test_changed_job_between_preflight_and_send(web, job):
    await web.inspect_job(job)
    assert await web.preflight(job)
    await web.detail.goto("https://www.zhipin.com/job_detail/other.html")
    with pytest.raises(LayoutChanged):
        await web.greet(job, "默认")
    assert await web.detail.locator("#contact").inner_text() == "立即沟通"


async def test_captcha_stops_without_click(web):
    await web.page.set_content("<h1>安全验证</h1><button>验证</button>")
    with pytest.raises(NeedsAttention):
        await web.gate(web.page)


async def test_security_redirect_is_remembered_after_site_returns_to_jobs(web):
    await web.page.goto("https://www.zhipin.com/web/passport/zp/security.html?code=37&seed=private")
    await web.page.goto("https://www.zhipin.com/web/geek/jobs")
    with pytest.raises(NeedsAttention, match="code=37") as error:
        await web.search("AI应用")
    assert "private" not in str(error.value)
    assert await web.page.locator('input[placeholder="搜索职位、公司"]').input_value() == ""


async def test_security_gate_on_list_also_blocks_detail_send(web, job):
    await web.inspect_job(job)
    await web.page.goto("https://www.zhipin.com/web/passport/zp/security.html?code=37")
    with pytest.raises(NeedsAttention):
        await web.gate(web.page)
    with pytest.raises(NeedsAttention):
        await web.preflight(job)
    assert await web.detail.locator("#contact").inner_text() == "立即沟通"


async def test_custom_message_in_boss_dialog_with_div_send_button(web, job):
    html = CHAT_HTML.replace('class="chat-window"', 'class="dialog-container"')
    html = html.replace(
        'id="chat-input" contenteditable', 'id="chat-input" class="input-area" contenteditable'
    )
    html = html.replace("<button onclick=", '<div class="send-message" onclick=').replace(
        "</button>", "</div>"
    )
    await web.page.set_content(html)
    result = await web.send_custom(web.page, job, "您好，希望了解这个岗位。")
    assert result.status == "sent"
