export const HOST_NAME = 'com.promptcalculus.studio';
export const CHUNK_BYTES = 192 * 1024;
export const MAX_CAPTURE_BYTES = 16 * 1024 * 1024;
export const FINAL_STATES = new Set(['committed', 'cancelled', 'failed']);

export function isChatGPTUrl(value) {
  try {
    const url = new URL(value);
    return url.protocol === 'https:' && ['chatgpt.com', 'chat.openai.com'].includes(url.hostname);
  } catch { return false; }
}

export function selectCapture(capture, selectedIds) {
  const selected = new Set(selectedIds);
  const messages = capture.messages.filter(message => selected.has(message.id));
  if (!messages.length) throw new Error('請選擇要轉移的訊息。');
  return {
    version: 1, provider: 'chatgpt', conversation_id: capture.conversation_id,
    title: capture.title, url: capture.url, method: 'dom',
    scope: messages.length === capture.messages.length ? 'loaded' : 'selection',
    messages: messages.map(({id, role, content, resources}) => ({id, role, content,
      ...(resources?.length ? {resources: resources.map(({kind, label, url}) => ({kind, label, url}))} : {})})),
    adapter_version: '1', captured_at: capture.captured_at,
  };
}

export function markdownDocument(capture) {
  const role = {user: '你', assistant: 'ChatGPT', system: '系統', tool: '工具'};
  const title = capture.title.replace(/[\r\n]+/g, ' ').trim() || 'ChatGPT 對話';
  const scope = capture.scope === 'selection' ? '選取的訊息' : '頁面已載入的訊息';
  return `# ${title}\n\n來源：${capture.url}\n\n範圍：${scope}\n\n` +
    capture.messages.map(message => `## ${role[message.role] || message.role}\n\n${messageMarkdown(message, capture.url)}`).join('\n\n---\n\n') + '\n';
}

export function messageMarkdown(message, sourceUrl) {
  const names = {image: '圖片', chart: '圖表', attachment: '附件', embed: '嵌入內容', citation: '來源'};
  const references = (message.resources || []).map(resource => {
    const label = resource.label.replace(/[\r\n]+/g, ' ').replace(/([\\`*_[\]<>])/g, '\\$1');
    const url = (resource.url || sourceUrl).replace(/[()<>\s]/g, char => encodeURIComponent(char));
    const kind = names[resource.kind] || '附件';
    return `- ${kind}${resource.kind === 'citation' ? '' : '（僅來源）'}：[${label}](${url})`;
  });
  return [message.content, references.join('\n')].filter(Boolean).join('\n\n');
}

export function contentSummary(message) {
  const items = [];
  const types = {image: '圖片', chart: '圖表', attachment: '附件', embed: '嵌入內容', citation: '來源'};
  const tables = (message.content.match(/^\s*\|(?:\s*:?-+:?\s*\|)+\s*$/gm) || []).length;
  if (tables) items.push(`表格 ${tables}`);
  for (const [kind, label] of Object.entries(types)) {
    const count = (message.resources || []).filter(item => item.kind === kind).length;
    if (count) items.push(`${label} ${count}`);
  }
  const citations = (message.content.match(/\]\(https?:\/\//g) || []).length;
  if (citations) items.push(`連結 ${citations}`);
  return items.join(' · ');
}

export function downloadName(title, extension) {
  let base = String(title || 'ChatGPT 對話').replace(/[<>:"/\\|?*\u0000-\u001f]/g, '_').replace(/[. ]+$/g, '').slice(0, 100);
  if (!base || /^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)/i.test(base)) base = `對話-${base}`;
  return `${base}.${extension}`;
}

export async function sha256(bytes) {
  return Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)), b => b.toString(16).padStart(2, '0')).join('');
}

export async function encodeCapture(capture) {
  const bytes = new TextEncoder().encode(JSON.stringify(capture));
  if (bytes.length > MAX_CAPTURE_BYTES) throw new Error('內容太長，請減少選取的訊息。');
  return {bytes, sha256: await sha256(bytes), size: bytes.length};
}

export function* chunks(bytes, chunkSize = CHUNK_BYTES) {
  if (!Number.isInteger(chunkSize) || chunkSize < 1) throw new Error('Invalid chunk size');
  for (let offset = 0, index = 0; offset < bytes.length; offset += chunkSize, index++) {
    let binary = '';
    for (const byte of bytes.subarray(offset, offset + chunkSize)) binary += String.fromCharCode(byte);
    yield {index, data: btoa(binary)};
  }
}

export function statusLabel(state) {
  return ({receiving: '傳送中…', previewing: '正在保存…', committing: '正在保存…',
    committed: '已保存', cancelled: '已取消', failed: '保存失敗', unknown: '尚未取得結果'})[state] || '正在連線…';
}

// A response only acknowledges its own request. A finish response is not a save receipt.
export async function transferCapture(request, capture, identity, onProgress = () => {}, destinationId) {
  const encoded = await encodeCapture(capture);
  const send = (command, fields = {}) => request(command, {...identity, ...fields});
  const start = await send('begin', {sha256: encoded.sha256, size: encoded.size,
    ...(destinationId ? {destination_id: destinationId} : {})});
  if (start.state !== 'receiving') throw new Error('PCS 未能接收這份對話。');
  for (const part of chunks(encoded.bytes)) {
    await send('chunk', part);
    onProgress(Math.min(encoded.size, (part.index + 1) * CHUNK_BYTES), encoded.size);
  }
  return await send('finish');
}
