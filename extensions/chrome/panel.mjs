import {selectCapture, markdownDocument, downloadName, statusLabel, FINAL_STATES, encodeCapture, messageMarkdown, contentSummary} from './core.mjs';
import {excerpt, renderReading} from './preview.mjs';

const $ = id => document.getElementById(id);
let source = null;
let context = null;
let selected = new Set();
let busy = false;
let stale = false;
let previewing = false;
let instances = [];
let explicitInstance = '';
let operation = null;
let pollingTimer = null;
let pollFailures = 0;

async function call(action, fields = {}) {
  const response = await chrome.runtime.sendMessage({action, ...fields});
  if (!response?.ok) {
    const error = new Error(response?.error?.message || '無法完成操作，請重試。');
    error.code = response?.error?.code;
    throw error;
  }
  return response.result;
}

function feedback(message, {error = false, success = false, retry = false, check = false, cancel = false, restart = false} = {}) {
  $('feedback').hidden = !message;
  $('feedback').className = `feedback${error ? ' error' : success ? ' success' : ''}`;
  $('feedback-text').textContent = message;
  $('retry').hidden = !retry;
  $('check-status').hidden = !check;
  $('cancel').hidden = !cancel;
  $('new-selection').hidden = !restart;
}

function showError(error, duringOperation = false) {
  const messages = {
    host_unavailable: '尚未連接 PCS。請先完成桌面端連接設定。',
    instance_unavailable: '找不到這個 PCS 視窗，請開啟 PCS 後重新連線。',
    timeout: 'PCS 暫時沒有回應。', disconnected: '與 PCS 的連線已中斷。',
    instance_required: '請選擇要接收的 PCS。',
  };
  feedback(messages[error.code] || error.message, {error: true, retry: !duringOperation &&
    ['host_unavailable', 'instance_unavailable', 'timeout', 'disconnected'].includes(error.code), check: duringOperation});
}

function updateActions() {
  const available = !!source && selected.size > 0 && !stale;
  $('send').disabled = !available || busy || (!!operation && !FINAL_STATES.has(operation.state));
  $('send').textContent = busy ? '傳送中…' : '送到 PCS';
  $('download').disabled = !source || selected.size === 0;
  $('refresh').disabled = busy;
  $('select-all').checked = !!source && selected.size === source.messages.length;
  $('select-all').indeterminate = selected.size > 0 && selected.size < (source?.messages.length || 0);
  $('select-all').disabled = busy || previewing;
  $('selection-count').textContent = source ? (selected.size === source.messages.length ? `已載入 ${source.messages.length} 則` : `已選 ${selected.size} / ${source.messages.length} 則`) : '';
  $('preview-toggle').disabled = !selected.size;
  for (const input of document.querySelectorAll('.message input')) input.disabled = busy;
}

function selectedCapture() { return selectCapture(source, selected); }

function renderMessages() {
  $('messages').replaceChildren();
  source.messages.forEach((message, index) => {
    const article = document.createElement('article');
    article.className = 'message';
    const header = document.createElement('label');
    header.className = 'message-header check-label';
    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox'; checkbox.checked = selected.has(message.id);
    checkbox.setAttribute('aria-label', `選取第 ${index + 1} 則訊息`);
    const role = document.createElement('span');
    role.className = 'message-role'; role.textContent = message.role === 'user' ? '你' : 'ChatGPT';
    const number = document.createElement('span');
    number.className = 'message-number'; number.textContent = `${index + 1}`;
    header.append(checkbox, role, number);
    const body = document.createElement('div');
    body.className = 'message-content collapsed'; body.textContent = excerpt(message.content) || (message.resources || []).map(item => item.label).join('、');
    article.append(header, body);
    const summaryText = contentSummary(message);
    if (summaryText) {
      const summary = document.createElement('div'); summary.className = 'resource-summary'; summary.textContent = summaryText;
      article.append(summary);
    }
    if (message.content.length > 100 || message.content.includes('\n') || message.resources?.length) {
      const expand = document.createElement('button');
      expand.className = 'text-button message-expand'; expand.textContent = '展開';
      expand.setAttribute('aria-expanded', 'false');
      expand.addEventListener('click', () => {
        const collapsed = body.classList.toggle('collapsed');
        if (collapsed) body.textContent = excerpt(message.content) || (message.resources || []).map(item => item.label).join('、');
        else body.replaceChildren(renderReading(messageMarkdown(message, source.url)));
        expand.textContent = collapsed ? '展開' : '收合';
        expand.setAttribute('aria-expanded', String(!collapsed));
      });
      article.append(expand);
    }
    checkbox.addEventListener('change', () => {
      if (checkbox.checked) selected.add(message.id); else selected.delete(message.id);
      article.classList.toggle('unselected', !checkbox.checked);
      if (operation && FINAL_STATES.has(operation.state)) operation = null;
      updateActions();
    });
    article.classList.toggle('unselected', !checkbox.checked);
    $('messages').append(article);
  });
}

function setPreview(value) {
  previewing = value;
  $('messages').hidden = value;
  $('preview').hidden = !value;
  $('select-all').disabled = value || busy;
  $('preview-toggle').textContent = value ? '返回選取' : '預覽';
  if (value) {
    $('preview-content').replaceChildren();
    for (const message of selectedCapture().messages) {
      const article = document.createElement('article'); article.className = 'preview-message';
      const heading = document.createElement('h2'); heading.textContent = message.role === 'user' ? '你' : 'ChatGPT';
      article.append(heading, renderReading(messageMarkdown(message, source.url))); $('preview-content').append(article);
    }
  }
}

async function refresh() {
  if (busy) return;
  $('refresh').disabled = true;
  $('empty').hidden = false;
  $('empty-title').textContent = '讀取對話中…';
  $('open-chatgpt').hidden = true;
  $('conversation').hidden = true;
  source = null; context = null;
  try {
    const result = await call('capture');
    source = result.capture; context = result.context;
    selected = new Set(source.messages.map(message => message.id));
    stale = false;
    $('conversation-title').textContent = source.title;
    $('conversation').hidden = false; $('empty').hidden = true;
    setPreview(false); renderMessages();
    if (!operation || FINAL_STATES.has(operation.state)) feedback('');
  } catch (error) {
    $('empty-title').textContent = error.code === 'wrong_page' ? '選一段 ChatGPT 對話' : error.message;
    $('open-chatgpt').hidden = error.code !== 'wrong_page';
  } finally { $('refresh').disabled = false; updateActions(); }
}

async function connect() {
  const result = await call('hello');
  instances = Array.isArray(result.instances) ? result.instances : [];
  $('instances').replaceChildren();
  if (instances.length > 1) {
    const placeholder = new Option('選擇 PCS 視窗', '');
    $('instances').append(placeholder);
  }
  for (const instance of instances) $('instances').append(new Option(instance.display_name || 'PCS', instance.instance_id));
  if (instances.some(instance => instance.instance_id === explicitInstance)) $('instances').value = explicitInstance;
  $('instances-row').hidden = instances.length <= 1;
  if (!instances.length) {
    feedback('開啟 PCS 後即可接收對話。', {retry: true});
    return '';
  }
  return $('instances').value;
}

async function send() {
  if (!source || busy) return;
  if (operation && FINAL_STATES.has(operation.state)) operation = null;
  busy = true; updateActions();
  try {
    const capture = selectedCapture();
    await encodeCapture(capture);
    const instance_id = await connect();
    if (!instance_id) {
      if (instances.length) { feedback('選擇接收對話的 PCS。'); $('instances').focus(); }
      return;
    }
    operation = {operation_id: crypto.randomUUID(), instance_id, state: 'receiving', title: source.title};
    feedback(statusLabel(operation.state), {cancel: true});
    const result = await call('transfer', {...operation, capture, context});
    operation = {...operation, ...result};
    showOperation();
    schedulePoll();
  } catch (error) {
    if (['page_changed', 'capture_failed', 'wrong_page'].includes(error.code)) {
      stale = true; operation = null;
    }
    if (operation?.state === 'cancelled') showOperation(); else showError(error, !!operation);
  } finally { busy = false; updateActions(); }
}

function showOperation() {
  if (!operation) return;
  feedback(statusLabel(operation.state), {success: operation.state === 'committed', error: operation.state === 'failed',
    check: operation.state === 'unknown', cancel: ['receiving', 'previewing'].includes(operation.state),
    restart: ['unknown', 'cancelled'].includes(operation.state)});
  updateActions();
  if (operation.state === 'committed') loadRecent();
}

function schedulePoll() {
  clearTimeout(pollingTimer);
  if (!operation || FINAL_STATES.has(operation.state) || operation.state === 'unknown') return;
  pollingTimer = setTimeout(checkStatus, 1500);
}

async function checkStatus() {
  if (!operation) return;
  const current = operation;
  try {
    const result = await call('status', current);
    if (operation?.operation_id !== current.operation_id) return;
    operation = {...operation, ...result};
    pollFailures = 0;
    showOperation(); schedulePoll();
  } catch (error) {
    pollFailures++;
    showError(error, true);
    if (pollFailures < 3) pollingTimer = setTimeout(checkStatus, 3000);
  }
}

async function cancel() {
  if (!operation) return;
  try {
    const result = await call('cancel', operation);
    operation = {...operation, ...result};
    showOperation(); schedulePoll();
  } catch (error) { showError(error, true); }
}

function download() {
  const capture = selectedCapture();
  const format = $('download-format').value;
  const content = format === 'json' ? JSON.stringify(capture, null, 2) + '\n' : markdownDocument(capture);
  const blob = new Blob([content], {type: format === 'json' ? 'application/json;charset=utf-8' : 'text/markdown;charset=utf-8'});
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url; link.download = downloadName(capture.title, format); link.click();
  setTimeout(() => URL.revokeObjectURL(url), 10000);
}

async function loadRecent(restore = false) {
  const recent = await call('recents');
  $('recent-list').replaceChildren();
  const saved = recent.filter(item => item.state === 'committed').slice(0, 3);
  $('recent-section').hidden = !saved.length;
  for (const receipt of saved) {
    const row = document.createElement('div'); row.className = 'recent-item';
    const title = document.createElement('span'); title.className = 'recent-title'; title.textContent = receipt.title;
    const path = document.createElement('div'); path.className = 'recent-path'; path.textContent = receipt.path;
    const actions = document.createElement('div'); actions.className = 'recent-actions';
    for (const [action, label] of [['open', '開啟文件'], ['reveal', '顯示資料夾']]) {
      const button = document.createElement('button'); button.className = 'text-button'; button.textContent = label;
      button.addEventListener('click', async () => {
        try { await call(action, receipt); }
        catch (error) { showError(error); }
      });
      actions.append(button);
    }
    row.append(title, path, actions); $('recent-list').append(row);
  }
  if (restore && !operation) {
    const pending = recent.find(item => !item.dismissed && !FINAL_STATES.has(item.state));
    if (pending) { operation = pending; await checkStatus(); }
  }
}

function invalidate(tabId) {
  if (context && (tabId === undefined || tabId === context.tabId)) {
    stale = true; updateActions();
    if (!busy && (!operation || FINAL_STATES.has(operation.state))) feedback('對話已變更，請重新讀取。');
  }
}

$('refresh').addEventListener('click', refresh);
$('open-chatgpt').addEventListener('click', () => chrome.tabs.create({url: 'https://chatgpt.com/'}));
$('select-all').addEventListener('change', () => {
  selected = $('select-all').checked ? new Set(source.messages.map(message => message.id)) : new Set();
  if (operation && FINAL_STATES.has(operation.state)) operation = null;
  renderMessages(); updateActions();
});
$('preview-toggle').addEventListener('click', () => setPreview(!previewing));
$('send').addEventListener('click', send);
$('instances').addEventListener('change', () => { explicitInstance = $('instances').value; });
$('download').addEventListener('click', download);
$('check-status').addEventListener('click', checkStatus);
$('cancel').addEventListener('click', cancel);
$('new-selection').addEventListener('click', async () => {
  if (!operation || busy) return;
  try {
    await call('dismiss', operation);
    clearTimeout(pollingTimer); operation = null; pollFailures = 0;
    feedback(''); await refresh();
  } catch (error) { showError(error, true); }
});
$('retry').addEventListener('click', async () => {
  try { if (await connect()) feedback('已連接 PCS。', {success: true}); }
  catch (error) { showError(error); }
});
chrome.tabs.onActivated.addListener(() => invalidate());
chrome.tabs.onUpdated.addListener((tabId, change) => { if (change.url || change.status === 'loading') invalidate(tabId); });
window.addEventListener('unload', () => clearTimeout(pollingTimer));
refresh();
loadRecent(true).catch(() => {});
