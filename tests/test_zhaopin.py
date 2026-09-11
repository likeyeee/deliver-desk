import asyncio
import json
from types import SimpleNamespace

import pytest
from playwright.async_api import async_playwright

from boss_cli.browser import LayoutChanged, NeedsAttention
from boss_cli.config import Config
from boss_cli.control import Controller
from boss_cli.models import canonical_job_url, salary_range
from boss_cli.runner import Runner
from boss_cli.zhaopin import ZhaopinAdapter

ZHAOPIN_LIST_HTML = """<!doctype html><html><body>
<span class="c-login__top__name">示例用户</span>
<input class="query-sug__input" placeholder="搜索职位、公司"><button class="query-sug__button" onclick="search()">搜索</button>
<div class="filter-select-box filter-region-box"><div class="filter-select-box__trigger" onclick="document.querySelector('.s-dialog').hidden=false"><span class="filter-select-box__label">厦门</span></div></div>
<div id="filters"></div>
<div class="s-dialog" hidden><input placeholder="搜索城市名/区县" oninput="document.querySelector('.s-option__label').textContent='甘肃-'+this.value"><div class="s-option"><p class="s-option__label" onclick="city(this)">甘肃-酒泉</p></div></div>
<div class="job-list-panel"></div>
<div class="job-detail-modules"><span class="job-detail-summary__title-text"></span><span class="job-detail-summary__company-name"></span><a class="job-company-info__view-all">查看更多信息</a></div>
<script>
const data=[['zp001','办公室文员','示例甲公司'],['zp002','行政文员','示例乙公司']];
window.cardClicks=0;window.revision=0;
function render(){revision++;document.querySelector('.job-list-panel').innerHTML=data.map(([id,title,company])=>`<div class="job-card" data-fixture-id="${id}" onclick="select(this)"><div class="job-card__title-main">${title}</div><span class="job-card__salary">4000-6000元</span><a class="job-card__company-name">${company}</a><span class="job-card__location">酒泉</span></div>`).join('')+'<div class="job-list-panel__status">没有更多了</div>';select(document.querySelector('.job-card'));}
function select(card){cardClicks++;document.querySelectorAll('.job-card').forEach(e=>e.classList.toggle('job-card--active',e===card));if(window.stalePane)return;const row=data.find(r=>r[0]===card.dataset.fixtureId);setTimeout(()=>{document.querySelector('.job-detail-summary__title-text').textContent=row[1];document.querySelector('.job-detail-summary__company-name').textContent=row[2];document.querySelector('.job-company-info__view-all').href='https://www.zhaopin.com/jobdetail/'+row[0]+'.htm?track=discard';},100);}
function change(){if(!window.staleResults)setTimeout(render,160);}
function search(){const u=new URL(location);u.searchParams.set('kw',document.querySelector('.query-sug__input').value);history.replaceState({},'',u);change();}
function city(e){const value=e.textContent.split('-').at(-1);document.querySelector('.filter-region-box .filter-select-box__label').textContent=value;document.querySelector('.s-dialog').hidden=true;const u=new URL(location);u.searchParams.set('jl',value);history.replaceState({},'',u);change();}
for(const [label,values] of [['薪资',['不限','4K-6K']],['学历',['不限','本科']],['经验',['全部','经验不限','1-3年']]]){const root=document.createElement('div');root.className='filter-select-box';root.innerHTML='<div class="filter-select-box__trigger"><span class="filter-select-box__label">'+label+'</span></div><div class="filter-select-box__panel" hidden><ul>'+values.map((v,i)=>'<li class="filter-select-box__item'+(i===0?' filter-select-box__item--selected':'')+'"><span class="filter-select-box__item-text">'+v+'</span></li>').join('')+'</ul></div>';root.querySelector('.filter-select-box__trigger').onclick=()=>{const panel=root.querySelector('.filter-select-box__panel');panel.hidden=!panel.hidden;};root.querySelectorAll('li').forEach(li=>li.onclick=()=>{root.querySelectorAll('li').forEach(n=>n.classList.toggle('filter-select-box__item--selected',n===li));root.querySelector('.filter-select-box__label').textContent=li.innerText;root.querySelector('.filter-select-box__panel').hidden=true;change();});document.querySelector('#filters').append(root);}
const industry=document.createElement('div');industry.className='filter-select-box';industry.innerHTML='<div class="filter-select-box__trigger"><span class="filter-select-box__label">公司行业</span></div><div class="filter-select-box__panel" hidden><ul class="filter-select-box__column"><li class="filter-select-box__item filter-select-box__item--has-child"><span class="filter-select-box__item-text">汽车/摩托车/电动车</span></li></ul><ul class="filter-select-box__column" hidden><li class="filter-select-box__item"><span class="filter-select-box__item-text">不限</span></li><li class="filter-select-box__item"><span class="filter-select-box__item-text">汽车4S店/经销商</span></li></ul></div>';
industry.querySelector('.filter-select-box__trigger').onclick=()=>{const p=industry.querySelector('.filter-select-box__panel');p.hidden=!p.hidden;};industry.querySelector('.filter-select-box__item--has-child').onclick=()=>{industry.querySelectorAll('.filter-select-box__column')[1].hidden=false;};industry.querySelectorAll('.filter-select-box__column')[1].querySelectorAll('li').forEach(li=>li.onclick=()=>{industry.querySelectorAll('li').forEach(n=>n.classList.toggle('filter-select-box__item--selected',n===li));industry.querySelector('.filter-select-box__label').textContent=li.innerText;industry.querySelector('.filter-select-box__panel').hidden=true;change();});document.querySelector('#filters').append(industry);
render();
</script><style>body{font:16px sans-serif;padding:24px}button,.filter-select-box__trigger,.filter-select-box__item,.s-option__label{padding:8px;cursor:pointer}.job-card{padding:12px;border:1px solid #ddd;width:440px}.job-card__title-main{padding:10px}.job-detail-modules{position:fixed;right:24px;top:24px;width:400px}.filter-select-box{display:inline-block}.filter-select-box__panel{position:absolute;background:white;z-index:5}.s-dialog:not([hidden]){position:fixed;inset:100px;background:white;z-index:10}[hidden]{display:none!important}</style></body></html>"""

ZHAOPIN_DETAIL_HTML = """<!doctype html><html><body>
<span class="c-login__top__name">示例用户</span>
<section class="summary-planes"><h1 class="summary-planes__title">_TITLE_</h1><div class="summary-planes__left"><span class="summary-planes__salary">4000-6000元</span><p>酒泉</p><p>经验不限</p><p>本科</p></div><div class="summary-planes__action"><button onclick="apply(this)">立即投递</button></div></section>
<a class="company-info__name">_COMPANY_</a><section class="describtion-card"><h2>职位描述</h2><p>处理日常办公资料和会议安排，使用办公软件。</p></section>
<div class="deliver-greeting-modal" hidden><h3 class="deliver-greeting-modal__title">已向对方发送简历和打招呼语</h3><p class="deliver-greeting-modal__content-text">您好，我对贵司【_TITLE_】很感兴趣，请您查看我的简历。</p><button class="deliver-greeting-modal__btn--secondary" onclick="this.parentElement.hidden=true">留在此页</button><button>继续沟通</button></div>
<script>
const id='_ID_';
const applyButton=document.querySelector('.summary-planes__action button');applyButton.disabled=true;
fetch('https://i.zhaopin.com/__fixture_greetings').then(r=>r.json()).then(state=>{const selected=state.rows.find(r=>r.selected);window.fixtureGreeting=state.wrongReceipt?'错误岗位招呼':selected.category==='自定义'?selected.text:'';applyButton.disabled=false;});
function apply(button){if(!event.isTrusted)throw Error('native input required');localStorage.setItem('applied:'+id,String(Number(localStorage.getItem('applied:'+id)||0)+1));button.textContent='继续沟通';if(window.fixtureGreeting)document.querySelector('.deliver-greeting-modal__content-text').textContent=window.fixtureGreeting;if(!window.missingReceipt)document.querySelector('.deliver-greeting-modal').hidden=false;}
if(localStorage.getItem('applied:'+id))document.querySelector('.summary-planes__action button').textContent='继续沟通';
</script><style>body{font:16px sans-serif;padding:30px}button{padding:12px}.deliver-greeting-modal:not([hidden]){position:fixed;inset:50px;background:white;border:1px solid #ccc;padding:40px}[hidden]{display:none!important}</style></body></html>"""

ZHAOPIN_LOGIN_HTML = """<!doctype html><html><body><h1>智联模拟登录</h1><button class="zppp-panel-normal-bar__img" onclick="this.hidden=true;document.querySelector('#qr').hidden=false">二维码入口</button><div id="qr" hidden>请使用微信扫一扫<button onclick="location.href='https://www.zhaopin.com/jobs/?pageMode=search'">模拟扫码完成</button></div></body></html>"""

ZHAOPIN_FEEDBACK_HTML = """<!doctype html><html><body><div class="st-nav"><ul><li class="on"><a>投递成功</a></li></ul></div><section class="ji-item" hidden><a class="ji-item-info-jobName" href="http://jobs.zhaopin.com/zp001.htm">办公室文员</a><a class="ji-item-info-companyName">示例甲公司</a><div class="ji-item-status"><p>成功投递</p></div></section></body></html>"""


ZHAOPIN_SETTINGS_HTML = """<!doctype html><html><body>
<div class="fixture-sticky-header">示例固定搜索栏</div>
<div class="greeting-left-nav__item" onclick="openGreeting()">招呼语</div>
<section class="greeting-panel" hidden><h1 class="greeting-panel__title">招呼语</h1><button class="greeting-panel__add-btn" onclick="edit('')">添加招呼语</button><div id="tabs"></div><ul class="greeting-list"></ul></section>
<div class="phrase-modal" hidden><h2>招呼语</h2><textarea maxlength="500"></textarea><button class="phrase-modal__btn--cancel" onclick="document.querySelector('.phrase-modal').hidden=true">取消</button><button class="phrase-modal__btn--ok" onclick="save()">保存</button></div>
<script>
let state,category='',editing='';
async function refresh(){state=await fetch('/__fixture_greetings').then(r=>r.json());}
async function openGreeting(){await refresh();category=state.rows.find(r=>r.selected).category;document.querySelector('.greeting-panel').hidden=false;render();document.querySelector('#tabs').scrollIntoView({block:'start',behavior:'instant'});setTimeout(()=>document.querySelector('#tabs').scrollIntoView({block:'start',behavior:'instant'}),180);}
function render(){document.querySelector('#tabs').innerHTML=['自定义','常规'].map(c=>`<span class="greeting-panel__tab ${c===category?'greeting-panel__tab--active':''}" onclick="category='${c}';render()">${c}</span>`).join('');document.querySelector('.greeting-list').innerHTML='';for(const row of state.rows.filter(r=>r.category===category)){const li=document.createElement('li');li.id=row.id;li.className='greeting-list__item';li.innerHTML='<p class="greeting-list__content"></p><div class="greeting-list__actions"><div class="greeting-list__default"><img class="greeting-list__radio-icon" src="/assets/'+(row.selected?'radio-active-icon.':'radio-icon.')+'png">设为默认</div></div>';li.querySelector('p').textContent=row.text;li.querySelector('.greeting-list__default').onclick=()=>setDefault(row.id);if(category==='自定义'){const icon=document.createElement('img');icon.className='greeting-list__action-icon';icon.src='/assets/edit-icon.png';icon.width=28;icon.height=28;icon.onclick=()=>edit(row.id);li.querySelector('.greeting-list__actions').append(icon);}document.querySelector('.greeting-list').append(li);}}
function edit(id){editing=id;document.querySelector('.phrase-modal textarea').value=state.rows.find(r=>r.id===id)?.text||'';document.querySelector('.phrase-modal').hidden=false;}
async function mutate(op,fields){await fetch('/__fixture_greetings',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({op,...fields})});await refresh();}
async function save(){const text=document.querySelector('.phrase-modal textarea').value;await mutate(editing?'edit':'add',{id:editing,text});if(state.failSave)return;category='自定义';render();document.querySelector('.phrase-modal').hidden=true;}
async function setDefault(id){await mutate('default',{id});render();}
</script><style>body{font:16px sans-serif;padding:140px 30px 30px;min-height:1800px}.fixture-sticky-header{position:fixed;inset:0 0 auto;height:80px;background:white;z-index:10}.greeting-left-nav__item,.greeting-panel__tab,.greeting-list__default{display:inline-block;padding:12px;cursor:pointer}.greeting-list__item{padding:16px}.greeting-list__action-icon{cursor:pointer}.phrase-modal:not([hidden]){position:fixed;inset:90px;background:white;border:1px solid gray;padding:30px;z-index:11}.phrase-modal textarea{width:500px;height:120px}button{padding:10px}[hidden]{display:none!important}</style></body></html>"""


def greeting_fixture_state():
    return {
        "rows": [
            {
                "id": "greeting-item-14",
                "text": "您好，请查看我的简历。",
                "category": "常规",
                "selected": True,
            },
            {
                "id": "greeting-item-90",
                "text": "用户原有自定义招呼",
                "category": "自定义",
                "selected": False,
            },
        ],
        "writes": [],
    }


def mutate_greeting_fixture(state, data):
    state["writes"].append(data)
    if data["op"] in {"add", "edit"} and state.get("failSave"):
        return
    if data["op"] == "add":
        state["rows"].append(
            {
                "id": f"greeting-item-{100 + len(state['rows'])}",
                "text": data["text"],
                "category": "自定义",
                "selected": False,
            }
        )
    elif data["op"] == "edit":
        next(row for row in state["rows"] if row["id"] == data["id"])["text"] = data["text"]
    elif data["op"] == "default" and not state.get("failDefault"):
        for row in state["rows"]:
            row["selected"] = row["id"] == data["id"]


@pytest.fixture
async def zp(config, store):
    config.platform = "zhaopin"
    config.message.mode = "platform"
    config.search.city = "酒泉"
    config.search.keywords = ["文员"]
    config.match.title_any = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(channel="chrome", headless=True)
        context = await browser.new_context(viewport={"width": 1400, "height": 1100})

        settings = greeting_fixture_state()

        async def fulfill(route):
            path = route.request.url
            if "/__fixture_greetings" in path:
                if route.request.method == "POST":
                    if route.request.post_data_json["op"] == "add" and settings.get("saveDelay"):
                        settings["saving"] = True
                        await asyncio.sleep(settings["saveDelay"])
                    mutate_greeting_fixture(settings, route.request.post_data_json)
                await route.fulfill(
                    status=200,
                    content_type="application/json",
                    headers={"access-control-allow-origin": "*"},
                    body=json.dumps(settings),
                )
                return
            if "/assets/" in path:
                await route.fulfill(
                    status=200,
                    content_type="image/svg+xml",
                    body='<svg xmlns="http://www.w3.org/2000/svg" width="28" height="28"><rect width="28" height="28"/></svg>',
                )
                return
            if "/im/greeting/setting" in path:
                html = ZHAOPIN_SETTINGS_HTML
            elif "/schedule" in path:
                html = ZHAOPIN_FEEDBACK_HTML
            elif "/jobdetail/" in path:
                second = "zp002" in path
                html = (
                    ZHAOPIN_DETAIL_HTML.replace("_ID_", "zp002" if second else "zp001")
                    .replace("_TITLE_", "行政文员" if second else "办公室文员")
                    .replace("_COMPANY_", "示例乙公司" if second else "示例甲公司")
                )
            else:
                html = ZHAOPIN_LIST_HTML
            await route.fulfill(status=200, content_type="text/html; charset=utf-8", body=html)

        await context.route("**/*", fulfill)
        page = await context.new_page()
        await page.goto("https://www.zhaopin.com/jobs/?pageMode=search")
        session = SimpleNamespace(
            page=page, context=context, owned_pages=[page], directory=store.directory
        )
        adapter = ZhaopinAdapter(session, config, Controller(store, "test-run"))
        adapter.fixture_settings = settings
        yield adapter
        await browser.close()


def test_platform_urls_and_salary():
    zp_id, url = canonical_job_url("http://www.zhaopin.com/jobdetail/abc123.htm?tracking=private")
    assert zp_id == "zhaopin:abc123"
    assert url == "https://www.zhaopin.com/jobdetail/abc123.htm"
    boss_id, _ = canonical_job_url("https://www.zhipin.com/job_detail/abc123.html")
    assert boss_id != zp_id
    assert Config.model_validate({}).platform == "boss"
    assert salary_range("4000-6000元·13薪") == (4000, 6000)
    assert salary_range("800-1000元/周") is None
    for invalid in [
        "https://www.zhaopin.com.evil.test/jobdetail/x.htm",
        "https://www.zhaopin.com@evil.test/jobdetail/x.htm",
        "https://www.zhaopin.com/companydetail/x.htm",
    ]:
        with pytest.raises(ValueError):
            canonical_job_url(invalid)


async def test_search_city_filters_and_card_detail_identity(zp):
    zp.config.search.filters = {
        "薪资待遇": "4K-6K",
        "学历要求": "本科",
        "工作经验": "经验不限",
        "公司行业": "汽车/摩托车/电动车 > 汽车4S店/经销商",
    }
    await zp.search("文员")
    jobs = await zp.collect()
    assert [(j.job_id, j.company, j.platform) for j in jobs] == [
        ("zhaopin:zp001", "示例甲公司", "zhaopin"),
        ("zhaopin:zp002", "示例乙公司", "zhaopin"),
    ]
    clicks = await zp.page.evaluate("cardClicks")
    assert await zp.collect() == jobs
    assert await zp.page.evaluate("cardClicks") == clicks
    assert not await zp.more(set())
    full = await zp.inspect_job(jobs[0])
    assert full.salary == "4000-6000元"
    assert full.education == "本科"
    assert full.description.startswith("职位描述")


async def test_cascade_requires_a_complete_observed_path(zp):
    with pytest.raises(NeedsAttention, match="完整路径"):
        await zp.select_filter("公司行业", "汽车/摩托车/电动车")
    assert await zp.page.locator(".filter-select-box__label").last.inner_text() == "公司行业"


async def test_runner_sends_resume_once_without_custom_or_ai(zp, store):
    zp.config.message.mode = "platform"
    await zp.search("文员")
    jobs = await zp.collect()

    async def unexpected(**kwargs):
        raise AssertionError("网站当前招呼模式不应调用 AI")

    runner = Runner(zp.config, store, "test-run", send=True, greeting=unexpected)
    await runner.process(zp, jobs[0])
    assert runner.counts["sent"] == 1
    assert store.blocked(jobs[0].job_id) == "sent"
    assert store.history()[0]["platform"] == "zhaopin"
    assert "【办公室文员】" in store.history()[0]["message"]
    assert await zp.detail.evaluate("localStorage.getItem('applied:zp001')") == "1"
    await runner.process(zp, jobs[0])
    assert runner.attempts == 1
    assert store.attempts_today() == 1
    assert not store.reply_contacts()
    with pytest.raises(ValueError, match="BOSS"):
        store.reply_job(jobs[0].job_id)
    with pytest.raises(ValueError, match="BOSS"):
        store.mark_reply_contact(jobs[0])


async def test_hidden_receipt_never_confirms_submission(zp, store):
    jobs = await zp.collect()
    original = zp.inspect_job

    async def inspect(job):
        result = await original(job)
        await zp.detail.evaluate("window.missingReceipt=true")
        return result

    zp.inspect_job = inspect
    zp.config.browser.timeout_seconds = 0.3
    runner = Runner(zp.config, store, "test-run", send=True)
    with pytest.raises(NeedsAttention):
        await runner.process(zp, jobs[0])
    assert store.blocked(jobs[0].job_id) == "unknown"
    assert not await zp.verify_delivery(jobs[0])
    assert await zp.detail.evaluate("localStorage.getItem('applied:zp001')") == "1"
    await runner.process(zp, jobs[0])
    assert runner.attempts == 1


async def test_preflight_refuses_changed_target(zp):
    job = (await zp.collect())[0]
    await zp.inspect_job(job)
    await zp.detail.locator(".company-info__name").evaluate("e=>e.textContent='其他公司'")
    with pytest.raises(LayoutChanged, match="不一致"):
        await zp.greet(job, "不应发送")
    assert await zp.detail.evaluate("localStorage.getItem('applied:zp001')") is None


async def test_last_instant_target_change_never_submits(zp):
    job = (await zp.collect())[0]
    await zp.inspect_job(job)
    await zp.detail.locator(".summary-planes__action button").evaluate(
        "e=>e.addEventListener('pointerdown',()=>document.querySelector('.company-info__name').textContent='其他公司')"
    )
    with pytest.raises(LayoutChanged, match="不一致"):
        await zp.greet(job, "不应发送")
    assert await zp.detail.evaluate("localStorage.getItem('applied:zp001')") is None


async def test_records_platform_greeting_without_assuming_a_title_template(zp):
    job = (await zp.collect())[0]
    await zp.inspect_job(job)
    await zp.detail.locator(".deliver-greeting-modal__content-text").evaluate(
        "e=>e.textContent='您好，简历中有相关经验，期待沟通。'"
    )
    result = await zp.greet(job, "不应额外发送")
    assert result.status == "sent"
    assert result.message == "您好，简历中有相关经验，期待沟通。"
    assert await zp.detail.evaluate("localStorage.getItem('applied:zp001')") == "1"


async def test_stale_pane_does_not_reuse_previous_job_id(zp):
    await zp.page.wait_for_timeout(200)
    await zp.page.evaluate("window.stalePane=true")
    zp.config.browser.timeout_seconds = 0.5
    with pytest.raises(LayoutChanged, match="未一致刷新"):
        await zp.collect()


async def test_stale_results_not_accepted_after_search(zp):
    await zp.page.wait_for_timeout(200)
    await zp.begin_result_change()
    await zp.page.evaluate(
        "window.staleResults=true;document.querySelector('.query-sug__input').value='其他';search()"
    )
    zp.config.browser.timeout_seconds = 0.6
    with pytest.raises(LayoutChanged, match="列表未加载"):
        await zp.wait_results(changed=True)


async def test_nationwide_and_challenge_stop(zp):
    with pytest.raises(NeedsAttention, match="具体城市"):
        await zp.select_city("全国")
    await zp.page.locator("body").evaluate("e=>e.innerHTML='<p>安全验证</p>'")
    with pytest.raises(NeedsAttention, match="验证"):
        await zp.gate(zp.page)


async def test_readonly_feedback_verifies_exact_job_id_and_company(zp):
    job = (await zp.collect())[0]
    await zp.inspect_job(job)

    async def feedback(route):
        await route.fulfill(
            status=200,
            content_type="text/html; charset=utf-8",
            body=ZHAOPIN_FEEDBACK_HTML.replace(" hidden", ""),
        )

    await zp.session.context.route("**/schedule*", feedback)
    assert await zp.verify_delivery(job)
    assert await zp.detail.evaluate("localStorage.getItem('applied:zp001')") is None
    wrong = ZHAOPIN_FEEDBACK_HTML.replace(" hidden", "").replace(
        "jobs.zhaopin.com/zp001.htm", "jobs.zhaopin.com/wrong.htm"
    )

    async def wrong_feedback(route):
        await route.fulfill(status=200, content_type="text/html; charset=utf-8", body=wrong)

    await zp.session.context.route("**/schedule*", wrong_feedback)
    zp.config.browser.timeout_seconds = 0.4
    assert not await zp.verify_delivery(job)


async def test_ai_preview_never_changes_settings_or_reserves(zp, store):
    zp.config.message.mode = "ai"
    calls = []

    async def greeting(*, job, control):
        calls.append(job.description)
        return f"您好，我希望应聘{job.title}，有相关资料整理经验，期待交流。"

    runner = Runner(zp.config, store, "test-run", send=False, greeting=greeting)
    for job in await zp.collect():
        await runner.process(zp, job)
    assert len(calls) == 2 and all(calls)
    assert runner.attempts == 0 and not zp.fixture_settings["writes"]
    assert zp.greetings.page is None


async def test_per_job_ai_settings_receipts_reuse_and_restore(zp, store):
    zp.config.message.mode = "ai"
    messages = []

    async def greeting(*, job, control):
        message = (
            f"您好，关注贵司{job.title}岗位，我的资料整理与办公软件经验可用于岗位工作，期待交流。"
        )
        messages.append(message)
        return message

    runner = Runner(zp.config, store, "test-run", send=True, greeting=greeting)
    for job in await zp.collect():
        await runner.process(zp, job)
    assert runner.counts["sent"] == 2 and runner.attempts == 2
    assert len(set(messages)) == 2
    assert {row["message"] for row in store.history()} == set(messages)
    assert len(zp.fixture_settings["rows"]) == 3
    assert zp.fixture_settings["rows"][1]["text"] == "用户原有自定义招呼"
    assert [w["op"] for w in zp.fixture_settings["writes"]].count("add") == 1
    assert [w["op"] for w in zp.fixture_settings["writes"]].count("edit") == 1
    await zp.greetings.restore()
    assert zp.fixture_settings["rows"][0]["selected"]
    assert not store.greeting_settings("zhaopin").get("restore")
    assert store.expected_delivery_message("zhaopin:zp001") in messages


@pytest.mark.parametrize("failure", ["failSave", "failDefault"])
async def test_settings_failure_prevents_any_reservation_or_application(zp, store, failure):
    zp.config.message.mode = "custom"
    zp.config.browser.timeout_seconds = 1.2
    zp.fixture_settings[failure] = True
    job = (await zp.collect())[0]
    runner = Runner(zp.config, store, "test-run", send=True)
    with pytest.raises(NeedsAttention, match="未.*投递"):
        await runner.process(zp, job)
    assert runner.attempts == 0 and store.attempts_today() == 0
    assert await zp.detail.evaluate("localStorage.getItem('applied:zp001')") is None
    await zp.greetings.restore()
    assert zp.fixture_settings["rows"][0]["selected"]


async def test_settings_click_error_stops_batch_before_next_job(zp, store, monkeypatch):
    zp.config.message.mode = "custom"
    attempted = []

    async def covered(job, message):
        attempted.append(job.job_id)
        raise LayoutChanged("目标控件被其他内容遮挡")

    monkeypatch.setattr(zp.greetings, "prepare", covered)
    runner = Runner(zp.config, store, "test-run", send=True)
    with pytest.raises(NeedsAttention, match="招呼设置操作失败，未投递"):
        await runner.execute(zp)
    assert len(attempted) == 1
    assert runner.attempts == 0 and store.attempts_today() == 0
    assert not zp.fixture_settings["writes"]


async def test_changed_default_before_click_stops_without_sending(zp):
    zp.config.message.mode = "custom"
    job = await zp.inspect_job((await zp.collect())[0])
    message = "您好，我有办公资料整理经验，希望交流。"
    await zp.greetings.prepare(job, message)
    zp.fixture_settings["rows"][-1]["text"] = "外部改成其他岗位招呼"
    with pytest.raises(NeedsAttention, match="不一致"):
        await zp.greet(job, message)
    assert await zp.detail.evaluate("localStorage.getItem('applied:zp001')") is None


async def test_wrong_greeting_receipt_retains_expected_original_and_blocks_retry(zp, store):
    zp.config.message.mode = "custom"
    zp.fixture_settings["wrongReceipt"] = True
    job = (await zp.collect())[0]
    runner = Runner(zp.config, store, "test-run", send=True)
    with pytest.raises(NeedsAttention):
        await runner.process(zp, job)
    history = store.history()[0]
    assert history["status"] == "partial" and "错误岗位招呼" in history["note"]
    assert "错误" not in history["message"]
    assert not await zp.verify_delivery(job, history["message"])
    await runner.process(zp, job)
    assert runner.attempts == 1
    await zp.greetings.restore()


async def test_stop_restores_default_and_next_adapter_reuses_owned_template(zp, store):
    from boss_cli.zhaopin_greetings import ZhaopinGreetings

    job = await zp.inspect_job((await zp.collect())[0])
    await zp.greetings.prepare(job, "首个岗位的完整招呼。")
    zp.control.stopped = True
    await zp.greetings.restore()
    assert zp.fixture_settings["rows"][0]["selected"]
    zp.control.stopped = False
    zp.greetings = ZhaopinGreetings(zp)
    await zp.greetings.prepare(job, "第二次运行针对该岗位改写的招呼。")
    assert len(zp.fixture_settings["rows"]) == 3
    # If the user's original default is itself the managed entry, restore its text too.
    zp.greetings.state.pop("restore")
    zp.greetings.save()
    zp.greetings = ZhaopinGreetings(zp)
    await zp.greetings.prepare(job, "第三次运行临时招呼。")
    await zp.greetings.restore()
    assert zp.fixture_settings["rows"][-1]["text"] == "第二次运行针对该岗位改写的招呼。"


async def test_oversized_and_emoji_greetings_never_touch_settings(zp, store):
    zp.config.message.mode = "ai"

    async def greeting(**kwargs):
        return "😀" * 251

    job = (await zp.collect())[0]
    runner = Runner(zp.config, store, "test-run", send=True, greeting=greeting)
    with pytest.raises(NeedsAttention, match="500"):
        await runner.process(zp, job)
    assert not zp.fixture_settings["writes"] and runner.attempts == 0


async def test_last_instant_editor_change_is_not_saved(zp):
    job = await zp.inspect_job((await zp.collect())[0])
    original = zp.greetings.open

    async def open_settings():
        await original()
        await zp.greetings.page.locator(".phrase-modal__btn--ok").evaluate(
            "e=>e.addEventListener('pointerdown',()=>document.querySelector('.phrase-modal textarea').value='另一条消息')"
        )

    zp.greetings.open = open_settings
    zp.config.browser.timeout_seconds = 0.5
    with pytest.raises(NeedsAttention, match="未确认.*保存"):
        await zp.greetings.prepare(job, "原本为这个岗位准备的招呼。")
    assert not zp.fixture_settings["writes"]
    assert await zp.detail.evaluate("localStorage.getItem('applied:zp001')") is None


async def test_interrupted_save_recovers_created_template_without_adding_another(zp, store):
    from boss_cli.control import StopRequested
    from boss_cli.zhaopin_greetings import ZhaopinGreetings

    job = await zp.inspect_job((await zp.collect())[0])
    original_wait = zp.greetings.wait

    async def wait(read, note, **kwargs):
        result = await original_wait(read, note, **kwargs)
        if "保存成功" in note:
            raise StopRequested
        return result

    zp.greetings.wait = wait
    with pytest.raises(StopRequested):
        await zp.greetings.prepare(job, "中断前已经保存的完整招呼。")
    assert store.greeting_settings("zhaopin")["pending"]
    zp.greetings = ZhaopinGreetings(zp)
    await zp.greetings.prepare(job, "恢复后生成的岗位招呼。")
    assert len(zp.fixture_settings["rows"]) == 3
    assert len([w for w in zp.fixture_settings["writes"] if w["op"] == "add"]) == 1
    await zp.greetings.restore()
    assert zp.fixture_settings["rows"][0]["selected"]


async def test_stop_during_save_waits_for_readback_before_restoring(zp, store):
    from boss_cli.control import StopRequested

    job = await zp.inspect_job((await zp.collect())[0])
    zp.fixture_settings["saveDelay"] = 0.4
    task = asyncio.create_task(zp.greetings.prepare(job, "保存请求期间停止的岗位招呼。"))
    async with asyncio.timeout(4):
        while not zp.fixture_settings.get("saving"):
            await asyncio.sleep(0.02)
    zp.control.stopped = True
    with pytest.raises(StopRequested):
        await task
    assert store.greeting_settings("zhaopin")["managed"]["text"] == "保存请求期间停止的岗位招呼。"
    assert not store.greeting_settings("zhaopin").get("pending")
    await zp.greetings.restore()
    assert zp.fixture_settings["rows"][0]["selected"]
    assert store.attempts_today() == 0
