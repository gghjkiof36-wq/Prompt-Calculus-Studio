// Optional headless DOM/UI checks. No real ChatGPT requests or private conversations.
// Set PCS_PLAYWRIGHT_MODULE to an existing playwright package; nothing is installed.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile, mkdir} from 'node:fs/promises';
import {pathToFileURL} from 'node:url';
import path from 'node:path';
import {captureLoadedConversation} from '../extensions/chrome/capture.mjs';
import {extensionFixture} from './connector_chrome_helpers.mjs';

const enabled = !!process.env.PCS_PLAYWRIGHT_MODULE;
const fixture = `<!doctype html><html><head><meta charset="utf-8"><title>京都四天旅行安排 - ChatGPT</title></head><body>
<main>
<article><div data-message-author-role="user" data-message-id="user-one"><div class="whitespace-pre-wrap">我們安排四天的京都旅行，兩位大人，住在京都車站附近。

第一天中午抵達，第四天下午離開。每天最多安排兩個主要景點，想留時間逛街和喝咖啡。

請把清水寺排進去，也希望去一天嵐山。交通以電車和步行為主。</div></div></article>
<article><div data-message-author-role="assistant" data-message-id="assistant-one"><div class="markdown">
<p>可以把行程分成四個區域，減少往返交通：</p>
<h3>第一天｜京都車站周邊</h3><p>抵達後先寄放行李，下午逛東本願寺，晚上在車站附近吃飯。</p>
<h3>第二天｜清水寺與東山</h3><p>早上清水寺，下午沿二年坂、三年坂散步，在祇園找間咖啡店休息。</p>
<h3>第三天｜嵐山</h3><p>竹林與天龍寺，再沿桂川散步。</p>
</div><button>複製</button></div></article>
<article><div data-message-author-role="user" data-message-id="user-two"><div class="whitespace-pre-wrap">第二天想晚一點出門，清水寺和祇園之間有午餐建議嗎？</div></div></article>
<article><div data-message-author-role="assistant" data-message-id="assistant-two"><div class="markdown"><p>可以在十點出門，把午餐放在清水坂周邊，下午再慢慢走向祇園。</p><table><thead><tr><th>時間</th><th>安排</th></tr></thead><tbody><tr><td>10:00</td><td>從飯店出發</td></tr><tr><td>12:30</td><td>清水坂午餐</td></tr></tbody></table></div></div></article>
<article hidden><div data-message-author-role="assistant" data-message-id="hidden-branch"><div class="markdown"><p>HIDDEN_OTHER_BRANCH</p></div></div></article>
</main></body></html>`;

const operations='<div class="turn-actions" role="group" aria-label="訊息操作"><button data-testid="copy-turn-action-button" aria-label="複製">⧉</button><button aria-label="分享回覆">↗</button></div>';
const styledFixture=fixture.replaceAll('</article>',operations+'</article>').replace('</head>',`<style>body{margin:0;background:#212121;color:#ececec;font:15px/1.6 'Segoe UI','Microsoft JhengHei',sans-serif}main{max-width:700px;margin:auto;padding:30px 44px 180px}article{margin:24px 0}.whitespace-pre-wrap{white-space:pre-wrap;background:#303030;border-radius:16px;padding:16px}h3{font-size:17px}.turn-actions{display:flex;align-items:center;gap:2px;margin-top:8px}.turn-actions button,header button{font:inherit;background:transparent;border:0;color:#aaa;padding:6px;cursor:pointer}table{width:100%;border-collapse:collapse}td,th{padding:7px;border-bottom:1px solid #555}header{display:flex;align-items:center;justify-content:space-between;padding:14px 24px;border-bottom:1px solid #383838;position:sticky;top:0;background:#212121;z-index:2}@media(max-width:540px){main{padding:18px 42px 180px}}</style></head>`).replace('<main>','<header><strong>ChatGPT</strong><div><button data-testid="share-chat-button" aria-label="分享">分享</button></div></header><main>');

test('actual MV3: original action menus, selection, transfer and late completions',{skip:!enabled},async t=>{
  const live=await extensionFixture(styledFixture),{page,worker}=live;
  const header=()=>page.locator('[data-pcs-ui="entry"]').getByRole('button');
  const menu=()=>page.locator('[data-pcs-ui="menu"]');
  const beginSelection=async()=>{await page.getByRole('button',{name:'選取訊息',exact:true}).click();await page.getByRole('checkbox',{name:'選取第 1 則訊息',exact:true}).waitFor();};
  try{
    assert.match(worker.url(),/migmpicacfjoipmfcgdjocgdbplgldan\/background.mjs$/);
    await t.test('real injection returns serializable exceptions and correct source',async()=>{
      assert.equal((await live.capture()).result.capture.messages.length,4);
      await page.evaluate(()=>document.querySelector('main').hidden=true);
      assert.equal((await live.capture()).result.capture_error.code,'capture_failed');
      await page.evaluate(()=>document.querySelector('main').hidden=false);
    });
    await t.test('classic file reinjection keeps one interface and one toggle listener',async()=>{
      const before=(await live.capture()).result.fingerprint;
      await worker.evaluate(async()=>{
        const [tab]=await chrome.tabs.query({url:'https://chatgpt.com/*'});
        await chrome.scripting.executeScript({target:{tabId:tab.id},files:['content.js']});
        await chrome.scripting.executeScript({target:{tabId:tab.id},files:['content.js']});
      });
      assert.equal(await page.locator('[data-pcs-ui="entry"]').count(),1);
      assert.equal(await page.locator('[data-pcs-ui="menu"]').count(),1);
      assert.equal(await page.locator('[data-pcs-ui="message-action"]').count(),4);
      const toggle=()=>worker.evaluate(async()=>{
        const [tab]=await chrome.tabs.query({url:'https://chatgpt.com/*'});
        await chrome.tabs.sendMessage(tab.id,{action:'pcs-toggle-selection'});
      });
      await toggle();await menu().waitFor({state:'visible'});
      await toggle();await menu().waitFor({state:'hidden'});
      assert.equal((await live.capture()).result.fingerprint,before);
    });
    await t.test('PCS is appended to original controls; ordinary view has no checkboxes or toolbar',async()=>{
      assert.equal(await page.locator('[data-pcs-ui="message-action"]').count(),4);
      assert.equal(await page.locator('[data-pcs-ui="selection"]').count(),0);
      assert.equal(await page.locator('[data-pcs-ui="actions"]').count(),0);
      assert.equal(await menu().isVisible(),false);
      const before=(await live.capture()).result;
      await page.getByRole('button',{name:'PCS：此則訊息',exact:true}).nth(1).click();
      await page.getByText('此則訊息',{exact:true}).waitFor();
      const after=(await live.capture()).result;assert.equal(before.fingerprint,after.fingerprint);
      assert.ok(!JSON.stringify(after.capture).includes('下載 JSON'));
      assert.equal(await page.getByRole('button',{name:'儲存到 PCS',exact:true}).evaluate(e=>e===e.getRootNode().activeElement),true);
      await page.keyboard.press('Tab');assert.equal(await page.getByRole('button',{name:'下載 JSON',exact:true}).evaluate(e=>e===e.getRootNode().activeElement),true);
      await page.keyboard.press('Escape');assert.equal(await menu().isVisible(),false);
      assert.equal(await page.getByRole('button',{name:'PCS：此則訊息',exact:true}).nth(1).evaluate(e=>e===e.getRootNode().activeElement),true);
      await page.keyboard.press('Enter');await page.getByRole('button',{name:'儲存到 PCS',exact:true}).waitFor();
      for(const [label,extension] of [['下載 JSON','json'],['下載 Markdown','md']]){
        const event=page.waitForEvent('download');await page.getByRole('button',{name:label,exact:true}).click();
        const file=await event,text=await readFile(await file.path(),'utf8');assert.ok(file.suggestedFilename().endsWith('.'+extension));
        if(extension==='json')assert.deepEqual(JSON.parse(text).messages.map(m=>m.id),['assistant-one']);
        else assert.match(text,/### 第二天/);
        assert.ok(!text.includes('第二天想晚一點'));
      }
    });
    await t.test('single-message transfer uses source tab and committed remains distinct',async()=>{
      await page.getByRole('button',{name:'儲存到 PCS',exact:true}).click();await page.getByRole('button',{name:'儲存',exact:true}).click();await page.getByText('正在保存…',{exact:true}).waitFor();
      const calls=await worker.evaluate(()=>testNative.calls),body=Buffer.concat(calls.filter(c=>c.command==='chunk').map(c=>Buffer.from(c.data,'base64')));
      assert.deepEqual(JSON.parse(body.toString('utf8')).messages.map(m=>m.id),['assistant-one']);
      assert.equal(await page.getByRole('button',{name:'PCS：此則訊息',exact:true}).first().isDisabled(),true);
      await page.evaluate(()=>{history.pushState({},'','/c/new-page-while-pending');document.title='另一段對話 - ChatGPT';});
      await page.waitForTimeout(850);assert.equal(await menu().isVisible(),false);await header().click();
      await page.getByText('京都四天旅行安排',{exact:true}).waitFor();
      assert.equal(await page.getByText('此則訊息',{exact:true}).count(),0);
      await page.getByRole('button',{name:'取消',exact:true}).click();await page.getByText('已取消',{exact:true}).waitFor();
      await page.getByRole('button',{name:'PCS：此則訊息',exact:true}).first().click();
      await page.locator('[data-pcs-ui="menu"] button:enabled').filter({hasText:'儲存到 PCS'}).waitFor();
      await page.evaluate(()=>document.querySelector('.whitespace-pre-wrap').textContent+=' 更新');
      await page.getByRole('button',{name:'下載 JSON',exact:true}).click();await page.getByRole('button',{name:'重新讀取',exact:true}).click();
      await page.locator('[data-pcs-ui="menu"] button:enabled').filter({hasText:'儲存到 PCS'}).waitFor();await header().click();
    });
    await t.test('selection-only checkboxes, multiple PCS and unknown result never resend implicitly',async()=>{
      await beginSelection();await page.getByRole('checkbox',{name:'選取第 1 則訊息',exact:true}).check();await header().click();
      await worker.evaluate(()=>{testNative.instances.push({instance_id:'second-pcs',display_name:'PCS · 第二工作區',capabilities:['save_destination_v1']});testNative.state='unknown';});
      await page.getByRole('button',{name:'儲存到 PCS',exact:true}).click();
      const choice=page.getByRole('combobox',{name:'PCS 工作區'});await choice.waitFor();assert.equal(await choice.inputValue(),'fixture-pcs');
      await choice.focus();await page.keyboard.press('End');await page.keyboard.press('Enter');
      await page.getByRole('button',{name:'儲存',exact:true}).click();await page.getByText('尚未取得結果',{exact:true}).waitFor();
      const count=await worker.evaluate(()=>testNative.calls.filter(c=>c.command==='finish').length);
      await page.getByRole('button',{name:'重新選取',exact:true}).click();
      assert.equal(await worker.evaluate(()=>testNative.calls.filter(c=>c.command==='finish').length),count);
    });
    await t.test('navigation while hello waits does not create a phantom operation',async()=>{
      await beginSelection();await page.getByRole('checkbox',{name:'選取第 1 則訊息',exact:true}).check();await header().click();
      await worker.evaluate(()=>{testNative.instances=testNative.instances.slice(0,1);testNative.holdHello=true;});
      const before=await worker.evaluate(()=>testNative.calls.filter(c=>c.command==='begin').length);
      await page.getByRole('button',{name:'儲存到 PCS',exact:true}).click();
      await page.waitForTimeout(100);
      await page.evaluate(()=>history.pushState({},'','/c/same-body-different-chat'));
      await worker.evaluate(()=>{testNative.holdHello=false;testNative.releaseHello();});await page.waitForTimeout(1100);
      assert.equal(await worker.evaluate(()=>testNative.calls.filter(c=>c.command==='begin').length),before);
      assert.equal(await page.getByRole('checkbox',{name:'選取第 1 則訊息',exact:true}).isChecked(),false);
      assert.equal(await page.getByText('尚未取得結果',{exact:true}).count(),0);
    });
    await t.test('cancelled state is not replaced by a late finish response',async()=>{
      if(await menu().isVisible())await page.keyboard.press('Escape');
      await page.getByRole('checkbox',{name:'選取第 1 則訊息',exact:true}).check();
      if(!(await menu().isVisible()))await header().click();
      await worker.evaluate(()=>{testNative.holdFinish=true;testNative.state='committing';});
      await page.getByRole('button',{name:'儲存到 PCS',exact:true}).click();await page.getByRole('button',{name:'儲存',exact:true}).click();await page.getByText('傳送中…',{exact:true}).waitFor();
      await page.waitForTimeout(150);await page.getByRole('button',{name:'取消',exact:true}).click();await page.getByText('已取消',{exact:true}).waitFor();
      await worker.evaluate(()=>{testNative.holdFinish=false;testNative.releaseFinish();});await page.waitForTimeout(150);
      assert.equal(await page.getByText('已取消',{exact:true}).isVisible(),true);
      assert.equal(await worker.evaluate(async()=>(await chrome.storage.local.get('recent')).recent[0].state),'cancelled');
      await page.getByRole('button',{name:'重新選取',exact:true}).click();
    });
    await t.test('page theme, keyboard and narrow menu bounds; selection does not refresh itself',async()=>{
      await beginSelection();await page.getByRole('checkbox',{name:'選取第 2 則訊息',exact:true}).check();await page.waitForTimeout(900);
      assert.equal(await page.getByRole('checkbox',{name:'選取第 2 則訊息',exact:true}).isChecked(),true);await header().click();
      await page.getByRole('button',{name:'完成選取',exact:true}).click();
      assert.equal(await page.locator('[data-pcs-ui="selection"]').count(),0);
      assert.equal(await page.getByText('已選 1 則',{exact:true}).isVisible(),true);
      assert.equal(await page.getByRole('button',{name:'儲存到 PCS',exact:true}).isEnabled(),true);
      for(const width of [360,600]){await page.setViewportSize({width,height:900});await page.waitForTimeout(100);const box=await menu().boundingBox();assert.ok(box.x>=0&&box.x+box.width<=width);assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);}
      await page.evaluate(()=>document.documentElement.className='light');await page.waitForTimeout(800);
      assert.equal(await menu().getAttribute('data-theme'),'light');
      await page.keyboard.press('Escape');assert.equal(await menu().isVisible(),false);assert.deepEqual(live.errors,[]);
    });
  }finally{await live.close();}
});

test('headless loaded DOM capture preserves data boundaries', {skip: !enabled}, async t => {
  const playwright = await import(pathToFileURL(process.env.PCS_PLAYWRIGHT_MODULE).href);
  const browser = await playwright.chromium.launch({headless: true,
    ...(process.env.PCS_CHROME_EXECUTABLE ? {executablePath: process.env.PCS_CHROME_EXECUTABLE} : {})});
  let captured;
  try {
    const capturePage = await browser.newPage();
    // Every network request is fulfilled locally or blocked. No site access occurs.
    await capturePage.route('**/*', route => route.request().url().startsWith('https://chatgpt.com/') ?
      route.fulfill({contentType: 'text/html', body: fixture}) : route.abort());
    await capturePage.goto('https://chatgpt.com/c/synthetic-kyoto?test=fixture');
    await t.test('loaded messages only, correct current URL, Markdown structure and source IDs', async () => {
      captured = await capturePage.evaluate(captureLoadedConversation);
      assert.equal(captured.capture.title, '京都四天旅行安排');
      assert.equal(captured.capture.conversation_id, 'synthetic-kyoto');
      assert.equal(captured.capture.url, 'https://chatgpt.com/c/synthetic-kyoto');
      assert.equal(captured.capture.messages.length, 4);
      assert.ok(!JSON.stringify(captured).includes('HIDDEN_OTHER_BRANCH'));
      assert.ok(!captured.capture.messages[1].content.includes('複製'));
      assert.match(captured.capture.messages[1].content, /### 第二天/);
      assert.match(captured.capture.messages[3].content, /\| 時間 \| 安排 \|/);
      assert.match(captured.capture.messages[3].content, /\| --- \| --- \|/);
    });
    await t.test('long content, code fences, formulas, external references and anonymous message IDs', async () => {
      await capturePage.evaluate(() => {
        const container = document.createElement('div');
        container.dataset.messageAuthorRole = 'assistant';
        container.innerHTML = '<div class="markdown"><pre><code class="language-js">const code = ```;\n\nconst x = 1;</code></pre><p><span class="katex"><math><semantics><annotation encoding="application/x-tex">x^2 + y^2</annotation></semantics></math></span></p><a href="https://example.com/file?token=secret">notes.txt</a><img src="https://example.com/image?sig=secret" alt="行程地圖"><p id="long-text"></p></div>';
        document.querySelector('main').append(container);
        document.getElementById('long-text').textContent = '長文保留。'.repeat(50000);
      });
      const result = await capturePage.evaluate(captureLoadedConversation);
      const last = result.capture.messages.at(-1);
      assert.match(last.id, /^capture-local-/);
      assert.match(last.content, /````js\nconst code = ```;/);
      assert.match(last.content, /\n\nconst x = 1;/);
      assert.match(last.content, /\$x\^2 \+ y\^2\$/);
      assert.match(last.content, /notes.txt/);
      assert.deepEqual(last.resources, [{kind: 'image', label: '行程地圖', url: null}]);
      assert.ok(!last.content.includes('secret'));
      assert.equal(last.content.split('長文保留。').length - 1, 50000);
    });
    await t.test('navigation during capture and streaming replies are rejected', async () => {
      await capturePage.goto('https://chatgpt.com/c/synthetic-kyoto');
      await capturePage.evaluate(() => {
        const old = crypto.subtle.digest.bind(crypto.subtle);
        crypto.subtle.digest = async (...args) => { history.pushState({}, '', '/c/another-conversation'); return old(...args); };
        document.querySelector('[data-message-id]').removeAttribute('data-message-id');
      });
      await assert.rejects(capturePage.evaluate(captureLoadedConversation), /對話已切換/);
      await capturePage.goto('https://chatgpt.com/c/synthetic-kyoto');
      await capturePage.evaluate(() => {
        const button = document.createElement('button'); button.dataset.testid = 'stop-button';
        document.querySelector('main').append(button);
      });
      await assert.rejects(capturePage.evaluate(captureLoadedConversation), /還在回覆/);
    });
    await capturePage.close();

  } finally { await browser.close(); }
});
