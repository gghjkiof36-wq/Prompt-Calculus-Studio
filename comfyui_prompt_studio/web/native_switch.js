import {nativeIdentity,confirmNativeWorkflow} from './native_open.js';
import {graphFingerprint} from './graph_fingerprint.js';

const copy=value=>JSON.parse(JSON.stringify(value));
const same=(a,b)=>a.frontend_id===b.frontend_id&&(b.path?a.path===b.path:a.workflow===b.workflow);
const recovering=new WeakSet();
export const nativeDraftRecoveryPending=app=>recovering.has(app);

// Use Comfy's real loaded workflow and native loader. During its clean/configure
// interval, old window key callbacks must not save the partial graph. Guard the
// tracker entry points as well as input events: an undo must be refused BEFORE
// updateState pops either history stack. No loader interception during ordinary
// native submission/serialization (where user editing still invalidates PCS).
export async function openNativeWorkflow(app,target,guard,timeoutMs=12000) {
    guard();
    const store=app.extensionManager?.workflow,source=store?.activeWorkflow,graph=app.rootGraph;
    const destinations=store?.workflows?.filter(w=>w.isLoaded&&same(nativeIdentity(w),target))??[];
    if(destinations.length!==1||!target.frontend_id)throw new Error('找不到唯一的已載入原生工作流，請先在 ComfyUI 打開綁定的原件。');
    const destination=destinations[0];
    if(destination===source)return confirmNativeWorkflow(app,target,guard);
    const workflows=store.workflows.filter(w=>w.isLoaded),trackers=workflows.map(w=>w.changeTracker);
    if(!source||app.graph!==graph||app.configuringGraph||graph.id!==source.activeState?.id||typeof app.loadGraphData!=='function'||
        trackers.some(t=>!t||t.changeCount!==0||t._restoringState||!Array.isArray(t.undoQueue)||!Array.isArray(t.redoQueue)||
            t.constructor?.isLoadingGraph===true||
            ['checkState','updateState','updateModified','store'].some(key=>typeof t[key]!=='function')))
        throw new Error('原生工作流正在編輯或版本不支援安全切換，請完成操作後再試。');
    globalThis.document?.activeElement?.blur?.();
    guard();
    source.changeTracker.checkState();
    source.changeTracker.store();
    if(graphFingerprint(source.activeState)!==graphFingerprint(graph.serialize()))
        throw new Error('目前原生草稿尚未保存至工作流，未切換。');
    const saved=workflows.map(w=>({w,t:w.changeTracker,initial:w.changeTracker.initialState,
        active:copy(w.activeState),undo:[...w.changeTracker.undoQueue],redo:[...w.changeTracker.redoQueue],
        cache:Object.fromEntries(['ds','nodeOutputs','subgraphState'].map(key=>[key,w.changeTracker[key]]))}));
    const sourceState=copy(source.activeState),destinationState=copy(destination.activeState);
    const loader=app.loadGraphData,restore=[];
    function replace(object,key,value) {
        const descriptor=Object.getOwnPropertyDescriptor(object,key);
        Object.defineProperty(object,key,{configurable:true,writable:true,value});
        restore.push(()=>descriptor?Object.defineProperty(object,key,descriptor):delete object[key]);
    }
    const blocked=()=>{throw new Error('PCS 正在切換原生工作流，請等候完成。');};
    const blockEvent=event=>{event.preventDefault();event.stopImmediatePropagation();};
    const surface=globalThis.window??globalThis.document;
    const events=['keydown','keyup','pointerdown','pointerup','mousedown','mouseup','click','dblclick','wheel','beforeinput','input','change','paste','cut','drop'];
    let release=true,succeeded=false,pendingLoad=null;
    function restoreDrafts() {
        for(const {t,initial,active,undo,redo,cache} of saved) {
            t.initialState=initial;t.undoQueue.splice(0,t.undoQueue.length,...undo);t.redoQueue.splice(0,t.redoQueue.length,...redo);
            if(!succeeded){t.activeState=copy(active);Object.assign(t,cache);}
            t.updateModified();
        }
    }
    const load=async state=>{
        let timer;
        const pending=Promise.resolve().then(()=>loader.call(app,state,true,true,state===destinationState?destination:source,
            {checkForRerouteMigration:false,deferWarnings:true,skipAssetScans:true}));
        pendingLoad=pending;
        pending.then(()=>{if(pendingLoad===pending)pendingLoad=null;},()=>{if(pendingLoad===pending)pendingLoad=null;});
        try{return await Promise.race([pending,new Promise((_,reject)=>{timer=setTimeout(()=>{
            const error=new Error('原生工作流載入逾時；請重新整理 ComfyUI 網頁後再試。');
            error.loadPending=true;reject(error);
        },timeoutMs);})]);}finally{clearTimeout(timer);}
    };
    try {
        for(const t of trackers){replace(t,'checkState',()=>{});replace(t,'updateState',async()=>{});}
        replace(app,'loadGraphData',blocked);
        for(const name of events)surface?.addEventListener?.(name,blockEvent,{capture:true,passive:false});
        try {
            await load(destinationState);
            if(store.activeWorkflow!==destination||app.rootGraph!==graph||app.graph!==graph||app.configuringGraph||
                !same(nativeIdentity(destination),target)||graph.id!==target.frontend_id||
                graphFingerprint(graph.serialize())!==graphFingerprint(destinationState))
                throw new Error('原生載入結果與綁定工作流不符，未提交。');
        } catch(error) {
            // A native loader has no abort contract. Never start a concurrent
            // rollback over a suspended configure/afterLoad callback.
            if(error.loadPending){release=false;throw error;}
            try {
                await load(sourceState);
                if(store.activeWorkflow!==source||graphFingerprint(graph.serialize())!==graphFingerprint(sourceState))throw new Error('restore mismatch');
            } catch {
                // Protect the saved drafts from a partial graph, but release
                // native input and loading so the user can reopen a workflow.
                release=false;
                const failure=new Error('原生切換及畫布還原失敗；已保留草稿，請重新開啟工作流或重新整理 ComfyUI。');
                failure.unrecoverable=true;throw failure;
            }
            throw error;
        }
        succeeded=true;return nativeIdentity(destination);
    } finally {
        restoreDrafts();
        if(release){for(const undo of restore.reverse())undo();}
        else {
            // The last replacement is the loader, the remaining replacements
            // guard only autosave/history. A successful native load releases
            // those guards without leaving a PCS transaction or click lock.
            restore.pop()();recovering.add(app);
            // Release the whole-window input lock even if native code never
            // returns. Protect only the incomplete canvas; browser refresh,
            // native controls and menus remain available. Late completion may
            // restore a partial tracker, so restore the saved drafts again.
            const canvas=app.canvasEl??app.canvas?.canvas;
            const protectCanvas=event=>{
                if(event.key==='F5'||((event.ctrlKey||event.metaKey)&&event.key?.toLowerCase()==='r'))return;
                if(pendingLoad&&canvas?.contains?.(event.target))blockEvent(event);
            };
            if(pendingLoad){
                for(const name of events)surface?.addEventListener?.(name,protectCanvas,{capture:true,passive:false});
                const settled=()=>{
                    restoreDrafts();
                    for(const name of events)surface?.removeEventListener?.(name,protectCanvas,{capture:true});
                };
                pendingLoad.then(settled,settled);
            }
            const recoveryLoader=async function(...args){
                if(pendingLoad)throw new Error('原生工作流仍未結束載入；請重新整理 ComfyUI 網頁。');
                const result=await loader.apply(this,args);
                const current=store.activeWorkflow;
                if(!app.configuringGraph&&app.rootGraph===graph&&app.graph===graph&&current?.activeState&&
                    graphFingerprint(graph.serialize())===graphFingerprint(current.activeState)){
                    for(const undo of restore.splice(0).reverse())undo();
                    if(app.loadGraphData===recoveryLoader)app.loadGraphData=loader;
                    recovering.delete(app);
                }
                return result;
            };
            app.loadGraphData=recoveryLoader;
        }
        for(const name of events)surface?.removeEventListener?.(name,blockEvent,{capture:true});
    }
}
