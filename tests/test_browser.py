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

DETAIL_COMPOSER_HTML = """<!doctype html><html><body>
<nav><a href="/web/geek/chat" aria-label="消息 7">消息<span>7</span></a></nav>
<div class="name"><h1>AI应用工程师</h1><span class="salary">20-30K·14薪</span></div><a href="/gongsi/company.html">示例科技</a>
<a id="contact" href="javascript:;" onclick="this.textContent='继续沟通';document.getElementById('composer').hidden=false">立即沟通</a>
<section><h3>职位描述</h3><div class="job-sec-text">Python 大模型应用开发</div></section>
<h2 class="boss-name">示例招聘者</h2>
<div id="composer" hidden><div class="startchat-content"><h2>示例招聘者 示例科技</h2>
<textarea class="input-area" placeholder="请简短描述您的问题"></textarea>
<button disabled>发送</button><aside>订阅回复消息 在微信上实时收到他的回复</aside>
</div></div>
<style>#composer{position:fixed;inset:0;background:#0008;z-index:100}.startchat-content{margin:10vh 10vw;padding:30px;background:white}textarea{display:block}</style>
</body></html>"""

CHAT_HTML = """<!doctype html><html><body>
<div class="chat-window">
<header><a href="/job_detail/abc123.html">AI应用工程师</a><span>示例科技</span></header>
<div id="messages"></div><div id="chat-input" contenteditable="true" role="textbox"></div>
<button onclick="const editor=document.getElementById('chat-input'); const item=document.createElement('div');item.className='message-item is-self'; const text=document.createElement('div');text.className='text';text.textContent=editor.textContent;item.append(text); const receipt=document.createElement('span');receipt.textContent='送达';item.append(receipt); document.getElementById('messages').append(item);editor.textContent='';">发送</button>
</div></body></html>"""

FULL_CHAT_HTML = """<!doctype html><html><body>
<aside><button id="other-contact">另一位联系人</button></aside>
<div class="chat-conversation">
<header><strong>示例招聘者</strong><span>示例科技</span></header>
<div class="message-content">
<div class="job-card"><span>AI应用工程师</span><button id="view-job" onclick="window.open('/job_detail/abc123.html')">查看职位</button></div>
<div class="chat-record" id="messages"><div class="competitor-card">
<strong>你与该职位竞争者PK情况</strong><p>共若干人投递</p>
<button id="competitor-analysis" onclick="document.body.dataset.analysisOpened='yes'">查看详细分析</button>
</div></div>
<div class="message-controls"><div class="editor-container"><div id="chat-input" contenteditable="true" role="textbox"></div></div>
<button id="send-message" onclick="if(!event.isTrusted)throw Error('native input required');const editor=document.getElementById('chat-input'); const item=document.createElement('div');item.className='message-item item-myself'; const text=document.createElement('div');text.className='text-content';text.textContent=editor.textContent;item.append(text); const receipt=document.createElement('span');receipt.textContent='发送中';item.append(receipt);document.getElementById('messages').append(item);editor.textContent='';setTimeout(()=>receipt.textContent='送达',700);">发送</button>
</div></div></div>
<div id="floating-help" aria-label="网页悬浮帮助"></div>
<style>
#chat-input{min-height:60px;border:1px solid #aaa}
#send-message{position:fixed;right:-20px;bottom:12px;width:140px;height:46px}
#floating-help{position:fixed;right:30px;bottom:5px;width:70px;height:70px;border-radius:50%;background:#ddd;z-index:20}
</style></body></html>"""

CONTACT_LIST_HTML = """<aside id="contacts">
<button id="other-recruiter" onclick="document.body.dataset.selectedContact='other';document.querySelector('.chat-conversation').hidden=false;document.getElementById('view-job').onclick=()=>window.open('/job_detail/other999.html')"><strong>另一位招聘者</strong><span>示例科技</span><p>示例招聘者</p></button>
<button id="expected-recruiter" onclick="document.body.dataset.selectedContact='expected';document.querySelector('.chat-conversation').hidden=false"><strong>示例招聘者</strong><span>示例科技</span><p>新的沟通</p></button>
</aside>"""


INBOX_HTML = """<!doctype html><html><body>
<nav><a href="/web/geek/recommend">示例用户</a><a href="/web/geek/chat">消息 <span id="global-unread">3</span></a></nav>
<aside class="chat-user"><input placeholder="搜索30天内的联系人">
<div class="label-list"><ul><li class="selected"><span class="label-name">全部</span></li><li><span class="label-name">未读 <i>(3)</i></span></li></ul></div>
<div class="user-list"><div class="user-list-content"><ul id="inbox-rows"></ul><div class="boss-list-footer"><span class="finished">没有更多了</span></div></div></div></aside>
<div class="chat-conversation" hidden>
<div class="top-info-content"><div class="user-info"><div class="base-info"><div class="name-content"><span class="name-text"></span></div><span class="company"></span><span class="base-title">招聘者</span></div></div>
<div class="chat-position-content"><div class="position-content"><span class="position-name">AI应用工程师</span><button id="view-job">查看职位</button></div></div></div>
<div class="chat-record" id="messages"></div><div id="chat-input" contenteditable="true" role="textbox"></div><button id="inbox-send">发送</button>
</div>
<script>
const contacts = [
 {id:'inbox001',name:'李招聘',company:'示例科技',unread:3,text:'请介绍你对 AI 应用开发的理解。'},
 {id:'inbox002',name:'王招聘',company:'示例科技',unread:0,text:'收到，想了解一下你对岗位的看法。'},
 {id:'inbox003',name:'赵招聘',company:'示例科技',unread:7,text:'感谢，期待进一步沟通。',replied:true},
 {id:'inbox004',name:'孙招聘',company:'示例科技',unread:2,text:'[图片]',unsupported:true},
 {id:'inbox005',name:'周招聘',company:'示例科技',unread:1,text:'请发一下作品集。',draft:true}
];
let selected=null;
const state = window.inboxFixture = {contacts,sends:[],receipt:true};
const normMessage=(role,content,id,supported=true)=>({role,content,id,supported});
for(const c of contacts) c.messages=[normMessage('assistant','您好，希望了解这个职位。',c.id+'-own0'),normMessage(c.replied?'assistant':'user',c.text,c.id+'-last',!c.unsupported)];
function renderRows(){
 document.getElementById('inbox-rows').innerHTML='';
 for(const c of contacts){
  const li=document.createElement('li'),row=document.createElement('div');row.className='friend-content';row.dataset.fixtureId=c.id;
  row.innerHTML='<div class="figure"><span class="notice-badge"></span></div><div class="text"><div class="title-box"><span class="name-box"><span class="name-text"></span><span class="brand"></span><i class="vline"></i><span>招聘者</span></span></div><div class="last-msg"><span class="draft"></span><span class="last-msg-text"></span></div></div>';
  row.querySelector('.name-text').textContent=c.name;row.querySelector('.brand').textContent=c.company;
  row.querySelector('.last-msg-text').textContent=c.messages.at(-1).content;
  row.querySelector('.notice-badge').textContent=c.unread?String(c.unread):'';
  if(c.draft)row.querySelector('.draft').textContent='[草稿]';else row.querySelector('.draft').remove();
  row.onclick=()=>select(c);li.append(row);document.getElementById('inbox-rows').append(li);
 }
}
function renderMessages(){
 const list=document.getElementById('messages');list.innerHTML='';
 for(const m of selected.messages){const row=document.createElement('div');row.className='message-item '+(m.role==='assistant'?'item-myself':'item-friend');row.dataset.messageId=m.id;
  const text=document.createElement('div');text.className=m.supported?'text-content':'file-card';text.textContent=m.content;row.append(text);
  if(m.role==='assistant'){const mark=document.createElement('span');mark.className='receipt';mark.textContent=m.receipt||'送达';row.append(mark);}list.append(row);
 }
 list.scrollTop=list.scrollHeight;
}
function select(c){
 selected=c;state.selected=c.id;document.querySelector('.chat-conversation').hidden=false;
 for(const row of document.querySelectorAll('.friend-content'))row.classList.toggle('selected',row.dataset.fixtureId===c.id);
 document.querySelector('.user-info .name-text').textContent=c.name;document.querySelector('.user-info .company').textContent=c.company;
 document.querySelector('.position-name').textContent='AI应用工程师';document.getElementById('view-job').onclick=()=>window.open('/job_detail/'+(state.wrongJob||c.id)+'.html');
 document.getElementById('chat-input').textContent=c.draft?'我的未完成草稿':'';
 c.unread=0;document.querySelector('[data-fixture-id="'+c.id+'"] .notice-badge').textContent='';renderMessages();
}
state.addMessage=(id,text)=>{const c=contacts.find(c=>c.id===id);c.messages.push(normMessage('user',text,id+'-'+c.messages.length));c.unread++;
 const row=document.querySelector('[data-fixture-id="'+id+'"]');row.querySelector('.last-msg-text').textContent=text;row.querySelector('.notice-badge').textContent=String(c.unread);if(selected===c)renderMessages();};
state.select=id=>select(contacts.find(c=>c.id===id));
document.getElementById('inbox-send').onclick=event=>{
 if(!event.isTrusted)throw Error('native input required');const editor=document.getElementById('chat-input'),text=editor.textContent;
 if(!text.trim())return;state.sends.push({id:selected.id,text});const m=normMessage('assistant',text,selected.id+'-own'+selected.messages.length);m.receipt='发送中';selected.messages.push(m);editor.textContent='';renderMessages();
 const row=document.querySelector('[data-fixture-id="'+selected.id+'"]');row.querySelector('.last-msg-text').textContent=text;
 if(state.receipt)setTimeout(()=>{m.receipt='送达';renderMessages();},120);
};
for(const label of document.querySelectorAll('.label-list li'))label.onclick=()=>{for(const item of document.querySelectorAll('.label-list li'))item.classList.toggle('selected',item===label);};
renderRows();
</script>
<style>body{font:14px/1.6 sans-serif;padding:16px}.chat-user{float:left;width:260px}.label-list ul{display:flex;gap:18px;list-style:none;padding:0}.label-name{cursor:pointer}.user-list-content{height:245px;overflow:auto;border:1px solid #ccc}.user-list-content ul{list-style:none;margin:0;padding:0}.friend-content{height:78px;padding:10px;cursor:pointer}.friend-content.selected{background:#e2efe8}.name-box{display:flex;gap:6px}.last-msg{font-size:12px;overflow:hidden;height:20px}.chat-conversation{margin-left:290px}.base-info{display:flex;gap:12px}.chat-record{height:290px;overflow:auto}.message-item{margin:12px 0;padding:8px;background:#f1f4f2}.item-myself{background:#e3f0e7}.receipt{display:block;font-size:11px}#chat-input{min-height:70px;max-height:140px;overflow:auto;border:1px solid #aaa;white-space:pre-wrap}button{padding:10px}.boss-list-footer{padding:10px}</style>
</body></html>"""


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


@pytest.mark.parametrize(
    "entry",
    [
        '<a href="/web/geek/chat">消息</a>',
        '<a href="/web/geek/chat">消息<span>7</span></a>',
        '<a href="/web/geek/chat" aria-label="消息 99+">消息<span>99+</span></a>',
        '<a href="https://www.zhipin.com/web/geek/chat?ka=header">消息 <span>7</span></a>',
    ],
)
async def test_detail_composer_continues_in_verified_full_chat(web, job, entry):
    await web.inspect_job(job)
    await web.detail.set_content(
        DETAIL_COMPOSER_HTML.replace(
            '<a href="/web/geek/chat" aria-label="消息 7">消息<span>7</span></a>', entry
        )
    )
    assert not await web.detail.locator("#composer").is_visible()
    result = await web.greet(job, "完整会话回执验证")
    assert result.status == "sent"
    assert await web.detail.locator("#composer").is_visible()
    assert await web.detail.locator("textarea").input_value() == ""
    chats = [page for page in web.session.owned_pages if "/web/geek/chat" in page.url]
    assert len(chats) == 1
    assert await chats[0].locator(".message-item.is-self .text").inner_text() == "完整会话回执验证"


@pytest.mark.parametrize(
    "entry",
    [
        '<a href="/web/geek/chat" hidden>消息 7</a>',
        '<a href="https://example.org/web/geek/chat">消息 7</a>',
        '<a href="https://www.zhipin.com.evil.example/web/geek/chat">消息 7</a>',
        '<a href="http://www.zhipin.com/web/geek/chat">消息 7</a>',
        '<a href="/web/geek/chat/archive">消息 7</a>',
        '<a href="/web/geek/chat?contact=1">消息</a><a href="/web/geek/chat?contact=2">消息 7</a>',
    ],
)
async def test_unconfirmed_message_entry_leaves_composer_untouched(web, job, entry):
    await web.inspect_job(job)
    await web.detail.set_content(
        DETAIL_COMPOSER_HTML.replace(
            '<a href="/web/geek/chat" aria-label="消息 7">消息<span>7</span></a>', entry
        )
    )
    pages = list(web.session.owned_pages)
    result = await web.greet(job, "入口未核对，不能发送")
    assert result.status == "partial"
    assert "网站消息入口" in result.note
    assert web.session.owned_pages == pages
    assert await web.detail.locator("textarea").input_value() == ""


@pytest.mark.parametrize("case", ["distinct", "reversed", "selected", "same_name", "wrong_job"])
async def test_full_chat_matches_recruiter_and_still_checks_job(web, job, case):
    await web.inspect_job(job)
    job.recruiter = "示例招聘者\n在线"
    await web.detail.set_content(DETAIL_COMPOSER_HTML)
    contacts = CONTACT_LIST_HTML
    if case == "same_name":
        contacts = contacts.replace("另一位招聘者", "示例招聘者")
    html = FULL_CHAT_HTML.replace(
        '<aside><button id="other-contact">另一位联系人</button></aside>', contacts
    ).replace('class="chat-conversation"', 'class="chat-conversation" hidden')
    # Contact identity is shared by both adapters. Native partially-covered input
    # has its own Electron regression; Playwright requires an unobstructed center.
    html = html.replace("</style>", "#floating-help{display:none}#send-message{right:12px}</style>")
    if case == "reversed":
        html = html.replace(
            "</body>",
            "<script>const list=document.getElementById('contacts');"
            "list.prepend(list.lastElementChild);</script></body>",
        )
    if case == "selected":
        html = html.replace('class="chat-conversation" hidden', 'class="chat-conversation"')
    if case == "wrong_job":
        html = html.replace("abc123.html", "wrong999.html")

    async def other_chat(route):
        await route.fulfill(content_type="text/html; charset=utf-8", body=html)

    await web.session.context.route("**/web/geek/chat", other_chat)
    result = await web.greet(job, "同公司目标招聘者的消息")
    chat = next(page for page in web.session.owned_pages if "/web/geek/chat" in page.url)
    delivered = case in {"distinct", "reversed", "selected"}
    assert result.status == ("sent" if delivered else "partial"), result.note
    assert await chat.locator(".message-item").count() == int(delivered)
    assert await chat.locator("#chat-input").inner_text() == ""
    assert await chat.locator("body").get_attribute("data-selected-contact") == (
        None if case in {"selected", "same_name"} else "expected"
    )
    if case == "same_name":
        assert "同名招聘者" in result.note
    if case == "wrong_job":
        assert "其他职位" in result.note


async def test_full_chat_rejects_wrong_direct_job_link(web, job):
    await web.inspect_job(job)
    await web.detail.set_content(DETAIL_COMPOSER_HTML)
    await web.session.context.route(
        "**/web/geek/chat",
        lambda route: route.fulfill(
            content_type="text/html; charset=utf-8",
            body=CHAT_HTML.replace("abc123.html", "other999.html"),
        ),
    )
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


async def test_popup_identity_is_reused_through_delayed_receipt_and_incoming_messages(web, job):
    await web.page.set_content(
        FULL_CHAT_HTML.replace("<span>示例科技</span>", "<span>示例科技 | 招聘负责人</span>")
    )
    # Playwright's click also requires an exposed button center in this test;
    # the Electron-specific partial obstruction is tested through RpcLocator.
    await web.page.locator("#floating-help").evaluate("e => e.remove()")
    await web.page.locator("#send-message").evaluate("e => e.style.right='0px'")
    popups = []
    web.page.on("popup", lambda page: popups.append(page))

    async def incoming(_bounds):
        await web.page.locator("#messages").evaluate(
            "e => e.insertAdjacentHTML('beforeend','<p>一条新回复</p>')"
        )

    web.control.delay = incoming
    result = await web.send_custom(web.page, job, "仅发送一次并等待回执")
    assert result.status == "sent"
    assert await web.has_delivery_receipt(web.page, job, "仅发送一次并等待回执")
    assert len(popups) == 1
    assert await web.page.locator(".message-item").count() == 1


@pytest.mark.parametrize("change", ["job", "recruiter", "editor", "scope", "contact", "transient"])
async def test_verified_popup_identity_cannot_survive_a_conversation_switch(web, job, change):
    await web.page.set_content(FULL_CHAT_HTML)
    popups = []
    web.page.on("popup", lambda page: popups.append(page))

    async def switch(_bounds):
        if change == "job":
            await web.page.locator("#view-job").evaluate(
                "e => e.setAttribute('onclick',\"window.open('/job_detail/other.html')\")"
            )
        elif change == "recruiter":
            await web.page.locator("header strong").evaluate("e => e.textContent='另一位招聘者'")
        elif change == "contact":
            # A SPA can reuse identical title/company nodes for another recruiter.
            await web.page.locator("#other-contact").click()
        elif change == "transient":
            await web.page.locator("header strong").evaluate(
                "e => {const text=e.textContent;e.textContent='别的会话';e.textContent=text}"
            )
        else:
            selector = "#chat-input" if change == "editor" else ".chat-conversation"
            await web.page.locator(selector).evaluate("e => e.replaceWith(e.cloneNode(true))")

    web.control.delay = switch
    with pytest.raises(LayoutChanged, match="会话发生变化"):
        await web.send_custom(web.page, job, "不能发送到切换后的会话")
    assert await web.page.locator(".message-item").count() == 0
    assert len(popups) == 1


async def test_switch_during_popup_verification_is_rejected(web, job):
    await web.page.set_content(FULL_CHAT_HTML)

    async def changed_detail(route):
        await web.page.locator("header strong").evaluate("e => e.textContent='另一位招聘者'")
        await route.fulfill(content_type="text/html; charset=utf-8", body=DETAIL_HTML)

    await web.session.context.route("**/job_detail/**", changed_detail)
    with pytest.raises(LayoutChanged, match="收件人发生变化"):
        await web.send_custom(web.page, job, "核对期间不能换人")
    assert await web.page.locator("#chat-input").inner_text() == ""


@pytest.mark.parametrize("obstruction", ["partial", "clipped", "full", "disabled"])
async def test_native_locator_only_clicks_exposed_enabled_control(web, obstruction):
    from playwright.async_api import Error as PlaywrightError

    from boss_cli.rpc_browser import RpcPage

    await web.page.set_content(FULL_CHAT_HTML)
    await web.page.locator("#send-message").evaluate(
        "e => e.onclick=event=>{if(event.isTrusted)e.dataset.clicked='yes'}"
    )
    if obstruction == "full":
        await web.page.locator("#floating-help").evaluate(
            "e => e.style='position:fixed;inset:0;z-index:100;border-radius:0;width:auto;height:auto'"
        )
    elif obstruction == "disabled":
        await web.page.locator("#send-message").evaluate(
            "e => e.setAttribute('aria-disabled','true')"
        )
    elif obstruction == "clipped":
        await web.page.locator("#floating-help").evaluate("e => e.remove()")
        await web.page.locator("#send-message").evaluate("e => e.style.right='-100px'")

    async def request(method, **params):
        if method == "evaluate":
            try:
                return await web.page.evaluate("() => {" + params["body"] + "}")
            except PlaywrightError as error:
                raise LayoutChanged(str(error)) from error
        if method == "click":
            await web.page.mouse.click(params["x"], params["y"])
        return True

    rpc = RpcPage(SimpleNamespace(bridge=SimpleNamespace(request=request)), "test")
    if obstruction in {"full", "disabled"}:
        with pytest.raises(LayoutChanged, match="遮挡|不可操作"):
            await rpc.locator("#send-message").click()
        assert await web.page.locator("#send-message").get_attribute("data-clicked") is None
    else:
        await rpc.locator("#send-message").click()
        assert await web.page.locator("#send-message").get_attribute("data-clicked") == "yes"


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


async def add_reply_message(page, text="请问你做过哪些 AI 项目？", *, incoming=True):
    await page.evaluate(
        """({text, incoming}) => {
          const row = document.createElement('div');
          row.className = incoming ? 'message-item' : 'message-item item-myself';
          const bubble = document.createElement('div'); bubble.className = 'text-content';
          bubble.textContent = text; row.append(bubble);
          document.getElementById('messages').append(row);
        }""",
        {"text": text, "incoming": incoming},
    )


async def test_reply_context_is_scoped_ordered_and_ignores_unread_badges(web, job):
    await web.page.set_content(CHAT_HTML)
    await add_reply_message(web.page, "您好，我对这个职位很感兴趣。", incoming=False)
    await add_reply_message(web.page)
    before = await web.read_conversation(web.page, job)
    assert [m["role"] for m in before["messages"]] == ["assistant", "user"]
    assert before["canReply"]
    await web.page.evaluate("""() => {
      const elsewhere = document.createElement('aside');
      elsewhere.innerHTML = '<div class="message-item"><div class="text-content">其他人的私信</div></div>';
      document.body.append(elsewhere);
      document.querySelector('header').append(document.createTextNode(' 未读 91'));
    }""")
    after = await web.read_conversation(web.page, job)
    assert before == after


async def test_new_message_during_reply_delay_clears_own_draft_without_sending(
    web, job, monkeypatch
):
    await web.page.set_content(CHAT_HTML)
    await add_reply_message(web.page)
    context = await web.read_conversation(web.page, job)

    async def new_message(_bounds):
        await add_reply_message(web.page, "补充一下，请发一下作品集。")

    monkeypatch.setattr(web.control, "delay", new_message)
    with pytest.raises(LayoutChanged, match="出现新消息"):
        await web.send_custom(
            web.page, job, "这里是我的回复", expected_context=context["fingerprint"]
        )
    assert await web.page.locator(".is-self").count() == 0
    assert not (await web.page.locator("#chat-input").inner_text()).strip()


async def test_attachment_and_conversation_switch_block_reply(web, job):
    await web.page.set_content(CHAT_HTML)
    await web.page.locator("#messages").evaluate(
        'e => e.innerHTML = \'<div class="message-item"><div class="file-card">作品集.pdf</div></div>\''
    )
    assert not (await web.read_conversation(web.page, job))["canReply"]
    await web.page.locator("header a").evaluate("e => e.href='/job_detail/wrong123.html'")
    with pytest.raises(LayoutChanged, match="不一致"):
        await web.read_conversation(web.page, job)


async def test_reply_workflow_generates_edits_sends_and_blocks_stale_model_result(
    web, config, store, job
):
    from contextlib import asynccontextmanager

    from boss_cli.replies import ReplyWorkflow

    store.save_job(job)
    store.mark_contacted(job, "test-run")
    store.update_run("test-run", status="completed")
    await web.page.set_content(CHAT_HTML)
    await add_reply_message(web.page)
    payloads = []

    class Model:
        async def request(self, method, **params):
            payloads.append(params)
            return {"message": "模型生成的回复"}

    @asynccontextmanager
    async def factory(_config, _directory, _retained):
        yield web.session

    workflow = ReplyWorkflow(store, store.directory, Model(), factory)
    workflow.chat, workflow.job_id = web.page, job.job_id
    params = {"jobId": job.job_id}
    await workflow.run(config, "read", params, "read-chat")
    await workflow.run(config, "generate", params, "generate-draft")
    assert workflow.state["status"] == "draft", workflow.state
    assert payloads[0]["config"]["system_prompt"] == config.llm.system_prompt
    assert payloads[0]["messages"][-1]["content"] == "请问你做过哪些 AI 项目？"
    assert store.attempts_today() == 0
    draft = workflow.state["draft"]
    await workflow.run(
        config, "send", {"replyId": draft["id"], "message": "编辑后确认发送的文字"}, "send-reply"
    )
    assert store.reply(draft["id"])["status"] == "sent", workflow.state
    assert await web.page.locator(".is-self .text").inner_text() == "编辑后确认发送的文字"
    assert store.attempts_today() == 1
    assert store.history()[0]["status"] == "contacted"

    await add_reply_message(web.page, "可以详细介绍吗？")
    await workflow.run(config, "read", params, "read-again")

    class SlowModel:
        async def request(self, method, **params):
            await add_reply_message(web.page, "先不用了，谢谢。")
            return {"message": "已经过期的回复"}

    workflow.bridge = SlowModel()
    await workflow.run(config, "generate", params, "changed-chat")
    assert workflow.state["status"] == "error"
    assert "会话已变化" in workflow.state["note"]
    assert workflow.state["draft"] is None
    assert len(store.reply_history()) == 1


async def test_auto_inbox_reads_without_sending_then_replies_once_to_unanswered_text(
    web, config, store
):
    from contextlib import asynccontextmanager

    from boss_cli.auto_replies import AutoReplyWorkflow

    await web.page.route(
        "**/web/geek/chat",
        lambda route: route.fulfill(body=INBOX_HTML, content_type="text/html; charset=utf-8"),
    )
    await web.page.goto("https://www.zhipin.com/web/geek/chat")
    calls = []
    long_reply = "我对岗位的理解是先明确问题，再验证方案与实际效果。" * 100 + "以上是完整说明。"

    class Model:
        async def request(self, method, **params):
            calls.append(params)
            return {"message": long_reply, "usage": {"completion_tokens": 1800}}

    @asynccontextmanager
    async def factory(*_args):
        yield web.session

    config.auto_reply.settle_seconds = 0
    workflow = AutoReplyWorkflow(store, store.directory, Model(), factory)
    scanned = await workflow.cycle(config, "inbox-read", scan_only=True)
    assert scanned["status"] == "completed", workflow.state
    assert scanned["matched"] == 2, store.reply_events()
    assert not calls
    assert store.history() == []
    assert store.attempts_today() == 0
    assert {row["job_id"] for row in store.reply_contacts()} == {
        "inbox001",
        "inbox002",
        "inbox003",
        "inbox004",
    }
    sent = await workflow.cycle(config, "inbox-send")
    assert sent["status"] == "completed", workflow.state
    assert sent["sent"] == 2, store.reply_events()
    assert len(calls) == 2
    assert all("max_tokens" not in call["config"] for call in calls)
    assert all(
        row["message"] == long_reply and row["source"] == "auto" for row in store.reply_history()
    )
    actual = await web.page.evaluate("window.inboxFixture.sends")
    assert {row["id"] for row in actual} == {"inbox001", "inbox002"}
    assert all(row["text"] == long_reply for row in actual)
    assert store.attempts_today() == 2
    assert store.history() == []
    await web.page.evaluate("document.getElementById('global-unread').textContent='999'")
    again = await workflow.cycle(config, "inbox-again")
    assert again["sent"] == 0
    assert len(calls) == 2
    kinds = {event["kind"] for event in store.reply_events()}
    assert {
        "auto_pending",
        "reply_generate",
        "reply_generated",
        "reply_send",
        "reply_sent",
        "auto_skipped",
    } <= kinds


async def inbox_workflow(web, config, store, model):
    from contextlib import asynccontextmanager

    from boss_cli.auto_replies import AutoReplyWorkflow

    await web.page.route(
        "**/web/geek/chat",
        lambda route: route.fulfill(body=INBOX_HTML, content_type="text/html; charset=utf-8"),
    )
    await web.page.goto("https://www.zhipin.com/web/geek/chat")
    await web.page.evaluate("contacts.splice(1); renderRows()")
    config.auto_reply.settle_seconds = 0

    @asynccontextmanager
    async def factory(*_args):
        yield web.session

    return AutoReplyWorkflow(store, store.directory, model, factory)


async def test_auto_reply_discards_generation_when_new_message_arrives(web, config, store):
    calls = []

    class Model:
        async def request(self, method, **params):
            calls.append(params)
            if len(calls) == 1:
                await web.page.evaluate(
                    "inboxFixture.addMessage('inbox001','补充：请先介绍项目目标。')"
                )
            return {"message": "过期的回复" if len(calls) == 1 else "这是针对项目目标的完整回复。"}

    workflow = await inbox_workflow(web, config, store, Model())
    first = await workflow.cycle(config, "incoming-during-model")
    assert first["sent"] == 0
    assert not store.reply_history()
    assert store.attempts_today() == 0
    second = await workflow.cycle(config, "new-context")
    assert second["sent"] == 1, workflow.state
    assert calls[-1]["messages"][-1]["content"] == "补充：请先介绍项目目标。"
    assert await web.page.evaluate("inboxFixture.sends") == [
        {"id": "inbox001", "text": "这是针对项目目标的完整回复。"}
    ]


async def test_auto_reply_unknown_receipt_blocks_new_inbound_and_reenable(web, config, store):
    calls = []

    class Model:
        async def request(self, method, **params):
            calls.append(params)
            return {"message": "这一条只提交一次。"}

    workflow = await inbox_workflow(web, config, store, Model())
    await web.page.evaluate("inboxFixture.receipt=false")
    first = await workflow.cycle(config, "missing-receipt")
    assert first["sent"] == 0
    assert store.reply_history()[0]["status"] == "unknown"
    assert store.attempts_today() == 1
    await web.page.evaluate("inboxFixture.addMessage('inbox001','你还在吗？')")
    workflow.prepare()
    second = await workflow.cycle(config, "uncertain-reenable")
    assert second["sent"] == 0
    assert len(calls) == 1
    assert await web.page.evaluate("inboxFixture.sends.length") == 1
    assert any("待核对" in event["message"] for event in store.reply_events())


async def test_auto_reply_respects_quota_and_preserves_existing_web_draft(web, config, store):
    class Model:
        async def request(self, *_args, **_kwargs):
            pytest.fail("Draft preservation and quota checks must precede model generation")

    workflow = await inbox_workflow(web, config, store, Model())
    await web.page.evaluate(
        "inboxFixture.select('inbox001'); document.getElementById('chat-input').textContent='正在编辑的私人草稿'"
    )
    blocked = await workflow.cycle(config, "preserve-web-draft")
    assert blocked["status"] == "needs_attention"
    assert await web.page.locator("#chat-input").inner_text() == "正在编辑的私人草稿"
    assert store.attempts_today() == 0
    await web.page.locator("#chat-input").fill("")
    config.run.daily_limit = 1
    store.event("test-run", "INFO", "send_reserved", "已使用最后一个发送名额")
    limited = await workflow.cycle(config, "limit-auto")
    assert limited["status"] == "needs_attention"
    assert "今日发送上限" in limited["note"]
    assert await web.page.evaluate("inboxFixture.sends.length") == 0


async def test_inbox_recycled_row_and_wrong_public_job_are_rejected(web, config, store):
    from boss_cli.inbox import InboxReader

    await inbox_workflow(web, config, store, None)
    reader = InboxReader(web)
    await reader.open()
    entry = (await reader.rows())[0]
    await web.page.locator(".friend-content .name-text").evaluate(
        "e => e.textContent='另一位招聘者'"
    )
    with pytest.raises(LayoutChanged, match="列表已更新"):
        await reader.select(entry)
    assert not await web.page.locator(".chat-conversation").is_visible()
    await web.page.locator(".friend-content .name-text").evaluate("e => e.textContent='李招聘'")
    await web.session.context.route(
        "**/job_detail/*.html",
        lambda route: route.fulfill(
            body=DETAIL_HTML.replace(
                "<h1>AI应用工程师</h1>", "<h1>其他职位</h1><p>AI应用工程师</p>"
            ),
            content_type="text/html; charset=utf-8",
        ),
    )
    with pytest.raises(LayoutChanged, match="职位名称与公开职位详情不一致"):
        await reader.select(entry)
    assert store.attempts_today() == 0


async def test_identical_inbox_previews_do_not_hide_distinct_contacts(web, config, store):
    workflow = await inbox_workflow(web, config, store, None)
    await web.page.evaluate("contacts.push({...contacts[0], id:'inbox002'}); renderRows()")
    run = await workflow.cycle(config, "same-names", scan_only=True)
    assert run["matched"] == 2, workflow.state
    assert {row["job_id"] for row in store.reply_contacts()} == {"inbox001", "inbox002"}
