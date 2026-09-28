// Versioned adapter for ComfyUI frontend 1.43.18. The native queue owns the
// graph, serialization, seed hooks, HTTP submission and job registration.
import {workflowIdentity} from './workflow_sync.js';
import {nativeCatalog,confirmNativeWorkflow} from './native_open.js';
import {graphFingerprint} from './graph_fingerprint.js';
import {captureManual} from './state.js';
import {openNativeWorkflow,nativeDraftRecoveryPending} from './native_switch.js';

// Apply only explicitly bound fields on the real graph before native queue
// serialization. Validate all destinations before changing any of them.
export async function applyNativeBindings(app,command,guard=()=>{}) {
    guard();
    const graph=app.rootGraph,updates=[],seen=new Set();
    for(const item of [...(command.texts??[]),...(command.images??[])]) {
        const key=`${item.node}/${item.field}`,node=graph?._nodes?.find(n=>String(n.id)===item.node);
        const widget=node?.widgets?.find(w=>w.name===item.field);
        const port=node?.inputs?.find(p=>p.name===item.field||p.widget?.name===item.field);
        if(seen.has(key)||node?.type!==item.class_type||!widget||port?.link!=null||widget.type==='converted-widget'||typeof widget.value!=='string')
            throw new Error(`綁定節點 #${item.node} / ${item.field} 已變更或由接線供值，未提交。`);
        seen.add(key);
        if(item.text_source==='web'||(item.field!=='image'&&item.text_source!=='pcs'))continue;
        if(typeof item.text!=='string')throw new Error('綁定內容格式無效，未提交。');
        if(widget.value!==item.text)updates.push({node,widget,item});
    }
    if(updates.length) {
        graph.beforeChange?.();
        try {
            for(const {node,widget,item} of updates) {
                guard();widget.value=item.text;
                if(item.field==='image')await widget.callback?.(item.text,app.canvas,node);
            }
        } finally {graph.afterChange?.();graph.setDirtyCanvas?.(true,true);}
    }
    guard();
}

export function installNativeQueue(app,api,request,session,notice=()=>{},timeoutMs=12000,onIdle=()=>{},externalUnavailable=()=>'',seeds=null) {
    const nativeQueue=app.queuePrompt;
    let stopped=false,epoch=0,active=null,ordinary=0,polling=false,composing=false,loading=0;
    const nativeLoader=app.loadGraphData;
    // Observe native loads from their entry, before clean()/async validation.
    // Delegate unchanged; ordinary editing and undo are never refused here.
    const loaderObserver=typeof nativeLoader==='function'?async function(...args){
        loading++;
        try{return await nativeLoader.apply(this,args);}finally{loading--;}
    }:null;
    if(loaderObserver)app.loadGraphData=loaderObserver;
    const compositionStart=()=>{composing=true;},compositionEnd=()=>{composing=false;};
    globalThis.document?.addEventListener?.('compositionstart',compositionStart,true);
    globalThis.document?.addEventListener?.('compositionend',compositionEnd,true);
    // Existing queued native operations cannot be associated after the fact.
    let installedIdle=app.processingQueue===false&&Array.isArray(app.queueItems)&&app.queueItems.length===0;
    const identity=()=>workflowIdentity(app);
    const key=value=>JSON.stringify(value);
    function assertCurrent(operation) {
        if(stopped||operation.cancelled||active!==operation||epoch!==operation.epoch||epoch!==(operation.expectedEpoch??operation.command.epoch)||app.rootGraph!==operation.graph||app.graph!==operation.graph
            ||app.configuringGraph||key(identity())!==key(operation.currentIdentity??operation.command.identity)||api.clientId!==operation.clientId)
            throw new Error('原生工作流或連線已切換，停止這次提交。');
    }
    // Install once, before accepting PCS commands. Never probe busy state by
    // calling nativeQueue: it enqueues even when it returns false.
    const queueGate=async function(...args) {
        // The native Run button always belongs to ComfyUI. A desktop pause,
        // missing receipt or closed PCS process must never intercept it.
        // Only defer across our short serialization transaction, never GPU work.
        if(active)await new Promise(resolve=>nativeClicks.push(resolve));
        ordinary++;
        try {return await nativeQueue.apply(this,args);} finally {ordinary--;}
    };
    const nativeClicks=[];
    function release(operation){
        if(active!==operation)return;
        active=null;
        for(const resolve of nativeClicks.splice(0))resolve();
        queueMicrotask(()=>{if(!stopped)onIdle();});
    }
    app.queuePrompt=queueGate;

    // Poll and command acceptance must use the same strict check. Missing
    // frontend fields must never look idle through falsy/optional access.
    const unavailable=()=>{
        const external=externalUnavailable();if(external)return external;
        if(stopped)return '原生同步已停止，請重新整理 ComfyUI 頁面。';
        if(nativeDraftRecoveryPending(app))return '工作流載入失敗，請在 ComfyUI 重新開啟工作流後再執行 PCS。';
        if(composing)return '文字仍在輸入中，請完成輸入後再加入工作。';
        if(!installedIdle&&app.processingQueue===false&&Array.isArray(app.queueItems)&&app.queueItems.length===0)installedIdle=true;
        if(!installedIdle)return 'ComfyUI 正在完成原生提交，請稍後再試。';
        if(app.queuePrompt!==queueGate)return '其他擴充已變更執行入口，PCS 已停止提交；請重新整理 ComfyUI 頁面。';
        if(typeof app.processingQueue!=='boolean'||!Array.isArray(app.queueItems))return '目前 ComfyUI 的提交狀態無法辨識，PCS 未執行。';
        if(active||ordinary||app.processingQueue||app.queueItems.length)return 'ComfyUI 正在提交，請稍後再試。';
        if(loading)return 'ComfyUI 正在載入工作流，請完成後再試。';
        if(loaderObserver&&app.loadGraphData!==loaderObserver)return '原生載入入口已變更，請重新整理 ComfyUI 頁面。';
        if(!api.clientId)return 'ComfyUI 連線尚未就緒，請稍後再試。';
        if(!app.rootGraph||app.graph!==app.rootGraph||app.configuringGraph)return '請等候原生工作流載入或切換完成，並回到主畫布後再執行。';
        return '';
    };
    let reportedUnavailable='';

    async function select(operation) {
        assertCurrent(operation);
        const target=operation.command.target;
        if(!target||identity().frontend_id===target.frontend_id&&(target.path?identity().path===target.path:identity().workflow===target.workflow))return;
        const opened=await openNativeWorkflow(app,target,()=>assertCurrent(operation));
        if(stopped||active!==operation||operation.cancelled||api.clientId!==operation.clientId)throw new Error('原生連線已變更，未提交。');
        operation.currentIdentity=identity();operation.epoch=epoch;
        // command identity remains the original authorization receipt; only
        // the local live guard follows this explicitly acknowledged transition.
        operation.expectedEpoch=epoch;
        assertCurrent(operation);
        if(operation.command.action!=='open')await request('workflow/native/activate',{
            id:operation.command.id,session,identity:operation.command.identity,epoch:operation.command.epoch,
            client_id:operation.clientId,opened_identity:opened,opened_epoch:epoch});
        assertCurrent(operation);
    }

    async function confirm(command) {
        const error=unavailable();
        if(error)return {error,uncertain:false};
        const operation={command,epoch,graph:app.rootGraph,clientId:api.clientId,cancelled:false};
        active=operation;
        try {
            assertCurrent(operation);
            await select(operation);
            return {opened_identity:confirmNativeWorkflow(app,command.target,()=>assertCurrent(operation))};
        } catch(error) {return {error:String(error?.message??error),uncertain:false};}
        finally {release(operation);}
    }

    async function execute(command) {
        if(command.action==='open')return confirm(command);
        const error=unavailable();
        if(error)return {error,uncertain:false};
        const operation={command,epoch,graph:app.rootGraph,clientId:api.clientId,attempted:false,observed:false,prompt_id:'',payload:null};
        active=operation;
        const originalSerialize=app.graphToPrompt,originalApi=api.queuePrompt;
        // Native editing and undo retain their own lifecycle. Any edit before
        // transport invalidates the identity/fingerprint checks below; do not
        // block its loader after native undo has already moved history entries.
        let serialized=null,completion=null,settled=false,timeout;
        const fingerprint=()=>graphFingerprint(operation.graph.serialize());
        const serialize=async function(...args) {
            assertCurrent(operation);
            // Our legacy wrapper normally records manual text during native
            // serialization. Finish that synchronous bookkeeping first.
            for(const node of operation.graph._nodes??[])captureManual(node);
            const before=fingerprint();
            const value=await originalSerialize.apply(this,args);
            assertCurrent(operation);
            if(fingerprint()!==before)throw new Error('序列化期間工作流被修改，未提交。');
            serialized={value,fingerprint:before};
            return value;
        };
        const submit=async function(...args) {
            assertCurrent(operation);
            const value=args[1];
            if(operation.observed||!serialized||value!==serialized.value||fingerprint()!==serialized.fingerprint)
                throw new Error('無法確認原生提交屬於這次操作，未提交。');
            operation.observed=true;
            // Store the exact native serialization before sending. This endpoint
            // does not generate or patch an API graph.
            await request('workflow/native/prepare',{id:command.id,session,epoch:command.epoch,
                identity:command.identity,client_id:operation.clientId,output:value.output,workflow:value.workflow});
            assertCurrent(operation);
            if(fingerprint()!==serialized.fingerprint)throw new Error('提交前工作流被修改，未提交。');
            const marked={...value,workflow:{...value.workflow,extra:{...value.workflow.extra,pcs_native_operation:command.id}}};
            operation.payload={prompt:structuredClone(value.output),extra_data:{extra_pnginfo:{workflow:structuredClone(marked.workflow)}}};
            operation.attempted=true;
            // Preserve this, options, the original response and exception chain.
            const result=await originalApi.apply(this,[args[0],marked,...args.slice(2)]);
            if(typeof result?.prompt_id==='string'&&result.prompt_id)operation.prompt_id=result.prompt_id;
            return result;
        };
        try {
            assertCurrent(operation);
            await select(operation);
            if(command.action==='capture') {
                globalThis.document?.activeElement?.blur?.();
                await Promise.resolve();assertCurrent(operation);
            }
            await applyNativeBindings(app,command,()=>assertCurrent(operation));
            if(command.action==='capture') {
                if(!seeds?.queueHooks)throw new Error('此網頁未載入 Queue 快照支援，請更新並重新整理。');
                completion=(async()=>{
                    assertCurrent(operation);
                    const hooks=seeds.queueHooks(operation.graph);
                    hooks.before();
                    const value=await serialize.call(app);
                    assertCurrent(operation);
                    await request('workflow/native/prepare',{id:command.id,session,epoch:command.epoch,
                        identity:command.identity,client_id:operation.clientId,output:value.output,workflow:value.workflow});
                    // Do not change a newly edited seed after an asynchronous
                    // receipt. The prepared record keeps its execution seed.
                    assertCurrent(operation);
                    if(fingerprint()===serialized.fingerprint)hooks.after();
                })();
                completion.then(()=>{settled=true;},()=>{settled=true;});
                await Promise.race([completion,new Promise((_,reject)=>{timeout=setTimeout(()=>{
                    operation.cancelled=true;reject(new Error('保存工作快照逾時，未派送生成。'));
                },timeoutMs);})]);
                return {captured:true};
            }
            // The exact click-time PCS text is now on the native graph. All
            // unbound parameters and explicit Web text remain native values.
            app.graphToPrompt=serialize; api.queuePrompt=submit;
            completion=Promise.resolve(nativeQueue.call(app,0,1));
            completion.then(()=>{settled=true;},()=>{settled=true;});
            await Promise.race([completion,new Promise((_,reject)=>{timeout=setTimeout(()=>{
                operation.cancelled=true;reject(new Error('原生提交逾時；結果未確認，不會重送。'));
            },timeoutMs);})]);
            if(!operation.prompt_id)throw new Error(operation.attempted?'提交回覆未確認，請查看任務紀錄。':'ComfyUI 未接受生成，請查看原生錯誤提示。');
            return {prompt_id:operation.prompt_id,payload:operation.payload};
        } catch(error) {
            return {prompt_id:operation.prompt_id||undefined,payload:operation.payload||undefined,
                error:String(error?.message??error),uncertain:operation.attempted&&!operation.prompt_id};
        } finally {
            clearTimeout(timeout);
            const restore=()=>{
                if(app.graphToPrompt===serialize)app.graphToPrompt=originalSerialize;
                if(api.queuePrompt===submit)api.queuePrompt=originalApi;
                release(operation);
            };
            // A late serialization must still pass the cancelled guard. Do not
            // restore the unguarded transport while nativeQueue is suspended.
            if(completion&&!settled)completion.then(restore,restore);else restore();
        }
    }
    async function poll() {
        if(stopped||polling||active||!api.clientId||!app.rootGraph||app.configuringGraph)return;
        polling=true;
        try {
            const error=unavailable();
            const abnormal=!installedIdle||app.queuePrompt!==queueGate||typeof app.processingQueue!=='boolean'||!Array.isArray(app.queueItems);
            if(abnormal&&error!==reportedUnavailable)notice(error,true);
            reportedUnavailable=abnormal?error:'';
            const value=identity();
            const result=await request('workflow/native/poll',{session,epoch,identity:value,client_id:api.clientId,
                workflows:nativeCatalog(app),bindings_protocol:1,capture_protocol:seeds?.queueHooks?1:0,navigation_protocol:1,
                ready:!error});
            for(const command of result.commands??[]) {
                const result=await execute(command);
                await request('workflow/native/reply',{id:command.id,session,epoch:command.epoch,identity:command.identity,...result});
                if(result.error)notice(result.error,true);
            }
        } catch(error) { /* Lost reply stays unconfirmed; a poll never repeats it. */ }
        finally {polling=false;}
    }
    const timer=setInterval(poll,500);
    return {poll,execute,get busy(){return !!active;},changed(){epoch++;},stop(){stopped=true;epoch++;clearInterval(timer);
        globalThis.document?.removeEventListener?.('compositionstart',compositionStart,true);
        globalThis.document?.removeEventListener?.('compositionend',compositionEnd,true);
        if(app.loadGraphData===loaderObserver)app.loadGraphData=nativeLoader;
        if(app.queuePrompt===queueGate)app.queuePrompt=nativeQueue;}};
}
