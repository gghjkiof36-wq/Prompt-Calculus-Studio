import test from 'node:test';
import assert from 'node:assert/strict';
import {extensionFixture} from './connector_chrome_helpers.mjs';

const html=`<!doctype html><html><head><meta charset="utf-8"><title>Public sample - ChatGPT</title><style>header{display:flex;justify-content:space-between;padding:16px}main{max-width:700px;margin:40px auto}</style></head><body>
<header><strong>ChatGPT</strong><div><button data-testid="share-chat-button" aria-label="分享">分享</button></div></header>
<main><article><div data-message-author-role="assistant" data-message-id="one"><div class="markdown"><p>Public answer.</p></div></div><button data-testid="copy-turn-action-button">複製</button></article></main></body></html>`;

test('actual MV3 closed menus: website cannot read local paths or unrelated history',{skip:!process.env.PCS_PLAYWRIGHT_MODULE},async()=>{
  const live=await extensionFixture(html),{page,worker}=live;
  try{
    await worker.evaluate(async()=>{
      await chrome.storage.local.set({recent:[{operation_id:'9'.repeat(32),instance_id:'fixture-pcs',state:'committed',title:'PRIVATE_HISTORY_SENTINEL',path:'D:/PRIVATE_PATH_SENTINEL/Secret.md'}]});
    });
    const header=page.locator('[data-pcs-ui="entry"]').getByRole('button');
    await header.click();await page.getByRole('button',{name:'下載紀錄',exact:true}).click();
    await page.getByText('PRIVATE_HISTORY_SENTINEL',{exact:true}).waitFor();
    const inspect=()=>page.evaluate(()=>{
      const hosts=[...document.querySelectorAll('[data-pcs-ui="menu"],[data-pcs-ui="submenu"]')];
      return {open:hosts.filter(h=>h.shadowRoot).length,markup:document.documentElement.outerHTML,lightText:hosts.map(h=>h.textContent).join('')};
    });
    let snapshot=await inspect();
    assert.equal(snapshot.open,0);
    assert.ok(!snapshot.markup.includes('PRIVATE_HISTORY_SENTINEL'));
    assert.ok(!snapshot.markup.includes('PRIVATE_PATH_SENTINEL'));
    assert.equal(snapshot.lightText,'');
    await page.keyboard.press('Escape');
    await page.getByRole('button',{name:'PCS：此則訊息',exact:true}).click();
    await page.getByRole('button',{name:'儲存到 PCS',exact:true}).click();
    await page.getByRole('menuitem',{name:'目前工作區',exact:true}).waitFor();
    snapshot=await inspect();assert.equal(snapshot.open,0);assert.ok(!snapshot.markup.includes('D:/PCS/筆記'));
    // Keyboard navigation must still work after closed-root event retargeting.
    await page.getByRole('menuitem',{name:'目前工作區',exact:true}).focus();
    await page.keyboard.press('ArrowDown');
    assert.equal(await page.getByRole('menuitem',{name:'旅行',exact:true}).evaluate(e=>e===e.getRootNode().activeElement),true);
    await page.keyboard.press('ArrowRight');
    await page.getByRole('menuitem',{name:'儲存於「旅行」',exact:true}).waitFor();
    await page.keyboard.press('Escape');
    await page.getByRole('menuitem',{name:'儲存於「旅行」',exact:true}).waitFor({state:'hidden'});
    await worker.evaluate(()=>{testNative.finishState='committed';testNative.state='committed';});
    await page.getByRole('menuitem',{name:'目前工作區',exact:true}).click();
    await page.getByRole('button',{name:'儲存到 PCS',exact:true}).and(page.locator('button:enabled')).waitFor();
    assert.equal(await worker.evaluate(()=>testNative.calls.filter(c=>c.command==='finish').length),1);
    assert.deepEqual(live.errors,[]);
  }finally{await live.close();}
});
