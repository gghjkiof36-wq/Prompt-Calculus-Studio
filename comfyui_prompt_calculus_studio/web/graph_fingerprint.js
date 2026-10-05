// ComfyUI graphToPrompt updates execution order, localized labels and frontend
// metadata. Preview layout also changes after a completed batch. None of these
// are execution inputs. Keep every other field, including arbitrary properties,
// links, modes, widget values, native identity and PCS ownership metadata.
export function graphFingerprint(workflow) {
    const value=JSON.parse(JSON.stringify(workflow));
    function graph(state) {
        if(state.extra){delete state.extra.ds;delete state.extra.frontendVersion;if(!Object.keys(state.extra).length)delete state.extra;}
        for(const node of state.nodes??[]) {
            delete node.order;delete node.pos;delete node.size;
            if(node.flags){delete node.flags.collapsed;if(!Object.keys(node.flags).length)delete node.flags;}
            for(const port of [...(node.inputs??[]),...(node.outputs??[])])delete port.localized_name;
        }
        for(const child of state.definitions?.subgraphs??[])graph(child);
    }
    graph(value);
    const canonical=v=>Array.isArray(v)?v.map(canonical):v&&typeof v==='object'?
        Object.fromEntries(Object.keys(v).sort().map(k=>[k,canonical(v[k])])):v;
    return JSON.stringify(canonical(value));
}
