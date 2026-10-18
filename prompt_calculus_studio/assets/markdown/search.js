'use strict';
// Ranges and CSS highlights are view state. The rendered DOM and source stay intact.
(() => {
  let state = {query:'',caseSensitive:false,root:null,first:null,ranges:[],index:-1};
  window.markSearch = (query, caseSensitive, direction=0) => {
    const root = document.querySelector('main');
    if (!root || !CSS.highlights) return JSON.stringify({active:0,total:0});
    if (!query) {
      CSS.highlights.delete('find-matches');CSS.highlights.delete('find-current');
      state.query='';state.ranges=[];state.index=-1;
      return JSON.stringify({active:0,total:0});
    }
    if (query!==state.query || caseSensitive!==state.caseSensitive || root!==state.root || root.firstChild!==state.first) {
      const nodes=[],chunks=[];let length=0,previousBlock=null;
      const walker=document.createTreeWalker(root,NodeFilter.SHOW_TEXT);
      let node;
      while (node=walker.nextNode()) {
        const parent=node.parentElement;
        if (!parent || parent.closest('script,style,[hidden],.katex-mathml') || !parent.getClientRects().length) continue;
        const block=parent.closest('p,li,h1,h2,h3,h4,h5,h6,pre,td,th,summary,blockquote');
        if (previousBlock && block!==previousBlock) {chunks.push('\n');length++;}
        previousBlock=block;
        nodes.push({node,start:length,end:length+node.length});chunks.push(node.data);length+=node.length;
      }
      const text=chunks.join(''),ranges=[];
      const pattern=new RegExp(query.replace(/[.*+?^${}()|[\]\\]/g,'\\$&'),caseSensitive?'gu':'giu');
      let a=0,b=0;
      for (const match of text.matchAll(pattern)) {
        const start=match.index,end=start+match[0].length;
        while (a<nodes.length && nodes[a].end<=start) a++;
        b=Math.max(a,b);while (b<nodes.length && nodes[b].end<end) b++;
        if (!nodes[a] || !nodes[b] || start<nodes[a].start || end>nodes[b].end) continue;
        const range=new Range();range.setStart(nodes[a].node,start-nodes[a].start);range.setEnd(nodes[b].node,end-nodes[b].start);ranges.push(range);
      }
      state={query,caseSensitive,root,first:root.firstChild,ranges,index:-1};
    }
    const ranges=state.ranges;
    if (direction && ranges.length) {
      state.index=state.index<0 ? (direction<0?ranges.length-1:0) : (state.index+direction+ranges.length)%ranges.length;
      const rect=ranges[state.index].getBoundingClientRect();
      if (rect.top<0 || rect.bottom>innerHeight) scrollBy(0,rect.top-innerHeight/2);
    }
    CSS.highlights.set('find-matches',new Highlight(...ranges));
    const active=state.index>=0 && ranges[state.index] ? [ranges[state.index]] : [];
    const current=new Highlight(...active);current.priority=1;CSS.highlights.set('find-current',current);
    return JSON.stringify({active:state.index+1,total:ranges.length});
  };
})();
