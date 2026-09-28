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
export async function openNativeWorkflow(app,target,guard) {
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
    let release=true,succeeded=false;
    try {
        for(const t of trackers){replace(t,'checkState',()=>{});replace(t,'updateState',async()=>{});}
        replace(app,'loadGraphData',blocked);
        for(const name of events)surface?.addEventListener?.(name,blockEvent,{capture:true,passive:false});
        const load=state=>loader.call(app,state,true,true,state===destinationState?destination:source,
            {checkForRerouteMigration:false,deferWarnings:true,skipAssetScans:true});
        try {
            await load(destinationState);
            if(store.activeWorkflow!==destination||app.rootGraph!==graph||app.graph!==graph||app.configuringGraph||
                !same(nativeIdentity(destination),target)||graph.id!==target.frontend_id||
                graphFingerprint(graph.serialize())!==graphFingerprint(destinationState))
                throw new Error('原生載入結果與綁定工作流不符，未提交。');
        } catch(error) {
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
        for(const {t,initial,active,undo,redo,cache} of saved) {
            // Native afterLoad resets initialState even when loading a dirty
            // draft. Preserve its saved baseline and its existing undo/redo.
            t.initialState=initial;t.undoQueue.splice(0,t.undoQueue.length,...undo);t.redoQueue.splice(0,t.redoQueue.length,...redo);
            if(!succeeded){t.activeState=active;Object.assign(t,cache);}
            t.updateModified();
        }
        if(release){for(const undo of restore.reverse())undo();}
        else {
            // The last replacement is the loader, the remaining replacements
            // guard only autosave/history. A successful native load releases
            // those guards without leaving a PCS transaction or click lock.
            restore.pop()();recovering.add(app);
            const recoveryLoader=async function(...args){
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
