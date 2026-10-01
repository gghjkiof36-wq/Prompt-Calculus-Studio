// Runtime widget projection and a single native submission's local transaction.
// Never infer widgets_values positions or construct an API prompt.
const SAFE=Number.MAX_SAFE_INTEGER, SEED_LIMIT=2**50;
const modes=['fixed','increment','decrement','randomize'];
const samplerClasses=new Set(['KSampler','KSamplerAdvanced','SamplerCustom','SamplerCustomAdvanced']);
const equal=(a,b)=>Object.is(a,b)||JSON.stringify(a)===JSON.stringify(b);
const primitive=v=>typeof v==='string'||typeof v==='boolean'||typeof v==='number'&&Number.isFinite(v);
const targetKey=p=>JSON.stringify([p.path,p.field]);

function definition(node,definitions) {return definitions?.[node.type]??node.constructor?.nodeData;}
function parameterSpec(spec) {
    const type=spec?.[0],options=spec?.[1];
    if(Array.isArray(type))return {type:'ENUM',choices:type};
    // Both native dropdown formats use the existing ENUM display/schema/value
    // contract. Remote, multi-select and automatic controls are not static lists.
    if(type==='COMBO'&&Array.isArray(options?.options)&&options.options.length&&options.remote==null&&
        [options.multi_select,options.multiselect,options.control_after_generate].every(v=>v==null||v===false)&&
        options.options.every(v=>typeof v==='string'||typeof v==='number'&&Number.isFinite(v)))
        return {type:'ENUM',choices:options.options};
    return {type};
}
function limits(spec,widget,seed) {
    const opts=spec[1]??{},live=widget?.options??{},value={};
    for(const name of ['min','max','step'])if(typeof opts[name]==='number')value[name]=opts[name];
    if(typeof live.min==='number')value.min=Math.max(value.min??-Infinity,live.min);
    if(typeof live.max==='number')value.max=Math.min(value.max??Infinity,live.max);
    if(seed)value.max=Math.min(value.max??SAFE,SEED_LIMIT);
    return value;
}

export function describeParameters(app,output,definitions={},seeds=null,capabilities=null) {
    const nodes=[],visited=new Set();
    const projectionReason=capabilities?.reason(app.rootGraph)??((app.rootGraph?._nodes??[]).some(n=>n.subgraph||n.isVirtualNode||
        (n.widgets??[]).some(w=>typeof w.serializeValue==='function'))?'此工作流含尚未適配的特殊序列化控制，參數僅供查看':'');
    function visit(graph,parent=[]) {
        if(!graph||visited.has(graph))return;visited.add(graph);
        for(const node of graph._nodes??[]) {
            const id=String(node.id),path=[...parent,id],spec=definition(node,definitions),inputs={...spec?.input?.required,...spec?.input?.optional};
            const serialized=parent.length?null:output?.[id];
            const fields=[];
            for(const widget of node.widgets??[]) {
                const name=widget.name,decl=inputs?.[name];
                if(typeof name!=='string'||!name)continue;
                // Native seed policy is a companion control, not a backend text
                // input. Keep it with its seed even when lifecycle proof fails.
                if(name==='control_after_generate'&&!decl&&(node.widgets??[]).some(w=>w.name==='seed'||w.name==='noise_seed'))continue;
                const linked=node.inputs?.find(p=>p.name===name&&p.link!=null);
                const raw=serialized?.inputs?.[name],{type,choices}=parameterSpec(decl);
                const seed=name==='seed'||name==='noise_seed',policy=seed&&!inputs.control_after_generate?node.widgets?.find(w=>w.name==='control_after_generate'):null;
                const seedControl=seed?seeds?.parameterControl?.(node,name):null,seedTiming=seed?seeds?.parameterTiming?.():null;
                const range=decl?limits(decl,widget,seed):{};
                let reason='';
                if(projectionReason)reason=projectionReason;
                else if(parent.length)reason='子圖尚未有可核對的原生欄位對應';
                else if(linked||Array.isArray(raw))reason='由連線供應';
                else if(!decl||!['INT','FLOAT','STRING','BOOLEAN','ENUM'].includes(type))reason='尚未適配的控制';
                else if(!serialized||serialized.class_type!==node.type||!primitive(raw))reason='此節點未包含可核對的提交欄位';
                else if(typeof widget.serializeValue==='function')reason='特殊序列化控制尚未適配';
                else if(!equal(raw,widget.value))reason='原生序列化與顯示值不同';
                else if(widget.beforeQueued!=null||widget.afterQueued!=null)reason='特殊提交回呼尚未適配';
                else if(!capabilities?.primitive(widget))reason='控制項來源或回呼已變更，尚未適配';
                else if(type==='INT'&&!Number.isSafeInteger(raw))reason='原生整數無法確認精確值';
                else if(seed&&(!policy||!seedControl||!modes.includes(policy.value)||!['before','after'].includes(seedTiming)))reason='原生種子生命週期尚未適配';
                else if(seed&&(raw<range.min||raw>range.max))reason='超過此原生種子控制可保真的範圍';
                const field={field:name,type:['INT','FLOAT','STRING','BOOLEAN','ENUM'].includes(type)?type:'STRING',
                    value:primitive(raw)?raw:primitive(widget.value)?widget.value:'',editable:!reason,reason,...range};
                if(type==='ENUM')field.choices=structuredClone(choices);
                if(type==='INT'&&range.step)field.enforce_step=true;
                if(seed&&policy){
                    field.seed_control=true;
                    if(typeof policy.value==='string')field.seed_control_mode=policy.value;
                    field.seed_control_reason=reason;
                    if(seedControl&&modes.includes(policy.value)&&['before','after'].includes(seedTiming)){
                        field.seed_mode=policy.value;field.seed_timing=seedTiming;
                    }
                }
                // Definition and runtime capability, never labels/positions/current values.
                field.schema='pcs-parameters-v1:'+JSON.stringify([node.type,spec?.python_module??'',name,field.type,
                    field.choices??null,range,widget.type??'',typeof widget.serializeValue,!!linked,
                    seed?field.seed_timing:null]);
                fields.push(field);
            }
            const sources=Object.entries(serialized?.inputs??{}).filter(([,v])=>Array.isArray(v)&&v.length===2)
                .map(([input,v])=>({input,node:String(v[0]),slot:v[1]}));
            nodes.push({id,path,class_type:node.type,title:String(node.title??node.type),mode:node.mode??0,
                category:samplerClasses.has(node.type)?'sampling':/Latent|VAEEncode|ImageScale/.test(node.type)?'latent':'other',
                fields,sources,module:spec?.python_module??'',frontend_required:true,
                reason:parent.length?'子圖欄位對應尚未適配':''});
            if(node.subgraph)visit(node.subgraph,path);
        }
    }
    visit(app.rootGraph);return {version:1,nodes,frontend_required:true,projection_reason:projectionReason};
}

function validValue(p,value) {
    const valid=p.type==='INT'?Number.isSafeInteger(value):p.type==='FLOAT'?typeof value==='number'&&Number.isFinite(value):
        p.type==='BOOLEAN'?typeof value==='boolean':p.type==='STRING'?typeof value==='string':p.choices?.some(v=>equal(v,value));
    if(!valid||typeof value==='number'&&((p.min!=null&&value<p.min)||(p.max!=null&&value>p.max)))
        throw new Error(`#${p.node} / ${p.field}：參數值或範圍無效。`);
    if(p.type==='INT'&&p.enforce_step&&p.step&&(value-(p.min??0))%p.step!==0)
        throw new Error(`#${p.node} / ${p.field}：不符合欄位步進。`);
}

export function parameterTransaction(app,command,description,guard,seeds,onProgress=()=>{},capabilities=null) {
    const config=command.parameters;
    if(!config?.patches?.length)return null;
    if(description.projection_reason)throw new Error(description.projection_reason);
    for(const node of app.rootGraph?._nodes??[])for(const widget of node.widgets??[])
        if(typeof widget.serializeValue==='function'&&!capabilities?.trusted(widget))throw new Error('工作流的特殊序列化能力已變更，未套用參數。');
    const id=config.identity,target=command.target??command.identity;
    if(config.version!==1||id?.frontend_id!==target.frontend_id||id?.origin?.path&&id.origin.path!==target.path)
        throw new Error('Stage 參數不屬於這份原生工作流。');
    const all=new Map(description.nodes.flatMap(n=>n.fields.map(f=>[targetKey({path:n.path,field:f.field}),{...f,node:n.id,path:n.path,class_type:n.class_type}])));
    const owned=new Set([...(command.texts??[]),...(command.images??[])].map(v=>JSON.stringify([v.node,v.field])));
    const entries=[],seen=new Set(),graph=app.rootGraph;
    // Validate the complete intention before mutating any field or calling hooks.
    for(const p of config.patches) {
        guard();const field=all.get(targetKey(p)),node=graph._nodes?.find(n=>String(n.id)===p.node),widget=node?.widgets?.find(w=>w.name===p.field);
        if(seen.has(targetKey(p))||p.path.length!==1||!field?.editable||field.schema!==p.schema||field.class_type!==p.class_type||!widget)
            throw new Error(`#${p.node} / ${p.field}：欄位定義、連線或序列化已變更。`);
        if(owned.has(JSON.stringify([p.node,p.field])))throw new Error(`#${p.node} / ${p.field}：由 PCS 輸入供應。`);
        if(!equal(field.value,p.base))throw new Error(`#${p.node} / ${p.field}：ComfyUI 同一欄位已被修改，請重新核對變更。`);
        validValue({...field,node:p.node},p.resolved??p.value);
        if(p.seed_mode&&(!modes.includes(p.seed_mode)||p.seed_timing!==field.seed_timing))throw new Error('原生種子政策或時機已變更。');
        entries.push({patch:p,node,widget,original:widget.value,last:widget.value,field});seen.add(targetKey(p));
    }
    let closed=false,lastProof=[],projected=false,afterCount=0,folds=[],serializeGraph=null;
    const sameNode=e=>app.rootGraph===graph&&graph._nodes?.includes(e.node)&&e.node.widgets?.includes(e.widget);
    const safeEntry=e=>sameNode(e)&&capabilities?.primitive(e.widget)&&e.widget.beforeQueued==null&&e.widget.afterQueued==null;
    const proof=output=>entries.map(e=>{
        guard();const {patch:p}=e,value=output?.[p.node]?.inputs?.[p.field];validValue({...e.field,node:p.node},value);
        if(!equal(value,e.last))throw new Error('原生參數序列化與套用值不同，未提交。');
        if(!p.seed_mode&&!equal(value,p.value)||p.seed_mode==='fixed'&&!equal(value,p.resolved??p.value))throw new Error('Stage 參數尚未正確套用，未提交。');
        return {node:p.node,path:p.path,class_type:p.class_type,field:p.field,schema:p.schema,requested:p.value,
            actual:value,...(p.seed_mode?{seed_mode:p.seed_mode,seed_timing:p.seed_timing}:{})};
    });
    return {
        apply(){
            guard();for(const e of entries){if(!safeEntry(e))throw new Error('參數控制項已變更，未套用。');e.last=e.patch.resolved??e.patch.value;}
            for(const e of entries)if(e.patch.seed_mode){
                const control=seeds.parameterControl(e.node,e.patch.field);
                e.control=control;e.policy=control.value;e.before=control.beforeQueued;e.after=control.afterQueued;
                const wrap=(original,after)=>function(...args){
                    if(closed)return;
                    guard();if(!safeEntry(e)||!equal(e.widget.value,e.original)||control.value!==e.policy)throw new Error('種子在提交期間已被修改。');
                    const prior=e.widget.value;e.widget.value=e.last;control.value=command.replay?'fixed':e.patch.seed_mode;
                    try {
                        const result=original.apply(this,args);
                        if(result&&typeof result.then==='function')throw new Error('非同步種子控制尚未適配。');
                        e.last=e.widget.value;
                        if(after&&++afterCount===entries.filter(v=>v.patch.seed_mode).length)onProgress();
                        return result;
                    } finally {
                        // Hook projection ends synchronously, before transport/UI awaits.
                        if(sameNode(e))e.widget.value=prior;
                        if(control.value===(command.replay?'fixed':e.patch.seed_mode))control.value=e.policy;
                    }
                };
                e.beforeWrapper=wrap(e.before,false);e.afterWrapper=wrap(e.after,true);
                control.beforeQueued=e.beforeWrapper;control.afterQueued=e.afterWrapper;
            }
        },
        enterSerialize(){
            guard();for(const e of entries)if(!safeEntry(e)||!equal(e.widget.value,e.original))throw new Error('參數在提交期間已被修改，未提交。');
            if(command.replay)for(const e of entries){
                const used=command.replay[e.patch.node]?.inputs?.[e.patch.field];
                if(e.patch.seed_mode&&Number.isSafeInteger(used))e.last=used;
            }
            projected=true;for(const e of entries)e.widget.value=e.last;
            // Verified DynamicPrompts providers return synchronously. Fold each
            // once, so native graphToPrompt never suspends with live overrides.
            // Its workflow serialization still sees the original template text.
            for(const node of graph._nodes??[])for(const [index,widget] of (node.widgets??[]).entries()){
                if(typeof widget.serializeValue!=='function')continue;
                if(!capabilities?.trusted(widget))throw new Error('特殊序列化能力已變更，未提交。');
                const original=widget.serializeValue,raw=widget.value,value=original.call(widget,undefined,index);
                if(value?.then)throw new Error('此序列化控制無法同步隔離，未提交。');
                folds.push({widget,original,raw,value});widget.serializeValue=undefined;widget.value=value;
            }
            if(folds.length){
                const original=graph.serialize;
                const wrapper=function(...args){
                    for(const f of folds)f.widget.value=f.raw;
                    try{return original.apply(this,args);}
                    finally{for(const f of folds)f.widget.value=f.value;}
                };
                serializeGraph={original,wrapper};graph.serialize=wrapper;
            }
        },
        leaveSerialize(){
            if(!projected)return;projected=false;
            if(serializeGraph&&graph.serialize===serializeGraph.wrapper)graph.serialize=serializeGraph.original;
            serializeGraph=null;
            for(const f of folds){
                if(f.widget.serializeValue===undefined)f.widget.serializeValue=f.original;
                if(equal(f.widget.value,f.value))f.widget.value=f.raw;
            }
            folds=[];
            for(const e of entries)if(sameNode(e)&&equal(e.widget.value,e.last))e.widget.value=e.original;
        },
        evidence(output){lastProof=proof(output);return structuredClone(lastProof);},
        // Reading already observed evidence is safe after timeout/cancellation.
        // Never run another hook or infer a missing after-hook value.
        finish(){return structuredClone(lastProof.map((v,i)=>({...v,...(entries[i].patch.seed_mode&&afterCount===entries.filter(e=>e.patch.seed_mode).length?{next_value:entries[i].last}:{})})));},
        restore(){
            if(closed)return;
            this.leaveSerialize();closed=true;
            for(const e of entries){
                if(e.control){
                    if(e.control.beforeQueued===e.beforeWrapper)e.control.beforeQueued=e.before;
                    if(e.control.afterQueued===e.afterWrapper)e.control.afterQueued=e.after;
                }
            }
            graph.setDirtyCanvas?.(true,true);
        }
    };
}
