import {captureLoadedConversation} from './capture.mjs';
import {isChatGPTUrl, transferCapture, encodeCapture, selectCapture, markdownDocument, downloadName, FINAL_STATES} from './core.mjs';
import {NativeClient, BridgeError} from './native.mjs';

const client = new NativeClient(chrome.runtime);
const inFlight = new Map();
let storageQueue = Promise.resolve();
let downloadQueue = Promise.resolve();
const downloadFiles = new Map();
const downloadsReady = chrome.storage.local.get('downloads').then(({downloads=[]}) => {
  for(const item of downloads)if(Number.isInteger(item.download_id))downloadFiles.set(item.download_id,item);
});
function updateDownload(item){
  downloadQueue=downloadQueue.catch(()=>{}).then(async()=>{
    await downloadsReady;const previous=downloadFiles.get(item.id);if(!previous)return;
    const state=previous.state==='complete'&&['in_progress','interrupted'].includes(item.state)?previous.state:item.state||previous.state;
    const value={...previous,state,path:item.filename||previous.path,
      exists:item.exists!==false,error:item.error||'',updated_at:item.endTime||previous.updated_at};
    downloadFiles.set(item.id,value);await persistDownloads();
  });return downloadQueue;
}
async function persistDownloads(){
  const values=[...downloadFiles.values()].sort((a,b)=>b.created_at.localeCompare(a.created_at)).slice(0,20);
  downloadFiles.clear();for(const item of values)downloadFiles.set(item.download_id,item);
  await chrome.storage.local.set({downloads:values});
}
async function refreshDownload(id){
  await downloadsReady;if(!downloadFiles.has(id))return;
  const [item]=await chrome.downloads.search({id});
  await updateDownload(item||{id,state:'missing',exists:false});
}
chrome.downloads.onChanged.addListener(delta=>{refreshDownload(delta.id).catch(()=>{});});
chrome.downloads.onErased.addListener(id=>{updateDownload({id,state:'missing',exists:false}).catch(()=>{});});
chrome.action.onClicked.addListener(async tab => {
  if (!tab.id || !isChatGPTUrl(tab.url)) return;
  try { await chrome.tabs.sendMessage(tab.id, {action:'pcs-toggle-selection'}); }
  catch {
    await chrome.scripting.executeScript({target:{tabId:tab.id},files:['content.js']});
    await chrome.tabs.sendMessage(tab.id, {action:'pcs-toggle-selection'});
  }
});

async function readPage(tabId) {
  const tab = await chrome.tabs.get(tabId);
  if (!tab?.id || !isChatGPTUrl(tab.url)) throw new BridgeError('wrong_page', '請開啟 ChatGPT 對話。');
  const [result] = await chrome.scripting.executeScript({target: {tabId: tab.id}, func: captureLoadedConversation,args:[true]});
  if (result?.result?.capture_error) throw new BridgeError(result.result.capture_error.code,result.result.capture_error.message);
  if (!result?.result) throw new BridgeError('capture_failed', result?.error?.message || '頁面擷取沒有回傳結果，請重新整理 ChatGPT。');
  const current = await chrome.tabs.get(tab.id);
  if (current.url !== tab.url) throw new BridgeError('page_changed', '對話已切換，請重新讀取。');
  return {...result.result, context: {tabId: tab.id, url: tab.url, fingerprint: result.result.fingerprint}};
}

function record(identity, result, title = '') {
  // Store receipts only, never conversation bodies. Serialize read/modify/write.
  storageQueue = storageQueue.catch(() => {}).then(async () => {
    const {recent = []} = await chrome.storage.local.get('recent');
    const previous = recent.find(item => item.operation_id === identity.operation_id && item.instance_id === identity.instance_id);
    if (previous?.state === 'committed' && result.state !== 'committed') return;
    if (previous && FINAL_STATES.has(previous.state) && result.state && !FINAL_STATES.has(result.state)) result={...result,state:previous.state};
    const item = {
      operation_id: identity.operation_id, instance_id: identity.instance_id,
      state: result.state || previous?.state || 'unknown',
      title: String(result.title || title || previous?.title || 'ChatGPT 對話').slice(0, 300),
      path: typeof result.path === 'string' ? result.path : (previous?.path || ''),
      dismissed: result.dismissed === true || previous?.dismissed === true,
      updated_at: new Date().toISOString(),
    };
    await chrome.storage.local.set({recent: [item, ...recent.filter(entry =>
      entry.operation_id !== item.operation_id || entry.instance_id !== item.instance_id)].slice(0, 10)});
  });
  return storageQueue;
}

function identityOf(message) {
  if (!/^[\da-f-]{32,36}$/i.test(message.operation_id || '') || typeof message.instance_id !== 'string' || !message.instance_id) {
    throw new BridgeError('invalid_request', '請重新選擇 PCS。');
  }
  return {operation_id: message.operation_id, instance_id: message.instance_id};
}

async function selectedPage(message,sender){
  const fresh=await readPage(sender.tab.id);
  if(!message.context||fresh.context.tabId!==message.context.tabId||fresh.context.url!==message.context.url||fresh.context.fingerprint!==message.context.fingerprint)
    throw new BridgeError('page_changed','對話已變更，請重新讀取。');
  const allowed=new Map(fresh.capture.messages.map(item=>[item.id,item]));
  const comparable=item=>JSON.stringify([item?.id,item?.role,item?.content,(item?.resources||[]).map(resource=>[resource.kind,resource.label,resource.url])]);
  if(!Array.isArray(message.capture?.messages)||!message.capture.messages.length||message.capture.messages.some(item=>!allowed.has(item.id)||comparable(allowed.get(item.id))!==comparable(item)))
    throw new BridgeError('page_changed','選取內容已變更，請重新讀取。');
  const selected=selectCapture(fresh.capture,message.capture.messages.map(item=>item.id));await encodeCapture(selected);return selected;
}

async function handle(message,sender) {
  if(message.action==='download_open'||message.action==='download_reveal'){
    // The history view warms this trusted ID map before the user's icon click.
    // Do not await storage/search here: downloads.open needs that user gesture.
    const receipt=downloadFiles.get(message.download_id);
    if(!receipt||receipt.state!=='complete'||receipt.exists===false)throw new BridgeError('missing_download','找不到這個檔案。');
    if(message.action==='download_open')await new Promise((resolve,reject)=>chrome.downloads.open(receipt.download_id,()=>chrome.runtime.lastError?reject(new Error(chrome.runtime.lastError.message)):resolve()));
    else chrome.downloads.show(receipt.download_id);
    return {};
  }
  if (message.action === 'capture') return await readPage(sender.tab.id);
  if(message.action==='download'){
    if(!['json','md'].includes(message.format))throw new BridgeError('invalid_request','請選擇下載格式。');
    const capture=await selectedPage(message,sender),filename=downloadName(capture.title,message.format);
    const bytes=new TextEncoder().encode(message.format==='json'?JSON.stringify(capture,null,2):markdownDocument(capture));
    let binary='';for(let offset=0;offset<bytes.length;offset+=32768)binary+=String.fromCharCode(...bytes.subarray(offset,offset+32768));
    const mime=message.format==='json'?'application/json':'text/markdown';
    const id=await chrome.downloads.download({url:`data:${mime};charset=utf-8;base64,${btoa(binary)}`,filename,conflictAction:'uniquify'});
    const created=new Date().toISOString();
    downloadQueue=downloadQueue.catch(()=>{}).then(async()=>{await downloadsReady;downloadFiles.set(id,{kind:'download',download_id:id,format:message.format,title:filename,path:'',state:'in_progress',exists:true,created_at:created,updated_at:created});await persistDownloads();});
    await downloadQueue;await refreshDownload(id);
    return {download_id:id};
  }
  if (message.action === 'hello') {
    const hello = await client.request('hello');
    const {preferred_destination} = await chrome.storage.local.get('preferred_destination');
    return {...hello, preferred_destination};
  }
  if (message.action === 'destinations') {
    if (typeof message.instance_id !== 'string' || !message.instance_id ||
        (message.folder_id !== undefined && !/^[a-f\d]{32}$/i.test(message.folder_id))) {
      throw new BridgeError('invalid_request', '請重新選擇資料夾。');
    }
    return await client.request('destinations', {instance_id: message.instance_id,
      ...(message.folder_id ? {folder_id: message.folder_id} : {})});
  }
  if (message.action === 'recents') {
    await storageQueue;await downloadsReady;
    await Promise.all([...downloadFiles.keys()].map(id=>refreshDownload(id)));await downloadQueue;
    const recent=(await chrome.storage.local.get('recent')).recent||[];
    return [...recent,...downloadFiles.values()].sort((a,b)=>b.updated_at.localeCompare(a.updated_at));
  }
  if (message.action === 'dismiss') {
    const identity = identityOf(message);
    await record(identity, {dismissed: true});
    return {dismissed: true};
  }
  if (message.action === 'transfer') {
    const identity = identityOf(message);
    if (!/^[a-f\d]{32}$/i.test(message.destination_id || '')) {
      throw new BridgeError('invalid_request', '請先選擇資料夾。');
    }
    const key = identity.instance_id + ':' + identity.operation_id;
    if (inFlight.has(key)) throw new BridgeError('busy', '這份對話正在傳送。');
    const transfer={cancelled:false,started:false};inFlight.set(key,transfer);
    try {
      // A new navigation or switched branch invalidates the captured selection.
      const selected=await selectedPage(message,sender);
      if(transfer.cancelled)return {state:'cancelled'};
      await record(identity, {state: 'receiving'}, selected.title);
      const result = await transferCapture((command, fields) => {
        if(transfer.cancelled)throw new BridgeError('cancel_requested','已停止傳送，保存結果請查看下載紀錄。');
        transfer.started=true;return client.request(command,fields);
      }, selected, identity,
        undefined, message.destination_id);
      await record(identity, result, selected.title);
      await chrome.storage.local.set({preferred_destination: {
        instance_id: identity.instance_id, folder_id: message.destination_id,
      }});
      return result;
    } catch (error) {
      // Unknown outcomes remain queryable. Never retry begin/chunk/finish here.
      if (['destination_changed', 'destination_limit'].includes(error.code)) {
        await record(identity, {state: 'failed', message: error.message});
      }else if(error.code==='cancel_requested'){
        await record(identity,{state:'unknown'});
      }
      throw error;
    } finally { inFlight.delete(key); }
  }
  if (['status', 'open', 'reveal', 'cancel'].includes(message.action)) {
    const identity = identityOf(message);
    if(message.action==='cancel'){
      const transfer=inFlight.get(identity.instance_id+':'+identity.operation_id);
      if(transfer){transfer.cancelled=true;if(!transfer.started){const result={state:'cancelled'};await record(identity,result);return result;}}
    }
    const result = await client.request(message.action, identity);
    if (['status', 'cancel'].includes(message.action)) await record(identity, result);
    return result;
  }
  throw new BridgeError('invalid_request', '無法完成這個動作。');
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (sender.id !== chrome.runtime.id || !sender.tab?.id || sender.frameId !== 0 || !isChatGPTUrl(sender.url)) return false;
  handle(message,sender).then(result => sendResponse({ok: true, result}), error => sendResponse({ok: false,
    error: {code: error.code || 'failed', message: error.message || '操作失敗，請再試一次。'}}));
  return true;
});
