import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {extensionFixture} from './connector_chrome_helpers.mjs';

const enabled=!!process.env.PCS_PLAYWRIGHT_MODULE;
const unit=(index,role,content,ids)=>`<div class="block-fixture"><h4 class="sr-only">${role==='user'?'你說：':''}</h4><div data-chatgpt-search-unit-key="fallback-turn-4:${index}:${role}" data-chatgpt-search-message-ids="${ids}" ${role==='assistant'?`data-content-search-unit-key="fallback-turn-4:${index}:${role}"`:''}>
${role==='assistant'?'<h4 class="sr-only" data-conversation-role="assistant">ChatGPT 說：</h4>':''}
${role==='user'?`<div data-content-search-unit-key="fallback-turn-4:${index}:${role}"><div class="whitespace-pre-wrap">${content}</div></div>`:`<div data-markdown-text-style="assistant-message"><div data-markdown-copy="contents">${content}</div></div>`}
</div><div class="turn-action-controls"><button aria-label="${role==='user'?'複製訊息':'複製'}">複製</button><button aria-label="分享">分享</button></div></div>`;
const fixture=`<!doctype html><html class="dark"><head><title>新結構合成對話 - ChatGPT</title><style>body{font:16px sans-serif;margin:20px;background:#212121;color:#eee}.sr-only{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0,0,0,0)}[role=region]{max-width:650px;margin:auto}.block-fixture{margin:30px 0}.turn-action-controls{display:flex}button{padding:8px;background:transparent;color:inherit;border:0}</style></head><body>
<header>ChatGPT<button data-testid="share-chat-button" aria-label="分享">分享</button></header><main><p>空殼，沒有對話</p></main>
<div role="main" aria-hidden="true"><div role="region" aria-label="對話">${unit(0,'user','HIDDEN_OLD_CHAT','old-user')}${unit(2,'assistant','<p>HIDDEN_OLD_REPLY</p>','old-assistant')}</div></div>
<main><div role="region" aria-label="對話"><div data-content-search-turn-key="fallback-turn-4"><div data-talvt-turn-state="complete"><div class="turn-blocks">
${unit(0,'user','請整理明天的行程。','11111111-1111-1111-1111-111111111111')}
<div data-content-search-unit-key="fallback-turn-4:1:assistant"><h4>思考時間</h4><p>THINKING_BLOCK_EXCLUDED</p></div>
${unit(2,'assistant','<h2>行程</h2><p>上午散步，下午看展。</p><table><tr><th>時間</th><th>安排</th></tr><tr><td>10:00</td><td>散步</td></tr></table>','22222222-2222-2222-2222-222222222222 33333333-3333-3333-3333-333333333333')}
</div></div></div></div></main></body></html>`;

test('modern ChatGPT units: current visible conversation, source boundaries and native controls',{skip:!enabled},async t=>{
  const live=await extensionFixture(fixture),{page}=live;
  try{
    await t.test('selects conversation region, excludes hidden SPA page and thinking, deduplicates nested user unit',async()=>{
      const result=(await live.capture()).result;
      assert.equal(await page.locator('[data-message-author-role]').count(),0);
      assert.equal(result.capture.messages.length,2);
      assert.equal(result.capture.messages[0].role,'user');
      assert.equal(result.capture.messages[0].id,'11111111-1111-1111-1111-111111111111');
      assert.match(result.capture.messages[1].id,/^capture-local-/);
      assert.match(result.capture.messages[1].content,/## 行程/);
      assert.match(result.capture.messages[1].content,/\| 10:00 \| 散步 \|/);
      assert.deepEqual(result.anchors.map(a=>a.index),[0,1]);
      for(const text of ['HIDDEN_OLD','THINKING_BLOCK','ChatGPT 說','複製','PCS'])assert.ok(!JSON.stringify(result.capture).includes(text));
    });
    await t.test('each PCS control is adjacent to its own native copy control and captures only that message',async()=>{
      const controls=page.getByRole('button',{name:'PCS：此則訊息',exact:true});assert.equal(await controls.count(),2);
      assert.deepEqual(await page.locator('[data-pcs-ui="message-action"]').evaluateAll(hosts=>hosts.map(h=>h.previousElementSibling.getAttribute('aria-label'))),['複製訊息','複製']);
      await controls.nth(1).click();const event=page.waitForEvent('download');await page.getByRole('button',{name:'下載 JSON',exact:true}).click();
      const file=await event,capture=JSON.parse(await readFile(await file.path(),'utf8'));
      assert.equal(capture.messages.length,1);assert.equal(capture.messages[0].role,'assistant');assert.ok(!JSON.stringify(capture).includes('請整理明天'));
      await page.keyboard.press('Escape');
    });
    await t.test('site render-failure status is rejected, while the same phrase in ordinary prose remains data',async()=>{
      await page.evaluate(()=>{document.querySelector('main [data-markdown-copy="contents"]').innerHTML='<p>無法載入這則回覆</p>';});
      assert.equal((await live.capture()).result.capture.messages[1].content,'無法載入這則回覆');
      await page.evaluate(()=>{document.querySelector('main [data-markdown-copy="contents"]').innerHTML='<div role="status" data-dil-render-failed>無法載入這則回覆</div>';});
      const failed=(await live.capture()).result;assert.equal(failed.capture,undefined);assert.match(failed.capture_error.message,/尚未載入/);
    });
    assert.deepEqual(live.errors,[]);
  }finally{await live.close();}
});

test('reply action row shared by a complete user/thought/assistant turn',{skip:!enabled},async t=>{
  const live=await extensionFixture(fixture),{page}=live;
  try{
    await page.evaluate(()=>{
      const group=document.querySelector('main [data-talvt-turn-state]');
      const blocks=group.querySelectorAll('.block-fixture');
      const user=blocks[0].querySelector('[data-chatgpt-search-unit-key]');
      user.append(blocks[0].querySelector('.turn-action-controls'));
      const assistant=blocks[1].querySelector('[data-chatgpt-search-unit-key]');
      const row=blocks[1].querySelector('.turn-action-controls');
      row.querySelector('[data-pcs-ui]')?.remove();
      const copy=row.querySelector('button');
      const wrapper=document.createElement('div');wrapper.className='flex min-h-5';copy.replaceWith(wrapper);wrapper.append(copy);group.append(row);
      const code=document.createElement('pre');code.innerHTML='<code>echo 1;</code><button aria-label="複製">複製</button>';assistant.append(code);
    });
    await page.waitForTimeout(200);
    await t.test('assistant PCS binds the unique external row, user stays local, code copy receives no PCS',async()=>{
      assert.equal(await page.locator('[data-pcs-ui="message-action"]').count(),2);
      assert.equal(await page.locator('pre [data-pcs-ui]').count(),0);
      const external=page.locator('main [data-talvt-turn-state] > .turn-action-controls');
      assert.equal(await external.locator('[data-pcs-ui]').count(),1);
      await external.getByRole('button',{name:'PCS：此則訊息',exact:true}).click();
      const event=page.waitForEvent('download');await page.getByRole('button',{name:'下載 JSON',exact:true}).click();
      const file=await event,capture=JSON.parse(await readFile(await file.path(),'utf8'));
      assert.equal(capture.messages.length,1);assert.equal(capture.messages[0].role,'assistant');assert.match(capture.messages[0].content,/echo 1;/);
      await page.keyboard.press('Escape');
    });
    await t.test('missing own reply row does not borrow a copy action from the next turn',async()=>{
      await page.evaluate(()=>{
        const group=document.querySelector('main [data-talvt-turn-state]');group.querySelector(':scope > .turn-action-controls').remove();
        const next=document.createElement('div');next.dataset.contentSearchTurnKey='next-turn';next.innerHTML='<div class="turn-action-controls"><button aria-label="複製">複製</button></div>';group.parentElement.after(next);
      });await page.waitForTimeout(200);
      assert.equal(await page.locator('[data-pcs-ui="message-action"]').count(),1);
    });
    assert.deepEqual(live.errors,[]);
  }finally{await live.close();}
});
