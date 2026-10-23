import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdir} from 'node:fs/promises';
import path from 'node:path';
import {extensionFixture} from './connector_chrome_helpers.mjs';

const enabled=!!process.env.PCS_PLAYWRIGHT_MODULE;
const header=`<header data-app-shell-titlebar="true"><div data-app-shell-main-titlebar="true" aria-hidden="false"><div data-app-shell-page-header="true"><strong>ChatGPT</strong></div><div class="right-actions" data-app-shell-header-obstacle="true" data-app-shell-thread-content-overlap="true"><div class="native-wrapper"><button aria-label="更多" aria-haspopup="menu">•••</button></div><div class="native-wrapper"><button aria-label="分享">分享</button></div></div></div></header>`;
const copy='<div class="turn-action-controls"><button aria-label="複製">複製</button><button aria-label="分享回覆">分享</button></div>';
const message=(id,role,body)=>`<section data-chatgpt-search-unit-key="turn-${id}:0:${role}" data-chatgpt-search-message-ids="${id}">${role==='assistant'?'<h4 class="sr-only" data-conversation-role="assistant">ChatGPT 說：</h4>':''}<div class="message-body">${body}</div>${copy}</section>`;
const body=message('one','user','<p id="request">我想每天花 20 分鐘練英文，先把聽力和日常表達練起來。</p>')+
  message('two','assistant','<h2>先從每天做得到的練習開始</h2><p id="prose">把練習分成三段，不需要一次記住很多單字。</p><ol><li>聽一小段英文，先抓住主題。</li><li>挑兩句實用的表達，跟著說一次。</li><li>用自己的生活改寫一句。</li></ol><p>例如用 <code id="inline-code">I usually start work at nine.</code> 描述你的日常。</p><pre><button id="code-copy" aria-label="複製程式碼">複製</button><code id="code-text">Today I learned something new.</code></pre><p><a id="reference" href="#reference">練習參考</a></p><details><summary>更多練習</summary><p>請讀一句短文。</p></details><input aria-label="原頁輸入" placeholder="原頁互動欄位"><button id="native-button">播放例句</button>')+
  message('three','user','<p>平日以聽力為主，週末可以多留一些時間。</p>')+
  message('four','assistant','<p>平日照 20 分鐘的安排，週末再回顧本週練過的句子。</p>');
const styles=`*{box-sizing:border-box}html{color-scheme:dark}body{margin:0;background:#212121;color:#ececec;font:15px/1.65 system-ui,"Microsoft JhengHei",sans-serif}header{position:sticky;top:0;height:52px;background:#212121;z-index:10;pointer-events:none}[data-app-shell-main-titlebar]{display:flex;align-items:center;height:52px;padding:0 20px}[data-app-shell-page-header]{font-size:20px}.right-actions{display:flex;align-items:center;gap:4px;margin-left:auto}.native-wrapper{display:flex;pointer-events:auto}button,input{font:inherit}button{padding:5px 9px;border:0;border-radius:6px;background:transparent;color:inherit;cursor:pointer}main{max-width:780px;padding:30px 44px 80px;margin:auto}section{margin:0 0 34px}section[data-chatgpt-search-unit-key$=user] .message-body{background:#303030;border-radius:18px;padding:12px 18px;margin-left:64px}p{margin:0 0 14px}p:last-child{margin-bottom:0}h2{font-size:19px;margin:0 0 14px}.turn-action-controls{display:flex;margin-top:8px;color:#aaa;font-size:13px}pre{position:relative;background:#171717;border-radius:8px;padding:40px 16px 14px;overflow:auto}pre button{position:absolute;top:4px;right:6px;font:12px system-ui}a{color:#abc7ef}input{max-width:180px;margin:8px 8px 8px 0;padding:6px;background:#303030;border:1px solid #555;color:inherit}.sr-only{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0,0,0,0)}@media(max-width:650px){main{padding:24px 42px 64px}section[data-chatgpt-search-unit-key$=user] .message-body{margin-left:16px}[data-app-shell-main-titlebar]{padding:0 12px}}`;
const pageHTML=(withHeader=true)=>`<!doctype html><html class="dark"><head><title>每天 20 分鐘的英文練習 - ChatGPT</title><style>${styles}</style></head><body><aside hidden>${header}</aside>${withHeader?header:''}<main><div role="region" aria-label="對話">${body}</div></main><script>window.nativeClicks=0;document.querySelectorAll('#reference,#code-copy,#native-button').forEach(element=>element.onclick=event=>{event.preventDefault();window.nativeClicks++});</script></body></html>`;

test('right header placement follows late app-shell and SPA replacement without toolbar activation',{skip:!enabled},async()=>{
  const live=await extensionFixture(pageHTML(false),{waitForEntry:false});
  try{
    const {page}=live;
    await page.locator('[data-pcs-ui="menu"]').waitFor({state:'attached'});
    assert.equal(await page.locator('[data-pcs-ui="entry"]').count(),0);
    await page.evaluate(html=>document.body.insertAdjacentHTML('afterbegin',html),header);
    const entry=page.locator('[data-pcs-ui="entry"]');await entry.waitFor();
    assert.equal(await entry.evaluate(element=>element.parentElement.matches('.right-actions')&&element.nextElementSibling.classList.contains('native-wrapper')),true);
    assert.equal(await entry.evaluate(element=>getComputedStyle(element).pointerEvents),'auto');
    const rect=await entry.boundingBox();assert.ok(rect.x>700);
    await entry.getByRole('button').click();
    const menu=page.locator('[data-pcs-ui="menu"]');await menu.waitFor({state:'visible'});
    assert.ok((await menu.boundingBox()).x>600);
    await page.evaluate(html=>{document.querySelector('body>header').setAttribute('aria-hidden','true');history.pushState({},'', '/project/synthetic/c/second');document.body.insertAdjacentHTML('afterbegin',html);},header);
    await page.waitForFunction(()=>document.querySelector('[data-pcs-ui="entry"]')?.closest('header')===document.querySelector('body>header'));
    assert.equal(await entry.count(),1);
    assert.equal(await page.locator('header[aria-hidden="true"] [data-pcs-ui="entry"]').count(),0);
    assert.equal(await page.locator('aside [data-pcs-ui="entry"]').count(),0);
  }finally{await live.close();}
});

test('whole-message selection preserves native reading and interactions',{skip:!enabled},async t=>{
  const live=await extensionFixture(pageHTML()),{page}=live;
  const entry=page.locator('[data-pcs-ui="entry"]');
  const first=page.getByRole('checkbox',{name:'選取第 1 則訊息',exact:true});
  const second=page.getByRole('checkbox',{name:'選取第 2 則訊息',exact:true});
  const original=(await live.capture()).result.fingerprint;
  try{
    await entry.getByRole('button').click();await page.getByRole('button',{name:'選取訊息',exact:true}).click();await first.waitFor();
    await t.test('right markers and whole-message click toggle a visible selection state',async()=>{
      const geometry=await first.evaluate(input=>({box:input.getBoundingClientRect().toJSON(),message:document.querySelector('[data-chatgpt-search-message-ids="one"]').getBoundingClientRect().toJSON()}));
      assert.ok(geometry.box.x>geometry.message.right);
      await page.locator('#request').click();await page.waitForFunction(()=>document.querySelector('[data-chatgpt-search-message-ids="one"]').hasAttribute('data-pcs-selected'));
      assert.equal(await first.isChecked(),true);
      assert.equal(await page.locator('[data-chatgpt-search-message-ids="one"]').evaluate(element=>getComputedStyle(element).borderRadius),'8px');
      assert.equal(await page.locator('[data-chatgpt-search-message-ids="one"]').evaluate(element=>getComputedStyle(element).outlineStyle),'none');
      await page.locator('#request').click();await page.waitForFunction(()=>!document.querySelector('[data-chatgpt-search-message-ids="one"]').hasAttribute('data-pcs-selected'));
      assert.equal(await first.isChecked(),false);
      assert.equal((await live.capture()).result.fingerprint,original);
    });
    await t.test('text drag and a later double-click preserve both text selection and prior message selection',async()=>{
      const rect=await page.locator('#prose').boundingBox();
      await page.mouse.move(rect.x+2,rect.y+10);await page.mouse.down();await page.mouse.move(rect.x+180,rect.y+10,{steps:10});await page.mouse.up();
      assert.ok(await page.evaluate(()=>getSelection().toString().length>0));await page.waitForTimeout(320);assert.equal(await second.isChecked(),false);
      await page.evaluate(()=>getSelection().removeAllRanges());
      await page.locator('#prose').click();await page.waitForFunction(()=>document.querySelector('[data-chatgpt-search-message-ids="two"]').hasAttribute('data-pcs-selected'));
      await page.waitForTimeout(350);await page.locator('#prose').dblclick({position:{x:30,y:10}});await page.waitForTimeout(320);
      assert.ok(await page.evaluate(()=>getSelection().toString().length>0));assert.equal(await second.isChecked(),true);
      await page.evaluate(()=>getSelection().removeAllRanges());
      await second.uncheck();
    });
    await t.test('inline and block code are selectable, while code copy and native controls retain their actions',async()=>{
      await page.locator('#inline-code').click();await page.waitForFunction(()=>document.querySelector('[data-chatgpt-search-message-ids="two"]').hasAttribute('data-pcs-selected'));
      await page.locator('#code-text').click();await page.waitForFunction(()=>!document.querySelector('[data-chatgpt-search-message-ids="two"]').hasAttribute('data-pcs-selected'));
      await page.locator('#code-copy').click();await page.locator('#reference').click();await page.locator('#native-button').click();
      await page.getByRole('textbox',{name:'原頁輸入'}).fill('原生輸入仍可使用');
      await page.waitForTimeout(320);assert.equal(await second.isChecked(),false);
      assert.equal(await page.evaluate(()=>nativeClicks),3);
      assert.equal(await page.getByRole('textbox',{name:'原頁輸入'}).inputValue(),'原生輸入仍可使用');
      assert.equal((await live.capture()).result.fingerprint,original);
    });
    await t.test('completion removes styling and content handlers while keeping selected data for saving',async()=>{
      await first.check();await entry.getByRole('button').click();await page.getByRole('button',{name:'完成選取',exact:true}).click();
      assert.equal(await page.locator('[data-pcs-ui="selection"]').count(),0);
      assert.equal(await page.locator('[data-pcs-ui="selection-style"]').count(),0);
      assert.equal(await page.locator('[data-pcs-selectable],[data-pcs-selected]').count(),0);
      assert.equal(await page.getByRole('button',{name:'儲存到 PCS',exact:true}).isEnabled(),true);
      await page.locator('#request').click();await page.waitForTimeout(320);
      await entry.getByRole('button').click();assert.equal(await page.getByText('已選 1 則',{exact:true}).count(),1);
      assert.equal((await live.capture()).result.fingerprint,original);
    });
  }finally{await live.close();}
});

test('right header and selected messages fit normal and narrow viewports',{skip:!enabled},async()=>{
  const live=await extensionFixture(pageHTML(),{width:1200,height:1050}),{page}=live;
  try{
    const entry=page.locator('[data-pcs-ui="entry"]');await entry.getByRole('button').click();
    const directory=process.env.PCS_SELECTION_SCREENSHOT_DIR;
    if(directory){await mkdir(directory,{recursive:true});await page.screenshot({path:path.join(directory,'chrome-right-header-wide.png')});await page.setViewportSize({width:600,height:1050});await page.screenshot({path:path.join(directory,'chrome-right-header-narrow.png')});await page.setViewportSize({width:1200,height:1050});}
    await page.getByRole('button',{name:'選取訊息',exact:true}).click();
    await page.getByRole('checkbox',{name:'選取第 1 則訊息',exact:true}).check();
    await page.getByRole('checkbox',{name:'選取第 2 則訊息',exact:true}).check();
    if(directory)await page.screenshot({path:path.join(directory,'chrome-right-selection-wide.png')});
    await page.setViewportSize({width:600,height:1050});
    await page.waitForFunction(()=>{const entry=document.querySelector('[data-pcs-ui="entry"]');return entry.getBoundingClientRect().right<600;});
    const first=page.getByRole('checkbox',{name:'選取第 1 則訊息',exact:true});
    const geometry=await first.evaluate(input=>({box:input.getBoundingClientRect().toJSON(),message:document.querySelector('[data-chatgpt-search-message-ids="one"]').getBoundingClientRect().toJSON()}));
    assert.ok(geometry.box.x>geometry.message.right);assert.ok(geometry.box.right<=600);
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
    if(directory)await page.screenshot({path:path.join(directory,'chrome-right-selection-narrow.png')});
    await entry.getByRole('button').click();const rect=await page.locator('[data-pcs-ui="menu"]').boundingBox(),anchor=await entry.boundingBox();assert.ok(rect.x>200&&rect.x+rect.width<=600);assert.ok(Math.abs(rect.x+rect.width-(anchor.x+anchor.width))<2);
  }finally{await live.close();}
});
