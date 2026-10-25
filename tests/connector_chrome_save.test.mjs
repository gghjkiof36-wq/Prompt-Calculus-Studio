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
async function saved(live,count=1){
  await live.worker.evaluate(count=>new Promise((resolve,reject)=>{const timeout=setTimeout(()=>{clearInterval(timer);reject(new Error('Saved receipt not recorded'));},5000);const timer=setInterval(async()=>{const records=(await chrome.storage.local.get('recent')).recent||[];if(records.filter(record=>record.state==='committed').length>=count){clearInterval(timer);clearTimeout(timeout);resolve();}},10);}),count);
  await live.page.getByRole('button',{name:'儲存到 PCS',exact:true}).and(live.page.locator('button:enabled')).waitFor();
  await live.page.locator('[data-pcs-ui="menu"] .status').waitFor({state:'hidden'});
}
async function choose(live){await live.page.getByRole('button',{name:'PCS：此則訊息',exact:true}).nth(1).click();await button(live.page,'儲存到 PCS').hover();await live.page.getByRole('menuitem',{name:'目前工作區',exact:true}).waitFor();}

const item=(page,name)=>page.getByRole('menuitem',{name,exact:true});
const submenus=page=>page.locator('[data-pcs-ui="submenu"]');

test('actual MV3 cascades preserve the first menu; hover and arrows only browse',{skip:!enabled},async()=>{
  const live=await extensionFixture(html),{page,worker}=live;page.setDefaultTimeout(6000);
  try{
    await choose(live);
    assert.deepEqual(await Promise.all((await page.locator('[data-pcs-ui="menu"]').getByRole('button').all()).map(button=>button.textContent())),['儲存到 PCS','下載 JSON','下載 Markdown']);
    assert.equal(await button(page,'下載紀錄').count(),0);
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
    await live.download(()=>button(page,'下載 JSON').click());
    assert.equal(await submenus(page).count(),0);assert.equal((await calls(worker,'begin')).length,0);
    await button(page,'儲存到 PCS').hover();await item(page,'目前工作區').waitFor();
    if(process.env.PCS_SAVE_SCREENSHOT_DIR){await mkdir(process.env.PCS_SAVE_SCREENSHOT_DIR,{recursive:true});await page.screenshot({path:path.join(process.env.PCS_SAVE_SCREENSHOT_DIR,'chrome-message-cascade.png')});}
    await item(page,'旅行').click();await page.getByText('正在保存…',{exact:true}).waitFor();
    assert.equal((await calls(worker,'begin')).at(-1).destination_id,'2'.repeat(32));
    const body=Buffer.concat((await calls(worker,'chunk')).map(value=>Buffer.from(value.data,'base64')));assert.deepEqual(JSON.parse(body.toString()).messages.map(value=>value.id),['two']);
    assert.equal(await submenus(page).count(),0);assert.equal(await button(page,'查詢結果').count(),0);
    await worker.evaluate(()=>testNative.state='committed');await saved(live);
    assert.deepEqual(await Promise.all((await page.locator('[data-pcs-ui="menu"]').getByRole('button').all()).map(button=>button.textContent())),['儲存到 PCS','下載 JSON','下載 Markdown']);
    assert.equal(await page.getByText('已保存',{exact:true}).count(),0);assert.equal(await page.getByRole('status').count(),0);
    assert.equal((await calls(worker,'open')).length,0);assert.equal((await calls(worker,'reveal')).length,0);
    assert.equal(await button(page,'開啟文件').count(),0);await page.locator('[data-pcs-ui="entry"]').getByRole('button').click();await button(page,'下載紀錄').click();
    const pcsRow=page.locator('[data-pcs-ui="menu"] .receipt').filter({hasText:'京都四天旅行安排'}).filter({hasNotText:'.json'});
    await pcsRow.getByRole('button',{name:'開啟文件',exact:true}).click();await pcsRow.getByRole('button',{name:'開啟資料夾',exact:true}).click();
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

test('Escape closes the open submenu before deferred focus restoration and leaves the closed page alone',{skip:!enabled},async()=>{
  const live=await extensionFixture(html),{page,worker}=live;page.setDefaultTimeout(6000);
  try{
    await worker.evaluate(()=>testNative.instances[0].capabilities=[]);
    await page.getByRole('button',{name:'PCS：此則訊息',exact:true}).nth(1).click();
    await button(page,'儲存到 PCS').waitFor();
    await page.evaluate(()=>new Promise(requestAnimationFrame));
    await worker.evaluate(async()=>{
      const [tab]=await chrome.tabs.query({url:'https://chatgpt.com/*'});
      await chrome.scripting.executeScript({target:{tabId:tab.id},func:()=>{
        globalThis.testDeferredFrames=[];globalThis.testOriginalFrame=requestAnimationFrame;
        globalThis.requestAnimationFrame=callback=>{testDeferredFrames.push(callback);return testDeferredFrames.length;};
      }});
    });
    await page.evaluate(()=>{globalThis.nativeEscapes=[];window.addEventListener('keydown',event=>{if(event.key==='Escape')nativeEscapes.push(event.defaultPrevented);});});
    await button(page,'儲存到 PCS').click();
    await page.getByText('請更新 PCS 後再儲存。',{exact:true}).waitFor();
    assert.equal(await page.evaluate(()=>document.activeElement.tagName),'BODY','render replaced the focused button before the held frame');
    assert.equal(await submenus(page).count(),1);
    await page.keyboard.press('Escape');
    assert.equal(await submenus(page).count(),0);
    assert.equal(await button(page,'儲存到 PCS').isVisible(),true,'only the child menu closes');
    assert.equal(await button(page,'儲存到 PCS').isEnabled(),true);
    await page.keyboard.press('Escape');
    assert.equal(await page.locator('[data-pcs-ui="menu"]').isVisible(),false);
    await page.keyboard.press('Escape');
    assert.deepEqual(await page.evaluate(()=>[nativeEscapes[0],nativeEscapes.at(-1)]),[true,false],'closed PCS menus do not consume the page Escape');
    assert.equal((await calls(worker,'begin')).length,0);assert.deepEqual(live.errors,[]);
  }finally{await live.close();}
});

test('right-edge cascades flip left; narrow overlap retains Back and outside dismissal',{skip:!enabled},async()=>{
  const live=await extensionFixture(html),{page,worker}=live;page.setDefaultTimeout(6000);
  try{
    await page.locator('[data-pcs-ui="entry"]').getByRole('button').click();await button(page,'全選').click();await button(page,'儲存到 PCS').hover();await item(page,'目前工作區').waitFor();
    assert.equal(await button(page,'全選').isDisabled(),true);assert.equal(await button(page,'取消').isEnabled(),true);
    const parent=await page.locator('[data-pcs-ui="menu"]').boundingBox(),child=await submenus(page).boundingBox();assert.ok(child.x+child.width<=parent.x+8);
    await page.setViewportSize({width:390,height:850});await item(page,'返回').waitFor();
    const bounds=await submenus(page).boundingBox();assert.ok(bounds.x>=0&&bounds.x+bounds.width<=390);
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    if(process.env.PCS_SAVE_SCREENSHOT_DIR){await mkdir(process.env.PCS_SAVE_SCREENSHOT_DIR,{recursive:true});await page.screenshot({path:path.join(process.env.PCS_SAVE_SCREENSHOT_DIR,'chrome-cascade-narrow.png')});}
    await item(page,'返回').click();assert.equal(await submenus(page).count(),0);assert.equal(await button(page,'下載 Markdown').isVisible(),true);
    assert.equal(await button(page,'全選').isEnabled(),true);assert.equal(await button(page,'取消').isEnabled(),true);
    await button(page,'儲存到 PCS').hover();await item(page,'目前工作區').waitFor();
    await page.mouse.click(380,810);assert.equal(await submenus(page).count(),0);assert.equal(await page.locator('[data-pcs-ui="menu"]').isVisible(),false);
    assert.equal((await calls(worker,'begin')).length,0);assert.deepEqual(live.errors,[]);
  }finally{await live.close();}
});

test('completed saves allow new message selection and follow SPA conversation changes',{skip:!enabled},async()=>{
  const live=await extensionFixture(html),{page,worker}=live;page.setDefaultTimeout(6000);
  try{
    await worker.evaluate(()=>testNative.finishResult={state:'committed',title:'京都四天旅行安排',path:'D:/PCS/筆記/京都.md'});
    const header=page.locator('[data-pcs-ui="entry"]').getByRole('button');
    await header.click();await button(page,'全選').click();
    await button(page,'儲存到 PCS').hover();await item(page,'目前工作區').click();await saved(live);
    await header.click();
    await page.evaluate(()=>document.querySelector('main').insertAdjacentHTML('beforeend','<article><div data-message-author-role="assistant" data-message-id="three"><div class="markdown"><p>新加入的交通資訊。</p></div></div><div class="turn-action-controls"><button aria-label="複製">複製</button></div></article>'));
    const third=page.getByRole('checkbox',{name:'選取第 3 則訊息',exact:true});await third.waitFor();
    assert.equal(await page.locator('[data-pcs-selected]').count(),0,'changed content clears the old selection');
    await third.check();await header.click();
    assert.equal(await button(page,'儲存到 PCS').isEnabled(),true);assert.equal(await button(page,'重新選取').count(),0);
    await button(page,'儲存到 PCS').hover();await item(page,'目前工作區').click();await saved(live,2);
    const second=(await calls(worker,'begin')).at(-1);
    const bytes=Buffer.concat((await calls(worker,'chunk')).filter(part=>part.operation_id===second.operation_id).map(part=>Buffer.from(part.data,'base64')));
    assert.deepEqual(JSON.parse(bytes).messages.map(message=>message.id),['three']);
    await page.evaluate(()=>{history.pushState({},'', '/c/another-conversation');document.querySelector('main').innerHTML='<article><div data-message-author-role="assistant" data-message-id="four"><div class="markdown"><p>另一個對話的內容。</p></div></div><div class="turn-action-controls"><button aria-label="複製">複製</button></div></article>';});
    const first=page.getByRole('checkbox',{name:'選取第 1 則訊息',exact:true});await first.waitFor();
    await page.waitForFunction(()=>document.querySelectorAll('[data-pcs-ui="selection"]').length===1);
    assert.equal(await first.isChecked(),false);await first.check();await header.click();
    assert.equal(await button(page,'重新選取').count(),0);assert.equal(await button(page,'下載 JSON').isEnabled(),true);
    const downloaded=await live.download(()=>button(page,'下載 JSON').click());
    assert.deepEqual(JSON.parse(downloaded.text).messages.map(message=>message.id),['four']);
    assert.equal((await calls(worker,'begin')).length,2,'refresh and navigation must never save automatically');
  }finally{await live.close();}
});

test('committed selection remains usable for downloads and a deliberate save to another destination',{skip:!enabled},async()=>{
  const live=await extensionFixture(html),{page,worker}=live;page.setDefaultTimeout(6000);
  try{
    await worker.evaluate(()=>testNative.finishResult={state:'committed',title:'京都四天旅行安排',path:'D:/PCS/筆記/京都.md'});
    const header=page.locator('[data-pcs-ui="entry"]').getByRole('button');
    await header.click();assert.equal(await button(page,'全選').getAttribute('title'),'選取頁面已載入的訊息');assert.equal(await button(page,'全選').getAttribute('aria-description'),'選取頁面已載入的訊息');await button(page,'全選').click();
    await page.getByText('已選 2 則（已載入）',{exact:true}).waitFor();
    await header.click();
    await page.getByRole('checkbox',{name:'選取第 1 則訊息',exact:true}).uncheck();
    await header.click();
    if(process.env.PCS_SAVE_AGAIN_SCREENSHOT_DIR){await mkdir(process.env.PCS_SAVE_AGAIN_SCREENSHOT_DIR,{recursive:true});await page.screenshot({path:path.join(process.env.PCS_SAVE_AGAIN_SCREENSHOT_DIR,'chrome-selection-actions.png')});}
    await button(page,'儲存到 PCS').click();await item(page,'目前工作區').click();
    await saved(live);
    const first=(await calls(worker,'begin'))[0];
    assert.equal(await page.getByRole('checkbox',{name:'選取第 2 則訊息',exact:true}).isChecked(),true);
    assert.equal(await page.getByRole('checkbox',{name:'選取第 1 則訊息',exact:true}).isChecked(),false);
    assert.equal(await page.getByRole('checkbox',{name:'選取第 2 則訊息',exact:true}).isEnabled(),true);
    for(const name of ['儲存到 PCS','下載 JSON','下載 Markdown'])assert.equal(await button(page,name).isEnabled(),true);
    assert.equal(await page.getByRole('status').count(),0);assert.equal(await page.getByText('已保存',{exact:true}).count(),0);
    for(const name of ['開啟文件','開啟資料夾','重新選取','完成選取'])assert.equal(await button(page,name).count(),0);
    if(process.env.PCS_SAVE_AGAIN_SCREENSHOT_DIR)await page.screenshot({path:path.join(process.env.PCS_SAVE_AGAIN_SCREENSHOT_DIR,'chrome-saved-actions.png')});
    for(const name of ['下載 JSON','下載 Markdown']){
      const {text:content}=await live.download(()=>button(page,name).click());
      if(name==='下載 JSON')assert.deepEqual(JSON.parse(content).messages.map(message=>message.id),['two']);
      else {assert.match(content,/四天慢慢遊京都/);assert.ok(!content.includes('我們安排四天'));}
    }
    await header.click();await header.click();assert.equal((await calls(worker,'begin')).length,1);
    await button(page,'儲存到 PCS').hover();await item(page,'旅行').waitFor();assert.equal((await calls(worker,'begin')).length,1);
    await item(page,'旅行').click();await saved(live,2);
    const begins=await calls(worker,'begin');assert.equal(begins.length,2);assert.notEqual(begins[1].operation_id,first.operation_id);
    assert.equal(begins[1].destination_id,'2'.repeat(32));assert.equal(first.destination_id,'1'.repeat(32));
    for(const begin of begins){const body=Buffer.concat((await calls(worker,'chunk')).filter(part=>part.operation_id===begin.operation_id).map(part=>Buffer.from(part.data,'base64')));assert.deepEqual(JSON.parse(body.toString()).messages.map(message=>message.id),['two']);}
    await button(page,'下載紀錄').click();
    assert.equal(await page.locator('[data-pcs-ui="menu"] .receipt').count(),4);
    const jsonRow=page.locator('[data-pcs-ui="menu"] .receipt').filter({hasText:'.json'}),mdRow=page.locator('[data-pcs-ui="menu"] .receipt').filter({hasText:'.md'});
    assert.equal(await jsonRow.count(),1);assert.equal(await mdRow.count(),1);
    await jsonRow.getByRole('button',{name:'開啟資料夾',exact:true}).click();await mdRow.getByRole('button',{name:'開啟文件',exact:true}).click();
    const downloadActions=await worker.evaluate(()=>testDownloadActions),downloadRecords=await worker.evaluate(async()=>(await chrome.storage.local.get('downloads')).downloads);
    assert.equal(downloadActions[0].id,downloadRecords.find(record=>record.format==='json').download_id);
    assert.equal(downloadActions[1].id,downloadRecords.find(record=>record.format==='md').download_id);
    assert.ok(downloadRecords.every(record=>record.state==='complete'&&record.path));
    assert.ok(!JSON.stringify(downloadRecords).includes('清水寺可以安排在早上'));
    for(const name of ['開啟文件','開啟資料夾']){const action=button(page,name).first();assert.equal(await action.textContent(),'');assert.equal(await action.locator('svg').count(),1);assert.equal(await action.getAttribute('title'),name);const box=await action.boundingBox();assert.ok(box.width>=32&&box.height>=32);}
    if(process.env.PCS_SAVE_AGAIN_SCREENSHOT_DIR)await page.screenshot({path:path.join(process.env.PCS_SAVE_AGAIN_SCREENSHOT_DIR,'chrome-download-history.png')});
    await button(page,'開啟文件').first().click();assert.equal((await calls(worker,'open')).at(-1).operation_id,begins[1].operation_id);
    assert.deepEqual(live.errors,[]);
  }finally{await live.close();}
});

test('a confirmed failed save keeps the selection and explicit retry uses a new operation',{skip:!enabled},async()=>{
  const live=await extensionFixture(html),{page,worker}=live;page.setDefaultTimeout(6000);
  try{
    await worker.evaluate(()=>testNative.finishResult={state:'failed',message:'資料夾已移動，請重新選擇。',error_code:'destination_changed'});
    await choose(live);await item(page,'旅行').click();await page.getByText('資料夾已移動，請重新選擇。',{exact:true}).waitFor();
    const first=(await calls(worker,'begin'))[0];
    assert.equal(await button(page,'儲存到 PCS').isEnabled(),true);assert.equal(await button(page,'下載 JSON').isEnabled(),true);
    assert.equal(await button(page,'查詢結果').count(),0);assert.equal((await calls(worker,'finish')).length,1);
    await worker.evaluate(()=>testNative.finishResult={state:'committed',title:'京都四天旅行安排',path:'D:/PCS/筆記/京都.md'});
    await button(page,'儲存到 PCS').click();await item(page,'目前工作區').click();await saved(live);
    const begins=await calls(worker,'begin');assert.equal(begins.length,2);assert.notEqual(begins[1].operation_id,first.operation_id);
    const records=await worker.evaluate(async()=>(await chrome.storage.local.get('recent')).recent);
    assert.equal(records.find(record=>record.operation_id===first.operation_id).state,'failed');
    assert.equal(records.find(record=>record.operation_id===begins[1].operation_id).state,'committed');
    assert.deepEqual(live.errors,[]);
  }finally{await live.close();}
});

test('inflight double clicks and unknown recovery never create a second save operation',{skip:!enabled},async()=>{
  const live=await extensionFixture(html),{page,worker}=live;page.setDefaultTimeout(6000);
  try{
    await worker.evaluate(()=>{testNative.holdBegin=true;testNative.holdFinish=true;testNative.state='unknown';});
    await choose(live);await item(page,'目前工作區').dblclick();await nativeReady(worker,'releaseBegin');
    if(!(await page.locator('[data-pcs-ui="menu"]').isVisible()))await page.locator('[data-pcs-ui="entry"]').getByRole('button').click();
    assert.equal((await calls(worker,'begin')).length,1);assert.equal((await calls(worker,'finish')).length,0);
    assert.equal(await button(page,'儲存到 PCS').isDisabled(),true);assert.equal(await button(page,'下載 JSON').isEnabled(),true);
    await page.keyboard.press('Escape');await page.locator('[data-pcs-ui="entry"]').getByRole('button').click();
    assert.equal((await calls(worker,'begin')).length,1);
    await worker.evaluate(()=>{testNative.holdBegin=false;testNative.releaseBegin();});await nativeReady(worker,'releaseFinish');
    await worker.evaluate(()=>{testNative.holdFinish=false;testNative.releaseFinish();});await page.getByText('尚未取得結果',{exact:true}).waitFor();
    const identity=(await calls(worker,'begin'))[0].operation_id;
    await button(page,'查詢結果').click();await page.getByText('尚未取得結果',{exact:true}).waitFor();
    assert.equal((await calls(worker,'begin')).length,1);assert.equal((await calls(worker,'finish')).length,1);
    assert.ok((await calls(worker,'status')).every(call=>call.operation_id===identity));
    assert.equal(await button(page,'儲存到 PCS').isDisabled(),true);assert.equal(await button(page,'下載 JSON').isEnabled(),true);
    assert.deepEqual(live.errors,[]);
  }finally{await live.close();}
});

test('a JSON download double-click creates one completed browser file without a PCS connection or success notice',{skip:!enabled},async()=>{
  const live=await extensionFixture(html),{page,worker}=live;page.setDefaultTimeout(6000);
  try{
    await page.getByRole('button',{name:'PCS：此則訊息',exact:true}).nth(1).click();
    const file=await live.download(()=>button(page,'下載 JSON').dblclick());
    assert.deepEqual(JSON.parse(file.text).messages.map(item=>item.id),['two']);
    const records=await worker.evaluate(async()=>(await chrome.storage.local.get('downloads')).downloads);
    assert.equal(records.length,1);assert.equal(records[0].download_id,file.download_id);assert.equal(records[0].state,'complete');
    assert.equal((await calls(worker,'hello')).length,0);assert.equal((await calls(worker,'begin')).length,0);
    if(!(await page.locator('[data-pcs-ui="menu"]').isVisible()))await page.getByRole('button',{name:'PCS：此則訊息',exact:true}).nth(1).click();
    for(const name of ['儲存到 PCS','下載 JSON','下載 Markdown'])assert.equal(await button(page,name).isEnabled(),true);
    assert.equal(await page.getByRole('status').count(),0);assert.equal(await page.getByText('已保存',{exact:true}).count(),0);
    await page.locator('[data-pcs-ui="entry"]').getByRole('button').click();await button(page,'下載紀錄').click();
    assert.equal(await page.locator('[data-pcs-ui="menu"] .receipt').count(),1);
    await button(page,'開啟資料夾').click();assert.deepEqual(await worker.evaluate(()=>testDownloadActions),[{action:'reveal',id:file.download_id}]);
    assert.deepEqual(live.errors,[]);
  }finally{await live.close();}
});

test('canonical native operation IDs poll to completion, unlock controls and keep the wire identity',{skip:!enabled},async()=>{
  const live=await extensionFixture(html),{page,worker}=live;page.setDefaultTimeout(6000);
  try{
    await choose(live);await item(page,'目前工作區').click();await page.getByText('正在保存…',{exact:true}).waitFor();
    const begin=(await calls(worker,'begin'))[0];assert.equal(begin.operation_id.length,36);
    // The fixture returns the real PCS public_record format: a 32-character key.
    // Before this fix that replaced the UI's UUID and skipped finally + polling.
    assert.equal(await button(page,'下載 JSON').isEnabled(),true);
    assert.equal(await button(page,'取消').isEnabled(),true);
    await worker.evaluate(()=>testNative.state='committed');await saved(live);
    assert.ok((await calls(worker,'status')).length>0);
    assert.ok((await calls(worker,'status')).every(call=>call.operation_id===begin.operation_id));
    for(const name of ['儲存到 PCS','下載 JSON','下載 Markdown'])assert.equal(await button(page,name).isEnabled(),true);
    assert.equal(await page.getByText('正在保存…',{exact:true}).count(),0);
    await button(page,'儲存到 PCS').click();await item(page,'旅行').click();await saved(live,2);
    assert.equal((await calls(worker,'begin')).length,2);
  }finally{await live.close();}
});

test('saving can export independently; cancelling exits immediately and late receipts cannot revive the old menu',{skip:!enabled},async()=>{
  const live=await extensionFixture(html),{page,worker}=live;page.setDefaultTimeout(6000);
  try{
    await worker.evaluate(()=>{testNative.holdFinish=true;testNative.holdCancel=true;testNative.state='committing';});
    await choose(live);await item(page,'目前工作區').click();await nativeReady(worker,'releaseFinish');
    const first=(await calls(worker,'begin'))[0];
    const file=await live.download(()=>button(page,'下載 Markdown').click());assert.match(file.text,/四天慢慢遊京都/);
    assert.equal((await calls(worker,'begin')).length,1);
    await button(page,'取消').click();await button(page,'選取訊息').waitFor();await nativeReady(worker,'releaseCancel');
    assert.equal(await page.getByText('正在保存…',{exact:true}).count(),0);
    assert.equal(await page.locator('[data-pcs-ui="selection"]').count(),0);
    await worker.evaluate(()=>{testNative.holdCancel=false;testNative.releaseCancel();testNative.holdFinish=false;testNative.releaseFinish();});
    await page.waitForTimeout(100);
    assert.equal(await button(page,'選取訊息').isEnabled(),true);
    const receipt=await worker.evaluate(async key=>(await chrome.storage.local.get('recent')).recent.find(item=>item.operation_id===key),first.operation_id);
    assert.equal(receipt.state,'cancelled');assert.equal(receipt.dismissed,true);
    await button(page,'全選').click();await button(page,'儲存到 PCS').hover();await item(page,'目前工作區').click();
    assert.equal((await calls(worker,'begin')).length,2,'only a fresh explicit save creates a new operation');
  }finally{await live.close();}
});

test('old unfinished receipts stay in history and never lock a new conversation',{skip:!enabled},async()=>{
  const live=await extensionFixture(html),{page,worker}=live;page.setDefaultTimeout(6000);
  try{
    const identity={operation_id:'12345678-1234-1234-1234-123456789abc',instance_id:'fixture-pcs'};
    await worker.evaluate(async identity=>chrome.storage.local.set({recent:[{...identity,state:'committing',title:'舊對話',updated_at:new Date().toISOString()}]}),identity);
    await page.locator('[data-pcs-ui="entry"]').getByRole('button').click();await button(page,'全選').click();
    for(const name of ['儲存到 PCS','下載 JSON','下載 Markdown','取消'])assert.equal(await button(page,name).isEnabled(),true);
    assert.equal((await calls(worker,'begin')).length,0);
    await button(page,'下載紀錄').click();await page.locator('[data-pcs-ui="menu"] .receipt').filter({hasText:'舊對話'}).waitFor();
    assert.equal(await page.getByText('尚未確認保存結果',{exact:true}).count(),1);
    await worker.evaluate(()=>testNative.state='committed');await button(page,'查詢結果').click();
    await page.locator('[data-pcs-ui="menu"] button[aria-label="開啟文件"]:enabled').waitFor();
    assert.equal((await calls(worker,'begin')).length,0);
  }finally{await live.close();}
});

test('a stalled save becomes queryable without resubmission and a late committed receipt still wins',{skip:!enabled},async()=>{
  const live=await extensionFixture(html),{page,worker}=live;page.setDefaultTimeout(6000);
  try{
    await choose(live);await item(page,'目前工作區').click();await page.getByText('正在保存…',{exact:true}).waitFor();
    await worker.evaluate(async()=>{const [tab]=await chrome.tabs.query({url:'https://chatgpt.com/*'});await chrome.scripting.executeScript({target:{tabId:tab.id},func:()=>{const now=Date.now;Date.now=()=>now()+31000;}});});
    await page.getByText('尚未取得結果',{exact:true}).waitFor();
    for(const name of ['下載 JSON','下載 Markdown','取消','查詢結果'])assert.equal(await button(page,name).isEnabled(),true);
    assert.equal((await calls(worker,'begin')).length,1);assert.equal((await calls(worker,'finish')).length,1);
    await worker.evaluate(()=>testNative.state='committed');await button(page,'查詢結果').click();await saved(live);
    assert.equal((await calls(worker,'begin')).length,1);
  }finally{await live.close();}
});

test('cancel during a held begin stops remaining chunks even when the begin reply arrives later',{skip:!enabled},async()=>{
  const live=await extensionFixture(html),{page,worker}=live;page.setDefaultTimeout(6000);
  try{
    await worker.evaluate(()=>testNative.holdBegin=true);
    await choose(live);await item(page,'目前工作區').click();await nativeReady(worker,'releaseBegin');
    await button(page,'取消').click();await button(page,'選取訊息').waitFor();
    await worker.evaluate(()=>{testNative.holdBegin=false;testNative.releaseBegin();});await page.waitForTimeout(100);
    assert.equal((await calls(worker,'chunk')).length,0);assert.equal((await calls(worker,'finish')).length,0);
    assert.equal((await calls(worker,'cancel')).length,1);
    assert.equal(await button(page,'全選').isEnabled(),true);
  }finally{await live.close();}
});

test('failed cancellation never keeps the selection locked or fabricates a cancelled receipt',{skip:!enabled},async()=>{
  const live=await extensionFixture(html),{page,worker}=live;page.setDefaultTimeout(6000);
  try{
    await worker.evaluate(()=>testNative.errors.cancel={code:'disconnected',message:'與 PCS 的連線已中斷。'});
    await choose(live);await item(page,'目前工作區').click();await page.getByText('正在保存…',{exact:true}).waitFor();
    await button(page,'取消').click();await button(page,'選取訊息').waitFor();
    await page.locator('[data-pcs-ui="entry"]').getByRole('button').click();
    await button(page,'下載紀錄').click();await page.getByText('尚未確認保存結果',{exact:true}).waitFor();
    const records=await worker.evaluate(async()=>(await chrome.storage.local.get('recent')).recent);
    assert.equal(records[0].state,'committing');assert.equal(records[0].dismissed,true);
    await button(page,'‹ 返回').click();assert.equal(await button(page,'全選').isEnabled(),true);
    assert.equal((await calls(worker,'begin')).length,1);
  }finally{await live.close();}
});
