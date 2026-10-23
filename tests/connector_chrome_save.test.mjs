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
const nativeReady=(worker,field)=>worker.evaluate(field=>new Promise((resolve,reject)=>{const timeout=setTimeout(()=>{clearInterval(timer);reject(new Error(`Native fixture did not reach ${field}`));},5000);const timer=setInterval(()=>{if(testNative[field]){clearInterval(timer);clearTimeout(timeout);resolve();}},10);}),field);
const calls=(worker,command)=>worker.evaluate(command=>testNative.calls.filter(item=>item.command===command),command);
async function choose(live){await live.page.getByRole('button',{name:'PCS：此則訊息',exact:true}).nth(1).click();await button(live.page,'儲存到 PCS').hover();await live.page.getByRole('menuitem',{name:'目前工作區',exact:true}).waitFor();}

const item=(page,name)=>page.getByRole('menuitem',{name,exact:true});
const submenus=page=>page.locator('[data-pcs-ui="submenu"]');

test('actual MV3 cascades preserve the first menu; hover and arrows only browse',{skip:!enabled},async()=>{
  const live=await extensionFixture(html),{page,worker}=live;page.setDefaultTimeout(6000);
  try{
    await choose(live);
    assert.equal(await button(page,'下載 JSON').isVisible(),true);
    assert.equal(await page.getByRole('combobox').count(),0);
    assert.equal((await calls(worker,'begin')).length,0);
    const parent=await button(page,'儲存到 PCS').boundingBox(),child=await submenus(page).first().boundingBox();assert.ok(child.x>=parent.x+parent.width);
    await item(page,'展開 旅行').hover();await item(page,'儲存於「旅行」').waitFor();
    assert.equal(await submenus(page).count(),2);assert.equal((await calls(worker,'begin')).length,0);
    await item(page,'展開 京都').focus();await page.keyboard.press('ArrowRight');await item(page,'儲存於「京都」').waitFor();
    assert.equal(await submenus(page).count(),3);assert.equal((await calls(worker,'begin')).length,0);
    await page.keyboard.press('ArrowLeft');assert.equal(await submenus(page).count(),2);
    assert.equal(await item(page,'展開 京都').evaluate(element=>element===element.getRootNode().activeElement),true);
    await page.keyboard.press('Escape');assert.equal(await submenus(page).count(),1);
    await page.keyboard.press('Escape');assert.equal(await submenus(page).count(),0);
    assert.equal(await button(page,'儲存到 PCS').evaluate(element=>element===element.getRootNode().activeElement),true);
    await page.keyboard.press('ArrowRight');await item(page,'目前工作區').waitFor();
    await button(page,'下載 JSON').hover();await page.waitForFunction(()=>document.querySelectorAll('[data-pcs-ui="submenu"]').length===0);
    await button(page,'儲存到 PCS').hover();await item(page,'目前工作區').waitFor();
    const download=page.waitForEvent('download');await button(page,'下載 JSON').click();await download;
    assert.equal(await submenus(page).count(),0);assert.equal((await calls(worker,'begin')).length,0);
    await button(page,'儲存到 PCS').hover();await item(page,'目前工作區').waitFor();
    if(process.env.PCS_SAVE_SCREENSHOT_DIR){await mkdir(process.env.PCS_SAVE_SCREENSHOT_DIR,{recursive:true});await page.screenshot({path:path.join(process.env.PCS_SAVE_SCREENSHOT_DIR,'chrome-message-cascade.png')});}
    await item(page,'旅行').click();await page.getByText('正在保存…',{exact:true}).waitFor();
    assert.equal((await calls(worker,'begin')).at(-1).destination_id,'2'.repeat(32));
    const body=Buffer.concat((await calls(worker,'chunk')).map(value=>Buffer.from(value.data,'base64')));assert.deepEqual(JSON.parse(body.toString()).messages.map(value=>value.id),['two']);
    assert.equal(await submenus(page).count(),0);assert.equal(await button(page,'查詢結果').count(),0);
    await worker.evaluate(()=>testNative.state='committed');await page.getByText('已保存',{exact:true}).waitFor();
    assert.equal((await calls(worker,'open')).length,0);assert.equal((await calls(worker,'reveal')).length,0);
    await button(page,'開啟文件').click();await button(page,'顯示資料夾').click();
    assert.equal((await calls(worker,'open')).length,1);assert.equal((await calls(worker,'reveal')).length,1);
    assert.deepEqual(live.errors,[]);
  }finally{await live.close();}
});

test('empty default saves only on the explicit main-item click and never on hover',{skip:!enabled},async()=>{
  const live=await extensionFixture(html),{page,worker}=live;page.setDefaultTimeout(6000);
  try{
    await worker.evaluate(()=>{testNative.folders=testNative.folders.filter(folder=>!folder.parent_id);});
    await choose(live);assert.equal((await calls(worker,'begin')).length,0);
    await page.waitForTimeout(250);assert.equal((await calls(worker,'begin')).length,0);
    await button(page,'儲存到 PCS').click();await page.getByText('正在保存…',{exact:true}).waitFor();
    assert.equal((await calls(worker,'begin')).length,1);assert.equal((await calls(worker,'begin'))[0].destination_id,'1'.repeat(32));
    assert.deepEqual(live.errors,[]);
  }finally{await live.close();}
});

test('multiple PCS and connected roots use cascades; remembered folders do not change the root entry',{skip:!enabled},async()=>{
  const live=await extensionFixture(html),{page,worker}=live;page.setDefaultTimeout(6000);
  try{
    await worker.evaluate(async()=>{testNative.instances.push({instance_id:'second-pcs',display_name:'PCS · 工作',capabilities:['save_destination_v1']});await chrome.storage.local.set({preferred_destination:{instance_id:'fixture-pcs',folder_id:'3'.repeat(32)}});});
    await page.getByRole('button',{name:'PCS：此則訊息',exact:true}).nth(1).click();await button(page,'儲存到 PCS').click();
    await item(page,'PCS · 工作').click();await item(page,'目前工作區').waitFor();
    assert.equal((await calls(worker,'destinations')).at(-1).instance_id,'second-pcs');assert.equal((await calls(worker,'destinations')).at(-1).folder_id,undefined);
    await item(page,'其他位置').hover();await item(page,'專案').waitFor();
    assert.equal((await calls(worker,'begin')).length,0);
    await item(page,'專案').click();await page.getByText('正在保存…',{exact:true}).waitFor();
    assert.equal((await calls(worker,'begin')).at(-1).destination_id,'4'.repeat(32));assert.equal((await calls(worker,'begin')).at(-1).instance_id,'second-pcs');
    assert.deepEqual(live.errors,[]);
  }finally{await live.close();}
});

test('late folder replies and rejected begin cannot revive a cascade after dismissal or SPA navigation',{skip:!enabled},async()=>{
  const live=await extensionFixture(html),{page,worker}=live;page.setDefaultTimeout(6000);
  try{
    await choose(live);await worker.evaluate(()=>{testNative.holdDestinations=true;delete testNative.releaseDestinations;});
    await item(page,'展開 旅行').click();await nativeReady(worker,'releaseDestinations');
    await page.keyboard.press('Escape');
    await worker.evaluate(()=>{testNative.holdDestinations=false;testNative.releaseDestinations();});
    await page.waitForTimeout(50);assert.equal(await submenus(page).count(),1);assert.equal(await item(page,'儲存於「旅行」').count(),0);
    await worker.evaluate(()=>{testNative.holdDestinations=true;delete testNative.releaseDestinations;});await item(page,'展開 旅行').click();await nativeReady(worker,'releaseDestinations');
    await page.evaluate(()=>history.pushState({},'','/c/cascade-navigation'));
    await worker.evaluate(()=>{testNative.holdDestinations=false;testNative.releaseDestinations();});await page.waitForFunction(()=>document.querySelectorAll('[data-pcs-ui="submenu"]').length===0);
    assert.equal((await calls(worker,'begin')).length,0);
    await page.getByRole('button',{name:'PCS：此則訊息',exact:true}).nth(1).click();await button(page,'儲存到 PCS').click();await item(page,'目前工作區').waitFor();
    await worker.evaluate(()=>{testNative.holdBegin=true;testNative.errors.begin={code:'destination_changed',message:'資料夾已變更，請重新選擇。'};});
    await item(page,'旅行').click();await nativeReady(worker,'releaseBegin');
    await page.evaluate(()=>history.pushState({},'','/c/after-begin-navigation'));await page.waitForTimeout(850);
    await worker.evaluate(()=>{testNative.holdBegin=false;testNative.releaseBegin();});
    await page.locator('[data-pcs-ui="entry"]').getByRole('button').click();await page.getByText('資料夾已變更，請重新選擇。',{exact:true}).waitFor();
    assert.equal(await submenus(page).count(),0);assert.equal(await page.getByText('尚未取得結果',{exact:true}).count(),0);assert.deepEqual(live.errors,[]);
  }finally{await live.close();}
});

test('cascade errors remain recoverable and do not fall back to a desktop preview',{skip:!enabled},async()=>{
  const live=await extensionFixture(html),{page,worker}=live;page.setDefaultTimeout(6000);
  try{
    await worker.evaluate(()=>testNative.instances[0].capabilities=[]);
    await page.getByRole('button',{name:'PCS：此則訊息',exact:true}).nth(1).click();await button(page,'儲存到 PCS').click();
    await page.getByText('請更新 PCS 後再儲存。',{exact:true}).waitFor();assert.equal((await calls(worker,'begin')).length,0);
    await page.keyboard.press('Escape');await worker.evaluate(()=>{testNative.instances[0].capabilities=['save_destination_v1'];testNative.errors.destinations={code:'destination_limit',message:'這個資料夾的項目太多。'};});
    await button(page,'儲存到 PCS').click();await page.getByText('這個資料夾的項目太多。',{exact:true}).waitFor();
    await worker.evaluate(()=>delete testNative.errors.destinations);await item(page,'重新讀取').click();await item(page,'目前工作區').waitFor();
    await worker.evaluate(()=>testNative.finishResult={state:'failed',message:'資料夾已移動，請重新選擇。',error_code:'destination_changed'});
    await item(page,'旅行').click();await page.getByText('資料夾已移動，請重新選擇。',{exact:true}).waitFor();
    assert.equal(await button(page,'開啟文件').count(),0);assert.equal(await button(page,'查詢結果').count(),0);assert.deepEqual(live.errors,[]);
  }finally{await live.close();}
});

test('right-edge cascades flip left; narrow overlap retains Back and outside dismissal',{skip:!enabled},async()=>{
  const live=await extensionFixture(html),{page,worker}=live;page.setDefaultTimeout(6000);
  try{
    await page.locator('[data-pcs-ui="entry"]').getByRole('button').click();await button(page,'選取全部').click();await button(page,'儲存到 PCS').hover();await item(page,'目前工作區').waitFor();
    assert.equal(await button(page,'全選').isDisabled(),true);assert.equal(await button(page,'完成選取').isDisabled(),true);
    const parent=await page.locator('[data-pcs-ui="menu"]').boundingBox(),child=await submenus(page).boundingBox();assert.ok(child.x+child.width<=parent.x+8);
    await page.setViewportSize({width:390,height:850});await item(page,'返回').waitFor();
    const bounds=await submenus(page).boundingBox();assert.ok(bounds.x>=0&&bounds.x+bounds.width<=390);
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    if(process.env.PCS_SAVE_SCREENSHOT_DIR){await mkdir(process.env.PCS_SAVE_SCREENSHOT_DIR,{recursive:true});await page.screenshot({path:path.join(process.env.PCS_SAVE_SCREENSHOT_DIR,'chrome-cascade-narrow.png')});}
    await item(page,'返回').click();assert.equal(await submenus(page).count(),0);assert.equal(await button(page,'下載 Markdown').isVisible(),true);
    assert.equal(await button(page,'全選').isEnabled(),true);assert.equal(await button(page,'完成選取').isEnabled(),true);
    await button(page,'儲存到 PCS').hover();await item(page,'目前工作區').waitFor();
    await page.mouse.click(380,810);assert.equal(await submenus(page).count(),0);assert.equal(await page.locator('[data-pcs-ui="menu"]').isVisible(),false);
    assert.equal((await calls(worker,'begin')).length,0);assert.deepEqual(live.errors,[]);
  }finally{await live.close();}
});
