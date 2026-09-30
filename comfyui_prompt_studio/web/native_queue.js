// Versioned adapter for ComfyUI frontend 1.43.18. The native queue owns the
// graph, serialization, seed hooks, HTTP submission and job registration.
import {workflowIdentity} from './workflow_sync.js';
import {nativeCatalog,confirmNativeWorkflow} from './native_open.js';
import {graphFingerprint} from './graph_fingerprint.js';
import {captureManual} from './state.js';
import {openNativeWorkflow,nativeDraftRecoveryPending} from './native_switch.js';

// Apply only explicitly bound fields on the real graph before native queue
// serialization. Validate all destinations before changing any of them.
export async function applyNativeBindings(app,command,guard=()=>{},wait=value=>value,checkpoint=null) {
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
                if(checkpoint)await checkpoint();
                guard();widget.value=item.text;
                if(item.field==='image')await wait(widget.callback?.(item.text,app.canvas,node));
            }
        } finally {graph.afterChange?.();graph.setDirtyCanvas?.(true,true);}
    }
    guard();
}

export function installNativeQueue(app,api,request,session,notice=()=>{},timeoutMs=12000,onIdle=()=>{},externalUnavailable=()=>'',seeds=null) {
    const nativeQueue=app.queuePrompt;
    const cancelledApplies=new Set(),applyOperations=new Map();
    let stopped=false,epoch=0,active=null,ordinary=0,polling=false,acknowledging=false,composing=false,loading=0,probe='';
    const nativeLoader=app.loadGraphData;
    // Observe native loads from their entry, before clean()/async validation.
    // Delegate unchanged; ordinary editing and undo are never refused here.
    const loaderObserver=typeof nativeLoader==='function'?async function(...args){
        loading++;
        seeds?.remember?.(app.rootGraph,app.extensionManager?.workflow?.activeWorkflow);
        try{
            const result=await nativeLoader.apply(this,args);
            seeds?.resume?.(app.rootGraph,app.extensionManager?.workflow?.activeWorkflow);
            return result;
        }finally{loading--;}
    }:null;
    if(loaderObserver)app.loadGraphData=loaderObserver;
    const compositionStart=()=>{composing=true;},compositionEnd=()=>{composing=false;};
    globalThis.document?.addEventListener?.('compositionstart',compositionStart,true);
    globalThis.document?.addEventListener?.('compositionend',compositionEnd,true);
    // Existing queued native operations cannot be associated after the fact.
    let installedIdle=app.processingQueue===false&&Array.isArray(app.queueItems)&&app.queueItems.length===0;
    const identity=()=>workflowIdentity(app);
    const key=value=>JSON.stringify(value);
    async function boundedRequest(route,value) {
        const controller=new AbortController();let timer;
        const expired=new Promise((_,reject)=>{timer=setTimeout(()=>{
            controller.abort();reject(new Error('原生通訊逾時；不會重送這次操作。'));
        },timeoutMs);});
        try{return await Promise.race([Promise.resolve().then(()=>request(route,value,{signal:controller.signal})),expired]);}
        finally{clearTimeout(timer);}
    }
    function recorder(command) {
        const start=Date.now(),clientId=api.clientId;let seq=0,phase='received';
        return (next=phase,state='started')=>{
            phase=next;if(seq>=64)return;
            const event={seq:++seq,phase,state,elapsed_ms:Math.min(86400000,Math.max(0,Date.now()-start))};
            // Best effort and bounded: diagnostics cannot stall or retry work.
            boundedRequest('workflow/native/event',{
                id:command.id,session,epoch:command.epoch,identity:command.identity,client_id:clientId,event
            }).catch(()=>{});
        };
    }
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
        queueMicrotask(()=>{if(!stopped&&!loading&&!nativeDraftRecoveryPending(app))onIdle();});
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
    const pollValue=error=>({session,epoch:active?(active.expectedEpoch??active.command.epoch):epoch,
        identity:active?(active.currentIdentity??active.command.identity):identity(),client_id:api.clientId,
        workflows:nativeCatalog(app),bindings_protocol:1,capture_protocol:seeds?.queueHooks?1:0,navigation_protocol:1,inspect_protocol:1,apply_protocol:1,
        ready:!error,reason:error,probe});

    async function select(operation) {
        assertCurrent(operation);
        const target=operation.command.target;
        if(!target||identity().frontend_id===target.frontend_id&&(target.path?identity().path===target.path:identity().workflow===target.workflow))return;
        operation.trace?.('select');
        const opened=await openNativeWorkflow(app,target,()=>assertCurrent(operation),timeoutMs);
        operation.trace?.('select','done');
        if(stopped||active!==operation||operation.cancelled||api.clientId!==operation.clientId)throw new Error('原生連線已變更，未提交。');
        operation.currentIdentity=identity();operation.epoch=epoch;
        // command identity remains the original authorization receipt; only
        // the local live guard follows this explicitly acknowledged transition.
        operation.expectedEpoch=epoch;
        assertCurrent(operation);
        if(operation.command.action!=='open'){
            operation.trace?.('activate');
            await (operation.wait??(v=>v))(request('workflow/native/activate',{
            id:operation.command.id,session,identity:operation.command.identity,epoch:operation.command.epoch,
            client_id:operation.clientId,opened_identity:opened,opened_epoch:epoch}));
            operation.trace?.('activate','done');
        }
        assertCurrent(operation);
    }

    async function confirm(command,trace) {
        const error=unavailable();
        if(error){trace(undefined,'error');return {error,uncertain:false};}
        const operation={command,epoch,graph:app.rootGraph,clientId:api.clientId,cancelled:false};
        operation.trace=trace;
        active=operation;
        try {
            assertCurrent(operation);
            await select(operation);
            return {opened_identity:confirmNativeWorkflow(app,command.target,()=>assertCurrent(operation))};
        } catch(error) {operation.trace(undefined,'error');return {error:String(error?.message??error),uncertain:false};}
        finally {release(operation);}
    }

    async function execute(command,trace=recorder(command)) {
        trace();
        if(command.action==='open')return confirm(command,trace);
        if(command.action==='apply'&&cancelledApplies.has(command.id))return {error:'這次輸入套用已取消。',uncertain:false};
        const error=unavailable();
        if(error){trace(undefined,'error');return {error,uncertain:false};}
        const operation={command,epoch,graph:app.rootGraph,clientId:api.clientId,attempted:false,observed:false,prompt_id:'',payload:null};
        operation.trace=trace;
        active=operation;
        const originalSerialize=app.graphToPrompt,originalApi=api.queuePrompt;
        // Native editing and undo retain their own lifecycle. Any edit before
        // transport invalidates the identity/fingerprint checks below; do not
        // block its loader after native undo has already moved history entries.
        let serialized=null,completion=null,settled=false,timeout;
        // Run the original method with an operation-local input queue. Its
        // auth await occurs outside native try/finally (frontend 1.43.18).
        // A cancelled continuation must never drain a newer native Run queue.
        const localQueue=[];let localProcessing=false,ownsQueue=false,ownsProcessing=false;
        const queueContext=new Proxy(app,{
            get(target,key){
                if(key==='queueItems'){ownsQueue=true;return localQueue;}
                if(key==='processingQueue')return localProcessing;
                if(key==='rootGraph'){assertCurrent(operation);return operation.graph;}
                if(key==='graphToPrompt')return serialize;
                return Reflect.get(target,key,target);
            },
            set(target,key,value){
                if(key==='processingQueue'){ownsProcessing=true;localProcessing=value;return true;}
                return Reflect.set(target,key,value,target);
            }
        });
        const expired=new Promise((_,reject)=>{timeout=setTimeout(()=>{
            operation.trace(undefined,'timeout');
            operation.cancelled=true;reject(new Error('原生提交逾時；結果未確認，不會重送。'));
        },timeoutMs);});
        // Bound the awaits inside nativeQueue as well as its outer promise.
        // A hung serializer/prepare/upload cannot keep processingQueue and the
        // temporary native transport wrappers alive forever.
        const wait=value=>Promise.race([Promise.resolve(value),expired]);
        operation.wait=wait;expired.catch(()=>{});
        operation.mutations=new Set();
        if(command.action==='apply')applyOperations.set(command.id,operation);
        const mutationWait=value=>{
            const pending=Promise.resolve(value);operation.mutations.add(pending);
            pending.then(()=>operation.mutations.delete(pending),()=>operation.mutations.delete(pending));
            return wait(pending);
        };
        const checkpoint=command.action==='apply'?async()=>{
            assertCurrent(operation);
            await wait(request('workflow/native/check_apply',{id:command.id,session,epoch:command.epoch,
                identity:command.identity,client_id:operation.clientId}));
            assertCurrent(operation);
        }:null;
        const fingerprint=()=>graphFingerprint(operation.graph.serialize());
        const serialize=async function(...args) {
            operation.trace('serialize');
            assertCurrent(operation);
            // Explicit retry keeps the recorded execution seed. Native seed
            // hooks still run once; the retry value is selected at serialization.
            for(const [id,saved] of Object.entries(command.replay??{})){
                const node=operation.graph._nodes?.find(n=>String(n.id)===id);
                for(const field of ['seed','noise_seed'])if(typeof saved.inputs?.[field]==='number'){
                    const widget=node?.widgets?.find(w=>w.name===field);
                    if(!widget||node.type!==saved.class_type)throw new Error('重試種子節點已變更，請重新準備。');
                    widget.value=saved.inputs[field];
                }
            }
            // Our legacy wrapper normally records manual text during native
            // serialization. Finish that synchronous bookkeeping first.
            for(const node of operation.graph._nodes??[])captureManual(node);
            const before=fingerprint();
            const value=await wait(originalSerialize.apply(this,args));
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
            operation.trace('prepare');
            await wait(request('workflow/native/prepare',{id:command.id,session,epoch:command.epoch,
                identity:command.identity,client_id:operation.clientId,output:value.output,workflow:value.workflow}));
            assertCurrent(operation);
            if(fingerprint()!==serialized.fingerprint)throw new Error('提交前工作流被修改，未提交。');
            const marked={...value,workflow:{...value.workflow,extra:{...value.workflow.extra,pcs_native_operation:command.id}}};
            operation.payload={prompt:structuredClone(value.output),extra_data:{extra_pnginfo:{workflow:structuredClone(marked.workflow)}}};
            operation.attempted=true;
            operation.trace('transport');
            // Preserve this, options, the original response and exception chain.
            const result=await wait(originalApi.apply(this,[args[0],marked,...args.slice(2)]));
            if(typeof result?.prompt_id==='string'&&result.prompt_id)operation.prompt_id=result.prompt_id;
            operation.trace('transport','done');operation.trace('native_finish');
            return result;
        };
        try {
            assertCurrent(operation);
            if(checkpoint)await checkpoint();
            await select(operation);
            if(command.action==='inspect') {
                // Read through ComfyUI's own serializer for custom nodes and
                // unsaved widgets. This does not run native queue/seed hooks or
                // apply PCS text/image bindings.
                const before=fingerprint();
                const value=await wait(originalSerialize.call(app));
                assertCurrent(operation);
                if(fingerprint()!==before)throw new Error('讀取期間工作流被修改，請重新整理節點。');
                return {inspection:{workflow:structuredClone(value.workflow),output:structuredClone(value.output),identity:identity()}};
            }
            if(command.action==='capture') {
                globalThis.document?.activeElement?.blur?.();
                await Promise.resolve();assertCurrent(operation);
            }
            operation.trace('apply');
            await applyNativeBindings(app,command,()=>assertCurrent(operation),mutationWait,checkpoint);
            operation.trace('apply','done');
            // Apply-only never serializes, advances seeds or calls native Run.
            if(command.action==='apply'){await checkpoint();return {applied:true};}
            if(command.action==='capture') {
                if(!seeds?.queueHooks)throw new Error('此網頁未載入 Queue 快照支援，請更新並重新整理。');
                completion=(async()=>{
                    assertCurrent(operation);
                    const hooks=seeds.queueHooks(operation.graph);
                    hooks.before();
                    const value=await serialize.call(app);
                    assertCurrent(operation);
                    await wait(request('workflow/native/prepare',{id:command.id,session,epoch:command.epoch,
                        identity:command.identity,client_id:operation.clientId,output:value.output,workflow:value.workflow}));
                    // Do not change a newly edited seed after an asynchronous
                    // receipt. The prepared record keeps its execution seed.
                    assertCurrent(operation);
                    if(fingerprint()===serialized.fingerprint)hooks.after();
                })();
                completion.then(()=>{settled=true;},()=>{settled=true;});
                await wait(completion);
                return {captured:true};
            }
            // The exact click-time PCS text is now on the native graph. All
            // unbound parameters and explicit Web text remain native values.
            app.graphToPrompt=serialize; api.queuePrompt=submit;
            operation.trace('native_wait');
            completion=Promise.resolve(nativeQueue.call(queueContext,0,1));
            completion.then(()=>{settled=true;},()=>{settled=true;});
            await wait(completion);
            if(!operation.prompt_id)throw new Error(operation.attempted?'提交回覆未確認，請查看任務紀錄。':'ComfyUI 未接受生成，請查看原生錯誤提示。');
            return {prompt_id:operation.prompt_id,payload:operation.payload};
        } catch(error) {
            operation.trace(undefined,'error');
            return {prompt_id:operation.prompt_id||undefined,payload:operation.payload||undefined,
                error:completion&&!settled&&!(ownsQueue&&ownsProcessing)?
                    '原生提交逾時且擴充執行入口無法安全解除；請重新整理 ComfyUI 網頁。':String(error?.message??error),
                uncertain:operation.attempted&&!operation.prompt_id};
        } finally {
            clearTimeout(timeout);
            operation.cancelled=true;localQueue.length=0;
            if(command.action==='apply')Promise.allSettled([...operation.mutations]).then(()=>applyOperations.delete(command.id));
            const restore=()=>{
                if(app.graphToPrompt===serialize)app.graphToPrompt=originalSerialize;
                if(api.queuePrompt===submit)api.queuePrompt=originalApi;
                release(operation);
            };
            // The suspended native call retains its guarded context and empty
            // private queue. Release only our wrappers, never native busy state.
            if(completion&&!settled&&!(ownsQueue&&ownsProcessing))completion.then(restore,restore);
            else restore();
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
            const result=await boundedRequest('workflow/native/poll',pollValue(error));
            for(const command of result.commands??[]) {
                const trace=recorder(command),result=await execute(command,trace);
                trace('reply');
                try{await boundedRequest('workflow/native/reply',{id:command.id,session,epoch:command.epoch,identity:command.identity,...result});trace('reply','done');}
                catch(error){trace('reply','error');throw error;}
                if(result.error)notice(result.error,true);
            }
        } catch(error) { /* Lost reply stays unconfirmed; a poll never repeats it. */ }
        finally {polling=false;}
    }
    const probeListener=async event=>{
        const value=event?.detail?.probe;
        if(typeof value!=='string'||!value||value.length>100)return;
        probe=value;
        if(stopped||!api.clientId)return;
        if(polling||active||loading||app.configuringGraph) {
            // A busy acknowledgement cannot claim or execute a command. It
            // lets the waiter distinguish a live switching browser from a
            // missing browser while the original owner keeps its transaction.
            if(acknowledging)return;
            acknowledging=true;
            try {await boundedRequest('workflow/native/poll',pollValue(unavailable()||'ComfyUI 正在回覆原生操作。'));}
            catch {} finally {acknowledging=false;}
        } else await poll();
    };
    api.addEventListener?.('prompt_studio_native_probe',probeListener);
    const cancelListener=async event=>{
        const value=event?.detail;
        if(value?.session!==session||value.client_id!==api.clientId||typeof value.id!=='string'||!value.id)return;
        const operation=applyOperations.get(value.id);
        if(operation&&(value.epoch!==operation.command.epoch||key(value.identity)!==key(operation.command.identity)))return;
        cancelledApplies.add(value.id);
        if(operation?.command.id===value.id&&operation.command.action==='apply') {
            operation.cancelled=true;
            // An image callback already entered may finish its own field. Do
            // not report cancellation confirmed until that callback settles.
            await Promise.allSettled([...operation.mutations]);
        }
        try {await request('workflow/native/cancel_applied',value);}catch {}
    };
    api.addEventListener?.('prompt_studio_native_cancel',cancelListener);
    const timer=setInterval(poll,500);
    return {poll,execute,get busy(){return !!active;},changed(){epoch++;},stop(){stopped=true;epoch++;clearInterval(timer);
        globalThis.document?.removeEventListener?.('compositionstart',compositionStart,true);
        globalThis.document?.removeEventListener?.('compositionend',compositionEnd,true);
        api.removeEventListener?.('prompt_studio_native_probe',probeListener);
        api.removeEventListener?.('prompt_studio_native_cancel',cancelListener);
        if(app.loadGraphData===loaderObserver)app.loadGraphData=nativeLoader;
        if(app.queuePrompt===queueGate)app.queuePrompt=nativeQueue;}};
}
