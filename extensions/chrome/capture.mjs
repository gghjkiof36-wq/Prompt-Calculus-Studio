// This function is serialized by chrome.scripting; keep dependencies inside it.
// It reads the displayed document only. It never calls ChatGPT's internal APIs.
export async function captureLoadedConversation(returnErrors = false) {
 try {
  const startUrl = location.href;
  const url = new URL(startUrl);
  if (url.protocol !== 'https:' || !['chatgpt.com', 'chat.openai.com'].includes(url.hostname)) {
    throw new Error('請開啟 ChatGPT 對話。');
  }
  function locateConversation() {
    const visible = element => {
      for (let node=element;node;node=node.parentElement) {
        const style=getComputedStyle(node);
        if(node.hidden||node.getAttribute('aria-hidden')==='true'||style.display==='none'||style.visibility==='hidden')return false;
      }
      return true;
    };
    function messagesIn(root) {
      const modern=[];
      for(const element of root.querySelectorAll('[data-chatgpt-search-unit-key],[data-content-search-unit-key]')) {
        if(!visible(element)||element.closest('[data-pcs-ui]'))continue;
        const key=element.getAttribute('data-chatgpt-search-unit-key')||element.getAttribute('data-content-search-unit-key');
        const role=key?.match(/:(user|assistant)$/)?.[1];
        if(!role||role==='assistant'&&!element.querySelector('h4[data-conversation-role="assistant"]'))continue;
        const duplicate=modern.find(item=>item.key===key);
        if(duplicate) {
          if(duplicate.element.contains(element))continue;
          if(element.contains(duplicate.element)){duplicate.element=element;continue;}
          throw new Error('對話正在更新，請重新讀取。');
        }
        modern.push({element,role,key});
      }
      const legacy=[...root.querySelectorAll('[data-message-author-role]')].filter(element=>visible(element)&&!element.closest('[data-pcs-ui]'))
        .filter(element=>['user','assistant'].includes(element.getAttribute('data-message-author-role')))
        .filter(element=>!modern.some(item=>item.element.contains(element)||element.contains(item.element)))
        .map(element=>({element,role:element.getAttribute('data-message-author-role'),key:null}));
      return [...modern,...legacy].sort((a,b)=>a.element.compareDocumentPosition(b.element)&Node.DOCUMENT_POSITION_FOLLOWING?-1:1);
    }
    const regions=[...document.querySelectorAll('[role="region"][aria-label="對話"],[role="region"][aria-label="Conversation"]')].filter(visible);
    const mains=[...document.querySelectorAll('main,[role="main"]')].filter(visible);
    for(const roots of [regions,mains]) {
      const candidates=roots.map(main=>({main,messages:messagesIn(main)})).filter(item=>item.messages.length);
      candidates.sort((a,b)=>b.messages.length-a.messages.length);
      if(candidates.length)return candidates[0];
    }
    return {main:mains[0]||regions[0]||null,messages:[]};
  }

  const {main,messages:elements}=locateConversation();
  if (!main) throw new Error('尚未找到對話，請稍後重新讀取。');
  const visible = (element, media = false) => {
    for (let current = element; current && current !== document.body; current = current.parentElement) {
      if (current.hidden || (current.getAttribute('aria-hidden') === 'true' && !(media && current === element))) return false;
      const style = getComputedStyle(current);
      if (style.display === 'none' || style.visibility === 'hidden') return false;
    }
    return true;
  };
  const assertCurrent = () => {
    if(location.href !== startUrl || !main.isConnected) throw new Error('對話已切換，請重新讀取。');
    if([...main.querySelectorAll('[data-testid="stop-button"], [data-is-streaming="true"]')].some(element => visible(element)))
      throw new Error('ChatGPT 還在回覆，完成後再讀取。');
  };
  assertCurrent();
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
  const visualImages = new Map();
  const inlineVisuals = new Set();
  const citationNotes = new Map(), citationPopovers = new Set(), usedNotes = new Set();
  let baselineRender = false, citationCount = 0, citationWait = 0, citationImageBytes = 0;
  const isActivity = node => !!node.closest(activitySelector);
  const component = (node, name) => node.getAttribute?.('data-d-component') === name;
  const citationPill = node => component(node, 'popover-trigger') && node.hasAttribute('data-d-inline') &&
    !!node.querySelector('[data-d-component="badge"]');
  const citationLabel = node => [...(node.querySelector('[data-d-component="badge"]') || node).querySelectorAll('[data-d-component="text"]')]
    .filter(item => visible(item) && !isActivity(item) && !item.closest('[data-pcs-ui]') && !item.querySelector('[data-d-component="text"]'))
    .map(item => plain(item).trim()).filter(Boolean).join(' ');
  const citationHref = node => safeLink(node.getAttribute('href') || node.getAttribute('data-href') || node.getAttribute('data-url') ||
    [...node.querySelectorAll('a[href]')].find(link=>visible(link) && !link.closest('[data-d-component="favicon"]'))?.getAttribute('href'));
  const citationSelector = '[data-d-component="popover-trigger"][data-d-inline], [data-testid*="citation"], [data-citation], .citation';
  const citationNode = node => citationPill(node) || node.matches('[data-testid*="citation"], [data-citation], .citation');
  const singleLine = value => String(value || '').replace(/\s+/g,' ').trim();
  const markdownText = value => singleLine(value).replace(/[\\`*_[\]<>]/g,'\\$&');
  const markdownUrl = value => value.replace(/[<>]/g,char=>encodeURIComponent(char));
  const sourceUrl = url.origin + url.pathname;
  function linkedPopovers(trigger) {
    const ids = `${trigger.getAttribute('aria-controls') || ''} ${trigger.getAttribute('aria-describedby') || ''}`.trim().split(/\s+/).filter(Boolean);
    return [...new Set(ids.map(id=>document.getElementById(id)).filter(node=>node && node !== trigger &&
      node.matches('[role="dialog"], [role="tooltip"], [data-radix-popper-content-wrapper]')))]
      .filter(node=>{citationPopovers.add(node);const rect=node.getBoundingClientRect();return visible(node) && rect.width>0 && rect.height>0 && getComputedStyle(node).opacity!=='0';});
  }
  const sourceItems = popover => {
    const cards=[...popover.querySelectorAll('button[data-d-component="pressable"]')].filter(visible);
    return cards.length ? cards : [...popover.querySelectorAll('a[href]')].filter(node=>visible(node) && !node.closest('[data-d-component="favicon"]'));
  };
  const inSourcePopover = node => {for(let parent=node;parent;parent=parent.parentElement)if(citationPopovers.has(parent))return true;return false;};
  async function citationSources(trigger) {
    let popovers=linkedPopovers(trigger);
    const alreadyOpen=popovers.length>0 || trigger.getAttribute('aria-expanded')==='true' || trigger.getAttribute('data-state')==='open';
    let hovered=false;
    try {
      const otherOpen=[...main.querySelectorAll(citationSelector)].some(node=>node!==trigger && citationNode(node) && visible(node) &&
        (node.getAttribute('aria-expanded')==='true'||node.getAttribute('data-state')==='open'||linkedPopovers(node).length));
      if(!popovers.length && !alreadyOpen && !otherOpen && citationWait<4000 &&
          (citationPill(trigger)||trigger.hasAttribute('aria-controls')||trigger.hasAttribute('aria-describedby'))) {
        hovered=true;const began=performance.now(),deadline=began+Math.min(500,4000-citationWait);
        trigger.dispatchEvent(new MouseEvent('mouseover',{bubbles:true,relatedTarget:null}));
        trigger.dispatchEvent(new MouseEvent('mouseenter',{bubbles:false,relatedTarget:null}));
        while(true) {
          assertCurrent();
          if(!trigger.isConnected)throw new Error('對話正在更新，請重新讀取。');
          popovers=linkedPopovers(trigger);
          if(popovers.some(node=>sourceItems(node).length)||performance.now()>=deadline)break;
          await new Promise(resolve=>setTimeout(resolve,Math.min(25,Math.max(0,deadline-performance.now()))));
        }
        citationWait+=performance.now()-began;
      }
      const items=[...new Set(popovers.flatMap(sourceItems))];
      if(items.length>32)throw new Error('這組來源太多，請縮小對話範圍後重新讀取。');
      const results=[];
      for(const item of items) {
        assertCurrent();const before=plain(item);
        const texts=[...item.querySelectorAll('[data-d-component="text"], span[data-size], time')].filter(node=>visible(node) &&
          !node.querySelector('[data-d-component="text"], span[data-size], time'));
        const site=singleLine(texts.find(node=>node.getAttribute('data-size')==='xs')?.textContent || item.getAttribute('aria-label')?.replace(/^Open\s+/i,'') || '');
        const titleNode=texts.find(node=>node.getAttribute('data-weight')==='medium');
        const title=singleLine(titleNode?.textContent || item.querySelector('a[href]')?.textContent ||
          (item.tagName==='A' ? item.textContent : ''));
        const date=singleLine(texts.find(node=>node.tagName==='TIME' || node.getAttribute('data-size')==='sm' && node!==titleNode)?.textContent);
        if(!site&&!title&&!date)continue;
        if(site.length>300 || title.length>4000 || date.length>300)throw new Error('來源資訊太長，請縮小對話範圍後重新讀取。');
        const href=citationHref(item);let png='';
        const image=[...item.querySelectorAll('img')].find(node=>visible(node,true) && node.complete && node.naturalWidth &&
          !node.closest('[data-d-component="favicon"]') && node.getBoundingClientRect().width>32 && node.getBoundingClientRect().height>32);
        if(image && citationImageBytes<4000000){const captured=await visualPng(image);if(captured && citationImageBytes+captured.length<=4000000){png=captured;citationImageBytes+=png.length;}}
        assertCurrent();if(!item.isConnected || plain(item)!==before)throw new Error('來源正在更新，請重新讀取。');
        results.push({site,title,date,href,png});
      }
      if(!results.length){const href=citationHref(trigger);if(href){const label=citationLabel(trigger)||plain(trigger).trim()||trigger.getAttribute('aria-label')||'來源';results.push({site:label,title:label,date:'',href,png:''});}}
      return results;
    } finally {
      if(hovered && trigger.isConnected){trigger.dispatchEvent(new MouseEvent('mouseout',{bubbles:true,relatedTarget:document.body}));trigger.dispatchEvent(new MouseEvent('mouseleave',{bubbles:false,relatedTarget:document.body}));}
    }
  }
  function sourceDefinition(id,label,sources) {
    const items=sources.map(source=>{
      const site=markdownText(source.site||source.title||'來源'),title=markdownText(source.title||source.site||'來源');
      const lines=[`    - **${site}**${source.date?' · '+markdownText(source.date):''}  `,
        `      ${source.href ? `[${title}](${markdownUrl(source.href)})` : title}`];
      if(source.png)lines.push(`      ![${site}](${source.png})`);
      if(!source.href)lines.push(`      [回到原對話查看來源](${markdownUrl(sourceUrl)})`);
      return lines.join('\n');
    });
    return `[^${id}]: ${markdownText(label)}\n\n${items.join('\n\n')}`;
  }
  const htmlText = value => value.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/\r/g, '&#13;').replace(/\n/g, '&#10;');
  const progressText = node => {
    const label = node.getAttribute('aria-label') || node.getAttribute('title') || '';
    const value = node.getAttribute('aria-valuetext') || node.getAttribute('aria-valuenow') || node.getAttribute('value');
    const maximum = node.getAttribute('aria-valuemax') || node.getAttribute('max');
    return value ? `${label}${label ? '：' : ''}${value}${maximum ? ' / ' + maximum : ''}` : plain(node);
  };
  const listNumber = value => /^-?[0-9]{1,9}$/.test(value || '') ? value : '';
  // Read the source once, rather than concatenating an equation's accessible
  // MathML and its visual glyphs. Keep TeX characters and precision untouched.
  function mathSource(node) {
    if (!node.classList.contains('katex') && !['math', 'mjx-container'].includes(node.localName)) return null;
    const source = node.querySelector('annotation[encoding="application/x-tex"], annotation[encoding="application/x-latex"]');
    if (!source || !plain(source).trim()) return null;
    const math = node.localName === 'math' ? node : node.querySelector('math');
    const display = !!node.closest('.katex-display') || math?.getAttribute('display') === 'block' ||
      node.localName === 'mjx-container' && node.getAttribute('display') === 'true';
    return {text: plain(source), display};
  }

  // Preserve already displayed graphics as inert PNGs. No network requests,
  // page scripts or external SVG resources are copied into the saved file.
  async function visualPng(media) {
    const kind = media.tagName.toUpperCase();
    if (!['SVG', 'CANVAS', 'IMG'].includes(kind) || media.closest('table')) return '';
    const rect = media.getBoundingClientRect();
    const width = Math.ceil(rect.width), height = Math.ceil(rect.height);
    if (!(width > 0 && height > 0 && width <= 4096 && height <= 4096 && width * height <= 16000000)) return '';
    const canvas = document.createElement('canvas');
    canvas.width = width; canvas.height = height;
    const context = canvas.getContext('2d');
    if (!context) return '';
    let image = media, objectUrl;
    try {
      if (kind === 'SVG') {
        if (media.querySelectorAll('*').length > 20000) return '';
        const ns = 'http://www.w3.org/2000/svg';
        const tags = new Set(['svg','g','defs','clippath','mask','lineargradient','radialgradient','stop','pattern',
          'path','rect','circle','ellipse','line','polyline','polygon','text','tspan','title','desc','use','symbol','marker']);
        const attributes = new Set(['viewBox','preserveAspectRatio','d','points','x','y','dx','dy','width','height','rx','ry',
          'r','cx','cy','x1','x2','y1','y2','id','transform','gradientTransform','gradientUnits','spreadMethod',
          'offset','clipPathUnits','maskUnits','maskContentUnits','patternUnits','patternContentUnits','patternTransform',
          'markerWidth','markerHeight','refX','refY','orient','textLength','lengthAdjust','rotate']);
        const styles = ['fill','fill-opacity','fill-rule','stroke','stroke-width','stroke-opacity','stroke-dasharray',
          'stroke-dashoffset','stroke-linecap','stroke-linejoin','stroke-miterlimit','opacity','color','font-family',
          'font-size','font-weight','font-style','letter-spacing','word-spacing','text-anchor','dominant-baseline',
          'stop-color','stop-opacity','clip-path','mask','marker-start','marker-mid','marker-end'];
        const identifiers = new Set([...media.querySelectorAll('[id]'),media].map(el=>el.id).filter(Boolean));
        const localPaint = value => value.replace(/url\(([^)]+)\)/g, (_, raw) => {
          const reference = raw.trim().replace(/^['"]|['"]$/g,'');
          const resolved = new URL(reference, startUrl);
          const documentUrl = new URL(startUrl); documentUrl.hash = '';
          const fragment = resolved.hash.slice(1); resolved.hash = '';
          if (!reference.startsWith('#') && resolved.href !== documentUrl.href) throw new Error('external SVG resource');
          if (!/^[A-Za-z0-9_.:-]+$/.test(fragment) || !identifiers.has(fragment)) throw new Error('external SVG resource');
          return `url(#${fragment})`;
        });
        // Recharts uses HTML foreignObject for plain axis labels. Convert only
        // a single rendered text line to SVG text, keeping its measured position.
        // Complex HTML, images and interactive content remain unsupported.
        const copyHtmlLabel = node => {
          const descendants = [...node.querySelectorAll('*')];
          if (!descendants.length || descendants.length > 8 || descendants.some(child =>
            !['div','span'].includes(child.localName) || child.namespaceURI !== 'http://www.w3.org/1999/xhtml' ||
            [...child.attributes].some(attr => /^on/i.test(attr.name)))) throw new Error('complex SVG HTML');
          const textNodes = [];
          const walker = document.createTreeWalker(node, NodeFilter.SHOW_TEXT);
          while (walker.nextNode()) if (walker.currentNode.textContent.trim()) textNodes.push(walker.currentNode);
          if (textNodes.length !== 1) throw new Error('complex SVG label');
          const textNode = textNodes[0], owner = textNode.parentElement;
          const style = getComputedStyle(owner);
          const range = document.createRange(); range.selectNodeContents(textNode);
          const boxes = [...range.getClientRects()].filter(box => box.width && box.height);
          const matrix = node.parentElement.getScreenCTM();
          if (boxes.length !== 1 || !matrix || Math.abs(matrix.b) > 0.0001 || Math.abs(matrix.c) > 0.0001 || matrix.a <= 0 || matrix.d <= 0)
            throw new Error('complex SVG label layout');
          const box = boxes[0], inverse = matrix.inverse();
          const origin = new DOMPoint(box.left, box.top).matrixTransform(inverse);
          const target = document.createElementNS(ns, 'text');
          target.setAttribute('x', String(origin.x)); target.setAttribute('y', String(origin.y));
          target.setAttribute('dominant-baseline', 'text-before-edge');
          target.setAttribute('fill', localPaint(style.color));
          for (const name of ['font-family','font-weight','font-style']) target.setAttribute(name, style.getPropertyValue(name));
          target.setAttribute('font-size', style.fontSize);
          if (style.display === 'none' || style.visibility === 'hidden') target.setAttribute('display','none');
          target.textContent = textNode.textContent;
          return target;
        };
        const copy = node => {
          if (node.nodeType === Node.TEXT_NODE) return document.createTextNode(node.textContent || '');
          if (node.nodeType !== Node.ELEMENT_NODE) return null;
          const tag = node.localName.toLowerCase();
          if (['script','style','metadata'].includes(tag)) return null;
          if (tag === 'foreignobject') return copyHtmlLabel(node);
          if (!tags.has(tag)) throw new Error('unsupported SVG element');
          const target = document.createElementNS(ns,node.localName);
          for (const attribute of node.attributes) if (attributes.has(attribute.name)) target.setAttribute(attribute.name,attribute.value);
          const computed = getComputedStyle(node);
          if (computed.display === 'none' || computed.visibility === 'hidden') target.setAttribute('display','none');
          for (const name of styles) {
            const value = computed.getPropertyValue(name);
            if (value) target.setAttribute(name,localPaint(value));
          }
          if (tag === 'use') {
            const reference = node.getAttribute('href') || node.getAttribute('xlink:href') || '';
            if (!reference.startsWith('#') || !identifiers.has(reference.slice(1))) throw new Error('external SVG use');
            target.setAttribute('href',reference);
          }
          for (const child of node.childNodes) {const result=copy(child);if(result)target.appendChild(result);}
          return target;
        };
        const svg = copy(media);
        svg.setAttribute('xmlns',ns); svg.setAttribute('width',String(width)); svg.setAttribute('height',String(height));
        const serialized = new XMLSerializer().serializeToString(svg);
        if (serialized.length > 2000000) return '';
        objectUrl = URL.createObjectURL(new Blob([serialized],{type:'image/svg+xml'}));
        image = new Image();
        await new Promise((resolve,reject)=>{
          const timer=setTimeout(()=>{image.src='';reject(new Error('SVG image timeout'));},3000);
          image.onload=()=>{clearTimeout(timer);resolve();};
          image.onerror=()=>{clearTimeout(timer);reject(new Error('SVG image unavailable'));};
          image.src=objectUrl;
        });
        for(let parent=media;parent;parent=parent.parentElement) {
          const color=getComputedStyle(parent).backgroundColor;
          if(color && color!=='rgba(0, 0, 0, 0)' && color!=='transparent') {context.fillStyle=color;context.fillRect(0,0,width,height);break;}
        }
      } else if (kind === 'IMG' && (!media.complete || !media.naturalWidth)) return '';
      context.drawImage(image,0,0,width,height);
      const png = canvas.toDataURL('image/png');
      return png.startsWith('data:image/png;base64,') && png.length <= 1400000 ? png : '';
    } catch { return ''; }
    finally { if(objectUrl) URL.revokeObjectURL(objectUrl); }
  }
  const htmlTags = new Set(['TABLE', 'CAPTION', 'THEAD', 'TBODY', 'TFOOT', 'TR', 'TH', 'TD',
    'P', 'DIV', 'H1', 'H2', 'H3', 'H4', 'H5', 'H6', 'UL', 'OL', 'LI', 'BLOCKQUOTE',
    'STRONG', 'B', 'EM', 'I', 'U', 'S', 'DEL', 'CODE', 'PRE', 'BR', 'HR', 'SUP', 'SUB', 'SMALL', 'SPAN']);
  // Merged/nested tables cannot be represented by a Markdown pipe grid. Keep
  // their hierarchy and structural spans; never infer or repeat cell values.
  function safeHtml(node, card = false) {
    if (node.nodeType === Node.TEXT_NODE) return htmlText(node.textContent || '');
    if (node.nodeType !== Node.ELEMENT_NODE || citationPopovers.has(node) || node.closest('[data-pcs-ui], .turn-action-controls') ||
        node.matches('h4[data-conversation-role], h4.sr-only') || ignored.has(node.tagName) ||
        !visible(node) || isActivity(node) || resourceNodes.has(node)) return '';
    const tag = node.tagName.toUpperCase();
    const children = () => [...node.childNodes].map(child => safeHtml(child, card)).join('');
    if (citationPill(node)) {
      const label = htmlText(citationLabel(node) || plain(node).trim());
      const href = citationHref(node);
      return href ? `<a href="${htmlText(href).replace(/"/g, '&quot;')}">${label}</a>` : '〔' + label + '〕';
    }
    if (['SVG', 'CANVAS', 'IFRAME', 'IMG', 'VIDEO', 'AUDIO'].includes(tag)) return '';
    if (tag === 'PROGRESS' || tag === 'METER' || node.getAttribute('role') === 'progressbar') return htmlText(progressText(node));
    if (tag === 'BUTTON') {
      if (!node.matches('[data-testid*="citation"], [data-citation], .citation')) return '';
      const link = node.querySelector('a[href]');
      const href = safeLink(link?.getAttribute('href') || node.getAttribute('data-href') || node.getAttribute('data-url'));
      const label = htmlText(plain(node).trim() || node.getAttribute('aria-label') || '來源');
      return href ? `<a href="${htmlText(href).replace(/"/g, '&quot;')}">${label}</a>` : label;
    }
    const formula = mathSource(node);
    if (formula) return htmlText(`$${formula.text}$`);
    if (tag === 'A') {
      const href = safeLink(node.getAttribute('href'));
      const label = children() || htmlText(node.getAttribute('aria-label') || '連結');
      return href ? `<a href="${htmlText(href).replace(/"/g, '&quot;')}">${label}</a>` : label;
    }
    let attrs = '';
    if (tag === 'OL' && listNumber(node.getAttribute('start'))) attrs += ` start="${node.getAttribute('start')}"`;
    if (tag === 'LI' && listNumber(node.getAttribute('value'))) attrs += ` value="${node.getAttribute('value')}"`;
    if (tag === 'TH' || tag === 'TD') {
      if (node.rowSpan !== 1) attrs += ` rowspan="${node.rowSpan}"`;
      if (node.colSpan !== 1) attrs += ` colspan="${node.colSpan}"`;
      if (tag === 'TH' && ['row', 'col', 'rowgroup', 'colgroup'].includes(node.scope)) attrs += ` scope="${node.scope}"`;
    }
    if (tag === 'BR' || tag === 'HR') return `<${tag.toLowerCase()}>`;
    if (card && component(node, 'row')) return '<p>' + [...node.childNodes]
      .filter(item => item.nodeType === Node.TEXT_NODE ? item.textContent.trim() : item.nodeType === Node.ELEMENT_NODE && visible(item))
      .map(child => safeHtml(child, true).replace(/^<p>([\s\S]*)<\/p>$/, '$1')).join(' ') + '</p>';
    const value = children();
    if (node.hasAttribute('data-d-default-strong')) return '<strong>' + value + '</strong>';
    if (card && /^H[1-6]$/.test(tag)) return '<p><strong class="pcs-metric-value">' + value + '</strong></p>';
    if (htmlTags.has(tag)) return `<${tag.toLowerCase()}${attrs}>${value}</${tag.toLowerCase()}>`;
    return blockTags.has(tag) ? `<div>${value}</div>` : value;
  }
  // Known web component relationships become portable, inert HTML tables.
  // Do not copy site CSS or infer values from the length of a drawn bar.
  function structuredCard(node) {
    if (!['box', 'row'].includes(node.getAttribute('data-d-component')) || node.hasAttribute('data-d-inline')) return '';
    if ([...node.childNodes].some(item => item.nodeType === Node.TEXT_NODE && item.textContent.trim())) return '';
    if (node.querySelector('table,svg,canvas,iframe,pre,.katex,math')) return '';
    const items = [...node.children].filter(item => visible(item));
    if (!items.length || items.length > 20) return '';
    const valueTitle = item => [...item.querySelectorAll('[data-d-component="title"][data-d-tabular-nums="true"]')]
      .find(title => visible(title) && !isActivity(title) && !title.closest('[data-pcs-ui]'));
    const layout = getComputedStyle(node);
    const columns = layout.display === 'grid' ? layout.gridTemplateColumns.split(/\s+/).filter(Boolean).length :
      component(node, 'row') && layout.flexDirection === 'row' ? items.length : 0;
    if (columns >= 2 && columns <= 4 && items.length <= 8 && items.every(item => component(item, 'box') && valueTitle(item))) {
      const rows = [];
      for (let i = 0; i < items.length; i += columns) rows.push('<tr>' + items.slice(i, i + columns)
        .map(item => '<td>' + safeHtml(item, true) + '</td>').join('') + '</tr>');
      return '\n\n<table class="pcs-metric-grid"><tbody>' + rows.join('') + '</tbody></table>\n\n';
    }
    if (!component(node, 'box')) return '';
    const title = items.find(item => component(item, 'title') && item.hasAttribute('data-d-tabular-nums'));
    const baseline = items.find(item => component(item, 'row') && item.getAttribute('data-d-align') === 'baseline' && valueTitle(item));
    const track = items.find(item => component(item, 'box') && item.getAttribute('data-d-clip') === 'true' &&
      !plain(item).trim() && item.children.length === 1 && visible(item.firstElementChild));
    let meter = '';
    if (baseline && track) {
      const numerator = plain(valueTitle(baseline)).trim();
      const siblings = [...baseline.children].filter(item => visible(item) && !component(item, 'title'));
      const denominator = siblings.length === 1 ? plain(siblings[0]).trim().match(/^\/\s*([0-9]+(?:\.[0-9]+)?)$/)?.[1] : null;
      const ratio = track.firstElementChild.style.width.match(/^([0-9]+(?:\.[0-9]+)?)%$/)?.[1];
      const value = Number(numerator), max = Number(denominator);
      if (/^[0-9]+(?:\.[0-9]+)?$/.test(numerator) && denominator && ratio && max > 0 &&
          value >= 0 && value <= max && Math.abs(Number(ratio) / 100 - value / max) < 0.0001) {
        const danger = items.some(item => [...item.querySelectorAll('[data-d-component="badge"][data-color="danger"]')].some(badge => visible(badge)));
        meter = `<meter${danger ? ' class="pcs-meter-danger"' : ''} min="0" value="${numerator}" max="${denominator}">${numerator} / ${denominator}</meter>`;
      }
    }
    if (!(node.hasAttribute('data-d-has-border') && (title || baseline)) && !meter) return '';
    const rows = items.map(item => {
      if (component(item, 'divider')) return '<tr><td colspan="2"><hr></td></tr>';
      if (item === track) return meter ? '<tr><td colspan="2">' + meter + '</td></tr>' : '';
      const pair = [...item.children].filter(child => visible(child));
      if (component(item, 'row') && item.getAttribute('data-d-justify') === 'between' && pair.length === 2 &&
          ![...item.childNodes].some(child => child.nodeType === Node.TEXT_NODE && child.textContent.trim())) {
        return '<tr>' + pair.map(cell => '<td>' + safeHtml(cell, true) + '</td>').join('') + '</tr>';
      }
      return '<tr><td colspan="2">' + safeHtml(item, true) + '</td></tr>';
    });
    const tableClass = node.hasAttribute('data-d-has-border') ? 'pcs-summary-card' : 'pcs-score-card';
    return '\n\n<table class="' + tableClass + '"><tbody>' + rows.join('') + '</tbody></table>\n\n';
  }
  function render(node, depth = 0) {
    if (node.nodeType === Node.TEXT_NODE) return node.textContent || '';
    if (visualImages.has(node)) return inlineVisuals.has(node) ? visualImages.get(node) : '\n\n' + visualImages.get(node) + '\n\n';
    if (node.nodeType !== Node.ELEMENT_NODE || citationPopovers.has(node) || node.closest('[data-pcs-ui], .turn-action-controls') || node.matches('h4[data-conversation-role], h4.sr-only') || ignored.has(node.tagName) || !visible(node) || isActivity(node) || resourceNodes.has(node)) return '';
    if(!baselineRender && citationNotes.has(node)){const note=citationNotes.get(node);usedNotes.add(note);return `[^${note.id}]`;}
    const children = () => [...node.childNodes].map(child => render(child, depth)).join('');
    const text = () => children().trim();
    const tag = node.tagName;
    // An icon and its associated text form one content row. Leave paragraphs,
    // headings and exact text intact while attaching the small local image to
    // the first line; do not turn the entire row into a screenshot.
    if (component(node, 'row') && node.getAttribute('data-d-direction') === 'row' &&
        ![...node.childNodes].some(child => child.nodeType === Node.TEXT_NODE && child.textContent.trim())) {
      const items = [...node.children].filter(item => visible(item));
      if (items.length === 2) {
        const icons = [...items[0].querySelectorAll('svg')];
        const outsideText = document.createTreeWalker(items[0], NodeFilter.SHOW_TEXT);
        let hasText = false;
        while (outsideText.nextNode()) if (outsideText.currentNode.textContent.trim() && !outsideText.currentNode.parentElement.closest('svg')) { hasText = true; break; }
        if (!hasText && !items[0].querySelector('img,canvas,iframe,video,audio,a,button') && icons.length === 1 && inlineVisuals.has(icons[0])) {
          const icon = visualImages.get(icons[0]), value = render(items[1], depth).trim();
          if (value) return '\n\n' + (value.match(/^#{1,6} /) ? value.replace(/^(#{1,6} )/, '$1' + icon + ' ') : icon + ' ' + value) + '\n\n';
        }
      }
    }
    if (citationPill(node)) {
      const label = citationLabel(node) || plain(node).trim();
      const href = citationHref(node);
      return href ? `[${label.replace(/\]/g, '\\]')}](${href})` : `〔${label}〕`;
    }
    const card = structuredCard(node);
    if (card) return card;
    if (tag === 'BUTTON') {
      if (!node.matches('[data-testid*="citation"], [data-citation], .citation')) return '';
      const link = node.querySelector('a[href]');
      const href = safeLink(link?.getAttribute('href') || node.getAttribute('data-href') || node.getAttribute('data-url'));
      const label = (plain(node).trim() || node.getAttribute('aria-label') || '來源').replace(/\]/g, '\\]');
      return href ? `[${label}](${href})` : label;
    }
    if (tag === 'SVG' || tag === 'CANVAS' || tag === 'IFRAME') return '';
    if (tag === 'BR') return '\n';
    if (tag === 'HR' || component(node, 'divider') && !plain(node).trim() && !node.children.length) return '\n\n---\n\n';
    if (tag === 'PRE') {
      const code = node.querySelector('code') || node;
      const value = plain(code).replace(/\n$/, '');
      const runs = value.match(/`+/g) || [];
      const fence = '`'.repeat(Math.max(3, ...runs.map(run => run.length + 1)));
      const language = (code.className || '').match(/(?:^|\s)language-([\w+-]+)/)?.[1] || '';
      return `\n\n${fence}${language}\n${value}\n${fence}\n\n`;
    }
    const formula = mathSource(node);
    if (formula) return formula.display ? `\n\n$$\n${formula.text}\n$$\n\n` : `$${formula.text}$`;
    if (tag === 'CODE') {
      const value = plain(node);
      const fence = '`'.repeat(Math.max(1, ...(value.match(/`+/g) || []).map(run => run.length + 1)));
      const pad = /^[` ]|[` ]$/.test(value) ? ' ' : '';
      return `${fence}${pad}${value}${pad}${fence}`;
    }
    if (/^H[1-6]$/.test(tag)) return `\n\n${'#'.repeat(Number(tag[1]))} ${text()}\n\n`;
    if (tag === 'STRONG' || tag === 'B' || node.hasAttribute('data-d-default-strong')) {
      // The web renderer may put its strong marker inside a native <strong>.
      // One semantic emphasis must not become ambiguous ****Markdown****.
      return node.parentElement?.closest('strong,b,[data-d-default-strong]') ? text() : `**${text()}**`;
    }
    if (tag === 'EM' || tag === 'I') return `*${text()}*`;
    if (tag === 'DEL' || tag === 'S') return `~~${text()}~~`;
    // Keep the operator/chemical index, rather than joining it to the base.
    // PCS already understands these inert inline tags in writing and reading.
    if (tag === 'SUP' || tag === 'SUB') return safeHtml(node);
    if (tag === 'A') {
      const href = safeLink(node.getAttribute('href') || '');
      const label = text() || node.getAttribute('aria-label') || '連結';
      return href ? `[${label.replace(/\]/g, '\\]')}](${href})` : label;
    }
    if (tag === 'IMG' || tag === 'VIDEO' || tag === 'AUDIO') return '';
    if (tag === 'PROGRESS' || tag === 'METER' || node.getAttribute('role') === 'progressbar') {
      return progressText(node);
    }
    if (tag === 'BLOCKQUOTE') return '\n\n' + text().split('\n').map(line => `> ${line}`).join('\n') + '\n\n';
    if (tag === 'UL' || tag === 'OL') {
      let ordinal = listNumber(node.getAttribute('start')) ? Number(node.getAttribute('start')) : 1;
      const lines = [...node.children].filter(child => child.tagName === 'LI').map((li, i) => {
        const value = [...li.childNodes].map(child => render(child, depth + 1)).join('').trim();
        if (listNumber(li.getAttribute('value'))) ordinal = Number(li.getAttribute('value'));
        const marker = tag === 'OL' ? `${ordinal++}. ` : '- ';
        return marker + value.replace(/\n/g, '\n' + ' '.repeat(marker.length));
      });
      return '\n\n' + lines.join('\n') + '\n\n';
    }
    if (tag === 'TABLE') {
      const complex = [...node.querySelectorAll('td, th')].some(cell => cell.rowSpan !== 1 || cell.colSpan !== 1) || node.querySelector('table');
      if (complex) return '\n\n' + safeHtml(node) + '\n\n';
      const rows = [...node.querySelectorAll('tr')].filter(row => row.closest('table') === node && visible(row)).map(row => [...row.children]
        .filter(cell => ['TD', 'TH'].includes(cell.tagName) && visible(cell))
        .map(cell => render(cell).trim().replace(/\n+/g, '<br>').replace(/\|/g, '\\|'))).filter(row => row.length);
      const width = Math.max(0, ...rows.map(row => row.length));
      if (!width) return '';
      const line = row => '| ' + Array.from({length: width}, (_, i) => row[i] || '').join(' | ') + ' |';
      const caption = [...node.children].find(child => child.tagName === 'CAPTION' && visible(child));
      return '\n\n' + (caption ? render(caption).trim() + '\n\n' : '') +
        [line(rows[0]), line(Array(width).fill('---')), ...rows.slice(1).map(line)].join('\n') + '\n\n';
    }
    if (blockTags.has(tag)) return '\n\n' + text() + '\n\n';
    return children();
  }
  const hash = async value => [...new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(value)))]
    .map(byte => byte.toString(16).padStart(2, '0')).join('');
  const messages = [];
  const anchors = [];
  const ids = new Set();
  const messageStates=[];
  const baseline = element => {baselineRender=true;try{return render(element).trim();}finally{baselineRender=false;}};
  let resourceCount = 0;
  for (const [elementIndex,descriptor] of elements.entries()) {
    const {element,role}=descriptor;
    assertCurrent();
    if (!['user', 'assistant'].includes(role)) continue;
    if([...element.querySelectorAll('[data-dil-render-failed]')].some(node=>visible(node))) {
      throw new Error('這則回覆尚未載入，請在 ChatGPT 重新整理後再試。');
    }
    const triggers=[...element.querySelectorAll(citationSelector)].filter(node=>citationNode(node) && visible(node) &&
      !isActivity(node) && !node.closest('[data-pcs-ui], .turn-action-controls') && !node.parentElement.closest(citationSelector));
    for(const trigger of triggers)linkedPopovers(trigger);
    const resources = [];
    const seenResources = new Set();
    const candidates = element.querySelectorAll('img, audio, video, canvas, iframe, svg, a[download], [data-testid*="attachment"][title], [data-testid*="file"][title]');
    for (const media of candidates) {
      if (media.closest('[data-pcs-ui], .turn-action-controls') || inSourcePopover(media) || !visible(media, true) || isActivity(media) || media.closest('.katex, math, mjx-container, button, [role="button"], nav, [role="toolbar"], [data-d-component="favicon"]')) continue;
      const contentIcon = media.tagName.toUpperCase() === 'SVG' && component(media, 'icon') &&
        !media.closest('a') && Math.max(media.getBoundingClientRect().width, media.getBoundingClientRect().height) <= 40;
      if (media.tagName.toUpperCase() === 'SVG' && !media.matches('[role="img"], .recharts-surface, .highcharts-root') &&
          Math.max(media.getBoundingClientRect().width, media.getBoundingClientRect().height) <= 40 && !contentIcon) continue;
      const kind = media.tagName === 'IMG' || contentIcon ? 'image' : ['CANVAS', 'svg'].includes(media.tagName) || media.tagName.toUpperCase() === 'SVG' ? 'chart' :
        media.matches('a[download], [data-testid*="attachment"], [data-testid*="file"]') ? 'attachment' : 'embed';
      const figure = media.closest('figure, [data-chart], [data-testid*="chart"]');
      const caption = figure?.querySelector('figcaption, h2, h3, h4')?.textContent?.trim();
      const descriptionId = media.getAttribute('aria-describedby');
      const description = descriptionId ? document.getElementById(descriptionId)?.textContent?.trim() : '';
      const fallback = {image: '圖片', chart: '圖表', attachment: '附件', embed: media.tagName === 'AUDIO' ? '音訊' : media.tagName === 'VIDEO' ? '影片' : '嵌入內容'}[kind];
      const name = media.getAttribute('download') || media.getAttribute('alt') || media.getAttribute('aria-label') || media.getAttribute('title') || caption || (contentIcon ? '圖示' : fallback);
      const label = [...[name, description && description !== name ? description : ''].filter(Boolean).join(' — ').replace(/\s+/g, ' ').trim()].slice(0, 300).join('');
      const reference = safeLink(media.getAttribute('href') || media.getAttribute('src')) || null;
      if (kind === 'chart' || kind === 'image') {
        const png = await visualPng(media);
        if (png) {
          const escapedLabel = label.replace(/[\\`*_[\]<>]/g, '\\$&');
          const dimensions = contentIcon ? `|${Math.ceil(media.getBoundingClientRect().width)}x${Math.ceil(media.getBoundingClientRect().height)}` : '';
          visualImages.set(media, `![${escapedLabel}${dimensions}](${png})`);
          if (contentIcon) inlineVisuals.add(media);
          continue;
        }
      }
      const resource = {kind, label, url: reference};
      const signature = JSON.stringify(resource);
      resourceNodes.add(media);
      if (seenResources.has(signature)) continue;
      if (resources.length >= 32 || resourceCount >= 128) throw new Error('圖表或附件太多，請縮小對話範圍後重新讀取。');
      seenResources.add(signature); resources.push(resource); resourceCount++;
    }
    const body = element.querySelector('.whitespace-pre-wrap') || element;
    const sourceIds=(element.getAttribute('data-chatgpt-search-message-ids')||'').trim().split(/\s+/).filter(Boolean);
    const siteId = element.getAttribute('data-message-id') || element.closest('[data-message-id]')?.getAttribute('data-message-id') || (sourceIds.length===1?sourceIds[0]:null);
    const original=baseline(element);messageStates.push({element,original});
    const noteDigest=(await hash(sourceUrl+'\n'+(siteId||`${role}:${elementIndex}`)+'\n'+original)).slice(0,20);
    const notes=[];
    if(role!=='user'||body===element)for(const [index,trigger] of triggers.entries()) {
      // Existing safe HTML tables/cards keep ordinary links. Footnotes in HTML
      // cells are deliberately not projected as if the Markdown parser saw them.
      let htmlCell=false;
      for(let parent=trigger.parentElement;parent && parent!==element;parent=parent.parentElement)
        if(parent.tagName==='TABLE'||structuredCard(parent)){htmlCell=true;break;}
      if(htmlCell)continue;
      if(++citationCount>64)throw new Error('來源群組太多，請縮小對話範圍後重新讀取。');
      const sources=await citationSources(trigger);
      if(!sources.length)continue;
      const id=`pcs-source-${noteDigest}-${index+1}`;
      const label=citationLabel(trigger)||singleLine(plain(trigger))||trigger.getAttribute('aria-label')||'來源';
      const note={id,definition:sourceDefinition(id,label,sources)};citationNotes.set(trigger,note);notes.push(note);
    }
    assertCurrent();if(!element.isConnected || baseline(element)!==original)throw new Error('對話正在更新，請重新讀取。');
    // Rich cards may be siblings of .markdown, so the assistant root is the unit.
    let content = role === 'user' && body !== element ? plain(body).trim() : render(element).trim();
    const definitions=notes.filter(note=>usedNotes.has(note)).map(note=>note.definition);
    if(definitions.length)content+='\n\n'+definitions.join('\n\n');
    // Preserve source spacing in TeX, code, exact quantities and unit labels;
    // only the outer representation is trimmed, not NBSP within the content.
    if (!content && !resources.length) continue;
    const id = siteId || `capture-local-${messages.length}-${(await hash(role + '\n' + content + '\n' + JSON.stringify(resources))).slice(0, 20)}`;
    if (ids.has(id)) throw new Error('對話正在更新，請重新讀取。');
    ids.add(id);
    messages.push({id, role, content, ...(resources.length ? {resources} : {})});
    anchors.push({id,index:elementIndex});
  }
  if (!messages.length) throw new Error('這個頁面還沒有可讀取的訊息。');
  assertCurrent();
  const current=locateConversation().messages;
  if(current.length!==elements.length || current.some((item,index)=>item.element!==elements[index].element || item.role!==elements[index].role) ||
      messageStates.some(({element,original})=>!element.isConnected || baseline(element)!==original))throw new Error('對話正在更新，請重新讀取。');
  const fingerprint = await hash(JSON.stringify(messages));
  if (location.href !== startUrl) throw new Error('對話已切換，請重新讀取。');
  const conversationId = url.pathname.match(/\/(?:c|share)\/([^/?#]+)/)?.[1] || null;
  // Query/fragment parameters are not needed for source identity.
  const title = document.title.replace(/\s*[-–|]\s*ChatGPT\s*$/i, '').trim() || 'ChatGPT 對話';
  return {
    capture: {version: 1, provider: 'chatgpt', conversation_id: conversationId, title, url: sourceUrl,
      method: 'dom', scope: 'loaded', messages, adapter_version: '1', captured_at: new Date().toISOString()},
    fingerprint, anchors,
  };
 } catch (error) {
   if (!returnErrors) throw error;
   return {capture_error:{code:'capture_failed',message:error?.message || '讀取對話失敗，請重新整理頁面。'}};
 }
}
