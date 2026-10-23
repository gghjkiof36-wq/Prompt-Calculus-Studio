import test from 'node:test';
import assert from 'node:assert/strict';
import {selectCapture} from '../extensions/chrome/core.mjs';

test('toolbar fallback injects the classic file then toggles through ordinary messaging', async () => {
  let clicked, absent = true;
  const calls = [];
  globalThis.chrome = {
    runtime: {onMessage: {addListener() {}}},
    action: {onClicked: {addListener: listener => {clicked = listener;}}},
    tabs: {sendMessage: async (id, message) => {
      calls.push({type: 'message', id, message});
      if (absent) { absent = false; throw new Error('No content receiver yet'); }
    }},
    scripting: {executeScript: async options => {calls.push({type: 'inject', options});}},
  };
  try {
    await import('../extensions/chrome/background.mjs?classic-fallback');
    await clicked({id: 7, url: 'https://chatgpt.com/c/fixture'});
    assert.deepEqual(calls, [
      {type: 'message', id: 7, message: {action: 'pcs-toggle-selection'}},
      {type: 'inject', options: {target: {tabId: 7}, files: ['content.js']}},
      {type: 'message', id: 7, message: {action: 'pcs-toggle-selection'}},
    ]);
    calls.length = 0;
    await clicked({id: 7, url: 'https://chatgpt.com/c/fixture'});
    assert.equal(calls.length, 1);
    assert.equal(calls[0].type, 'message');
    await clicked({id: 8, url: 'https://example.com/'});
    assert.equal(calls.length, 1);
  } finally { delete globalThis.chrome; }
});

test('background validates the current document and only forwards selected bodies', async () => {
  const capture = {version: 1, provider: 'chatgpt', conversation_id: 'fixture', title: '測試對話',
    url: 'https://chatgpt.com/c/fixture', method: 'dom', scope: 'loaded', adapter_version: '1', captured_at: '2026-10-23T00:00:00Z',
    messages: [{id: 'selected', role: 'user', content: '選取中文'}, {id: 'omitted', role: 'assistant', content: 'UNSELECTED_SECRET_FIXTURE'}]};
  const current = {tabId: 7, url: capture.url, fingerprint: 'fixture-fingerprint'};
  const storage = {};
  const nativeRequests = [];
  let handler, receive;
  const runtime = {id: 'fixture-extension', getURL: () => 'chrome-extension://fixture-extension/',
    onInstalled: {addListener() {}}, onStartup: {addListener() {}}, onMessage: {addListener: f => { handler = f; }},
    connectNative: () => ({
      onMessage: {addListener: f => { receive = f; }}, onDisconnect: {addListener() {}}, disconnect() {},
      postMessage: message => {
        nativeRequests.push(message);
        const state = message.command === 'finish' ? 'committing' : message.command === 'status' ? 'committed' : 'receiving';
        queueMicrotask(() => receive({request_id: message.request_id, ok: true, result: {state, ...(state === 'committed' ? {path: 'D:/synthetic/output.md'} : {})}}));
      },
    })};
  globalThis.chrome = {
    runtime, action: {onClicked:{addListener(){}}},
    tabs: {get: async id => ({id, url: current.url})},
    scripting: {executeScript: async () => [{result: {capture, fingerprint: current.fingerprint}}]},
    storage: {local: {get: async key => ({[key]: storage[key]}), set: async values => Object.assign(storage, values)}},
  };
  try {
    await import('../extensions/chrome/background.mjs');
    const sender = {id: runtime.id, url:capture.url,tab:{id:7},frameId:0};
    const call = message => new Promise(resolve => { assert.equal(handler(message, sender, resolve), true); });
    const identity = {operation_id: '12345678-1234-1234-1234-123456789abc', instance_id: 'pcs-fixture'};
    const payload = {action: 'transfer', ...identity, destination_id:'a'.repeat(32), context: {...current}, capture: selectCapture(capture, ['selected'])};
    // Chrome's API serialization may reorder dictionary keys between worlds.
    payload.capture.messages=payload.capture.messages.map(m=>({content:m.content,role:m.role,id:m.id}));
    current.fingerprint = 'switched-branch';
    const stale = await call(payload);
    assert.equal(stale.error.code, 'page_changed');
    assert.equal(nativeRequests.length, 0);
    current.fingerprint = payload.context.fingerprint;
    const tampered=structuredClone(payload);tampered.capture.messages[0].content='changed body';
    assert.equal((await call(tampered)).error.code,'page_changed');
    assert.equal(nativeRequests.length,0);
    const missingDestination={...payload};delete missingDestination.destination_id;
    assert.equal((await call(missingDestination)).error.code,'invalid_request');
    assert.equal(nativeRequests.length,0);
    await call({action:'destinations',instance_id:identity.instance_id,folder_id:'b'.repeat(32),path:'C:/ignored'});
    assert.equal(nativeRequests.at(-1).folder_id,'b'.repeat(32));
    assert.equal(nativeRequests.at(-1).path,undefined);
    const result = await call(payload);
    assert.equal(nativeRequests.find(item=>item.command==='begin').destination_id,'a'.repeat(32));
    assert.ok(nativeRequests.filter(item=>item.command!=='begin').every(item=>!item.destination_id));
    assert.equal(result.result.state, 'committing');
    const body = Buffer.concat(nativeRequests.filter(item => item.command === 'chunk').map(item => Buffer.from(item.data, 'base64'))).toString('utf8');
    assert.ok(body.includes('選取中文'));
    assert.ok(!body.includes('UNSELECTED_SECRET_FIXTURE'));
    assert.ok(!JSON.stringify(storage).includes('選取中文'));
    assert.equal(storage.recent[0].state, 'committing');
    await call({action: 'status', ...identity});
    assert.equal(storage.recent[0].state, 'committed');
    await call({action: 'open', ...identity, path: 'C:/arbitrary/untrusted.exe'});
    assert.equal(nativeRequests.at(-1).path, undefined);
    assert.equal(nativeRequests.at(-1).operation_id, identity.operation_id);
    assert.equal(handler({action: 'hello'}, {...sender, frameId:1}, () => {}), false);
    assert.equal(handler({action: 'hello'}, {...sender, url:'https://example.com'}, () => {}), false);
    assert.equal(handler({action: 'hello'}, {...sender, tab:undefined}, () => {}), false);
    const another = {...identity, operation_id: '11111111-1234-1234-1234-123456789abc'};
    await call({action: 'dismiss', ...another});
    assert.equal(storage.recent[0].dismissed, true);
    assert.equal(storage.recent[0].state, 'unknown');
  } finally { delete globalThis.chrome; }
});
