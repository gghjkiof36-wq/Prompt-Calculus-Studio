import test from 'node:test';
import assert from 'node:assert/strict';
import {selectCapture} from '../extensions/chrome/core.mjs';

test('toolbar fallback injects the classic file then toggles through ordinary messaging', async () => {
  let clicked, absent = true;
  const calls = [];
  globalThis.chrome = {
    runtime: {onMessage: {addListener() {}}},
    downloads:{onChanged:{addListener(){}},onErased:{addListener(){}}},
    storage:{local:{get:async()=>({})}},
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
    downloads:{onChanged:{addListener(){}},onErased:{addListener(){}},search:async()=>[]},
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

test('downloads record browser completion, persist IDs, reject foreign files and keep failures distinct',async()=>{
  const capture={version:1,provider:'chatgpt',conversation_id:'download-fixture',title:'工程量測',url:'https://chatgpt.com/c/download-fixture',method:'dom',scope:'loaded',adapter_version:'1',captured_at:'2026-10-24T00:00:00Z',messages:[{id:'selected',role:'assistant',content:'1.2300 × 10<sup>−6</sup> PRIVATE_BODY'},{id:'omitted',role:'user',content:'DO_NOT_DOWNLOAD'}]};
  const context={tabId:7,url:capture.url,fingerprint:'download-fingerprint'},storage={},items=new Map(),apiCalls=[];let handler,changed,erased,next=10;
  const sender={id:'fixture-extension',url:capture.url,tab:{id:7},frameId:0};
  globalThis.chrome={runtime:{id:sender.id,onMessage:{addListener:f=>{handler=f;}}},action:{onClicked:{addListener(){}}},tabs:{get:async id=>({id,url:context.url})},
    scripting:{executeScript:async()=>[{result:{capture,fingerprint:context.fingerprint}}]},storage:{local:{get:async key=>({[key]:structuredClone(storage[key])}),set:async value=>Object.assign(storage,structuredClone(value))}},
    downloads:{onChanged:{addListener:f=>{changed=f;}},onErased:{addListener:f=>{erased=f;}},download:async options=>{apiCalls.push({action:'download',options});const id=next++;items.set(id,{id,state:'in_progress',filename:`D:/synthetic/${options.filename}`,exists:true});return id;},search:async query=>{apiCalls.push({action:'search',query});return items.has(query.id)?[items.get(query.id)]:[];},open:(id,callback)=>{apiCalls.push({action:'open',id});callback();},show:id=>apiCalls.push({action:'reveal',id})}};
  try{
    await import('../extensions/chrome/background.mjs?downloads-contract');
    const call=message=>new Promise(resolve=>{assert.equal(handler(message,sender,resolve),true);});
    const payload={action:'download',format:'json',context,capture:selectCapture(capture,['selected'])};
    assert.equal((await call({...payload,format:'exe'})).error.code,'invalid_request');
    assert.equal((await call({...payload,context:{...context,fingerprint:'old'}})).error.code,'page_changed');
    assert.equal(apiCalls.filter(call=>call.action==='download').length,0);
    const started=await call(payload),id=started.result.download_id;
    assert.equal(storage.downloads[0].state,'in_progress');assert.equal((await call({action:'download_open',download_id:id})).error.code,'missing_download');
    const url=apiCalls.find(call=>call.action==='download').options.url;
    const body=JSON.parse(Buffer.from(url.split(',')[1],'base64').toString());assert.deepEqual(body.messages.map(item=>item.id),['selected']);
    assert.ok(!url.includes('DO_NOT_DOWNLOAD'));assert.ok(!JSON.stringify(storage).includes('PRIVATE_BODY'));
    items.set(id,{...items.get(id),state:'complete'});changed({id,state:{current:'complete'}});
    const receipts=(await call({action:'recents'})).result;assert.equal(receipts.find(item=>item.download_id===id).state,'complete');
    await call({action:'download_open',download_id:id});await call({action:'download_reveal',download_id:id});
    assert.deepEqual(apiCalls.filter(call=>['open','reveal'].includes(call.action)),[{action:'open',id},{action:'reveal',id}]);
    assert.equal((await call({action:'download_open',download_id:999})).error.code,'missing_download');
    changed({id:999,state:{current:'complete'}});await call({action:'recents'});assert.ok(apiCalls.filter(call=>call.action==='search').every(call=>call.query.id!==999));
    const md=(await call({...payload,format:'md'})).result.download_id;items.set(md,{...items.get(md),state:'interrupted',error:'USER_CANCELED'});changed({id:md,state:{current:'interrupted'}});
    const interrupted=(await call({action:'recents'})).result.find(item=>item.download_id===md);assert.equal(interrupted.state,'interrupted');assert.equal(interrupted.error,'USER_CANCELED');
    await import('../extensions/chrome/background.mjs?downloads-restart');await call({action:'recents'});await call({action:'download_open',download_id:id});
    assert.equal(apiCalls.at(-1).id,id);assert.equal(apiCalls.at(-1).action,'open');
    items.set(id,{...items.get(id),exists:false});changed({id,exists:{current:false}});await call({action:'recents'});assert.equal((await call({action:'download_reveal',download_id:id})).error.code,'missing_download');
    items.delete(id);erased(id);await call({action:'recents'});assert.equal(storage.downloads.find(item=>item.download_id===id).state,'missing');
    assert.ok(!JSON.stringify(storage).includes('PRIVATE_BODY'));
  }finally{delete globalThis.chrome;}
});
