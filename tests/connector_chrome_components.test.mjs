import test from 'node:test';
import assert from 'node:assert/strict';
import {writeFile} from 'node:fs/promises';
import {pathToFileURL} from 'node:url';
import {captureLoadedConversation} from '../extensions/chrome/capture.mjs';

// Reduced DOM structures inspected in the user's Chrome PHP conversation.
// The metric grid is synthetic: the weather web reply failed to load during
// inspection, so it is not evidence of that live page's exact markup.
const text = value => `<p data-d-component="text">${value}</p>`;
const title = value => `<h1 data-d-component="title" data-d-tabular-nums="true">${value}</h1>`;
const row = (label, value) => `<div data-d-component="row" data-d-justify="between">${text(label)}${text(value)}</div>`;
const pill = `<span data-d-component="popover-trigger" data-d-inline data-url="https://example.com/source"><span data-d-component="badge"><div data-d-component="favicon"><img src="https://www.google.com/s2/favicons?domain=https://example.com&amp;sz=32"></div><span data-d-component="text">PHP</span><span data-d-component="text">+1</span><span data-d-component="text" hidden>STALE_SOURCE</span></span></span>`;
const summary = `<div data-d-component="box" data-d-has-border>${text('Chrome 156 安全更新｜10 月 20 日')}${title('353 項')}${text('官方列出的安全修補總數')}<div data-d-component="divider"></div>${row('Critical（極危）','6 項')}${row('受影響平台','Windows、macOS、Linux')}${row('修補版本','156.0.8078.47／.48')}</div>`;
const score = `<div data-d-component="box"><div data-d-component="row" data-d-justify="between">${text('漏洞嚴重程度（CVSS v4.0）')}<div data-d-component="badge" data-color="danger">Critical／極危</div></div><div data-d-component="row" data-d-align="baseline">${title('9.2')}${text('/ 10')}</div><div data-d-component="box" data-d-clip="true"><div style="width:92%"></div></div>${text('CVE-2026-109618、CVE-2026-109619')}</div>`;
const grid = `<div data-d-component="box" style="display:grid;grid-template-columns:1fr 1fr">${[
  ['近中心最大風速','45 m/s','162 km/h，中度颱風'],['七級風暴風半徑','320 km',''],
  ['最大瞬間陣風','55 m/s','198 km/h'],['中心位置','410 km','鵝鑾鼻正東方海面'],
].map(([label,value,detail])=>`<div data-d-component="box">${text(label)}${title(value)}${text(detail)}</div>`).join('')}</div>`;

test('web component relationships survive capture', {skip:!process.env.PCS_PLAYWRIGHT_MODULE}, async t => {
  const {chromium} = await import(pathToFileURL(process.env.PCS_PLAYWRIGHT_MODULE).href);
  const browser = await chromium.launch({headless:true,...(process.env.PCS_CHROME_EXECUTABLE ? {executablePath:process.env.PCS_CHROME_EXECUTABLE}:{})});
  try {
    const page = await browser.newPage();
    let body = '';
    await page.route('**/*', route => route.request().url().startsWith('https://chatgpt.com/') ? route.fulfill({contentType:'text/html;charset=utf-8',body:`<!doctype html><html><head><title>Component comparison</title></head><body><main><article data-message-author-role="assistant" data-message-id="component-fixture">${body}</article></main></body></html>`}) : route.abort());
    async function capture(content) { body=content;await page.goto('https://chatgpt.com/c/component-fixture');return (await page.evaluate(captureLoadedConversation)).capture; }
    async function html(content) {return page.evaluate(source=>{const doc=new DOMParser().parseFromString(source,'text/html');return {rows:[...doc.querySelectorAll('tr')].map(row=>[...row.children].map(cell=>cell.textContent)),meter:doc.querySelector('meter')?.outerHTML,classes:[...doc.querySelectorAll('table')].map(el=>el.className)};},content);}
    const combined=await capture(summary+score+grid+`<div>普通文字 <span data-d-default-strong>粗體重點</span> ${pill}</div>`);
    const message=combined.messages[0];
    await t.test('summary label/value pairs and literal version remain paired',async()=>{
      const captured=await capture(summary);const view=await html(captured.messages[0].content);
      assert.deepEqual(view.classes,['pcs-summary-card']);
      assert.deepEqual(view.rows.slice(-3),[['Critical（極危）','6 項'],['受影響平台','Windows、macOS、Linux'],['修補版本','156.0.8078.47／.48']]);
      assert.doesNotMatch(captured.messages[0].content,/# 353/);
    });
    await t.test('score denominator stays adjacent and a matching visible bar keeps its ratio',async()=>{
      const captured=await capture(score);const view=await html(captured.messages[0].content);
      assert.equal(view.rows[1][0],'9.2 / 10');
      assert.match(view.meter,/value="9.2" max="10"/);
      assert.match(view.meter,/pcs-meter-danger/);
    });
    await t.test('hidden stale values never replace the displayed score',async()=>{
      const source=score.replace(title('9.2'),title('2').replace('<h1 ','<h1 hidden ')+title('9.2')).replace('width:92%','width:20%');
      const captured=await capture(source);
      assert.doesNotMatch(captured.messages[0].content,/<meter/);
      assert.match(captured.messages[0].content,/9\.2/);
    });
    await t.test('four metrics retain cell grouping, order and units',async()=>{
      const captured=await capture(grid);const view=await html(captured.messages[0].content);
      assert.deepEqual(view.classes,['pcs-metric-grid']);
      assert.deepEqual(view.rows.map(row=>row.length),[2,2]);
      assert.match(view.rows[0][0],/近中心最大風速45 m\/s162 km\/h/);
      assert.match(view.rows[0][1],/七級風暴風半徑320 km/);
      assert.match(view.rows[1][0],/最大瞬間陣風55 m\/s198 km\/h/);
      assert.match(view.rows[1][1],/中心位置410 km鵝鑾鼻/);
    });
    await t.test('bold semantics and source links survive without favicon attachments',async()=>{
      assert.match(message.content,/\*\*粗體重點\*\*/);
      assert.match(message.content,/\[PHP \+1\]\(https:\/\/example.com\/source\)/);
      assert.match(message.content,/\[\^pcs-source-[0-9a-f]{20}-1\]: PHP \+1/);
      assert.doesNotMatch(message.content,/STALE_SOURCE/);
      assert.equal(message.resources,undefined);
      const inCard=await capture(summary.replace(text('官方列出的安全修補總數'),text('官方列出的安全修補總數')+pill));
      assert.match(inCard.messages[0].content,/<a href="https:\/\/example.com\/source">PHP \+1<\/a>/);
    });
    await t.test('unknown direct text units survive instead of being discarded by a card adapter',async()=>{
      const captured=await capture(summary.replace(title('353 項'),title('353')+' milliseconds'));
      assert.match(captured.messages[0].content,/milliseconds/);
      const rowText=await capture(summary.replace(title('353 項'),`<div data-d-component="row" data-d-align="baseline">${title('353')} milliseconds</div>`));
      assert.match(rowText.messages[0].content,/milliseconds/);
      const betweenText=await capture(summary.replace(row('Critical（極危）','6 項'),'<div data-d-component="row" data-d-justify="between"><span>延遲</span> ms <strong>20</strong></div>'));
      assert.match(betweenText.messages[0].content,/ms/);
    });
    await t.test('progress values and explicit list numbering survive nested table conversion',async()=>{
      const table='<table><tr><td colspan="2"><progress value="0" max="100" aria-label="進度"></progress><ol start="7"><li>甲</li><li value="12">乙</li><li>丙</li></ol></td></tr></table>';
      const captured=await capture(table);
      assert.match(captured.messages[0].content,/進度：0 \/ 100/);
      assert.match(captured.messages[0].content,/<ol start="7">/);
      assert.match(captured.messages[0].content,/<li value="12">乙<\/li>/);
      const list=await capture('<ol start="0"><li>零</li><li value="7">七</li><li>八</li></ol>');
      assert.match(list.messages[0].content,/0\. 零\n7\. 七\n8\. 八/);
    });
    if(process.env.PCS_COMPONENT_CAPTURE_OUTPUT) await writeFile(process.env.PCS_COMPONENT_CAPTURE_OUTPUT,JSON.stringify(combined,null,2)+'\n','utf8');
  } finally {await browser.close();}
});
