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
      ADD_TAGS:['audio','video','source'], ADD_ATTR:['controls','preload','data-math','data-diagram'],
      ADD_URI_SAFE_ATTR:['width','height','type'],
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
