// Optional headless DOM/UI checks. No real ChatGPT requests or private conversations.
// Set PCS_PLAYWRIGHT_MODULE to an existing playwright package; nothing is installed.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile, mkdir} from 'node:fs/promises';
import {createServer} from 'node:http';
import {fileURLToPath, pathToFileURL} from 'node:url';
import path from 'node:path';
import {captureLoadedConversation} from '../extensions/chrome/capture.mjs';

const enabled = !!process.env.PCS_PLAYWRIGHT_MODULE;
const fixture = `<!doctype html><html><head><meta charset="utf-8"><title>京都四天旅行安排 - ChatGPT</title></head><body>
<main>
<article><div data-message-author-role="user" data-message-id="user-one"><div class="whitespace-pre-wrap">我們安排四天的京都旅行，兩位大人，住在京都車站附近。

第一天中午抵達，第四天下午離開。每天最多安排兩個主要景點，想留時間逛街和喝咖啡。

請把清水寺排進去，也希望去一天嵐山。交通以電車和步行為主。</div></div></article>
<article><div data-message-author-role="assistant" data-message-id="assistant-one"><div class="markdown">
<p>可以把行程分成四個區域，減少往返交通：</p>
<h3>第一天｜京都車站周邊</h3><p>抵達後先寄放行李，下午逛東本願寺，晚上在車站附近吃飯。</p>
<h3>第二天｜清水寺與東山</h3><p>早上清水寺，下午沿二年坂、三年坂散步，在祇園找間咖啡店休息。</p>
<h3>第三天｜嵐山</h3><p>竹林與天龍寺，再沿桂川散步。</p>
</div><button>複製</button></div></article>
<article><div data-message-author-role="user" data-message-id="user-two"><div class="whitespace-pre-wrap">第二天想晚一點出門，清水寺和祇園之間有午餐建議嗎？</div></div></article>
<article><div data-message-author-role="assistant" data-message-id="assistant-two"><div class="markdown"><p>可以在十點出門，把午餐放在清水坂周邊，下午再慢慢走向祇園。</p><table><thead><tr><th>時間</th><th>安排</th></tr></thead><tbody><tr><td>10:00</td><td>從飯店出發</td></tr><tr><td>12:30</td><td>清水坂午餐</td></tr></tbody></table></div></div></article>
<article hidden><div data-message-author-role="assistant" data-message-id="hidden-branch"><div class="markdown"><p>HIDDEN_OTHER_BRANCH</p></div></div></article>
</main></body></html>`;

test('headless DOM capture and real side-panel UI with synthetic Chrome bridge', {skip: !enabled}, async t => {
  const playwright = await import(pathToFileURL(process.env.PCS_PLAYWRIGHT_MODULE).href);
  const browser = await playwright.chromium.launch({headless: true,
    ...(process.env.PCS_CHROME_EXECUTABLE ? {executablePath: process.env.PCS_CHROME_EXECUTABLE} : {})});
  const extensionRoot = fileURLToPath(new URL('../extensions/chrome/', import.meta.url));
  const server = createServer(async (req, res) => {
    try {
      const name = new URL(req.url, 'http://localhost').pathname.slice(1) || 'panel.html';
      if (!/^[a-z.-]+$/.test(name)) { res.writeHead(404).end(); return; }
      const contents = await readFile(path.join(extensionRoot, name));
      res.writeHead(200, {'content-type': name.endsWith('.css') ? 'text/css' : name.endsWith('.mjs') ? 'text/javascript' : 'text/html'}).end(contents);
    } catch { res.writeHead(404).end(); }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const base = `http://127.0.0.1:${server.address().port}`;
  let captured;
  try {
    const capturePage = await browser.newPage();
    // Every network request is fulfilled locally or blocked. No site access occurs.
    await capturePage.route('**/*', route => route.request().url().startsWith('https://chatgpt.com/') ?
      route.fulfill({contentType: 'text/html', body: fixture}) : route.abort());
    await capturePage.goto('https://chatgpt.com/c/synthetic-kyoto?test=fixture');
    await t.test('loaded messages only, correct current URL, Markdown structure and source IDs', async () => {
      captured = await capturePage.evaluate(captureLoadedConversation);
      assert.equal(captured.capture.title, '京都四天旅行安排');
      assert.equal(captured.capture.conversation_id, 'synthetic-kyoto');
      assert.equal(captured.capture.url, 'https://chatgpt.com/c/synthetic-kyoto');
      assert.equal(captured.capture.messages.length, 4);
      assert.ok(!JSON.stringify(captured).includes('HIDDEN_OTHER_BRANCH'));
      assert.ok(!captured.capture.messages[1].content.includes('複製'));
      assert.match(captured.capture.messages[1].content, /### 第二天/);
      assert.match(captured.capture.messages[3].content, /\| 時間 \| 安排 \|/);
      assert.match(captured.capture.messages[3].content, /\| --- \| --- \|/);
    });
    await t.test('long content, code fences, formulas, external references and anonymous message IDs', async () => {
      await capturePage.evaluate(() => {
        const container = document.createElement('div');
        container.dataset.messageAuthorRole = 'assistant';
        container.innerHTML = '<div class="markdown"><pre><code class="language-js">const code = ```;\n\nconst x = 1;</code></pre><p><span class="katex"><math><semantics><annotation encoding="application/x-tex">x^2 + y^2</annotation></semantics></math></span></p><a href="https://example.com/file?token=secret">notes.txt</a><img src="https://example.com/image?sig=secret" alt="行程地圖"><p id="long-text"></p></div>';
        document.querySelector('main').append(container);
        document.getElementById('long-text').textContent = '長文保留。'.repeat(50000);
      });
      const result = await capturePage.evaluate(captureLoadedConversation);
      const last = result.capture.messages.at(-1);
      assert.match(last.id, /^capture-local-/);
      assert.match(last.content, /````js\nconst code = ```;/);
      assert.match(last.content, /\n\nconst x = 1;/);
      assert.match(last.content, /\$x\^2 \+ y\^2\$/);
      assert.match(last.content, /notes.txt/);
      assert.deepEqual(last.resources, [{kind: 'image', label: '行程地圖', url: null}]);
      assert.ok(!last.content.includes('secret'));
      assert.equal(last.content.split('長文保留。').length - 1, 50000);
    });
    await t.test('navigation during capture and streaming replies are rejected', async () => {
      await capturePage.goto('https://chatgpt.com/c/synthetic-kyoto');
      await capturePage.evaluate(() => {
        const old = crypto.subtle.digest.bind(crypto.subtle);
        crypto.subtle.digest = async (...args) => { history.pushState({}, '', '/c/another-conversation'); return old(...args); };
        document.querySelector('[data-message-id]').removeAttribute('data-message-id');
      });
      await assert.rejects(capturePage.evaluate(captureLoadedConversation), /對話已切換/);
      await capturePage.goto('https://chatgpt.com/c/synthetic-kyoto');
      await capturePage.evaluate(() => {
        const button = document.createElement('button'); button.dataset.testid = 'stop-button';
        document.querySelector('main').append(button);
      });
      await assert.rejects(capturePage.evaluate(captureLoadedConversation), /還在回覆/);
    });
    await capturePage.close();

    const panelContext = await browser.newContext({viewport: {width: 360, height: 900}, colorScheme: 'dark'});
    await panelContext.addInitScript(({captured}) => {
      const hooks = {activated: [], updated: []};
      const receipt = {operation_id: '12345678-1234-1234-1234-123456789abc', instance_id: 'pcs-fixture', state: 'committed',
        title: '週末閱讀筆記', path: 'D:\\PCS\\對話\\週末閱讀筆記.md'};
      const state = {calls: [], instances: [{instance_id: 'pcs-fixture', display_name: 'PCS · 我的工作區'}],
        currentState: 'previewing', recent: [receipt], captured};
      window.fixtureState = state; window.fixtureHooks = hooks;
      window.chrome = {
        runtime: {sendMessage: async message => {
          state.calls.push(message);
          if (state.error) return {ok: false, error: state.error};
          let result;
          if (message.action === 'capture') result = {...state.captured, context: {tabId: 7, url: state.captured.capture.url, fingerprint: state.captured.fingerprint}};
          else if (message.action === 'hello') result = {instances: state.instances};
          else if (message.action === 'recents') result = state.recent;
          else if (message.action === 'transfer') result = {state: 'previewing'};
          else if (message.action === 'status') result = {state: state.currentState};
          else if (message.action === 'cancel') result = {state: 'cancelled'};
          else result = {state: 'committed'};
          return {ok: true, result};
        }},
        tabs: {create: async () => {}, onActivated: {addListener: f => hooks.activated.push(f)}, onUpdated: {addListener: f => hooks.updated.push(f)}},
      };
    }, {captured});
    const panel = await panelContext.newPage();
    const errors = [];
    panel.on('pageerror', error => errors.push(error.message));
    await panel.route('**/*', route => route.request().url().startsWith(base) ? route.continue() : route.abort());
    await panel.goto(base + '/panel.html');
    await panel.locator('.message').last().waitFor();
    await t.test('readable tables and headings, with untrusted HTML remaining inert text', async () => {
      await panel.locator('.message').nth(3).getByRole('button', {name: '展開'}).click();
      assert.equal(await panel.locator('.message').nth(3).locator('table tr').count(), 3);
      await panel.locator('.message').nth(3).getByRole('button', {name: '收合'}).click();
      const projection = await panel.evaluate(async base => {
        const {renderReading} = await import(base + '/preview.mjs');
        const node = renderReading('<img src=x onerror="window.bad=1">\n\n[run](javascript:alert(1))\n\n```html\n<script>bad()</script>\n```');
        return {images: node.querySelectorAll('img').length, scripts: node.querySelectorAll('script').length,
          links: node.querySelectorAll('a').length, code: node.querySelector('code').textContent};
      }, base);
      assert.deepEqual(projection, {images: 0, scripts: 0, links: 0, code: '<script>bad()</script>'});
    });
    await t.test('select, preview and transfer only checked messages; finish remains previewing', async () => {
      await panel.locator('.message input').nth(1).uncheck();
      await panel.getByRole('button', {name: '預覽', exact: true}).click();
      assert.ok((await panel.locator('#preview-content').innerText()).includes('第二天想晚一點'));
      assert.ok(!(await panel.locator('#preview-content').innerText()).includes('竹林與天龍寺'));
      await panel.getByRole('button', {name: '返回選取'}).click();
      await panel.getByRole('button', {name: '送到 PCS', exact: true}).click();
      await panel.getByText('請在 PCS 預覽並保存', {exact: true}).waitFor();
      const sent = await panel.evaluate(() => fixtureState.calls.find(call => call.action === 'transfer'));
      assert.deepEqual(sent.capture.messages.map(item => item.id), ['user-one', 'user-two', 'assistant-two']);
      assert.equal(sent.capture.scope, 'selection');
      assert.ok(!JSON.stringify(sent.capture).includes('竹林與天龍寺'));
      await panel.getByRole('button', {name: '取消', exact: true}).click();
      await panel.getByText('已取消', {exact: true}).waitFor();
    });
    await t.test('downloads use selected snapshot and retain Unicode', async () => {
      const event = panel.waitForEvent('download');
      await panel.getByRole('button', {name: '下載檔案'}).click();
      const download = await event;
      assert.equal(download.suggestedFilename(), '京都四天旅行安排.json');
      const data = JSON.parse(await readFile(await download.path(), 'utf8'));
      assert.equal(data.messages.length, 3);
      assert.equal(data.title, '京都四天旅行安排');
    });
    await t.test('navigation disables sending stale data and refresh restores', async () => {
      await panel.evaluate(() => fixtureHooks.activated.forEach(listener => listener({tabId: 9})));
      assert.ok(await panel.locator('#send').isDisabled());
      await panel.getByRole('button', {name: '重新讀取對話'}).click();
      await panel.locator('#send:not(:disabled)').waitFor();
    });
    await t.test('multiple PCS instances require deliberate selection; paths go only through receipts', async () => {
      await panel.evaluate(() => { fixtureState.instances.push({instance_id: 'pcs-two', display_name: 'PCS · 第二個工作區'}); });
      await panel.getByRole('button', {name: '送到 PCS', exact: true}).click();
      await panel.locator('#instances-row').waitFor();
      // A newly discovered second instance must not inherit the earlier implicit choice.
      const selected = await panel.locator('#instances').inputValue();
      assert.equal(selected, '');
      await panel.locator('#instances').selectOption('pcs-two');
      await panel.getByRole('button', {name: '送到 PCS', exact: true}).click();
      await panel.getByText('請在 PCS 預覽並保存', {exact: true}).waitFor();
      const transfer = await panel.evaluate(() => fixtureState.calls.filter(call => call.action === 'transfer').at(-1));
      assert.equal(transfer.instance_id, 'pcs-two');
      await panel.getByRole('button', {name: '取消', exact: true}).click();
      await panel.getByRole('button', {name: '顯示資料夾'}).click();
      const call = await panel.evaluate(() => fixtureState.calls.find(call => call.action === 'reveal'));
      assert.equal(call.operation_id, '12345678-1234-1234-1234-123456789abc');
    });
    await t.test('unknown outcome allows deliberate new selection without automatic resend', async () => {
      await panel.evaluate(() => { fixtureState.currentState = 'unknown'; });
      await panel.getByRole('button', {name: '送到 PCS', exact: true}).click();
      await panel.getByText('尚未取得結果', {exact: true}).waitFor();
      const before = await panel.evaluate(() => fixtureState.calls.filter(call => call.action === 'transfer'));
      assert.ok(await panel.locator('#send').isDisabled());
      await panel.getByRole('button', {name: '重新選取', exact: true}).click();
      await panel.locator('#send:not(:disabled)').waitFor();
      const after = await panel.evaluate(() => fixtureState.calls.filter(call => call.action === 'transfer'));
      assert.equal(after.length, before.length);
      await panel.getByRole('button', {name: '送到 PCS', exact: true}).click();
      await panel.getByText('請在 PCS 預覽並保存', {exact: true}).waitFor();
      const latest = await panel.evaluate(() => fixtureState.calls.filter(call => call.action === 'transfer').at(-1));
      assert.notEqual(latest.operation_id, before.at(-1).operation_id);
      await panel.getByRole('button', {name: '取消', exact: true}).click();
    });
    await t.test('360/600 layout, keyboard focus, no horizontal overflow; actual UI captures', async () => {
      await panel.reload();
      await panel.locator('.message').last().waitFor();
      for (const width of [360, 600]) {
        await panel.setViewportSize({width, height: 900});
        const metrics = await panel.evaluate(() => ({scroll: document.documentElement.scrollWidth, width: innerWidth,
          button: document.getElementById('send').getBoundingClientRect().width,
          font: getComputedStyle(document.body).fontFamily}));
        assert.ok(metrics.scroll <= metrics.width);
        assert.ok(metrics.button > width - 50);
        assert.match(metrics.font, /Segoe UI/);
        if (process.env.PCS_SCREENSHOT_DIR) {
          await mkdir(process.env.PCS_SCREENSHOT_DIR, {recursive: true});
          await panel.screenshot({path: path.join(process.env.PCS_SCREENSHOT_DIR, `chrome-panel-${width}.png`), fullPage: true});
        }
      }
      await panel.locator('#refresh').focus();
      assert.equal(await panel.evaluate(() => document.activeElement.id), 'refresh');
      assert.deepEqual(errors, []);
    });
    await panelContext.close();
  } finally {
    await browser.close();
    await new Promise(resolve => server.close(resolve));
  }
});
