// Versioned adapter for ComfyUI frontend 1.43.18. The native queue owns the
// graph, serialization, seed hooks, HTTP submission and job registration.
import {workflowIdentity} from './workflow_sync.js';
import {nativeCatalog,confirmNativeWorkflow} from './native_open.js';

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

export function installNativeQueue(app,api,request,session,notice=()=>{},timeoutMs=12000,onIdle=()=>{},externalUnavailable=()=>'') {
    const nativeQueue=app.queuePrompt;
    let stopped=false,epoch=0,active=null,ordinary=0,polling=false;
    // Existing queued native operations cannot be associated after the fact.
    const installedIdle=app.processingQueue===false&&Array.isArray(app.queueItems)&&app.queueItems.length===0;
    const identity=()=>workflowIdentity(app);
    const key=value=>JSON.stringify(value);
    function assertCurrent(operation) {
        if(stopped||operation.cancelled||active!==operation||epoch!==operation.epoch||epoch!==operation.command.epoch||app.rootGraph!==operation.graph||app.graph!==operation.graph
            ||app.configuringGraph||key(identity())!==key(operation.command.identity)||api.clientId!==operation.clientId)
            throw new Error('原生工作流或連線已切換，停止這次提交。');
    }
    // Install once, before accepting PCS commands. Never probe busy state by
    // calling nativeQueue: it enqueues even when it returns false.
    const queueGate=async function(...args) {
        if(active)throw new Error('PCS 正在提交這個工作流，請等候提交完成。');
        ordinary++;
        try {return await nativeQueue.apply(this,args);} finally {ordinary--;}
    };
    app.queuePrompt=queueGate;

    // Poll and command acceptance must use the same strict check. Missing
    // frontend fields must never look idle through falsy/optional access.
    const unavailable=()=>{
        const external=externalUnavailable();if(external)return external;
        if(stopped)return '原生同步已停止，請重新整理 ComfyUI 頁面。';
        if(!installedIdle)return '無法確認接管前的提交狀態，請等候 ComfyUI 空閒後重新整理頁面。';
        if(app.queuePrompt!==queueGate)return '其他擴充已變更執行入口，PCS 已停止提交；請重新整理 ComfyUI 頁面。';
        if(typeof app.processingQueue!=='boolean'||!Array.isArray(app.queueItems))return '目前 ComfyUI 的提交狀態無法辨識，PCS 未執行。';
        if(active||ordinary||app.processingQueue||app.queueItems.length)return 'ComfyUI 正在提交，請稍後再試。';
        if(!api.clientId)return 'ComfyUI 連線尚未就緒，請稍後再試。';
        if(!app.rootGraph||app.graph!==app.rootGraph||app.configuringGraph)return '請等候原生工作流載入或切換完成，並回到主畫布後再執行。';
        return '';
    };
    let reportedUnavailable='';

    function confirm(command) {
        const error=unavailable();
        if(error)return {error,uncertain:false};
        const operation={command,epoch,graph:app.rootGraph,clientId:api.clientId,cancelled:false};
        active=operation;
        try {
            assertCurrent(operation);
            return {opened_identity:confirmNativeWorkflow(app,command.target,()=>assertCurrent(operation))};
        } catch(error) {return {error:String(error?.message??error),uncertain:false};}
        finally {if(active===operation)active=null;}
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
        const serialize=async function(...args) {
            assertCurrent(operation);
            const before=key(operation.graph.serialize());
            const value=await originalSerialize.apply(this,args);
            assertCurrent(operation);
            if(key(operation.graph.serialize())!==before)throw new Error('序列化期間工作流被修改，未提交。');
            serialized={value,fingerprint:before};
            return value;
        };
        const submit=async function(...args) {
            assertCurrent(operation);
            const value=args[1];
            if(operation.observed||!serialized||value!==serialized.value||key(operation.graph.serialize())!==serialized.fingerprint)
                throw new Error('無法確認原生提交屬於這次操作，未提交。');
            operation.observed=true;
            // Store the exact native serialization before sending. This endpoint
            // does not generate or patch an API graph.
            await request('workflow/native/prepare',{id:command.id,session,epoch:command.epoch,
                identity:command.identity,client_id:operation.clientId,output:value.output,workflow:value.workflow});
            assertCurrent(operation);
            if(key(operation.graph.serialize())!==serialized.fingerprint)throw new Error('提交前工作流被修改，未提交。');
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
            await applyNativeBindings(app,command,()=>assertCurrent(operation));
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
                if(active===operation){active=null;queueMicrotask(()=>{if(!stopped)onIdle();});}
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
                workflows:nativeCatalog(app),bindings_protocol:1,
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
    return {poll,execute,get busy(){return !!active;},changed(){epoch++;},stop(){stopped=true;epoch++;clearInterval(timer);if(app.queuePrompt===queueGate)app.queuePrompt=nativeQueue;}};
}
