import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdir, writeFile} from 'node:fs/promises';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import {captureLoadedConversation} from '../extensions/chrome/capture.mjs';
import {markdownDocument} from '../extensions/chrome/core.mjs';

// Synthetic DOM matching observed content rows. No live account, network,
// screenshot OCR, reconstructed numeric values or third-party SVG code.
const icon = (body, attributes = '') => `<svg width="1em" height="1em" viewBox="0 0 24 24" data-d-component="icon" style="font-size:18px" ${attributes}>${body}</svg>`;
const broadcast = icon('<circle cx="12" cy="12" r="3"/><path d="M5 7 A8 8 0 0 0 5 17 M19 7 A8 8 0 0 1 19 17" fill="none" stroke="currentColor" stroke-width="2"/>','fill="currentColor"');
const tower = icon('<path d="M12 3 L4 21 M12 3 L20 21 M7 15 H17 M10 8 H14"/>','fill="none" stroke="currentColor" stroke-width="2"');
const screen = icon('<rect x="3" y="4" width="18" height="13" rx="2"/><path d="M8 21 H16 M12 17 V21"/>','fill="none" stroke="currentColor" stroke-width="2"');
const row = (svg, title, description) => `<div data-d-component="row" data-d-direction="row"><div data-d-component="box">${svg}</div><div data-d-component="box"><p><strong><span data-d-default-strong>${title}</span></strong></p><p>${description}</p></div></div>`;
const html = body => `<!doctype html><html><head><meta charset="utf-8"><title>語意圖示與公式 - ChatGPT</title><style>body{background:#111;color:#ddd;font:18px/1.5 Arial}main{max-width:800px}svg{vertical-align:middle}[data-d-component=row]{display:flex;gap:12px}p{margin:0 0 12px}[data-d-component=divider]{height:1px;background:#333;margin:16px 0}</style></head><body><main><article data-message-author-role="assistant" data-message-id="math-icons">${body}</article></main></body></html>`;
const pngs = value => [...value.matchAll(/!\[[^\n]*?\]\((data:image\/png;base64,[A-Za-z0-9+/=]+)\)/g)].map(match => match[1]);
const escape = value => value.replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;');
const katex = (value, display = false) => `${display ? '<span class="katex-display"><span>' : ''}<span class="katex"><span class="katex-mathml"><math><semantics><mrow><mi>DUPLICATE_MATHML</mi></mrow><annotation encoding="application/x-tex">${escape(value)}</annotation></semantics></math></span><span class="katex-html" aria-hidden="true">DUPLICATE_VISUAL<svg width="100" height="50" role="img"><path d="M0 0L50 50"/></svg></span></span>${display ? '</span></span>' : ''}`;

test('content icons and LLM math retain inline relationships and exact source', {skip:!process.env.PCS_PLAYWRIGHT_MODULE}, async t => {
  const {chromium} = await import(pathToFileURL(process.env.PCS_PLAYWRIGHT_MODULE).href);
  const browser = await chromium.launch({headless:true,...(process.env.PCS_CHROME_EXECUTABLE?{executablePath:process.env.PCS_CHROME_EXECUTABLE}:{})});
  try {
    const page = await browser.newPage();
    await page.route('**/*', route => route.request().url().startsWith('https://chatgpt.com/c/') ?
      route.fulfill({contentType:'text/html;charset=utf-8',body:html('')}) : route.abort());
    await page.goto('https://chatgpt.com/c/math-icons-fixture');
    const read = async body => {await page.setContent(html(body));return page.evaluate(captureLoadedConversation);};
    let iconCapture;

    await t.test('18px content icons stay with their labels; toolbar, link adornments and favicon do not export', async () => {
      iconCapture = await read('<h2>歷史</h2>'+[
        row(broadcast,'1951 年：CONELRAD','保留精確值 002.50。'),
        row(tower,'1963 年：EBS','警報用途。'),
        row(screen,'1997 年：EAS','數位訊號。'),
      ].join('')+`<a href="https://example.com">來源${screen}</a><button>${screen}複製</button><div role="toolbar">${screen}</div><span data-d-component="favicon">${screen}</span>`);
      const content = iconCapture.capture.messages[0].content, images = pngs(content);
      assert.equal(images.length,3);assert.equal(new Set(images).size,3);
      for (const title of ['1951 年：CONELRAD','1963 年：EBS','1997 年：EAS']) {
        const line = content.split('\n').find(line=>line.includes(title));
        assert.match(line,/^!\[圖示\|18x18\]\(data:image\/png;base64,[A-Za-z0-9+/=]+\) \*\*/);
      }
      assert.match(content,/002\.50/);assert.match(content,/\[來源\]\(https:\/\/example.com\//);
      assert.equal(iconCapture.capture.messages[0].resources,undefined);
      const decoded = await page.evaluate(async images => Promise.all(images.map(async source => {
        const image = new Image();image.src = source;await image.decode();const canvas = document.createElement('canvas');
        canvas.width=image.naturalWidth;canvas.height=image.naturalHeight;const ctx=canvas.getContext('2d');ctx.drawImage(image,0,0);
        const pixels=ctx.getImageData(0,0,canvas.width,canvas.height).data;let ink=0;
        for(let i=0;i<pixels.length;i+=4)if(pixels[i]>150&&pixels[i+1]>150&&pixels[i+2]>150&&pixels[i+3]>200)ink++;
        return {width:image.naturalWidth,height:image.naturalHeight,ink};
      })), images);
      for(const image of decoded){assert.deepEqual([image.width,image.height],[18,18]);assert.ok(image.ink>15,JSON.stringify(image));}
      assert.equal((await page.evaluate(captureLoadedConversation)).fingerprint,iconCapture.fingerprint);
      assert.equal(pngs(markdownDocument(iconCapture.capture)).length,3);
    });

    await t.test('explicit content arrows and inline symbols survive without inventing a chart', async () => {
      const arrow = icon('<path d="M4 12 H20 M14 6 L20 12 L14 18"/>','fill="none" stroke="currentColor" stroke-width="2"');
      const result = await read(`<p>方向 ${arrow} 目的地</p><div>${arrow}</div><p>後文</p>`);
      const content=result.capture.messages[0].content;
      assert.equal(pngs(content).length,2);
      assert.match(content,/方向 !\[圖示\|18x18\]\(data:image\/png;base64,[A-Za-z0-9+/=]+\) 目的地/);
      assert.equal(result.capture.messages[0].resources,undefined);
    });

    await t.test('observed content dividers and nested strong labels preserve Markdown structure', async () => {
      const body = '<h2>它的歷史與核戰爭</h2><p>它經歷了幾個階段：</p><div data-d-component="box" data-d-direction="col">'+[
        row(broadcast,'1951 年：CONELRAD','冷戰時期建立的警報機制，著重於核攻擊情境下的廣播通訊。'),
        row(tower,'1963 年：EBS（Emergency Broadcast System）','緊急廣播系統，逐漸將警報用途擴展到其他重大緊急事件。'),
        row(screen,'1997 年：EAS（Emergency Alert System）','引入數位化警報訊號，取代 EBS，成為現代美國緊急廣播體系。'),
      ].join('<div data-d-component="divider"></div>')+'</div>';
      iconCapture = await read(body);
      const content=iconCapture.capture.messages[0].content;
      assert.equal(content.split('\n\n---\n\n').length-1,2);
      assert.match(content,/\*\*1951 年：CONELRAD\*\*/);
      assert.doesNotMatch(content,/\*\*\*\*|<table|<img/);
      assert.equal(pngs(content).length,3);
      if(process.env.PCS_ICON_CAPTURE_OUTPUT){
        const output=path.dirname(process.env.PCS_ICON_CAPTURE_OUTPUT);await mkdir(output,{recursive:true});
        for(const width of [800,372]) {
          await page.setViewportSize({width:width+16,height:1000});
          await page.locator('article').screenshot({path:path.join(output,`r9-icon-source-${width}.png`)});
        }
        await writeFile(path.join(output,'r9-icon-source-fixture.html'),html(body),'utf8');
      }
      const unusual=await read('<p>前</p><div data-d-component="divider">值 1.2300</div><div data-d-component="divider" hidden></div><p>後</p>');
      assert.match(unusual.capture.messages[0].content,/值 1\.2300/);
      assert.doesNotMatch(unusual.capture.messages[0].content,/---/);
    });

    await t.test('unsupported icon retains an explicit image reference rather than disappearing', async () => {
      const external=icon('<use href="https://example.com/icons.svg#x"/>');
      const result=await read(row(external,'外部圖示','仍保留文字。'));
      assert.equal(pngs(result.capture.messages[0].content).length,0);
      assert.match(result.capture.messages[0].content,/外部圖示/);
      assert.deepEqual(result.capture.messages[0].resources,[{kind:'image',label:'圖示',url:null}]);
    });

    await t.test('unrecognized row text is not discarded by the icon layout adapter', async () => {
      const result=await read(row(broadcast,'數值','正文').replace('<div data-d-component="row" data-d-direction="row">','<div data-d-component="row" data-d-direction="row">直接文字 1.2300 '));
      assert.match(result.capture.messages[0].content,/直接文字 1\.2300/);
      assert.match(result.capture.messages[0].content,/數值/);assert.match(result.capture.messages[0].content,/正文/);
      assert.equal(pngs(result.capture.messages[0].content).length,1);
    });

    await t.test('KaTeX inline and nested display wrappers export exact editable physics TeX exactly once', async () => {
      const inline = String.raw`\frac{\partial \psi}{\partial t} + \nabla\cdot\mathbf{J} = 0`;
      const display = String.raw`R(\theta)=\begin{pmatrix}\cos\theta&-\sin\theta\\\sin\theta&\cos\theta\end{pmatrix}`;
      const result=await read(`<p>連續方程 ${katex(inline)}。</p>${katex(display,true)}`);
      const content=result.capture.messages[0].content;
      assert.ok(content.includes(`$${inline}$`));assert.ok(content.includes(`$$\n${display}\n$$`));
      assert.equal(content.split(inline).length-1,1);assert.equal(content.split(display).length-1,1);
      assert.doesNotMatch(content,/DUPLICATE|data:image|僅來源/);assert.equal(result.capture.messages[0].resources,undefined);
    });

    await t.test('native MathML and MathJax annotation sources export summation, integrals and SI quantities', async () => {
      const tex = String.raw`\sum_{n=0}^{\infty}\frac{x^n}{n!}=\int_0^1\sqrt{1-x^2}\,\mathrm{d}x`;
      const precision = '1.2300\u00a0×\u00a010⁻⁶ Ω; 9007199254740993; 002.50 kg·m·s⁻²';
      const result=await read(`<math display="block"><semantics><mi>DUPLICATE</mi><annotation encoding="application/x-tex">${escape(tex)}</annotation></semantics></math><mjx-container display="true"><svg width="100" height="50"></svg><math><semantics><mi>DUPLICATE</mi><annotation encoding="application/x-latex">${escape('E=mc^2')}</annotation></semantics></math></mjx-container><p>${precision}</p>`);
      const content=result.capture.messages[0].content;
      assert.ok(content.includes(`$$\n${tex}\n$$`));assert.ok(content.includes('$$\nE=mc^2\n$$'));
      assert.ok(content.includes(precision));assert.doesNotMatch(content,/DUPLICATE/);
      assert.equal(result.capture.messages[0].resources,undefined);
    });

    await t.test('literal code and nonbreaking spacing are retained without formula interpretation', async () => {
      const literal = '<span class="katex">\\(x_1\\)</span>\n\nA\u00a0B = 002.50';
      const result=await read(`<pre><code>${escape(literal)}</code></pre><p>${katex('x\u00a0+\u00a0y')}</p>`);
      const content=result.capture.messages[0].content;
      assert.ok(content.includes('```\n'+literal+'\n```'));
      assert.ok(content.includes('$x\u00a0+\u00a0y$'));
    });

    if(process.env.PCS_ICON_CAPTURE_OUTPUT){
      await mkdir(path.dirname(process.env.PCS_ICON_CAPTURE_OUTPUT),{recursive:true});
      await writeFile(process.env.PCS_ICON_CAPTURE_OUTPUT,JSON.stringify(iconCapture.capture,null,2)+'\n','utf8');
    }
  } finally { await browser.close(); }
});
