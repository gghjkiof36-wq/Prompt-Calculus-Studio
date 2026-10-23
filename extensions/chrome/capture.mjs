// This function is serialized by chrome.scripting; keep dependencies inside it.
// It reads the displayed document only. It never calls ChatGPT's internal APIs.
export async function captureLoadedConversation() {
  const startUrl = location.href;
  const url = new URL(startUrl);
  if (url.protocol !== 'https:' || !['chatgpt.com', 'chat.openai.com'].includes(url.hostname)) {
    throw new Error('請開啟 ChatGPT 對話。');
  }
  const main = document.querySelector('main');
  if (!main) throw new Error('尚未找到對話，請稍後重新讀取。');
  const visible = (element, media = false) => {
    for (let current = element; current && current !== document.body; current = current.parentElement) {
      if (current.hidden || (current.getAttribute('aria-hidden') === 'true' && !(media && current === element))) return false;
      const style = getComputedStyle(current);
      if (style.display === 'none' || style.visibility === 'hidden') return false;
    }
    return true;
  };
  if ([...main.querySelectorAll('[data-testid="stop-button"], [data-is-streaming="true"]')].some(element => visible(element))) {
    throw new Error('ChatGPT 還在回覆，完成後再讀取。');
  }
  const safeLink = value => {
    if (!value) return '';
    try {
      const link = new URL(value, startUrl);
      if (!['https:', 'http:'].includes(link.protocol)) return '';
      if (link.username || link.password || link.href.length > 2048) return '';
      if (/^(?:localhost|127\.|10\.|192\.168\.|169\.254\.|172\.(?:1[6-9]|2\d|3[01])\.|\[::1\]|\[f[cd])/i.test(link.hostname) || /\.(?:local|internal)$/i.test(link.hostname)) return '';
      // File links may carry temporary credentials. Keep the name, not the token.
      if ([...link.searchParams.keys()].some(key => /(?:token|signature|credential|^sig$|^key$|^auth|^x-amz-)/i.test(key))) return '';
      return link.href.replace(/\(/g, '%28').replace(/\)/g, '%29');
    } catch { return ''; }
  };
  const plain = element => element.textContent || '';
  const blockTags = new Set(['P', 'DIV', 'SECTION', 'ARTICLE', 'FIGURE', 'FIGCAPTION', 'DL', 'DT', 'DD']);
  const ignored = new Set(['SCRIPT', 'STYLE', 'NAV', 'NOSCRIPT', 'TEXTAREA', 'INPUT', 'SELECT']);
  const activitySelector = '[data-message-author-role="tool"], [data-role="tool"], [data-testid*="tool-activity"], [data-testid*="reasoning"], [data-testid*="thought"]';
  const resourceNodes = new Set();
  const isActivity = node => !!node.closest(activitySelector);
  function render(node, depth = 0) {
    if (node.nodeType === Node.TEXT_NODE) return node.textContent || '';
    if (node.nodeType !== Node.ELEMENT_NODE || ignored.has(node.tagName) || !visible(node) || isActivity(node) || resourceNodes.has(node)) return '';
    const children = () => [...node.childNodes].map(child => render(child, depth)).join('');
    const text = () => children().trim();
    const tag = node.tagName;
    if (tag === 'BUTTON') {
      if (!node.matches('[data-testid*="citation"], [data-citation], .citation')) return '';
      const link = node.querySelector('a[href]');
      const href = safeLink(link?.getAttribute('href') || node.getAttribute('data-href') || node.getAttribute('data-url'));
      const label = (plain(node).trim() || node.getAttribute('aria-label') || '來源').replace(/\]/g, '\\]');
      return href ? `[${label}](${href})` : label;
    }
    if (tag === 'SVG' || tag === 'CANVAS' || tag === 'IFRAME') return '';
    if (tag === 'BR') return '\n';
    if (tag === 'HR') return '\n\n---\n\n';
    if (tag === 'PRE') {
      const code = node.querySelector('code') || node;
      const value = plain(code).replace(/\n$/, '');
      const runs = value.match(/`+/g) || [];
      const fence = '`'.repeat(Math.max(3, ...runs.map(run => run.length + 1)));
      const language = (code.className || '').match(/(?:^|\s)language-([\w+-]+)/)?.[1] || '';
      return `\n\n${fence}${language}\n${value}\n${fence}\n\n`;
    }
    if (node.classList.contains('katex')) {
      const formula = node.querySelector('annotation[encoding="application/x-tex"]');
      if (formula) {
        const display = node.parentElement?.classList.contains('katex-display');
        return display ? `\n\n$$\n${plain(formula)}\n$$\n\n` : `$${plain(formula)}$`;
      }
    }
    if (tag === 'CODE') {
      const value = plain(node);
      const fence = '`'.repeat(Math.max(1, ...(value.match(/`+/g) || []).map(run => run.length + 1)));
      const pad = /^[` ]|[` ]$/.test(value) ? ' ' : '';
      return `${fence}${pad}${value}${pad}${fence}`;
    }
    if (/^H[1-6]$/.test(tag)) return `\n\n${'#'.repeat(Number(tag[1]))} ${text()}\n\n`;
    if (tag === 'STRONG' || tag === 'B') return `**${text()}**`;
    if (tag === 'EM' || tag === 'I') return `*${text()}*`;
    if (tag === 'DEL' || tag === 'S') return `~~${text()}~~`;
    if (tag === 'A') {
      const href = safeLink(node.getAttribute('href') || '');
      const label = text() || node.getAttribute('aria-label') || '連結';
      return href ? `[${label.replace(/\]/g, '\\]')}](${href})` : label;
    }
    if (tag === 'IMG' || tag === 'VIDEO' || tag === 'AUDIO') return '';
    if (tag === 'PROGRESS' || tag === 'METER' || node.getAttribute('role') === 'progressbar') {
      const label = node.getAttribute('aria-label') || node.getAttribute('title') || '';
      const value = node.getAttribute('aria-valuetext') || node.getAttribute('aria-valuenow') || node.getAttribute('value');
      const maximum = node.getAttribute('aria-valuemax') || node.getAttribute('max');
      return value ? `${label}${label ? '：' : ''}${value}${maximum ? ' / ' + maximum : ''}` : text();
    }
    if (tag === 'BLOCKQUOTE') return '\n\n' + text().split('\n').map(line => `> ${line}`).join('\n') + '\n\n';
    if (tag === 'UL' || tag === 'OL') {
      const start = Number(node.getAttribute('start')) || 1;
      const lines = [...node.children].filter(child => child.tagName === 'LI').map((li, i) => {
        const value = [...li.childNodes].map(child => render(child, depth + 1)).join('').trim();
        const marker = tag === 'OL' ? `${start + i}. ` : '- ';
        return marker + value.replace(/\n/g, '\n' + ' '.repeat(marker.length));
      });
      return '\n\n' + lines.join('\n') + '\n\n';
    }
    if (tag === 'TABLE') {
      const rows = [...node.querySelectorAll('tr')].map(row => [...row.children]
        .filter(cell => ['TD', 'TH'].includes(cell.tagName))
        .map(cell => render(cell).trim().replace(/\n+/g, '<br>').replace(/\|/g, '\\|')));
      const width = Math.max(0, ...rows.map(row => row.length));
      if (!width) return '';
      const line = row => '| ' + Array.from({length: width}, (_, i) => row[i] || '').join(' | ') + ' |';
      return '\n\n' + [line(rows[0]), line(Array(width).fill('---')), ...rows.slice(1).map(line)].join('\n') + '\n\n';
    }
    if (blockTags.has(tag)) return '\n\n' + text() + '\n\n';
    return children();
  }
  const hash = async value => [...new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(value)))]
    .map(byte => byte.toString(16).padStart(2, '0')).join('');
  const messages = [];
  const ids = new Set();
  let resourceCount = 0;
  const elements = [...main.querySelectorAll('[data-message-author-role]')].filter(element => visible(element));
  for (const element of elements) {
    const role = element.getAttribute('data-message-author-role');
    if (!['user', 'assistant'].includes(role)) continue;
    const resources = [];
    const seenResources = new Set();
    const candidates = element.querySelectorAll('img, audio, video, canvas, iframe, svg, a[download], [data-testid*="attachment"][title], [data-testid*="file"][title]');
    for (const media of candidates) {
      if (!visible(media, true) || isActivity(media) || media.closest('.katex, button, nav')) continue;
      if (media.tagName.toUpperCase() === 'SVG' && !media.matches('[role="img"], .recharts-surface, .highcharts-root') &&
          Math.max(media.getBoundingClientRect().width, media.getBoundingClientRect().height) <= 40) continue;
      const kind = media.tagName === 'IMG' ? 'image' : ['CANVAS', 'svg'].includes(media.tagName) || media.tagName.toUpperCase() === 'SVG' ? 'chart' :
        media.matches('a[download], [data-testid*="attachment"], [data-testid*="file"]') ? 'attachment' : 'embed';
      const figure = media.closest('figure, [data-chart], [data-testid*="chart"]');
      const caption = figure?.querySelector('figcaption, h2, h3, h4')?.textContent?.trim();
      const descriptionId = media.getAttribute('aria-describedby');
      const description = descriptionId ? document.getElementById(descriptionId)?.textContent?.trim() : '';
      const fallback = {image: '圖片', chart: '圖表', attachment: '附件', embed: media.tagName === 'AUDIO' ? '音訊' : media.tagName === 'VIDEO' ? '影片' : '嵌入內容'}[kind];
      const name = media.getAttribute('download') || media.getAttribute('alt') || media.getAttribute('aria-label') || media.getAttribute('title') || caption || fallback;
      const label = [...[name, description && description !== name ? description : ''].filter(Boolean).join(' — ').replace(/\s+/g, ' ').trim()].slice(0, 300).join('');
      const reference = safeLink(media.getAttribute('href') || media.getAttribute('src')) || null;
      const resource = {kind, label, url: reference};
      const signature = JSON.stringify(resource);
      resourceNodes.add(media);
      if (seenResources.has(signature)) continue;
      if (resources.length >= 32 || resourceCount >= 128) throw new Error('圖表或附件太多，請縮小對話範圍後重新讀取。');
      seenResources.add(signature); resources.push(resource); resourceCount++;
    }
    const body = element.querySelector('.whitespace-pre-wrap') || element;
    // Rich cards may be siblings of .markdown, so the assistant root is the unit.
    let content = role === 'user' && body !== element ? plain(body).trim() : render(element).trim();
    // Preserve blank lines within fenced code; trim only the outer representation.
    content = content.replace(/\u00a0/g, ' ');
    if (!content && !resources.length) continue;
    const siteId = element.getAttribute('data-message-id') || element.closest('[data-message-id]')?.getAttribute('data-message-id');
    const id = siteId || `capture-local-${messages.length}-${(await hash(role + '\n' + content + '\n' + JSON.stringify(resources))).slice(0, 20)}`;
    if (ids.has(id)) throw new Error('對話正在更新，請重新讀取。');
    ids.add(id);
    messages.push({id, role, content, ...(resources.length ? {resources} : {})});
  }
  if (!messages.length) throw new Error('這個頁面還沒有可讀取的訊息。');
  const fingerprint = await hash(JSON.stringify(messages));
  if (location.href !== startUrl) throw new Error('對話已切換，請重新讀取。');
  const conversationId = url.pathname.match(/\/(?:c|share)\/([^/?#]+)/)?.[1] || null;
  // Query/fragment parameters are not needed for source identity.
  const sourceUrl = url.origin + url.pathname;
  const title = document.title.replace(/\s*[-–|]\s*ChatGPT\s*$/i, '').trim() || 'ChatGPT 對話';
  return {
    capture: {version: 1, provider: 'chatgpt', conversation_id: conversationId, title, url: sourceUrl,
      method: 'dom', scope: 'loaded', messages, adapter_version: '1', captured_at: new Date().toISOString()},
    fingerprint,
  };
}
