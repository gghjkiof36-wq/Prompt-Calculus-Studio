// Portable workflow settings. No modular prompt editing in the browser.
export const TRANSFER_KEY='prompt_studio_v08';

export function editableFields(nodes) {
    return nodes.flatMap(node => ['text','text_g','text_l'].flatMap(field => {
        const input=node.inputs?.find(i=>i.name===field||i.widget?.name===field);
        const widget=node.widgets?.find(w=>w.name===field);
        return input?.link==null&&widget&&typeof widget.value==='string'&&widget.type!=='converted-widget'
            ? [{node:String(node.id),field,title:node.title||node.type,text:widget.value}] : [];
    }));
}

export function transferSettings(graph) {
    graph.extra??={};
    return graph.extra[TRANSFER_KEY]??= {id:crypto.randomUUID(),name:'桌面工作流',bindings:[]};
}

export function assignOutput(graph,output,node,field) {
    const settings=transferSettings(graph);
    if (!editableFields(graph._nodes??[]).some(v=>v.node===node&&v.field===field)) throw new Error('欄位已接線或不存在。');
    if (settings.bindings.some(b=>b.node===node&&b.field===field&&b.output!==output)) throw new Error('此欄位已由另一份輸出占用，請先解除原綁定。');
    settings.bindings=settings.bindings.filter(b=>b.output!==output);
    settings.bindings.push({workflow:settings.id,output,node,field});
}

export function exportTransfer(graph,apiGraph,workflow) {
    const settings=transferSettings(graph); const fields=editableFields(graph._nodes??[]);
    for (const b of settings.bindings) {
        if (!fields.some(f=>f.node===b.node&&f.field===b.field)) throw new Error(`綁定失效：#${b.node} / ${b.field}`);
    }
    return {format:'prompt_studio_workflow',version:1,id:settings.id,name:settings.name,
        graph:structuredClone(apiGraph),bindings:structuredClone(settings.bindings),workflow:structuredClone(workflow)};
}
