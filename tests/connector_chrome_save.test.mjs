import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdir} from 'node:fs/promises';
import path from 'node:path';
import {extensionFixture} from './connector_chrome_helpers.mjs';

const enabled=!!process.env.PCS_PLAYWRIGHT_MODULE;
const html=`<!doctype html><html class="dark"><head><meta charset="utf-8"><title>京都四天旅行安排 - ChatGPT</title><style>
*{box-sizing:border-box}html{color-scheme:dark}body{margin:0;background:#212121;color:#ececec;font:15px/1.65 system-ui,"Microsoft JhengHei",sans-serif}header{position:sticky;top:0;height:52px;background:#212121;z-index:10;pointer-events:none}[data-app-shell-main-titlebar]{display:flex;align-items:center;height:52px;padding:0 20px}[data-app-shell-page-header]{font-size:20px}.right-actions{display:flex;align-items:center;gap:4px;margin-left:auto}.native-wrapper{display:flex;pointer-events:auto}button{font:inherit;padding:5px 9px;border:0;border-radius:6px;background:transparent;color:inherit;cursor:pointer}main{max-width:780px;padding:36px 44px 80px;margin:auto}article{margin:0 0 34px}.user{background:#303030;border-radius:18px;padding:12px 18px;margin-left:64px}p{margin:0 0 14px}h2{font-size:19px;margin:0 0 14px}.turn-action-controls{display:flex;margin-top:8px;color:#aaa;font-size:13px}@media(max-width:650px){main{padding:24px 42px 64px}.user{margin-left:16px}[data-app-shell-main-titlebar]{padding:0 12px}}
</style></head><body><header data-app-shell-titlebar="true"><div data-app-shell-main-titlebar="true" aria-hidden="false"><div data-app-shell-page-header="true"><strong>ChatGPT</strong></div><div class="right-actions" data-app-shell-header-obstacle="true" data-app-shell-thread-content-overlap="true"><div class="native-wrapper"><button aria-label="更多" aria-haspopup="menu">•••</button></div><div class="native-wrapper"><button aria-label="分享">分享</button></div></div></div></header>
<main><article><div class="user" data-message-author-role="user" data-message-id="one"><p>我們安排四天的京都旅行，兩位大人，住在京都車站附近。每天最多兩個景點，也想留時間逛街。</p></div><div class="turn-action-controls"><button aria-label="複製">複製</button></div></article>
<article><div data-message-author-role="assistant" data-message-id="two"><div class="markdown"><h2>四天慢慢遊京都</h2><p>以區域安排每天的路線，可以減少往返交通。</p><ol><li>第一天：京都車站與東本願寺。</li><li>第二天：清水寺、二年坂與祇園。</li><li>第三天：嵐山竹林、天龍寺與桂川。</li><li>第四天：錦市場，午餐後出發。</li></ol><p>清水寺可以安排在早上，再沿東山一路散步到祇園。</p></div></div><div class="turn-action-controls"><button aria-label="複製">複製</button><button aria-label="分享回覆">分享</button></div></article></main></body></html>`;
const button=(page,name)=>page.getByRole('button',{name,exact:true});
async function lastOption(page,label){const choice=page.getByRole('combobox',{name:label});await choice.focus();await page.keyboard.press('End');await page.keyboard.press('Enter');}
const nativeReady=(worker,field)=>worker.evaluate(field=>new Promise((resolve,reject)=>{const timeout=setTimeout(()=>{clearInterval(timer);reject(new Error(`Native fixture did not reach ${field}`));},5000);const timer=setInterval(()=>{if(testNative[field]){clearInterval(timer);clearTimeout(timeout);resolve();}},10);}),field);
const calls=(worker,command)=>worker.evaluate(command=>testNative.calls.filter(item=>item.command===command),command);
async function choose(live){await live.page.getByRole('button',{name:'PCS：此則訊息',exact:true}).nth(1).click();await button(live.page,'儲存到 PCS').click();await button(live.page,'儲存').waitFor();}
async function enabledSave(page){await page.waitForFunction(()=>[...document.querySelector('[data-pcs-ui="menu"]').shadowRoot.querySelectorAll('button')].some(item=>item.textContent==='儲存'&&!item.disabled));}

test('actual MV3 direct save selects destinations before any body is sent',{skip:!enabled},async t=>{
  const live=await extensionFixture(html),{page,worker}=live;page.setDefaultTimeout(6000);
  try{
    await t.test('single PCS opens server default, browses children/parent/roots, and Back sends nothing',async()=>{
      await choose(live);await enabledSave(page);
      assert.equal(await page.getByRole('combobox',{name:'PCS 工作區'}).count(),0);
      await button(page,'旅行').click();await button(page,'京都').click();await enabledSave(page);
      assert.equal((await calls(worker,'destinations')).at(-1).folder_id,'3'.repeat(32));
      await button(page,'上一層').click();await button(page,'京都').waitFor();
      await worker.evaluate(()=>testNative.holdDestinations=true);
      await lastOption(page,'位置');
      assert.equal(await page.getByRole('combobox',{name:'位置'}).evaluate(element=>element===element.getRootNode().activeElement),true);
      assert.equal(await page.getByRole('combobox',{name:'位置'}).inputValue(),'4'.repeat(32));
      await nativeReady(worker,'releaseDestinations');await worker.evaluate(()=>{testNative.holdDestinations=false;testNative.releaseDestinations();});await enabledSave(page);
      assert.equal((await calls(worker,'destinations')).at(-1).folder_id,'4'.repeat(32));
      await button(page,'‹ 返回').click();await button(page,'下載 JSON').waitFor();
      assert.equal((await calls(worker,'begin')).length,0);
      assert.equal((await calls(worker,'chunk')).length,0);
    });
    await t.test('final save binds destination and automatically reports commit without opening PCS',async()=>{
      await button(page,'儲存到 PCS').click();await enabledSave(page);await button(page,'旅行').click();await enabledSave(page);
      await button(page,'儲存').click();await page.getByText('正在保存…',{exact:true}).waitFor();
      assert.equal((await calls(worker,'begin')).at(-1).destination_id,'2'.repeat(32));
      const body=Buffer.concat((await calls(worker,'chunk')).map(item=>Buffer.from(item.data,'base64')));
      assert.deepEqual(JSON.parse(body.toString()).messages.map(item=>item.id),['two']);
      assert.equal(await button(page,'查詢結果').count(),0);
      await worker.evaluate(()=>testNative.state='committed');
      await page.getByText('已保存',{exact:true}).waitFor();
      assert.equal((await calls(worker,'open')).length,0);assert.equal((await calls(worker,'reveal')).length,0);
      await button(page,'開啟文件').click();await button(page,'顯示資料夾').click();
      assert.equal((await calls(worker,'open')).length,1);assert.equal((await calls(worker,'reveal')).length,1);
      assert.equal(page.url(),'https://chatgpt.com/c/synthetic-kyoto');
    });
    await t.test('last valid workspace/folder persists; stale IDs fall back to current server default',async()=>{
      await choose(live);await enabledSave(page);
      assert.equal((await calls(worker,'destinations')).at(-1).folder_id,'2'.repeat(32));
      await button(page,'‹ 返回').click();
      await worker.evaluate(()=>chrome.storage.local.set({preferred_destination:{instance_id:'fixture-pcs',folder_id:'f'.repeat(32)}}));
      await button(page,'儲存到 PCS').click();await enabledSave(page);
      const requests=await calls(worker,'destinations');assert.equal(requests.at(-2).folder_id,'f'.repeat(32));assert.equal(requests.at(-1).folder_id,undefined);
      await button(page,'‹ 返回').click();
    });
    await t.test('an old PCS cannot silently fall back to the previous desktop preview',async()=>{
      await worker.evaluate(()=>testNative.instances[0].capabilities=[]);
      const before=(await calls(worker,'begin')).length;await button(page,'儲存到 PCS').click();
      await page.getByText('請更新 PCS 後再儲存。',{exact:true}).waitFor();assert.equal(await button(page,'儲存').isDisabled(),true);
      assert.equal((await calls(worker,'begin')).length,before);await button(page,'‹ 返回').click();
      await worker.evaluate(()=>testNative.instances[0].capabilities=['save_destination_v1']);
    });
    await t.test('folder failure and a rejected begin remain recoverable without a phantom pending result',async()=>{
      await button(page,'儲存到 PCS').click();await enabledSave(page);
      await worker.evaluate(()=>testNative.errors.destinations={code:'destination_limit',message:'這個資料夾的項目太多。'});
      await button(page,'旅行').click();await page.getByText('這個資料夾的項目太多。',{exact:true}).waitFor();
      assert.equal(await button(page,'儲存').isDisabled(),true);
      await worker.evaluate(()=>delete testNative.errors.destinations);await button(page,'重新讀取').click();await enabledSave(page);
      await worker.evaluate(()=>testNative.errors.begin={code:'destination_changed',message:'資料夾已變更，請重新選擇。'});
      await button(page,'儲存').click();await page.getByText('資料夾已變更，請重新選擇。',{exact:true}).waitFor();
      assert.equal(await page.getByText('尚未取得結果',{exact:true}).count(),0);
      await worker.evaluate(()=>delete testNative.errors.begin);await button(page,'重新讀取').click();await enabledSave(page);
      await button(page,'‹ 返回').click();
    });
    await t.test('a terminal save failure stays in place and shows the server reason',async()=>{
      await button(page,'儲存到 PCS').click();await enabledSave(page);
      await worker.evaluate(()=>testNative.finishResult={state:'failed',message:'資料夾已移動，請重新選擇。',error_code:'destination_changed'});
      await button(page,'儲存').click();await page.getByText('資料夾已移動，請重新選擇。',{exact:true}).waitFor();
      assert.equal(await button(page,'開啟文件').count(),0);assert.equal(await button(page,'查詢結果').count(),0);
      await worker.evaluate(()=>delete testNative.finishResult);
      await page.getByRole('button',{name:'PCS：此則訊息',exact:true}).nth(1).click();await button(page,'儲存到 PCS').waitFor();
    });
    await t.test('a rejected begin after SPA navigation cannot restore the old folder picker',async()=>{
      await button(page,'儲存到 PCS').click();await enabledSave(page);
      await worker.evaluate(()=>{testNative.holdBegin=true;testNative.errors.begin={code:'destination_changed',message:'資料夾已變更，請重新選擇。'};});
      await button(page,'儲存').click();await page.getByText('傳送中…',{exact:true}).waitFor();
      await worker.evaluate(()=>new Promise((resolve,reject)=>{const timeout=setTimeout(()=>{clearInterval(timer);reject(new Error('begin request not received'));},5000);const timer=setInterval(()=>{if(testNative.releaseBegin){clearInterval(timer);clearTimeout(timeout);resolve();}},10);}));
      await page.evaluate(()=>history.pushState({},'','/c/after-begin-navigation'));
      await page.waitForTimeout(850);
      await worker.evaluate(()=>{testNative.holdBegin=false;delete testNative.errors.begin;testNative.releaseBegin();});
      await page.locator('[data-pcs-ui="entry"]').getByRole('button').click();
      await page.getByText('資料夾已變更，請重新選擇。',{exact:true}).waitFor();
      assert.equal(await button(page,'儲存').count(),0);assert.equal(await page.getByRole('combobox',{name:'位置'}).count(),0);
      await page.getByRole('button',{name:'PCS：此則訊息',exact:true}).nth(1).click();await button(page,'儲存到 PCS').waitFor();
    });
    await t.test('Back and SPA navigation invalidate delayed destination responses',async()=>{
      await worker.evaluate(()=>testNative.holdDestinations=true);await button(page,'儲存到 PCS').click();
      await page.waitForFunction(()=>document.querySelector('[data-pcs-ui="menu"]').shadowRoot.textContent.includes('讀取位置…'));
      await worker.evaluate(()=>new Promise((resolve,reject)=>{const timeout=setTimeout(()=>{clearInterval(timer);reject(new Error('destination request not received'));},5000);const timer=setInterval(()=>{if(testNative.releaseDestinations){clearInterval(timer);clearTimeout(timeout);resolve();}},10);}));
      await button(page,'‹ 返回').click();await worker.evaluate(()=>{testNative.holdDestinations=false;testNative.releaseDestinations();});
      await button(page,'下載 JSON').waitFor();assert.equal(await button(page,'儲存').count(),0);
      await worker.evaluate(()=>{testNative.holdDestinations=true;delete testNative.releaseDestinations;});
      await button(page,'儲存到 PCS').click();await worker.evaluate(()=>new Promise((resolve,reject)=>{const timeout=setTimeout(()=>{clearInterval(timer);reject(new Error('destination request not received'));},5000);const timer=setInterval(()=>{if(testNative.releaseDestinations){clearInterval(timer);clearTimeout(timeout);resolve();}},10);}));
      const before=(await calls(worker,'begin')).length;
      await page.evaluate(()=>history.pushState({},'','/c/new-conversation'));
      await worker.evaluate(()=>{testNative.holdDestinations=false;testNative.releaseDestinations();});
      await page.waitForTimeout(850);assert.equal(await button(page,'儲存').count(),0);assert.equal((await calls(worker,'begin')).length,before);
    });
    assert.deepEqual(live.errors,[]);
  }finally{await live.close();}
});

test('actual MV3 multiple PCS stays explicit and destination menu fits wide/narrow pages',{skip:!enabled},async()=>{
  const live=await extensionFixture(html),{page,worker}=live;page.setDefaultTimeout(6000);
  try{
    await worker.evaluate(()=>testNative.instances.push({instance_id:'other-pcs',display_name:'PCS · 工作',capabilities:['save_destination_v1']}));
    await choose(live);assert.equal(await page.getByRole('combobox',{name:'PCS 工作區'}).inputValue(),'');
    assert.equal(await button(page,'儲存').isDisabled(),true);assert.equal((await calls(worker,'destinations')).length,0);
    await lastOption(page,'PCS 工作區');await enabledSave(page);
    assert.equal((await calls(worker,'destinations')).at(-1).instance_id,'other-pcs');
    await button(page,'‹ 返回').click();await page.keyboard.press('Escape');
    // Fresh, single-workspace menu for review; the same unpacked extension performs all actions.
    await worker.evaluate(()=>testNative.instances=testNative.instances.slice(0,1));
    await page.locator('[data-pcs-ui="entry"]').getByRole('button').click();await button(page,'選取全部').click();
    await button(page,'儲存到 PCS').click();await enabledSave(page);await button(page,'旅行').click();await enabledSave(page);
    for(const width of [1100,390]){
      await page.setViewportSize({width,height:850});await page.waitForTimeout(150);
      const bounds=await page.locator('[data-pcs-ui="menu"]').boundingBox();assert.ok(bounds.x>=0&&bounds.x+bounds.width<=width);
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
      if(process.env.PCS_SAVE_SCREENSHOT_DIR){await mkdir(process.env.PCS_SAVE_SCREENSHOT_DIR,{recursive:true});await page.screenshot({path:path.join(process.env.PCS_SAVE_SCREENSHOT_DIR,`chrome-save-destination-${width}.png`)});}
    }
    // Long lists scroll independently, keeping the final action in view.
    await worker.evaluate(()=>{for(let i=10;i<50;i++)testNative.folders.push({id:i.toString(16).padStart(32,'0'),name:`專案 ${i}`,path:`D:/PCS/筆記/專案${i}`,parent_id:'1'.repeat(32)});});
    await button(page,'上一層').click();await enabledSave(page);
    assert.ok(await page.locator('[data-pcs-ui="menu"]').evaluate(host=>{const list=host.shadowRoot.querySelector('.folders'),save=host.shadowRoot.querySelector('.save').getBoundingClientRect();return list.scrollHeight>list.clientHeight&&save.bottom<=innerHeight;}));
    assert.deepEqual(live.errors,[]);
  }finally{await live.close();}
});
