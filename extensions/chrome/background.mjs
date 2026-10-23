import {captureLoadedConversation} from './capture.mjs';
import {isChatGPTUrl, transferCapture, encodeCapture} from './core.mjs';
import {NativeClient, BridgeError} from './native.mjs';

const client = new NativeClient(chrome.runtime);
const inFlight = new Set();
let storageQueue = Promise.resolve();
const setupPanel = () => chrome.sidePanel.setPanelBehavior({openPanelOnActionClick: true}).catch(() => {});
chrome.runtime.onInstalled.addListener(setupPanel);
chrome.runtime.onStartup.addListener(setupPanel);
setupPanel();

async function readPage() {
  const [tab] = await chrome.tabs.query({active: true, currentWindow: true});
  if (!tab?.id || !isChatGPTUrl(tab.url)) throw new BridgeError('wrong_page', '請開啟 ChatGPT 對話。');
  const [result] = await chrome.scripting.executeScript({target: {tabId: tab.id}, func: captureLoadedConversation});
  if (!result?.result) throw new BridgeError('capture_failed', '無法讀取對話，請重新讀取。');
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

async function handle(message) {
  if (message.action === 'capture') return await readPage();
  if (message.action === 'hello') return await client.request('hello');
  if (message.action === 'recents') {
    await storageQueue;
    return (await chrome.storage.local.get('recent')).recent || [];
  }
  if (message.action === 'dismiss') {
    const identity = identityOf(message);
    await record(identity, {dismissed: true});
    return {dismissed: true};
  }
  if (message.action === 'transfer') {
    const identity = identityOf(message);
    const key = identity.instance_id + ':' + identity.operation_id;
    if (inFlight.has(key)) throw new BridgeError('busy', '這份對話正在傳送。');
    inFlight.add(key);
    try {
      // A new navigation or switched branch invalidates the captured selection.
      const fresh = await readPage();
      if (!message.context || fresh.context.tabId !== message.context.tabId || fresh.context.url !== message.context.url ||
          fresh.context.fingerprint !== message.context.fingerprint) {
        throw new BridgeError('page_changed', '對話已變更，請重新讀取。');
      }
      const allowed = new Map(fresh.capture.messages.map(item => [item.id, item]));
      if (!Array.isArray(message.capture?.messages) || !message.capture.messages.length ||
          message.capture.messages.some(item => !allowed.has(item.id) || JSON.stringify(allowed.get(item.id)) !== JSON.stringify(item))) {
        throw new BridgeError('page_changed', '選取內容已變更，請重新讀取。');
      }
      await encodeCapture(message.capture);
      await record(identity, {state: 'receiving'}, message.capture.title);
      const result = await transferCapture((command, fields) => client.request(command, fields), message.capture, identity);
      await record(identity, result, message.capture.title);
      return result;
    } catch (error) {
      // Unknown outcomes remain queryable. Never retry begin/chunk/finish here.
      throw error;
    } finally { inFlight.delete(key); }
  }
  if (['status', 'open', 'reveal', 'cancel'].includes(message.action)) {
    const identity = identityOf(message);
    const result = await client.request(message.action, identity);
    if (['status', 'cancel'].includes(message.action)) await record(identity, result);
    return result;
  }
  throw new BridgeError('invalid_request', '無法完成這個動作。');
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (sender.id !== chrome.runtime.id || sender.tab || !sender.url?.startsWith(chrome.runtime.getURL(''))) return false;
  handle(message).then(result => sendResponse({ok: true, result}), error => sendResponse({ok: false,
    error: {code: error.code || 'failed', message: error.message || '操作失敗，請再試一次。'}}));
  return true;
});
