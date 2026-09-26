// ComfyUI frontend 1.43.18, widgets.ts addValueControlWidgets.
// Observe native calls; never probe by queueing or consume a random value.
const BEFORE='({isPartialExecution:e}={})=>{!e&&controlValueRunBefore()&&a[Ty]&&applyWidgetControl(),a[Ty]=!0}';
const AFTER='({isPartialExecution:e}={})=>{!e&&!controlValueRunBefore()&&applyWidgetControl()}';

export function createSeedObserver(setting) {
    const records=new WeakMap();
    function observe(node) {
        if(node.type!=='KSampler'||records.has(node))return;
        const seed=node.widgets?.find(w=>w.name==='seed');
        const control=node.widgets?.find(w=>w.name==='control_after_generate');
        if(!seed||!control||String(control.beforeQueued)!==BEFORE||String(control.afterQueued)!==AFTER)return;
        const original=control.beforeQueued,after=control.afterQueued;
        const record={seed,control,original,after,hasExecuted:false};
        record.before=function(...args){const result=original.apply(this,args);record.hasExecuted=true;return result;};
        control.beforeQueued=record.before;records.set(node,record);
    }
    function checked(node) {
        const r=records.get(node);
        if(!r||r.control.beforeQueued!==r.before||r.control.afterQueued!==r.after
            ||!node.widgets.includes(r.seed)||!node.widgets.includes(r.control))
            throw new Error('此採樣器的種子控制已被其它擴充修改，請保持網頁開啟執行。');
        return r;
    }
    function capture(graph) {
        const samplers=graph._nodes.filter(n=>n.type==='KSampler');
        if(samplers.length!==1)throw new Error('背景執行目前需要一個標準 KSampler。');
        const node=samplers[0],r=checked(node),timing=setting();
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
    return {observe,capture,restore};
}
