// Read-only backend lookup; only identified bindings are written into the open graph.
export function workflowIdentity(app) {
    const graph=app.graph;
    const active=app.extensionManager?.workflow?.activeWorkflow;
    const path=active?.path;
    return {workflow:graph?.extra?.prompt_studio_v08?.id??'',
        path:typeof path==='string'&&path.startsWith('workflows/')?path.slice(10):'',
        frontend_id:active?.activeState?.id??graph?.id??''};
}

function widgetFor(graph,item) {
    const node=graph?._nodes?.find(n=>String(n.id)===item.node);
    const port=node?.inputs?.find(p=>p.name===item.field||p.widget?.name===item.field);
    const widget=node?.widgets?.find(w=>w.name===item.field);
    return node?.type===item.class_type&&port?.link==null&&widget?.type!=='converted-widget'&&typeof widget?.value==='string'?widget:null;
}

export function captureFields(graph) {
    const values=new Map(),targets=new Map();
    for(const node of graph?._nodes??[])for(const widget of node.widgets??[]) {
        const key=`${node.id}/${widget.name}`;
        values.set(key,widget.value);targets.set(key,{node,widget});
    }
    values.targets=targets;
    return values;
}

export function applyLiveState(app,value,before,record) {
    record.source=null;
    if (!value){record.fields=new Map();record.edits=new Map();record.liveOwner=null;record.restorePending=null;return '';}
    const graph=app.graph;
    if(record.restorePending) {
        if(value.owner===record.restorePending.owner&&value.source_revision!==record.restorePending.source_revision)
            return '保留重開的網頁文字；PCS 來源已變，請重新確認欄位歸屬。';
        record.restorePending=null;
    }
    if(record.liveOwner&&record.liveOwner!==value.owner){record.fields=new Map();record.edits=new Map();}
    record.liveOwner=value.owner;
    record.fields??=new Map();
    record.edits??=new Map();
    const retained=new Set(),updates=[];let conflict=false;
    for(const item of value.texts??[]) {
        const key=`${item.node}/${item.field}`,field=key;
        if(retained.has(key)){conflict=true;continue;}
        retained.add(key);
        const node=graph?._nodes?.find(n=>String(n.id)===item.node),widget=widgetFor(graph,item);
        const captured=before.targets?.get(field),stored=record.fields.get(key),edited=record.edits.get(key);
        const prior=stored?.node===node&&stored.widget===widget&&stored.class_type===item.class_type
            &&(stored.restored||stored.binding===item.binding)?stored:null;
        const explicit=edited?.node===node&&edited.widget===widget&&edited.text===widget?.value
            &&(!edited.binding||edited.binding===item.binding);
        const controlled=item.text_source==='pcs'||item.text_source==='web';
        const manual=controlled?item.text_source==='web':explicit||(prior?.mode==='manual'&&prior.text===widget?.value);
        if(!widget||captured?.node!==node||captured?.widget!==widget||typeof item.text!=='string'
            ||(!manual&&(widget.value!==before.get(field)
                ||(!controlled&&widget.value!==item.text&&(!prior||prior.mode!=='pcs'||widget.value!==prior.applied))))) {
            conflict=true;
            // Preserve unknown/concurrent changes without inventing manual
            // ownership. Only a trusted, field-specific input can establish it.
            record.fields.delete(key);
            continue;
        }
        if(!manual&&widget.value!==item.text)updates.push({widget,item});
        record.fields.set(key,{node,widget,class_type:item.class_type,binding:item.binding,
            applied:item.text,text:manual?widget.value:item.text,mode:manual?'manual':'pcs'});
        record.edits.delete(key);
    }
    for(const key of record.fields.keys())if(!retained.has(key))record.fields.delete(key);
    for(const key of record.edits.keys())if(!retained.has(key))record.edits.delete(key);
    if(updates.length) {
        graph.beforeChange?.();
        for(const {widget,item} of updates)widget.value=item.text;
        graph.afterChange?.();graph.setDirtyCanvas?.(true,true);
    }
    if(!conflict&&typeof value.owner==='string'&&value.owner&&/^[0-9a-f]{64}$/.test(value.source_revision??'')
        &&Array.isArray(value.texts)) {
        record.source={owner:value.owner,source_revision:value.source_revision,texts:value.texts.map(item=>{
            const field=record.fields.get(`${item.node}/${item.field}`);
            return {node:item.node,field:item.field,class_type:item.class_type,text:field.text,mode:field.mode};
        })};
    }
    return conflict?'保留網頁目前文字；來源或手改歸屬尚未確認。':value.texts?.length?`已同步綁定文字 · ${value.name}`:'';
}

export function applyRunState(app,value,before,record,api) {
    if (!value){applyLiveState(app,null,before,record);return '';}
    const live=Object.hasOwn(value,'live');
    if(!live)applyLiveState(app,null,before,record);
    const liveMessage=live?applyLiveState(app,value.live,before,record):'';
    if (!value.prompt_id)return liveMessage||value.live_error||'';
    // History is evidence of a past execution, not an edit or a fresh native
    // event. Replaying it here overwrote newer widget values and image batches.
    // ComfyUI owns its live executing/progress/executed event stream.
    return liveMessage||value.live_error||`${value.name} · ${value.phase==='running'?'執行中':value.phase==='queued'?'等待中':value.phase==='failed'?'失敗':'最近任務'} · ${value.prompt_id}`;
}

export function watchWorkflowRuns(app,api,request,notice,withSync=fn=>fn(),paused=()=>false) {
    let pending=false,serial=0,failed=false,stopped=false;
    const records=new WeakMap();
    const rootReady=()=>!stopped&&app.graph&&(!app.rootGraph||app.graph===app.rootGraph)&&!app.configuringGraph;
    function currentRecord(create=false) {
        if(!rootReady())return null;
        const graph=app.graph,key=JSON.stringify(workflowIdentity(app)),workflow=app.extensionManager?.workflow?.activeWorkflow;
        let record=records.get(graph);
        if(!record||record.key!==key||record.workflow!==workflow||record.version!==serial) {
            if(!create)return null;
            record={key,workflow,version:serial,fields:new Map(),edits:new Map()};records.set(graph,record);
        }
        return record;
    }
    function sourceReceipt() {
        if(!app.rootGraph||app.graph!==app.rootGraph)return null;
        const record=currentRecord(),source=record?.source;
        if(!source)return null;
        for(const item of source.texts) {
            const field=record.fields.get(`${item.node}/${item.field}`),widget=widgetFor(app.graph,item);
            const node=app.graph._nodes.find(n=>String(n.id)===item.node);
            if(!field||field.node!==node||field.widget!==widget||widget.value!==item.text||field.mode!==item.mode)return null;
        }
        return structuredClone(source);
    }
    function markEdited(event) {
        if(event?.isTrusted!==true||event.isComposing||event.type!=='input'||!event.target||!rootReady()||!app.rootGraph)return false;
        const targets=[];
        for(const node of app.graph._nodes??[])for(const widget of node.widgets??[]) {
            const element=widget.element??widget.inputEl;
            if(typeof widget.value!=='string'||!element||!(element===event.target||element.contains?.(event.target)))continue;
            targets.push({node,widget});
        }
        if(targets.length!==1)return false;
        const record=currentRecord(true),{node,widget}=targets[0];
        // A fresh explicit edit may establish new ownership, but cannot
        // validate other fields from a restored, unconfirmed source.
        if(record.restorePending){record.fields=new Map();record.restorePending=null;}
        const key=`${node.id}/${widget.name}`,prior=record.fields.get(key);
        record.edits.set(key,{node,widget,text:widget.value,binding:prior?.binding});
        record.source=null;queueMicrotask(refresh);
        return true;
    }
    function restoreSourceReceipt(receipt,key) {
        const record=currentRecord(true),identity=workflowIdentity(app);
        if(!record||!app.rootGraph)return false;
        record.source=null;
        if(!receipt||!key||identity.frontend_id!==key.frontend_id||identity.path!==key.path
            ||receipt.owner!==`${key.library_id}/${key.workspace_id}/${key.workflow_id}`
            ||!/^[0-9a-f]{64}$/.test(receipt.source_revision??'')||!Array.isArray(receipt.texts))return false;
        const fields=new Map();
        for(const item of receipt.texts) {
            const widget=widgetFor(app.graph,item),node=app.graph._nodes.find(n=>String(n.id)===item.node);
            const field=`${item.node}/${item.field}`;
            if(!widget||widget.value!==item.text||!['pcs','manual'].includes(item.mode)||fields.has(field))return false;
            fields.set(field,{node,widget,class_type:item.class_type,applied:item.text,text:item.text,mode:item.mode,restored:true});
        }
        record.fields=fields;record.edits=new Map();record.liveOwner=receipt.owner;
        record.restorePending={owner:receipt.owner,source_revision:receipt.source_revision};
        return true; // Ownership only; a new live-state response must confirm the source.
    }
    async function refresh() {
        if(stopped||pending||paused()||document.hidden||!app.graph||app.configuringGraph)return;
        // Root execution IDs must not address a different node inside an open subgraph.
        if(app.rootGraph&&app.graph!==app.rootGraph)return;
        const graph=app.graph,identity=workflowIdentity(app),key=JSON.stringify(identity),version=serial,before=captureFields(graph);
        const workflow=app.extensionManager?.workflow?.activeWorkflow;
        if(!identity.workflow&&!identity.path)return;
        pending=true;
        try {
            const value=await request('workflow/run-state',identity);
            if(!rootReady()||paused()||version!==serial||graph!==app.graph||key!==JSON.stringify(workflowIdentity(app))
                ||workflow!==app.extensionManager?.workflow?.activeWorkflow)return;
            const record=currentRecord(true);
            const message=withSync(()=>applyRunState(app,value,before,record,api));
            if(message&&record.message!==message){notice(message);record.message=message;}
            failed=false;
        } catch(error) {
            const record=records.get(graph);if(record)record.source=null;
            if(!failed){notice('網頁同步未完成：'+error.message,true);failed=true;}
        } finally {pending=false;}
    }
    const timer=setInterval(refresh,500);
    api.addEventListener('execution_success',refresh);
    api.addEventListener('status',refresh);
    document.addEventListener('visibilitychange',refresh);
    return {refresh,sourceReceipt,markEdited,restoreSourceReceipt,
        changed(){serial++;queueMicrotask(refresh);},stop(){stopped=true;serial++;clearInterval(timer);
            api.removeEventListener?.('execution_success',refresh);api.removeEventListener?.('status',refresh);
            document.removeEventListener?.('visibilitychange',refresh);}};
}
