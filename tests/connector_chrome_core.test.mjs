import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {selectCapture, markdownDocument, downloadName, encodeCapture, chunks, transferCapture,
  CHUNK_BYTES, MAX_CAPTURE_BYTES, statusLabel, isChatGPTUrl} from '../extensions/chrome/core.mjs';
import {NativeClient} from '../extensions/chrome/native.mjs';

const sample = {
  version: 1, provider: 'chatgpt', conversation_id: 'synthetic-chat', title: '四天旅行',
  url: 'https://chatgpt.com/c/synthetic-chat', method: 'dom', scope: 'loaded', adapter_version: '1',
  captured_at: '2026-10-23T12:00:00.000Z', messages: [
    {id: 'one', role: 'user', content: '京都旅行 🗾'},
    {id: 'two', role: 'assistant', content: '未選內容 UNIQUE_UNSELECTED'},
    {id: 'three', role: 'user', content: '保留\n\n```js\nconst n = `好`;\n```'},
  ], internal: 'not-part-of-schema',
};

test('selected capture and both file formats exclude unselected bodies and extra fields', () => {
  const before = JSON.stringify(sample);
  const result = selectCapture(sample, ['one', 'three']);
  assert.deepEqual(result.messages.map(item => item.id), ['one', 'three']);
  assert.equal(result.scope, 'selection');
  assert.equal(result.internal, undefined);
  assert.ok(!JSON.stringify(result).includes('UNIQUE_UNSELECTED'));
  assert.ok(!markdownDocument(result).includes('UNIQUE_UNSELECTED'));
  assert.equal(JSON.stringify(sample), before);
  result.messages[0].content = 'changed';
  assert.equal(sample.messages[0].content, '京都旅行 🗾');
  assert.throws(() => selectCapture(sample, []), /選擇/);
});

test('all loaded is explicitly different from a complete conversation or arbitrary branch', () => {
  const result = selectCapture(sample, sample.messages.map(item => item.id));
  assert.equal(result.scope, 'loaded');
  assert.equal(result.method, 'dom');
  assert.match(markdownDocument(result), /頁面已載入的訊息/);
  assert.match(markdownDocument(result), /```js\nconst n = `好`;/);
});

test('UTF-8 chunk hash and framing preserve Chinese, emoji and split multibyte boundaries', async () => {
  const selected = selectCapture(sample, ['one', 'three']);
  const result = await encodeCapture(selected);
  assert.equal(result.size, Buffer.byteLength(JSON.stringify(selected), 'utf8'));
  assert.equal(result.sha256, createHash('sha256').update(JSON.stringify(selected)).digest('hex'));
  const parts = [...chunks(result.bytes, 5)];
  assert.deepEqual(parts.map(part => part.index), parts.map((_, i) => i));
  assert.equal(Buffer.concat(parts.map(part => Buffer.from(part.data, 'base64'))).toString('utf8'), JSON.stringify(selected));
});

test('long payload uses bounded frames and refuses over-limit payload before begin', async () => {
  const long = {...sample, messages: [{id: 'one', role: 'user', content: '字'.repeat(200000)}]};
  const encoded = await encodeCapture(long);
  const parts = [...chunks(encoded.bytes)];
  assert.ok(parts.length > 2);
  for (const part of parts) {
    assert.ok(Buffer.byteLength(part.data, 'base64') <= CHUNK_BYTES);
    assert.ok(Buffer.byteLength(JSON.stringify(part)) < 512 * 1024);
  }
  let calls = 0;
  const tooLarge = {...sample, messages: [{id: 'one', role: 'user', content: 'a'.repeat(MAX_CAPTURE_BYTES)}]};
  await assert.rejects(transferCapture(async () => { calls++; }, tooLarge, {}), /內容太長/);
  assert.equal(calls, 0);
});

test('finish preview is not a save receipt; lost finish is never retried', async () => {
  const requests = [];
  const identity = {instance_id: 'instance-one', operation_id: '12345678-1234-1234-1234-123456789abc'};
  const request = async (command, fields) => {
    requests.push({command, fields});
    return {state: command === 'finish' ? 'previewing' : 'receiving'};
  };
  const result = await transferCapture(request, selectCapture(sample, ['one']), identity);
  assert.equal(result.state, 'previewing');
  assert.equal(statusLabel(result.state), '請在 PCS 預覽並保存');
  assert.deepEqual(requests.map(item => item.command), ['begin', 'chunk', 'finish']);
  assert.ok(requests.every(item => item.fields.instance_id === identity.instance_id));
  let finishes = 0;
  await assert.rejects(transferCapture(async command => {
    if (command === 'finish') { finishes++; throw new Error('disconnect'); }
    return {state: 'receiving'};
  }, selectCapture(sample, ['one']), identity), /disconnect/);
  assert.equal(finishes, 1);
});

test('stable public manifest key matches registered host origin and minimum permissions', async () => {
  const manifest = JSON.parse(await readFile(new URL('../extensions/chrome/manifest.json', import.meta.url), 'utf8'));
  const id = [...createHash('sha256').update(Buffer.from(manifest.key, 'base64')).digest().subarray(0, 16)]
    .map(byte => String.fromCharCode(97 + (byte >> 4), 97 + (byte & 15))).join('');
  assert.equal(id, 'migmpicacfjoipmfcgdjocgdbplgldan');
  assert.deepEqual(manifest.host_permissions, ['https://chatgpt.com/*', 'https://chat.openai.com/*']);
  assert.ok(!manifest.permissions.includes('cookies'));
  assert.equal(manifest.manifest_version, 3);
});

test('URL boundary and Windows filenames', () => {
  assert.ok(isChatGPTUrl('https://chatgpt.com/c/id'));
  assert.ok(!isChatGPTUrl('https://chatgpt.com.evil.example/c/id'));
  assert.ok(!isChatGPTUrl('http://chatgpt.com/c/id'));
  assert.equal(downloadName('CON', 'json'), '對話-CON.json');
  assert.equal(downloadName('京都 / 四天?', 'md'), '京都 _ 四天_.md');
});

function fakeRuntime() {
  const sent = [];
  let receive, disconnect;
  const runtime = {connectNative: () => ({
    onMessage: {addListener: listener => { receive = listener; }},
    onDisconnect: {addListener: listener => { disconnect = listener; }},
    postMessage: message => sent.push(message), disconnect: () => disconnect(),
  })};
  return {runtime, sent, receive: message => receive(message), disconnect: () => disconnect()};
}

test('native response request IDs prevent cross-request completion', async () => {
  const fake = fakeRuntime(); const client = new NativeClient(fake.runtime);
  try {
    const first = client.request('hello'); const second = client.request('status', {operation_id: 'one'});
    assert.notEqual(fake.sent[0].request_id, fake.sent[1].request_id);
    fake.receive({request_id: 'unrelated', ok: true, result: {state: 'committed'}});
    fake.receive({request_id: fake.sent[1].request_id, ok: true, result: {state: 'previewing'}});
    fake.receive({request_id: fake.sent[0].request_id, ok: true, result: {instances: []}});
    assert.deepEqual(await first, {instances: []});
    assert.equal((await second).state, 'previewing');
    assert.equal(fake.sent[0].command, 'hello');
    assert.equal(fake.sent[0].cmd, undefined);
  } finally { client.close(); }
});

test('native timeout and disconnect reject once without resending', async () => {
  const fake = fakeRuntime(); const client = new NativeClient(fake.runtime, {timeout: 10});
  try {
    await assert.rejects(client.request('finish'), error => error.code === 'timeout');
    assert.equal(fake.sent.length, 1);
    const pending = client.request('status');
    fake.disconnect();
    await assert.rejects(pending, error => error.code === 'disconnected');
    assert.equal(fake.sent.length, 2);
  } finally { client.close(); }
});
