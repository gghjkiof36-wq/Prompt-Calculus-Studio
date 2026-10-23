import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdir, writeFile} from 'node:fs/promises';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import {captureLoadedConversation} from '../extensions/chrome/capture.mjs';
import {extensionFixture} from './connector_chrome_helpers.mjs';
import {selectCapture, markdownDocument, contentSummary} from '../extensions/chrome/core.mjs';

const enabled = !!process.env.PCS_PLAYWRIGHT_MODULE;
const richBody = `<header class="chat-header"><strong>ChatGPT</strong><div><button data-testid="share-chat-button" aria-label="分享">分享</button></div></header><main class="chat-main">
<h1>台北天氣與事件回顧</h1>
<article class="user-message" data-message-author-role="user" data-message-id="rich-user"><div class="whitespace-pre-wrap">幫我整理台北未來三天的天氣，再把這份安全性事件的重點與時間順序列出來。保留資料來源。</div></article>
<article data-message-author-role="assistant" data-message-id="rich-assistant">
<div class="markdown"><p>台北這幾天以多雲為主，週三降雨機率較高。以下把天氣與事件回顧分開整理。</p></div>
<section class="weather-card"><h2>台北市</h2><div class="weather-now"><strong>25°</strong><span>多雲<br>體感 26° · 濕度 72%</span></div>
<table><thead><tr><th>日期</th><th>天氣</th><th>溫度</th><th>降雨機率</th></tr></thead><tbody><tr><td>週一</td><td>多雲</td><td>23–27°C</td><td>20%</td></tr><tr><td>週二</td><td>晴時多雲</td><td>24–28°C</td><td>15%</td></tr><tr><td>週三</td><td>短暫陣雨</td><td>22–26°C</td><td>60%</td></tr></tbody></table>
<button class="citation" data-testid="citation-pill" data-url="https://example.com/weather">氣象資料</button></section>
<section class="event"><h2>安全性事件回顧</h2><p>評分和事件時間線如下。較高分數代表較嚴重的風險。</p>
<div class="score-line"><strong>CVSS 基礎分數</strong><meter min="0" max="10" value="9.8" aria-label="基礎分數"></meter><span>9.8 / 10</span></div>
<figure><figcaption>各版本風險比較</figcaption><canvas width="680" height="190" aria-label="各版本 CVSS 評分長條圖" aria-hidden="true"></canvas></figure>
<a class="citation" href="https://example.com/security-report">安全性報告</a>
<ol class="timeline"><li><strong>10 月 12 日</strong>：首次發現異常連線。</li><li><strong>10 月 15 日</strong>：完成影響範圍確認。</li><li><strong>10 月 18 日</strong>：發布修正版並提供更新指引。</li></ol>
<iframe title="事件互動時間軸" src="https://example.com/timeline" loading="lazy"></iframe>
</section>
<div data-testid="tool-activity"><span>TOOL_INTERNAL_ACTIVITY_SHOULD_NOT_EXPORT</span><img alt="TOOL_IMAGE_NOT_EXPORT" src="https://example.com/internal.png"></div>
<button>複製</button><button>重新產生</button>
</article>
<article class="user-message" data-message-author-role="user" data-message-id="rich-image"><div class="whitespace-pre-wrap"></div><img alt="事件附件截圖" src="https://example.com/image.png?X-Amz-Signature=SECRET_FIXTURE"><a download="事件紀錄.txt" href="https://example.com/record.txt">事件紀錄.txt</a></article>
</main>`;

const richStyles = `*{box-sizing:border-box}body{margin:0;background:#212121;color:#ececec;font:15px/1.65 'Segoe UI','Microsoft JhengHei',sans-serif}.chat-header{height:62px;display:flex;align-items:center;justify-content:space-between;padding:0 30px;border-bottom:1px solid #383838}.chat-header strong{font-size:19px}.turn-actions{display:flex;align-items:center;gap:2px;margin:8px 0;color:#aaa}.turn-actions button,.chat-header button{font:inherit;font-size:14px;border:0;background:transparent;color:#aaa;cursor:pointer;padding:5px 7px}.chat-header span{font-size:12px;color:#aaa}.chat-main{max-width:760px;margin:auto;padding:28px 30px 80px}.chat-main h1{font-size:23px;margin:0 0 22px}.chat-main h2{font-size:18px;margin:0 0 12px}.chat-main p{margin:0 0 18px}.user-message{background:#303030;border-radius:16px;padding:15px 18px;margin:0 0 24px 70px}.weather-card{background:#2b2b2b;border:1px solid #434343;border-radius:16px;padding:22px;margin:22px 0 28px}.weather-now{display:flex;gap:22px;align-items:center;margin-bottom:18px}.weather-now strong{font-size:46px;font-weight:500;line-height:1}.weather-now span{color:#c6c6c6;font-size:13px}table{width:100%;border-collapse:collapse;font-size:13px;text-align:left}th,td{padding:8px;border-bottom:1px solid #474747}th{font-weight:500;color:#bdbdbd}.citation{font:12px/1.5 inherit;display:inline-block;color:#c9d7f1;background:#3b4350;padding:3px 9px;border:0;border-radius:5px;text-decoration:none;margin-top:12px}.score-line{display:flex;align-items:center;gap:14px;font-size:13px}.score-line meter{flex:1;height:14px;accent-color:#e79d72}.score-line span{color:#efb18a}figure{margin:22px 0 0}figcaption{font-size:14px;margin-bottom:10px}canvas{width:100%;height:170px;background:#272727;border-radius:8px}.timeline{font-size:13px;padding-left:24px;margin:18px 0}.timeline li{padding:5px 0}iframe{display:none}.user-message img{width:140px;height:70px;object-fit:contain}[data-testid='tool-activity']{display:none}article>button{display:none}`;

const operations='<div class="turn-actions" role="group" aria-label="訊息操作"><button data-testid="copy-turn-action-button" aria-label="複製">⧉</button><button aria-label="分享回覆">↗</button></div>';
function fullPage(body, extra = '') { body=body.replaceAll('</article>',operations+'</article>'); return `<!doctype html><html><head><meta charset="utf-8"><title>台北天氣與事件回顧 - ChatGPT</title><style>${richStyles}${extra}</style></head><body>${body}</body></html>`; }

test('rich DOM preservation and actual in-page extension', {skip: !enabled}, async t => {
  const playwright = await import(pathToFileURL(process.env.PCS_PLAYWRIGHT_MODULE).href);
  const browser = await playwright.chromium.launch({headless: true,
    ...(process.env.PCS_CHROME_EXECUTABLE ? {executablePath: process.env.PCS_CHROME_EXECUTABLE} : {})});
  let richCapture;
  try {
    const page = await browser.newPage();
    await page.route('**/*', route => route.request().url().startsWith('https://chatgpt.com/') ?
      route.fulfill({contentType: 'text/html;charset=utf-8', body: fullPage(richBody)}) : route.abort());
    await page.goto('https://chatgpt.com/c/rich-fixture');
    await t.test('cards outside markdown, table, score and timeline survive; controls and tool activity do not', async () => {
      richCapture = await page.evaluate(captureLoadedConversation);
      const message = richCapture.capture.messages.find(item => item.id === 'rich-assistant');
      assert.match(message.content, /台北市/);
      assert.match(message.content, /\| 週三 \| 短暫陣雨 \| 22–26°C \| 60% \|/);
      assert.match(message.content, /基礎分數：9.8 \/ 10/);
      assert.match(message.content, /1\. \*\*10 月 12 日\*\*/);
      assert.match(message.content, /3\. \*\*10 月 18 日\*\*/);
      assert.match(message.content, /\[氣象資料\]\(https:\/\/example.com\/weather\)/);
      assert.match(message.content, /\[安全性報告\]\(https:\/\/example.com\/security-report\)/);
      assert.ok(!JSON.stringify(message).includes('TOOL_'));
      assert.ok(!message.content.includes('重新產生'));
      assert.ok(!message.content.includes('複製'));
      assert.deepEqual(message.resources, [{kind: 'chart', label: '各版本 CVSS 評分長條圖', url: null}]);
      assert.match(contentSummary(message), /表格 1/);
      assert.match(contentSummary(message), /圖表 1/);
    });
    await t.test('image/file resources remain explicit and signed URLs never leave capture', async () => {
      const message = richCapture.capture.messages.find(item => item.id === 'rich-image');
      assert.equal(message.content, '');
      assert.deepEqual(message.resources, [
        {kind: 'image', label: '事件附件截圖', url: null},
        {kind: 'attachment', label: '事件紀錄.txt', url: 'https://example.com/record.txt'},
      ]);
      assert.ok(!JSON.stringify(richCapture).includes('SECRET_FIXTURE'));
      const markdown = markdownDocument(selectCapture(richCapture.capture, ['rich-image']));
      assert.match(markdown, /- 圖片：\[事件附件截圖\]\(https:\/\/chatgpt.com\/c\/rich-fixture\)/);
      assert.match(markdown, /- 附件：\[事件紀錄.txt\]\(https:\/\/example.com\/record.txt\)/);
      assert.equal(markdown.split('事件附件截圖').length - 1, 1);
      const selected = selectCapture(richCapture.capture, ['rich-user']);
      assert.ok(!JSON.stringify(selected).includes('事件附件截圖'));
      assert.ok(!markdownDocument(selected).includes('example.com'));
    });
    await t.test('visible iframe retained as a reference, resource edits invalidate the fingerprint', async () => {
      await page.evaluate(() => { const frame = document.querySelector('iframe'); frame.style.display = 'block'; });
      const next = await page.evaluate(captureLoadedConversation);
      assert.notEqual(next.fingerprint, richCapture.fingerprint);
      const resources = next.capture.messages.find(item => item.id === 'rich-assistant').resources;
      assert.deepEqual(resources.at(-1), {kind: 'embed', label: '事件互動時間軸', url: 'https://example.com/timeline'});
    });
    await t.test('resource bounds fail explicitly instead of silently dropping media', async () => {
      await page.evaluate(() => {
        const container = document.querySelector('[data-message-id="rich-image"]');
        for (let i = 0; i < 33; i++) { const img = document.createElement('img'); img.alt = '圖片 ' + i; container.append(img); }
      });
      await assert.rejects(page.evaluate(captureLoadedConversation), /圖表或附件太多/);
    });
    await page.close();

    const live=await extensionFixture(fullPage(richBody),{width:1480,height:1120});
    try {
      await live.page.getByRole('button',{name:'PCS：此則訊息',exact:true}).first().click();
      await live.page.getByRole('button',{name:'送到 PCS',exact:true}).waitFor();
      await live.page.evaluate(() => {
        const canvas=document.querySelector('canvas'),draw=canvas.getContext('2d');
        [9.8,7.5,4.3].forEach((value,i)=>{draw.fillStyle=['#efb18a','#d3ba88','#b4c9a7'][i];draw.fillRect(90,20+i*55,value*47,28);draw.fillStyle='#eee';draw.font='14px Segoe UI';draw.fillText(String(value),102+value*47,40+i*55);});
      });
      await t.test('actual extension appends PCS to original action row and opens an anchored menu',async()=>{
        assert.equal(await live.page.locator('[data-pcs-ui="selection"]').count(),0);
        assert.equal(await live.page.locator('[data-pcs-ui="message-action"]').count(),3);
        assert.equal(await live.page.locator('[data-message-author-role="assistant"]').count(),1);
        assert.equal(await live.page.locator('iframe#pcs-panel').count(),0);
        assert.equal(await live.page.getByRole('button',{name:'送到 PCS',exact:true}).isEnabled(),true);
        if(process.env.PCS_SCREENSHOT_DIR){
          await mkdir(process.env.PCS_SCREENSHOT_DIR,{recursive:true});
          await live.page.screenshot({path:path.join(process.env.PCS_SCREENSHOT_DIR,'chrome-integrated-wide.png')});
          await live.page.setViewportSize({width:420,height:950});
          await live.page.screenshot({path:path.join(process.env.PCS_SCREENSHOT_DIR,'chrome-integrated-narrow.png')});
          await live.page.setViewportSize({width:1480,height:1120});await live.page.keyboard.press('Escape');
          await live.page.locator('[data-pcs-ui="entry"]').getByRole('button').click();
          await live.page.getByRole('button',{name:'選取訊息',exact:true}).click();
          await live.page.getByRole('checkbox',{name:'選取第 1 則訊息',exact:true}).check();
          await live.page.locator('[data-pcs-ui="entry"]').getByRole('button').click();
          await live.page.screenshot({path:path.join(process.env.PCS_SCREENSHOT_DIR,'chrome-integrated-selection.png')});
        }
      });
    }finally{await live.close();}
    if (process.env.PCS_CAPTURE_OUTPUT) {
      await mkdir(path.dirname(process.env.PCS_CAPTURE_OUTPUT), {recursive: true});
      await writeFile(process.env.PCS_CAPTURE_OUTPUT, JSON.stringify(richCapture.capture, null, 2) + '\n', 'utf8');
    }
  } finally {
    await browser.close();
  }
});
