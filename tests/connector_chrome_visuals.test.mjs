import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdir, writeFile} from 'node:fs/promises';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import {captureLoadedConversation} from '../extensions/chrome/capture.mjs';
import {markdownDocument} from '../extensions/chrome/core.mjs';

// Entirely synthetic graphics. These exercise browser rendering and capture,
// not a live ChatGPT chart, its hidden dataset, or a PCS reader implementation.
const svg = (label, body, attributes = '') => `<svg xmlns="http://www.w3.org/2000/svg" role="img" aria-label="${label}" width="160" height="100" viewBox="0 0 160 100" ${attributes}>${body}</svg>`;
const curve = svg('曲線：輸入 002.50，輸出 1.2300', '<rect width="160" height="100" fill="white"/><path d="M10 80 C40 10 120 10 150 80" fill="none" stroke="#0055dd" stroke-width="6"/>');
const pie = svg('圓形與扇形', '<desc id="pie-values">1 月 5045；7 月 10477；8 月 10740</desc><rect width="160" height="100" fill="white"/><circle cx="80" cy="50" r="36" fill="#dd2200"/><path d="M80 50 L80 14 A36 36 0 0 1 116 50 Z" fill="#0055dd"/>', 'aria-describedby="pie-values"');
const bars = svg('柱狀與折線：A 5045，B 10740', '<rect width="160" height="100" fill="white"/><rect x="12" y="52" width="28" height="38" fill="#dd2200"/><rect x="60" y="24" width="28" height="66" fill="#dd2200"/><polyline points="10,40 72,12 145,50" fill="none" stroke="#0055dd" stroke-width="5"/>');
const table = '<table><caption>月份統計</caption><thead><tr><th>Month</th><th>Count</th></tr></thead><tbody><tr><td>1 月</td><td>5045</td></tr><tr><td>7 月</td><td>10477</td></tr><tr><td>8 月</td><td>10740</td></tr></tbody></table>';
const html = body => `<!doctype html><html><head><meta charset="utf-8"><title>合成圖形測試 - ChatGPT</title><style>body{background:white;color:black}svg,canvas,img{display:block}main{width:700px}</style></head><body><main><article data-message-author-role="assistant" data-message-id="synthetic-visuals">${body}</article></main></body></html>`;
const pngs = content => [...content.matchAll(/!\[[^\n]*?\]\((data:image\/png;base64,[A-Za-z0-9+/=]+)\)/g)].map(match => match[1]);

async function pixels(page, png) {
  return page.evaluate(async source => {
    const image = new Image();image.src = source;await image.decode();
    const canvas = document.createElement('canvas');canvas.width = image.naturalWidth;canvas.height = image.naturalHeight;
    const ctx = canvas.getContext('2d');ctx.drawImage(image,0,0);
    const {data} = ctx.getImageData(0,0,canvas.width,canvas.height);
    let opaque=0,red=0,blue=0,green=0,ink=0;const bounds={red:[Infinity,Infinity,-1,-1],blue:[Infinity,Infinity,-1,-1]};
    const point=(kind,index)=>{const x=(index/4)%canvas.width,y=Math.floor(index/4/canvas.width),box=bounds[kind];box[0]=Math.min(box[0],x);box[1]=Math.min(box[1],y);box[2]=Math.max(box[2],x);box[3]=Math.max(box[3],y);};
    for(let i=0;i<data.length;i+=4){
      if(data[i+3]>200)opaque++;
      if(data[i+3]>200&&data[i]>150&&data[i+1]<100&&data[i+2]<100){red++;point('red',i);}
      if(data[i+3]>200&&data[i]<100&&data[i+1]<140&&data[i+2]>150){blue++;point('blue',i);}
      if(data[i+3]>200&&data[i]<100&&data[i+1]>120&&data[i+2]<100)green++;
      if(data[i+3]>200&&Math.min(data[i],data[i+1],data[i+2])<200)ink++;
    }
    const at=(x,y)=>[...ctx.getImageData(x,y,1,1).data];
    return {width:image.naturalWidth,height:image.naturalHeight,opaque,red,blue,green,ink,bounds,left:at(8,8),right:at(canvas.width-8,8)};
  },png);
}

test('synthetic graphics become self-contained PNGs without losing text or editable table data', {skip:!process.env.PCS_PLAYWRIGHT_MODULE}, async t => {
  const {chromium}=await import(pathToFileURL(process.env.PCS_PLAYWRIGHT_MODULE).href);
  const browser=await chromium.launch({headless:true,...(process.env.PCS_CHROME_EXECUTABLE?{executablePath:process.env.PCS_CHROME_EXECUTABLE}:{})});
  try {
    const page=await browser.newPage({viewport:{width:1100,height:900}});
    let imageBytes;const requests=[];
    await page.route('**/*',route=>{
      const url=route.request().url();if(/^https?:/.test(url))requests.push(url);
      if(url==='https://chatgpt.com/assets/synthetic-local.png'&&imageBytes)return route.fulfill({contentType:'image/png',body:imageBytes});
      if(url.startsWith('https://chatgpt.com/c/'))return route.fulfill({contentType:'text/html;charset=utf-8',body:html('')});
      return route.abort();
    });
    await page.goto('https://chatgpt.com/c/synthetic-visuals');
    imageBytes=Buffer.from(await page.evaluate(()=>{
      const canvas=document.createElement('canvas');canvas.width=80;canvas.height=40;const ctx=canvas.getContext('2d');
      ctx.fillStyle='#dd2200';ctx.fillRect(0,0,40,40);ctx.fillStyle='#00aa00';ctx.fillRect(40,0,40,40);return canvas.toDataURL('image/png').split(',')[1];
    }),'base64');
    const read=async body=>{await page.setContent(html(body));return page.evaluate(captureLoadedConversation);};
    let combined;

    await t.test('curve, circle/sector and line/bar SVGs decode to distinct nonempty pixels at displayed dimensions',async()=>{
      combined=await read(`<p>圖形前</p>${curve}<p>曲線之後</p>${pie}<p>扇形之後</p>${bars}<p>圖形後</p>`);
      const content=combined.capture.messages[0].content,images=pngs(content);
      assert.equal(images.length,3);
      const decoded=await Promise.all(images.map(image=>pixels(page,image)));
      for(const image of decoded){assert.equal(image.width,160);assert.equal(image.height,100);assert.equal(image.opaque,16000);assert.ok(image.ink>500);}
      assert.ok(decoded[0].blue>600);assert.equal(decoded[0].red,0);
      assert.ok(decoded[1].blue>850);assert.ok(decoded[1].red>2500);
      assert.ok(decoded[2].red>2500);assert.ok(decoded[2].blue>500);
      const order=['圖形前',images[0],'曲線之後',images[1],'扇形之後',images[2],'圖形後'].map(value=>content.indexOf(value));
      assert.ok(order.every((position,index)=>position>=0&&(index===0||position>order[index-1])));
      assert.match(content,/002\.50.*1\.2300/);assert.match(content,/1 月 5045；7 月 10477；8 月 10740/);
      assert.equal(combined.capture.messages[0].resources,undefined);
      const repeated=await page.evaluate(captureLoadedConversation);
      assert.equal(repeated.fingerprint,combined.fingerprint);
      assert.deepEqual(repeated.capture.messages,combined.capture.messages);
    });

    await t.test('canvas and already loaded same-origin image preserve actual pixels in their original sequence',async()=>{
      await page.setContent(html(`${curve}${pie}${bars}<p>畫布之前</p><canvas id="chart" width="100" height="50" aria-label="畫布 A 20，B 40"></canvas><p>圖片之前</p><img src="/assets/synthetic-local.png" width="80" height="40" alt="本機合成雙色圖片"><p>圖片之後</p>${table}`));
      await page.evaluate(async()=>{
        await document.querySelector('img').decode();const ctx=document.querySelector('canvas').getContext('2d');
        ctx.fillStyle='#0055dd';ctx.fillRect(0,0,50,50);ctx.fillStyle='#00aa00';ctx.fillRect(50,0,50,50);
      });
      const before=requests.length;combined=await page.evaluate(captureLoadedConversation);
      const content=combined.capture.messages[0].content,images=pngs(content);assert.equal(images.length,5);
      const [canvas,image]=await Promise.all(images.slice(-2).map(value=>pixels(page,value)));
      assert.deepEqual([canvas.width,canvas.height,canvas.blue,canvas.green],[100,50,2500,2500]);
      assert.deepEqual([image.width,image.height,image.red,image.green],[80,40,1600,1600]);
      assert.deepEqual(image.left,[221,34,0,255]);assert.deepEqual(image.right,[0,170,0,255]);
      assert.ok(content.indexOf('畫布之前')<content.indexOf(images[3]));
      assert.ok(content.indexOf(images[3])<content.indexOf('圖片之前'));
      assert.ok(content.indexOf('圖片之前')<content.indexOf(images[4]));
      assert.ok(content.indexOf(images[4])<content.indexOf('圖片之後'));
      assert.equal(requests.length,before,'capture must not reload the already displayed image');
      assert.equal(combined.capture.messages[0].resources,undefined);
      assert.equal((await page.evaluate(captureLoadedConversation)).fingerprint,combined.fingerprint);
    });

    await t.test('the table form remains editable text with exact month-to-count associations',async()=>{
      const content=combined.capture.messages[0].content;
      const rows=content.split('\n').filter(line=>line.startsWith('|')).map(line=>line.split('|').slice(1,-1).map(cell=>cell.trim()));
      assert.deepEqual(rows,[['Month','Count'],['---','---'],['1 月','5045'],['7 月','10477'],['8 月','10740']]);
      const exported=markdownDocument(combined.capture);
      assert.match(exported,/\| 7 月 \| 10477 \|/);
      assert.equal(pngs(exported).length,5,'the table itself must not be rasterized');
      assert.ok(content.indexOf('圖片之後')<content.indexOf('月份統計'));
    });

    await t.test('plain foreignObject axis labels retain their rendered position, size, color and reading order',async()=>{
      const axis=svg('軸標：5045，10740','<rect width="160" height="100" fill="white"/><foreignObject x="12" y="40" width="65" height="30"><div xmlns="http://www.w3.org/1999/xhtml" style="color:#dd2200;font:16px/20px Arial">5045</div></foreignObject><foreignObject x="80" y="40" width="80" height="30"><div xmlns="http://www.w3.org/1999/xhtml" style="color:#0055dd;font:16px/20px Arial">10740</div></foreignObject>');
      for(const scale of [1,2]){
        await page.setContent(html(`<p>標籤之前</p>${axis.replace('width="160" height="100" viewBox',`width="${160*scale}" height="${100*scale}" viewBox`)}<p>標籤之後</p>`));
        const original=await pixels(page,'data:image/png;base64,'+(await page.locator('svg').screenshot()).toString('base64'));
        const captured=await page.evaluate(captureLoadedConversation),content=captured.capture.messages[0].content,images=pngs(content);
        assert.equal(images.length,1);const saved=await pixels(page,images[0]);
        assert.deepEqual([saved.width,saved.height],[160*scale,100*scale]);
        for(const color of ['red','blue']){
          assert.ok(saved[color]>50*scale,`${color} axis label at scale ${scale}: saved ${JSON.stringify(saved)}, original ${JSON.stringify(original)}`);
          for(let i=0;i<4;i++)assert.ok(Math.abs(saved.bounds[color][i]-original.bounds[color][i])<=2,`${color} axis boundary ${i} at scale ${scale}: captured ${saved.bounds[color]}, original ${original.bounds[color]}`);
        }
        assert.ok(saved.bounds.red[2]<saved.bounds.blue[0],'left and right axis values must remain ordered');
        assert.ok(content.indexOf('標籤之前')<content.indexOf(images[0]));assert.ok(content.indexOf(images[0])<content.indexOf('標籤之後'));
        assert.match(content,/軸標：5045，10740/);
      }
    });

    await t.test('unsupported and external SVG resources fall back without requests during capture',async()=>{
      const unsafe=[
        svg('外部 use 圖表','<use href="https://outside.test/shapes.svg#shape"/>'),
        svg('外部 paint 圖表','<defs><linearGradient id="paint"><stop offset="0" stop-color="red"/></linearGradient></defs><rect width="100" height="80" fill="url(https://outside.test/colors.svg#paint)"/>'),
        svg('外部圖片圖表','<image href="https://outside.test/chart.png" width="160" height="100"/>'),
        svg('HTML 嵌入圖表','<foreignObject width="160" height="100"><div xmlns="http://www.w3.org/1999/xhtml"><button>嵌入值 99.50</button></div></foreignObject>'),
      ].join('');
      await page.setContent(html('<p>外部圖形</p>'+unsafe));
      const before=requests.length;const captured=await page.evaluate(captureLoadedConversation);
      assert.equal(pngs(captured.capture.messages[0].content).length,0);
      assert.deepEqual(captured.capture.messages[0].resources.map(({kind,label,url})=>[kind,label,url]),[
        ['chart','外部 use 圖表',null],['chart','外部 paint 圖表',null],['chart','外部圖片圖表',null],['chart','HTML 嵌入圖表',null],
      ]);
      assert.equal(requests.length,before,'rasterization cannot fetch an external SVG dependency');
      const exported=markdownDocument(captured.capture);assert.match(exported,/圖表（僅來源）/);assert.doesNotMatch(exported,/outside\.test/);
    });

    await t.test('foreignObject scripts, event handlers and external image content are never executed by capture',async()=>{
      await page.setContent(html('<p>不安全嵌入</p>'+[
        svg('事件內容','<foreignObject width="160" height="100"><div xmlns="http://www.w3.org/1999/xhtml" onmouseover="window.fixtureRuns++">99.50</div></foreignObject>'),
        svg('圖片內容','<foreignObject width="160" height="100"><div xmlns="http://www.w3.org/1999/xhtml"><img src="https://outside.test/foreign.png" onerror="window.fixtureRuns++"/></div></foreignObject>'),
        svg('指令內容','<foreignObject id="script-label" width="160" height="100"><div xmlns="http://www.w3.org/1999/xhtml">99.50</div></foreignObject>'),
      ].join('')));
      await page.evaluate(()=>{window.fixtureRuns=0;const script=document.createElement('script');script.type='text/plain';script.textContent='window.fixtureRuns++';document.querySelector('#script-label div').append(script);});
      const before=requests.length;const captured=await page.evaluate(captureLoadedConversation);
      assert.equal(pngs(captured.capture.messages[0].content).length,0);
      assert.deepEqual(captured.capture.messages[0].resources.filter(item=>item.kind==='chart').map(item=>item.label),['事件內容','圖片內容','指令內容']);
      assert.equal(await page.evaluate(()=>window.fixtureRuns),0);assert.equal(requests.length,before);
    });

    await t.test('unavailable external and hidden images never become fabricated PNGs',async()=>{
      await page.setContent(html('<p>附件</p><img src="https://outside.test/missing.png" width="80" height="40" alt="外部圖片"><img hidden src="/assets/synthetic-local.png" width="80" height="40" alt="隱藏舊圖">'));
      const before=requests.length;const captured=await page.evaluate(captureLoadedConversation);
      assert.equal(pngs(captured.capture.messages[0].content).length,0);
      assert.deepEqual(captured.capture.messages[0].resources,[{kind:'image',label:'外部圖片',url:'https://outside.test/missing.png'}]);
      assert.equal(requests.length,before);assert.doesNotMatch(JSON.stringify(captured),/隱藏舊圖/);
    });

    if(process.env.PCS_VISUAL_CAPTURE_OUTPUT){
      await mkdir(path.dirname(process.env.PCS_VISUAL_CAPTURE_OUTPUT),{recursive:true});
      await writeFile(process.env.PCS_VISUAL_CAPTURE_OUTPUT,JSON.stringify(combined.capture,null,2)+'\n','utf8');
    }
  }finally{await browser.close();}
});
