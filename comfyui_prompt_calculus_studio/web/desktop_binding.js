import {KEY, textWidget} from './state.js';

// Graph-local selection survives workflow serialization. Infer only an
// unambiguous positive path; never choose a negative prompt by node order.
export function workflowTarget(graph) {
    const nodes=graph?._nodes ?? [], valid=n=>n && !textWidget(n).error;
    const saved=nodes.find(n=>String(n.id)===String(graph?.extra?.prompt_studio_active));
    if (valid(saved)) return saved;
    const positive=new Set();
    function visit(node,seen=new Set()) {
        if (!node || seen.has(node)) return;
        seen.add(node);
        if (valid(node)) { positive.add(node); return; }
        for (let i=0;i<(node.inputs?.length ?? 0);i++) visit(node.getInputNode?.(i),seen);
    }
    for (const node of nodes) {
        const index=node.inputs?.findIndex(i=>i.name==='positive') ?? -1;
        if (index>=0) visit(node.getInputNode?.(index));
    }
    if (positive.size) return positive.size===1 ? [...positive][0] : null;
    const bound=nodes.filter(n=>valid(n) && n.properties?.[KEY]);
    return bound.length===1 ? bound[0] : null;
}

// Claim/release requests share one lane because backend ownership is scoped to
// the browser session. A late release must never revoke the new workflow.
export class DesktopHandoff {
    constructor(claim,release,changed) {
        this.claim=claim; this.release=release; this.changed=changed;
        this.version=0; this.held=null; this.current=null; this.desired=null; this.pending=Promise.resolve();
    }
    select(node) {
        if (node && this.desired===node) return this.pending;
        this.desired=node; const version=++this.version;
        this.current=null; this.changed(null);
        this.pending=this.pending.catch(()=>{}).then(async()=>{
            if (version!==this.version) return;
            if (this.held) { await this.release(); this.held=null; }
            if (!node || version!==this.version) return;
            const lease=await this.claim(node); this.held={node,lease};
            if (version!==this.version) { await this.release(); this.held=null; return; }
            this.current=this.held; this.changed(this.current);
        }).catch(error=>{
            if (version===this.version) this.desired=null;
            throw error;
        });
        return this.pending;
    }
}
