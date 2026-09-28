// Observe native calls without comparing compiled source strings or changing seeds.
// New typed queues use app.queuePrompt, which owns all before/after callbacks.

export function createSeedObserver(setting) {
    const records=new WeakMap();
    function observe(node,{loaded=false}={}) {
        if(node.type!=='KSampler'||records.has(node))return;
        const seed=node.widgets?.find(w=>w.name==='seed');
        const control=node.widgets?.find(w=>w.name==='control_after_generate');
        if(!seed||!control||typeof control.beforeQueued!=='function'||typeof control.afterQueued!=='function')return;
        const original=control.beforeQueued,after=control.afterQueued;
        const record={seed,control,original,after,hasExecuted:loaded?null:false};
        record.before=function(...args){const result=original.apply(this,args);record.hasExecuted=true;return result;};
        control.beforeQueued=record.before;records.set(node,record);
    }
    function checked(node) {
        const r=records.get(node);
        if(!r)throw new Error('尚未觀察到此採樣器的原生種子回呼；請使用網頁原生執行。');
        if(r.control.beforeQueued!==r.before||r.control.afterQueued!==r.after
            ||!node.widgets.includes(r.seed)||!node.widgets.includes(r.control))
            throw new Error('觀察後種子回呼已變更，無法確認舊快照狀態；請使用網頁原生執行。');
        return r;
    }
    function capture(graph) {
        const samplers=graph._nodes.filter(n=>n.type==='KSampler');
        if(samplers.length!==1)throw new Error('背景執行目前需要一個標準 KSampler。');
        const node=samplers[0],r=checked(node),timing=setting();
        if(r.hasExecuted===null)throw new Error('已載入節點的先前種子狀態不明；請使用網頁原生執行。');
        for(const current of graph._nodes)for(const widget of current.widgets??[]) {
            if(current===node&&widget===r.control)continue;
            if(widget.beforeQueued!=null||widget.afterQueued!=null)
                throw new Error('工作流含未支援的執行前後參數控制，請保持網頁開啟執行。');
        }
        if(!['before','after'].includes(timing))throw new Error('無法確認種子控制時機，請保持網頁開啟。');
        return {node_id:String(node.id),timing,hasExecuted:r.hasExecuted,policy:r.control.value,seed:r.seed.value,
            min:r.seed.options?.min??0,max:Math.min(1125899906842624,r.seed.options?.max??1),step2:r.seed.options?.step2??1};
    }
    function restore(graph,state) {
        const node=graph._nodes.find(n=>String(n.id)===state.node_id),r=checked(node);
        if(state.hasExecuted===true&&!r.hasExecuted) {
            // The verified native partial path initializes its private flag but
            // skips applyWidgetControl. No random draw or widget edit occurs.
            r.original.call(r.control,{isPartialExecution:true});r.hasExecuted=true;
        } else if(state.hasExecuted!==false&&state.hasExecuted!==true)throw new Error('種子狀態無法恢復。');
    }
    function queueHooks(graph) {
        const controls=[];
        for(const node of graph._nodes) {
            if(node.type==='KSampler')controls.push(checked(node).control);
            for(const widget of node.widgets??[]) {
                if(controls.includes(widget))continue;
                if(widget.beforeQueued!=null||widget.afterQueued!=null)
                    throw new Error('這份工作流含未支援的執行前後控制，無法建立可靠的 Queue 快照。');
            }
            if(typeof node.beforeQueued==='function'||typeof node.afterQueued==='function')
                throw new Error('這個節點含額外執行控制，尚不支援 Queue 快照。');
        }
        if(controls.length&&!['before','after'].includes(setting()))throw new Error('無法確認 seed 控制時機。');
        return {before(){for(const c of controls)c.beforeQueued.call(c);},
                after(){for(const c of controls)c.afterQueued.call(c);}};
    }
    return {observe,capture,restore,queueHooks};
}
