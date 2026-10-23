'use strict';
let bridge, revision = 0, request = 0, projectedVersion;
const engines = new Map();
function loadEngine(name, source) {
  if (!engines.has(name)) {
    engines.set(name, new Promise((resolve, reject) => {
      const script = document.createElement('script');
      script.src = source;
      script.onload = resolve;
      script.onerror = () => { engines.delete(name); script.remove(); reject(new Error('無法載入內容顯示元件')); };
      document.head.append(script);
    }));
  }
  return engines.get(name);
}
const nextPaint = () => new Promise(resolve => {
  // Hidden export views may throttle animation frames.
  const fallback = setTimeout(resolve, 50);
  requestAnimationFrame(() => { clearTimeout(fallback); setTimeout(resolve, 0); });
});
// Dispatch only our versioned native actions. Unknown URL scheme policies
// differ across WebEngine versions; no resource navigation is needed here.
document.addEventListener('click', event => {
  const anchor=event.target.closest('a[href]');
  const href=anchor?.getAttribute('href');
  if (bridge && href?.startsWith('pcs-action:')) {
    event.preventDefault();bridge.activate(href.slice('pcs-action:'.length));
  }
});
// Source footnotes reuse the sanitized projection and native link actions.
const citationPopover = document.createElement('section');
citationPopover.id = 'pcs-citation-popover';
citationPopover.className = 'pcs-citation-popover';
citationPopover.setAttribute('role','dialog');
citationPopover.tabIndex = -1;
citationPopover.hidden = true;
document.body.append(citationPopover);
let citationTrigger, citationTimer, suppressCitationFocus = false;
const citationButton = target => target instanceof Element ? target.closest('#document .pcs-citation') : null;
const citationFocusables = () => [...citationPopover.querySelectorAll('a[href],button,input,summary,[tabindex="0"]')]
  .filter(element => !element.disabled && element.getClientRects().length);
function closeCitation(restoreFocus = false) {
  clearTimeout(citationTimer);
  const previous = citationTrigger;
  citationTrigger = null;
  previous?.setAttribute('aria-expanded','false');
  previous?.removeAttribute('aria-controls');
  citationPopover.hidden = true;
  citationPopover.replaceChildren();
  if (restoreFocus && previous?.isConnected) {
    suppressCitationFocus = true;previous.focus({preventScroll:true});suppressCitationFocus = false;
  }
}
function positionCitation() {
  if (!citationTrigger) return;
  const rect = citationTrigger.getBoundingClientRect(), margin = 12, gap = 8;
  if (rect.bottom < 0 || rect.top > innerHeight) { closeCitation();return; }
  citationPopover.style.maxHeight = Math.max(48,innerHeight - margin * 2)+'px';
  const card = citationPopover.getBoundingClientRect();
  const left = Math.max(margin,Math.min(rect.left,innerWidth-card.width-margin));
  let top = rect.bottom + gap;
  if (top + card.height > innerHeight-margin) top = rect.top-card.height-gap;
  top = Math.max(margin,Math.min(top,innerHeight-card.height-margin));
  citationPopover.style.left = left+'px';citationPopover.style.top = top+'px';
}
function openCitation(trigger) {
  clearTimeout(citationTimer);
  if (citationTrigger === trigger) return;
  const note = document.getElementById(trigger.dataset.citationTarget);
  if (!note?.classList.contains('pcs-citation-note')) return;
  closeCitation();citationTrigger = trigger;
  const content = note.cloneNode(true);
  // Cloned anchors must not shadow document/source-line identities.
  content.removeAttribute('id');content.querySelectorAll('[id]').forEach(node => node.removeAttribute('id'));
  content.querySelectorAll('a[data-citation-url]').forEach(anchor => {
    const url = document.createElement('span');url.className = 'pcs-citation-url';
    url.textContent = anchor.dataset.citationUrl;anchor.append(url);
  });
  citationPopover.replaceChildren(...content.childNodes);
  citationPopover.setAttribute('aria-label',trigger.textContent);
  const font = getComputedStyle(document.getElementById('document'));
  citationPopover.style.fontFamily = font.fontFamily;
  citationPopover.style.fontSize = font.fontSize;
  citationPopover.style.lineHeight = font.lineHeight;
  citationPopover.hidden = false;
  trigger.setAttribute('aria-expanded','true');trigger.setAttribute('aria-controls',citationPopover.id);
  positionCitation();
}
function deferCitationClose() {
  clearTimeout(citationTimer);
  citationTimer = setTimeout(() => {
    if (!citationPopover.matches(':hover') && !citationTrigger?.matches(':hover') &&
        !citationPopover.contains(document.activeElement) && document.activeElement !== citationTrigger) closeCitation();
  },180);
}
document.addEventListener('pointerover',event => {
  const trigger = citationButton(event.target);
  if (trigger) openCitation(trigger);
  else if (citationPopover.contains(event.target)) clearTimeout(citationTimer);
});
document.addEventListener('pointerout',event => {
  if (citationButton(event.target) || citationPopover.contains(event.target)) deferCitationClose();
});
document.addEventListener('focusin',event => {
  const trigger = citationButton(event.target);
  if (trigger && !suppressCitationFocus) openCitation(trigger);
  else if (!citationPopover.contains(event.target)) closeCitation();
});
document.addEventListener('focusout',event => {
  if (citationButton(event.target) || citationPopover.contains(event.target)) deferCitationClose();
});
document.addEventListener('pointerdown',event => {
  if (!citationButton(event.target) && !citationPopover.contains(event.target)) closeCitation();
});
document.addEventListener('click',event => {
  const trigger = citationButton(event.target);
  if (trigger) { event.preventDefault();openCitation(trigger); }
});
document.addEventListener('keydown',event => {
  const trigger = citationButton(event.target);
  if (event.key === 'Escape' && citationTrigger) {
    event.preventDefault();closeCitation(citationPopover.contains(document.activeElement));return;
  }
  if (trigger && (['Enter',' ','ArrowDown'].includes(event.key) || (event.key === 'Tab' && !event.shiftKey))) {
    event.preventDefault();openCitation(trigger);(citationFocusables()[0] || citationPopover).focus();return;
  }
  if (event.key === 'Tab' && citationPopover.contains(event.target)) {
    const links = citationFocusables(), first = links[0], last = links.at(-1);
    if (event.shiftKey && (event.target === first || event.target === citationPopover)) {
      event.preventDefault();closeCitation(true);
    } else if (!event.shiftKey && (event.target === last || !last)) {
      const all = [...document.querySelectorAll('#document a[href],#document button,#document input,#document summary')]
        .filter(element => !element.disabled && element.getClientRects().length);
      const next = all[all.indexOf(citationTrigger)+1];
      if (next) { event.preventDefault();closeCitation();next.focus(); }
      else closeCitation(true);
    }
  }
});
window.addEventListener('resize',positionCitation);
new ResizeObserver(() => positionCitation()).observe(citationPopover);
window.addEventListener('scroll',event => { if (!citationPopover.contains(event.target)) positionCitation(); },true);
window.addEventListener('blur',() => closeCitation());
document.documentElement.classList.add('citations-ready');
function selectionOffsets(root) {
  const selection=window.getSelection();
  if (!selection || !selection.rangeCount || !root.contains(selection.anchorNode) || !root.contains(selection.focusNode)) return null;
  const count=(node,offset)=>{const range=document.createRange();range.selectNodeContents(root);range.setEnd(node,offset);return range.toString().length;};
  return [count(selection.anchorNode,selection.anchorOffset),count(selection.focusNode,selection.focusOffset)];
}
function restoreSelection(root,offsets) {
  if (!offsets) return;
  const locate=offset=>{const walker=document.createTreeWalker(root,NodeFilter.SHOW_TEXT);let node,last;while(node=walker.nextNode()){last=node;if(offset<=node.length)return [node,offset];offset-=node.length;}return last?[last,last.length]:[root,0];};
  const a=locate(offsets[0]),b=locate(offsets[1]);window.getSelection().setBaseAndExtent(a[0],a[1],b[0],b[1]);
}
window.refreshDocument = function () {
  if (!bridge) return;
  const requestId = ++request;
  bridge.document(async raw => {
    if (requestId !== request) return;
    const data = JSON.parse(raw), root = document.getElementById('document'), y = window.scrollY;
    // The channel and loadFinished can both request the first document. A
    // repeated request must neither rebuild it nor cancel an active diagram.
    if (data.version === projectedVersion) return;
    closeCitation();
    projectedVersion = data.version;
    const ticket = ++revision;
    const selection=selectionOffsets(root);
    const folded=new Map([...root.querySelectorAll('details')].map((element,index)=>[index+':'+element.querySelector('summary')?.textContent,element.open]));
    const tokens = data.palette || {};
    for (const key of ['base','text','secondary','surface','border','accent','info']) document.documentElement.style.setProperty('--'+key, tokens[key] || '#888');
    root.style.fontFamily = '"'+data.font.replaceAll('"','')+'", "Microsoft JhengHei", system-ui, sans-serif'; root.style.fontSize = data.size+'pt'; root.style.maxWidth = data.width+'px';root.style.lineHeight = data.lineHeight+'%';
    document.documentElement.classList.toggle('light',!!data.light);
    document.documentElement.classList.toggle('note-preview',!!data.preview);
    // Adopt the sanitized nodes directly, avoiding a serialize/parse cycle.
    root.replaceChildren(DOMPurify.sanitize(data.html || '', {
      RETURN_DOM_FRAGMENT:true,
      ADD_TAGS:['audio','video','source'], ADD_ATTR:['controls','preload','data-math','data-diagram','min','max','value','colspan','rowspan','scope','start'],
      ADD_URI_SAFE_ATTR:['width','height','type','min','max','value','colspan','rowspan','scope','start','disabled','checked'],
      FORBID_TAGS:['script','iframe','object','embed','form','style','link'],
      ALLOWED_URI_REGEXP:/^(?:(?:file|data|pcs-action):|#)/i
    }));
    root.querySelectorAll('details').forEach((element,index)=>{const key=index+':'+element.querySelector('summary')?.textContent;if(folded.has(key))element.open=folded.get(key);});
    root.querySelectorAll('audio,video').forEach(media => media.addEventListener('error', () => {
      const notice = media.parentElement.querySelector('.media-error');if(notice) notice.hidden = false;
    }));
    // Show readable text before loading optional engines or laying out diagrams.
    restoreSelection(root,selection);window.scrollTo(0,y);
    await nextPaint();
    if (ticket !== revision) return;
    bridge.presented(data.version);
    const formulas = [...root.querySelectorAll('[data-math]')];
    const diagrams = [...root.querySelectorAll('[data-diagram]')];
    let mathError, diagramError;
    await Promise.all([
      formulas.length ? loadEngine('math','katex/katex.min.js').catch(error => { mathError=error; }) : null,
      diagrams.length ? loadEngine('diagram','mermaid/mermaid.min.js').catch(error => { diagramError=error; }) : null
    ]);
    if (ticket !== revision) return;
    formulas.forEach(element => {
      const value = data.math[Number(element.dataset.math)];
      try { if (mathError) throw mathError; katex.render(value.source, element, {displayMode:value.block,throwOnError:true,trust:false,strict:'warn',maxExpand:1000,maxSize:20}); }
      catch(error) { element.textContent = value.source; element.classList.add('render-error');element.title = '公式未能顯示；原文完整保留。'; }
    });
    if (diagrams.length && !diagramError) mermaid.initialize({startOnLoad:false,securityLevel:'strict',theme:'base',fontFamily:data.font,themeVariables:{darkMode:!data.light,primaryColor:tokens.surface,primaryTextColor:tokens.text,primaryBorderColor:tokens.secondary,lineColor:tokens.secondary},htmlLabels:false,flowchart:{htmlLabels:false},maxTextSize:50000,maxEdges:500,suppressErrorRendering:true});
    for (const element of diagrams) {
      if (ticket !== revision) return;
      const source = data.diagrams[Number(element.dataset.diagram)];
      try {
        if (diagramError) throw diagramError;
        const result = await mermaid.render('diagram-'+ticket+'-'+element.dataset.diagram,source);
        if (ticket !== revision) return;
        element.innerHTML = DOMPurify.sanitize(result.svg,{USE_PROFILES:{svg:true,svgFilters:true},FORBID_TAGS:['foreignObject','script'],FORBID_ATTR:['href','xlink:href']});
      } catch(error) { element.textContent = source;element.classList.add('render-error');element.title='圖表未能顯示；原文完整保留。'; }
    }
    bridge.finished('ready', data.version);
  });
};
new QWebChannel(qt.webChannelTransport,channel=>{bridge=channel.objects.content;window.refreshDocument();});
