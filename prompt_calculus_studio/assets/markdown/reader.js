'use strict';
let bridge, revision = 0;
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
  const ticket = ++revision;
  bridge.document(async raw => {
    if (ticket !== revision) return;
    const data = JSON.parse(raw), root = document.getElementById('document'), y = window.scrollY;
    const selection=selectionOffsets(root);
    const folded=new Map([...root.querySelectorAll('details')].map((element,index)=>[index+':'+element.querySelector('summary')?.textContent,element.open]));
    const tokens = data.palette || {};
    for (const key of ['base','text','secondary','surface','border','accent','info']) document.documentElement.style.setProperty('--'+key, tokens[key] || '#888');
    root.style.fontFamily = '"'+data.font.replaceAll('"','')+'", "Microsoft JhengHei", system-ui, sans-serif'; root.style.fontSize = data.size+'pt'; root.style.maxWidth = data.width+'px';root.style.lineHeight = data.lineHeight+'%';
    document.documentElement.classList.toggle('light',!!data.light);
    document.documentElement.classList.toggle('note-preview',!!data.preview);
    root.innerHTML = DOMPurify.sanitize(data.html || '', {
      ADD_TAGS:['audio','video','source'], ADD_ATTR:['controls','preload','data-math','data-diagram'],
      ADD_URI_SAFE_ATTR:['width','height','type'],
      FORBID_TAGS:['script','iframe','object','embed','form','style','link'],
      ALLOWED_URI_REGEXP:/^(?:(?:file|data|pcs-action):|#)/i
    });
    root.querySelectorAll('details').forEach((element,index)=>{const key=index+':'+element.querySelector('summary')?.textContent;if(folded.has(key))element.open=folded.get(key);});
    root.querySelectorAll('audio,video').forEach(media => media.addEventListener('error', () => {
      const notice = media.parentElement.querySelector('.media-error');if(notice) notice.hidden = false;
    }));
    root.querySelectorAll('[data-math]').forEach(element => {
      const value = data.math[Number(element.dataset.math)];
      try { katex.render(value.source, element, {displayMode:value.block,throwOnError:true,trust:false,strict:'warn',maxExpand:1000,maxSize:20}); }
      catch(error) { element.textContent = value.source; element.classList.add('render-error');element.title = '公式未能顯示；原文完整保留。'; }
    });
    mermaid.initialize({startOnLoad:false,securityLevel:'strict',theme:'base',fontFamily:data.font,themeVariables:{darkMode:!data.light,primaryColor:tokens.surface,primaryTextColor:tokens.text,primaryBorderColor:tokens.secondary,lineColor:tokens.secondary},htmlLabels:false,flowchart:{htmlLabels:false},maxTextSize:50000,maxEdges:500,suppressErrorRendering:true});
    for (const element of root.querySelectorAll('[data-diagram]')) {
      if (ticket !== revision) return;
      const source = data.diagrams[Number(element.dataset.diagram)];
      try {
        const result = await mermaid.render('diagram-'+ticket+'-'+element.dataset.diagram,source);
        if (ticket !== revision) return;
        element.innerHTML = DOMPurify.sanitize(result.svg,{USE_PROFILES:{svg:true,svgFilters:true},FORBID_TAGS:['foreignObject','script'],FORBID_ATTR:['href','xlink:href']});
      } catch(error) { element.textContent = source;element.classList.add('render-error');element.title='圖表未能顯示；原文完整保留。'; }
    }
    restoreSelection(root,selection);window.scrollTo(0,y);
    bridge.finished('ready');
  });
};
new QWebChannel(qt.webChannelTransport,channel=>{bridge=channel.objects.content;window.refreshDocument();});
