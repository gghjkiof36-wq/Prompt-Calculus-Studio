import test from 'node:test';
import assert from 'node:assert/strict';
import {writeFile} from 'node:fs/promises';
import {pathToFileURL} from 'node:url';
import {captureLoadedConversation} from '../extensions/chrome/capture.mjs';

// Synthetic DOM reduced from inspected badge/card shapes. No live account,
// private state, article navigation or clipboard is used by this suite.
const pill = (id, label = 'Wiz Blog +1', attrs = '') => `<span id="${id}" data-d-component="popover-trigger" data-d-inline ${attrs}><span data-d-component="badge"><span data-d-component="text">${label}</span></span></span>`;
const source = (site, title, date, attrs = '', extra = '') => `<button data-d-component="pressable" aria-label="Open ${site}" ${attrs}><span data-d-component="text" data-size="xs">${site}</span><span data-d-component="text" data-size="sm" data-weight="medium">${title}</span><span data-d-component="text" data-size="sm">${date}</span>${extra}</button>`;
const article = (content, id = 'citation-message') => `<article data-message-author-role="assistant" data-message-id="${id}">${content}</article>`;
const documentHtml = (body, outside = '') => `<!doctype html><html><head><meta charset="utf-8"><title>合成來源擷取 - ChatGPT</title><style>body{font:16px sans-serif}button{display:block}img{display:block}main{max-width:700px}</style></head><body><input id="original-focus"><main>${body}</main>${outside}</body></html>`;
const refs = content => [...content.matchAll(/\[\^(pcs-source-[0-9a-f]{20}-[1-9][0-9]*)\](?!:)/g)].map(match => match[1]);

test('linked citation groups become portable footnotes with bounded DOM reads', {skip:!process.env.PCS_PLAYWRIGHT_MODULE}, async t => {
  const {chromium} = await import(pathToFileURL(process.env.PCS_PLAYWRIGHT_MODULE).href);
  const browser = await chromium.launch({headless:true,...(process.env.PCS_CHROME_EXECUTABLE?{executablePath:process.env.PCS_CHROME_EXECUTABLE}:{})});
  try {
    const page = await browser.newPage();
    const requests=[];
    await page.route('**/*', route => {
      requests.push(route.request().url());
      return route.request().url().startsWith('https://chatgpt.com/c/') ? route.fulfill({contentType:'text/html;charset=utf-8',body:documentHtml('')}) : route.abort();
    });
    await page.goto('https://chatgpt.com/c/synthetic-citations');
    const load = async (body, outside = '') => {
      await page.goto('https://chatgpt.com/c/synthetic-citations');
      await page.setContent(documentHtml(body,outside));
    };
    const read = () => page.evaluate(captureLoadedConversation);
    let portable;

    await t.test('direct citations retain exact article URLs and ordinary links stay ordinary',async()=>{
      const body = '<p>第一段 <a class="citation" href="https://example.com/article?a=002.50">Wiz Blog</a>。普通 <a href="https://example.com/manual">說明</a>。</p>';
      await load(article(body,'message-a')+article(body,'message-b'));
      const result=await read(),[first,second]=result.capture.messages;
      assert.equal(refs(first.content).length,1);assert.equal(refs(second.content).length,1);
      assert.notEqual(refs(first.content)[0],refs(second.content)[0]);
      assert.match(first.content,/\[Wiz Blog\]\(https:\/\/example.com\/article\?a=002\.50\)/);
      assert.match(first.content,/普通 \[說明\]\(https:\/\/example.com\/manual\)/);
      assert.equal((first.content.match(/\]: Wiz Blog/g)||[]).length,1);
      assert.equal((await read()).fingerprint,result.fingerprint);
    });

    await t.test('associated multi-source card keeps titles, dates and readable thumbnails without guessing button URLs',async()=>{
      const image=await page.evaluate(()=>{const c=document.createElement('canvas');c.width=80;c.height=40;const ctx=c.getContext('2d');ctx.fillStyle='#cc2200';ctx.fillRect(0,0,40,40);ctx.fillStyle='#0088dd';ctx.fillRect(40,0,40,40);return c.toDataURL();});
      await load(article('<p>證據 '+pill('badge','Wiz Blog +1','aria-controls="sources" aria-expanded="true"')+'。</p>'),
        `<div role="dialog" id="sources">${source('Wiz Blog','第一篇安全研究','10月 21, 2026','',`<div data-d-component="favicon"><img width="16" height="16" src="${image}"></div><img width="80" height="40" src="${image}">`)}${source('Wiz Blog','第二篇修復說明','10月 22, 2026','data-url="https://example.com/fix?version=002.50"')}</div><div role="dialog" id="unrelated">${source('Wrong','DO_NOT_CAPTURE','today','data-url="https://wrong.example/article"')}</div>`);
      await page.locator('#sources img').last().evaluate(image=>image.decode());
      const before=requests.length;portable=(await read()).capture;const content=portable.messages[0].content;
      assert.equal(refs(content).length,1);assert.match(content,/\]: Wiz Blog \+1\n\n    - \*\*Wiz Blog\*\* · 10月 21, 2026/);
      assert.match(content,/      第一篇安全研究\n/);
      assert.match(content,/\[回到原對話查看來源\]\(https:\/\/chatgpt.com\/c\/synthetic-citations\)/);
      assert.match(content,/\[第二篇修復說明\]\(https:\/\/example.com\/fix\?version=002\.50\)/);
      assert.doesNotMatch(content,/DO_NOT_CAPTURE|wrong\.example|google\.com/);
      assert.equal((content.match(/data:image\/png;base64,/g)||[]).length,1);
      const png=content.match(/!\[Wiz Blog\]\((data:image\/png;base64,[A-Za-z0-9+/=]+)\)/)[1];
      const pixels=await page.evaluate(async png=>{const im=new Image();im.src=png;await im.decode();const c=document.createElement('canvas');c.width=im.width;c.height=im.height;const ctx=c.getContext('2d');ctx.drawImage(im,0,0);return {width:im.width,height:im.height,left:[...ctx.getImageData(10,10,1,1).data],right:[...ctx.getImageData(70,10,1,1).data]};},png);
      assert.deepEqual(pixels,{width:80,height:40,left:[204,34,0,255],right:[0,136,221,255]});
      assert.equal(requests.length,before);assert.equal(portable.messages[0].resources,undefined);
      assert.deepEqual((await read()).capture.messages,portable.messages);
    });

    await t.test('closed source opens only by hover, reads delayed related DOM and restores without click or focus',async()=>{
      await load(article('<p>來源 '+pill('hover')+'</p>'));
      await page.evaluate(card=>{
        window.events={over:0,enter:0,leave:0,click:0,focus:0};const trigger=document.getElementById('hover');
        document.getElementById('original-focus').focus();let timer;
        trigger.addEventListener('mouseover',()=>{window.events.over++;timer=setTimeout(()=>{const popup=document.createElement('div');popup.id='delayed-sources';popup.role='dialog';popup.innerHTML=card;document.body.append(popup);trigger.setAttribute('aria-controls',popup.id);trigger.setAttribute('data-state','open');},30);});
        trigger.addEventListener('mouseenter',()=>window.events.enter++);
        trigger.addEventListener('mouseleave',()=>{window.events.leave++;clearTimeout(timer);document.getElementById('delayed-sources')?.remove();trigger.setAttribute('data-state','closed');});
        trigger.addEventListener('click',()=>window.events.click++);trigger.addEventListener('focus',()=>window.events.focus++);
      },source('Wiz Blog','Delayed precise title','2026-10-24','data-url="https://example.com/delayed"'));
      const first=await read();const second=await read();
      assert.equal(first.fingerprint,second.fingerprint);assert.match(first.capture.messages[0].content,/Delayed precise title/);
      assert.deepEqual(await page.evaluate(()=>({events:window.events,focus:document.activeElement.id,popup:!!document.getElementById('delayed-sources')})),
        {events:{over:2,enter:2,leave:2,click:0,focus:0},focus:'original-focus',popup:false});
    });

    await t.test('existing open popup stays open and another open group is not disturbed or mixed',async()=>{
      await load(article('<p>'+pill('open','已開來源','aria-controls="existing" data-state="open"')+pill('closed','未開來源','aria-controls="missing"')+'</p>'),
        `<div id="existing" role="dialog">${source('Existing','Keep this card','2026-10-24','data-url="https://example.com/existing"')}</div>`);
      await page.evaluate(()=>{window.hoverEvents=[];for(const node of document.querySelectorAll('[data-d-inline]'))for(const event of ['mouseover','mouseenter','mouseout','mouseleave'])node.addEventListener(event,()=>window.hoverEvents.push(node.id+':'+event));});
      const content=(await read()).capture.messages[0].content;
      assert.equal(refs(content).length,1);assert.match(content,/〔未開來源〕/);assert.match(content,/Keep this card/);
      assert.deepEqual(await page.evaluate(()=>window.hoverEvents),[]);assert.equal(await page.locator('#existing').isVisible(),true);
    });

    await t.test('favicon links and transparent stale popovers cannot masquerade as article URLs',async()=>{
      await load(article(pill('linked','Visible','aria-controls="visible-card stale-card"')),
        `<div id="visible-card" role="dialog">${source('Real site','Real article title','today','','<div data-d-component="favicon"><a href="https://wrong.example/home">logo</a></div>')}</div><div id="stale-card" role="dialog" style="opacity:0">${source('Stale','HIDDEN_OLD_SOURCE','yesterday','data-url="https://wrong.example/stale"')}</div>`);
      const content=(await read()).capture.messages[0].content;
      assert.match(content,/Real article title/);assert.match(content,/回到原對話查看來源/);assert.doesNotMatch(content,/wrong\.example|HIDDEN_OLD_SOURCE/);
    });

    await t.test('related popup nested in the message is not duplicated as body or attachment',async()=>{
      await load(article('<p>正文 '+pill('inside','Inside','aria-describedby="inner-card"')+'</p>'+`<div role="tooltip" id="inner-card">${source('Site','Unique article title','2026-10-24','data-url="https://example.com/inside"')}</div>`));
      const result=await read(),content=result.capture.messages[0].content;
      assert.equal((content.match(/Unique article title/g)||[]).length,1);assert.equal(result.capture.messages[0].resources,undefined);
      assert.equal((await read()).fingerprint,result.fingerprint);
    });

    await t.test('no detail stays a label; broken image and Markdown-shaped source text are not invented content',async()=>{
      await load(article('<p>'+pill('empty','無可讀來源')+' '+pill('escape','特殊來源','aria-controls="special"')+'</p>'),
        `<div role="dialog" id="special">${source('Site [x]','[bad](javascript:alert(1)) &lt;img src=x onerror=alert(1)&gt;','2026-10-24','','<img width="80" height="40" alt="Unavailable thumbnail">')}</div>`);
      const content=(await read()).capture.messages[0].content;
      assert.match(content,/〔無可讀來源〕/);assert.equal(refs(content).length,1);
      assert.match(content,/\\\[bad\\\]/);assert.doesNotMatch(content,/data:image|!\[Unavailable|javascript:alert\(1\)\)\]/);
    });

    await t.test('table and structured HTML cards retain safe direct links without fake footnotes',async()=>{
      const citation=pill('table-source','Table source','data-url="https://example.com/table"');
      await load(article(`<table><tr><td colspan="2">${citation}</td></tr></table><div data-d-component="box" data-d-has-border><h1 data-d-component="title" data-d-tabular-nums="true">353 項</h1>${pill('card-source','Card source','data-url="https://example.com/card"')}</div>`));
      const content=(await read()).capture.messages[0].content;
      assert.deepEqual(refs(content),[]);assert.match(content,/<a href="https:\/\/example.com\/table">Table source<\/a>/);assert.match(content,/<a href="https:\/\/example.com\/card">Card source<\/a>/);
    });

    await t.test('source item and group limits fail explicitly',async()=>{
      await load(article(pill('too-many','Too many','aria-controls="many"')),`<div role="dialog" id="many">${Array.from({length:33},(_,i)=>source('Site',`Article ${i}`,'today')).join('')}</div>`);
      await assert.rejects(read(),/這組來源太多/);
      await load(article(Array.from({length:65},(_,i)=>`<a class="citation" href="https://example.com/${i}">Source ${i}</a>`).join(' ')));
      await assert.rejects(read(),/來源群組太多/);
    });

    await t.test('unavailable hover cards have a bounded total wait and clean up each attempted hover',async()=>{
      await load(article(Array.from({length:12},(_,i)=>pill(`missing-${i}`,`來源 ${i}`)).join(' ')));
      await page.evaluate(()=>{window.entered=0;window.left=0;for(const node of document.querySelectorAll('[data-d-inline]')){node.addEventListener('mouseover',()=>window.entered++);node.addEventListener('mouseleave',()=>window.left++);}});
      const started=Date.now(),result=await read(),elapsed=Date.now()-started;
      assert.ok(elapsed<6000,`capture spent ${elapsed}ms`);assert.deepEqual(refs(result.capture.messages[0].content),[]);
      const counts=await page.evaluate(()=>({entered:window.entered,left:window.left}));assert.equal(counts.entered,counts.left);assert.ok(counts.entered<=8);
      assert.equal((result.capture.messages[0].content.match(/〔來源 /g)||[]).length,12);
    });

    for(const change of ['navigation','streaming','body']) await t.test(`${change} during hover invalidates capture and leaves the synthetic hover`,async()=>{
      await load(article('<p id="changing">原文 '+pill('race')+'</p>'));
      await page.evaluate(change=>{window.left=0;const trigger=document.getElementById('race');trigger.addEventListener('mouseleave',()=>window.left++);trigger.addEventListener('mouseover',()=>setTimeout(()=>{
        if(change==='navigation')history.pushState({},'', '/c/other-conversation');
        if(change==='streaming'){const stop=document.createElement('button');stop.dataset.testid='stop-button';document.querySelector('main').append(stop);}
        if(change==='body')document.getElementById('changing').prepend('LATE_NEW_TEXT');
      },20));},change);
      const result=await page.evaluate(captureLoadedConversation,true);
      assert.ok(result.capture_error);assert.match(result.capture_error.message,/對話|回覆/);
      assert.ok(await page.evaluate(()=>window.left)>=1);
    });
    if(process.env.PCS_CITATION_CAPTURE_OUTPUT)await writeFile(process.env.PCS_CITATION_CAPTURE_OUTPUT,JSON.stringify(portable,null,2)+'\n','utf8');
  } finally {await browser.close();}
});
