import {workflowIdentity} from './workflow_sync.js';

export const BACKGROUND_CAPABILITY={version:'standard8-v1',frontend:'1.43.18',backend:'0.21.1',hooks_verified:true};
const clone=value=>JSON.parse(JSON.stringify(value));
const key=value=>JSON.stringify(value);

// One normal-close handoff. An absent browser heartbeat never grants ownership.
export function createBackgroundWorkflow({app,api,request,session,seeds,notice=()=>{},
    paused=()=>false,registerJob=async()=>{},sourceReceipt=()=>null,restoreSourceReceipt=()=>{},
    host=window,setTimer=setInterval,clearTimer=clearInterval}) {
    let stopped=false,pending=false,lease=null,scope=null,receipt=null,fingerprint='',editSeq=0;
    let timer=null,restored=null,firstLoad=true,reported='',subscription=null,blocked='',captureAttempt=null,composing=false;
    const originalLoad=app.loadGraphData;
    const identity=()=>workflowIdentity(app);
    const idle=()=>!stopped&&!blocked&&!pending&&!composing&&!paused()&&app.rootGraph&&app.rootGraph===app.graph&&!app.configuringGraph
        &&app.processingQueue===false&&Array.isArray(app.queueItems)&&!app.queueItems.length;
    const read=()=>({identity:identity(),visual:clone(app.rootGraph.serialize()),seed:seeds.capture(app.rootGraph),source_receipt:sourceReceipt()});
    const say=(text,error=false)=>{if(text!==reported){reported=text;notice(text,error);}};

    // Install before the first native load. Never clear an already-open graph
    // and never reinterpret a saved file as the background execution version.
    const load=async function(...args) {
        subscription=null;
        if(!firstLoad)return originalLoad.apply(this,args);
        firstLoad=false;
        if(app.extensionManager.workflow.activeWorkflow)return originalLoad.apply(this,args);
        const incoming=args[0],urlId=host.location?.hash?.slice(1)||'';
        let context;
        try {context=await request('workflow/background/context',{cold:true,identity:{frontend_id:urlId||incoming?.id||'',path:'',workflow:''}});}
        catch(error){blocked='背景工作流尚未接回：'+error.message;say(blocked,true);return originalLoad.apply(this,args);}
        const saved=context?.accepted;
        if(!saved)return originalLoad.apply(this,args);
        try {
        if(app.extensionManager.workflow.activeWorkflow||stopped)throw new Error('頁面已開始編輯，未覆蓋目前工作流。');
        // Claim the editor before exposing a restored graph. Closing before
        // the first capture must not leave its older released state runnable.
        lease=await request('workflow/background/lease',{key:context.key,session,base_revision:context.revision});
        const target=app.extensionManager.workflow.getWorkflowByPath('workflows/'+context.key.path);
        if(!target||typeof target.load!=='function')throw new Error('找不到背景任務原本的工作流，未建立同名替代品。');
        await target.load();
        const baseline=JSON.parse(target.originalContent);
        if(baseline.id!==context.key.frontend_id||saved.visual.id!==context.key.frontend_id
            ||app.extensionManager.workflow.activeWorkflow||stopped)throw new Error('背景工作流的原生身分無法核對。');
        const result=await originalLoad.call(this,clone(saved.visual),args[1],args[2],target,args[4]);
        if(app.extensionManager.workflow.activeWorkflow!==target||app.rootGraph.id!==context.key.frontend_id)
            throw new Error('原生工作流恢復尚未完成。');
        // Native load resets its baseline to the loaded data. Preserve the
        // actual file baseline so the restored unsaved edit remains modified.
        target.changeTracker.initialState=clone(baseline);
        target.changeTracker.checkState();target.isModified=key(baseline)!==key(target.activeState);
        seeds.restore(app.rootGraph,saved.seed);
        scope={key:context.key,identity:identity()};restoreSourceReceipt(context.source_receipt,context.key);
        restored={context,graph:app.rootGraph,identity:identity()};
        say('已接回關頁前確認的工作流。');
        return result;
        } catch(error) {
            blocked='背景工作流尚未接回：'+error.message;say(blocked,true);throw error;
        }
    };
    app.loadGraphData=load;

    async function refresh() {
        if(!idle()||!api.clientId)return;
        let current;
        try {current=read();}catch(error){say(error.message,true);return;}
        if(!current.identity.frontend_id||!current.identity.path)return;
        const before=key(current);
        if(receipt&&fingerprint===before)return;
        pending=true;
        try {
            if(captureAttempt) {
                // A lost capture response is retried with the identical
                // operation, never a fresh operation against an older base.
                const recovered=await request('workflow/background/capture',captureAttempt.body);
                if(recovered.committed!==true)throw new Error('最新工作流尚未確認保存。');
                receipt={...recovered,edit_seq:captureAttempt.body.edit_seq};fingerprint=captureAttempt.fingerprint;
                captureAttempt=null;
                if(before===fingerprint)return;
            }
            if(restored) {
                const value=restored;
                if(value.graph!==app.graph||key(value.identity)!==key(identity()))throw new Error('接回期間工作流已切換。');
                if(value.context.operation) {
                    let registration;
                    try {
                    registration=await registerJob(value.context.operation,app.extensionManager.workflow.activeWorkflow);
                    if(value.graph!==app.graph||key(value.identity)!==key(identity()))throw new Error('接回期間工作流已切換。');
                    subscription={id:crypto.randomUUID(),prompt:value.context.operation.prompt_id,
                        identity:key(identity()),graph:app.graph,seq:0,initialized:false,canReceive:registration.canReceive};
                    const attached=await request('workflow/background/attach',{key:value.context.key,op_id:value.context.operation.op_id,
                        revision:value.context.operation.revision,digest:value.context.operation.digest,new_client_id:api.clientId,
                        server_epoch:value.context.server_epoch,subscription_id:subscription.id});
                    if(attached.finished){subscription=null;registration.dispose?.();say('背景任務已完成，可在任務紀錄查看結果。');}
                    } catch(error) {
                        subscription=null;registration?.dispose?.();blocked='背景任務尚未接回：'+error.message;throw new Error(blocked);
                    }
                }
                restored=null;
            }
            if(!current.source_receipt)throw new Error('等待網頁確認目前的 PCS 文字來源。');
            if(!lease||key(current.identity)!==key(scope?.identity)) {
                // A switched-away writer is deliberately not released from a
                // different graph. Its latest state must be confirmed there.
                lease=null;receipt=null;fingerprint='';
                const context=await request('workflow/background/context',{identity:current.identity});
                if(!context.key)return;
                scope={key:context.key,identity:current.identity};
                lease=await request('workflow/background/lease',{key:context.key,session,base_revision:context.revision??0});
            }
            const value=await app.graphToPrompt();
            if(stopped||paused()||key(read())!==before)throw new Error('工作流正在修改，等待最新內容同步。');
            const seq=++editSeq;
            const captureBody={key:scope.key,...lease,
                op_id:crypto.randomUUID(),base_revision:receipt?.revision??lease.revision,edit_seq:seq,
                visual:value.workflow,output:value.output,seed:current.seed,capability:BACKGROUND_CAPABILITY,
                source_receipt:current.source_receipt};
            captureAttempt={body:captureBody,fingerprint:before};
            const accepted=await request('workflow/background/capture',captureBody);
            if(accepted.committed!==true)throw new Error('最新工作流尚未確認保存。');
            receipt={...accepted,edit_seq:seq};fingerprint=before;captureAttempt=null;
            if(stopped||key(read())!==before)throw new Error('工作流已再修改，尚未完成最新同步。');
            say('最新工作流已同步，可正常關閉網頁後由 PCS 執行。');
        } catch(error) {
            if(captureAttempt&&error.uncommittedOperation===captureAttempt.body.op_id)captureAttempt=null;
            say(error.message,true);
        }
        finally {pending=false;}
    }
    function pagehide(event) {
        // pagehide runs synchronously at the last observable editor state. The
        // small keepalive release contains only the committed receipt. If it
        // is lost, the server retains the writer and refuses background runs.
        const clean=idle()&&lease&&receipt&&!event.persisted;
        let same=false;
        try {same=clean&&key(read())===fingerprint;}catch{}
        stopped=true;subscription=null;if(timer!==null)clearTimer(timer);
        if(same)request('workflow/background/release',{key:scope.key,...lease,op_id:crypto.randomUUID(),
            revision:receipt.revision,digest:receipt.digest,edit_seq:receipt.edit_seq},{keepalive:true}).catch(()=>{});
    }
    function pageshow(event) {
        if(event.persisted){blocked='此頁面從瀏覽器快取返回，請重新整理後核對工作流再執行 PCS。';say(blocked,true);}
    }
    const compositionStart=()=>{composing=true;};
    const compositionEnd=()=>{composing=false;};
    function currentSubscription(envelope) {
        const s=subscription;
        if(stopped||!s||app.configuringGraph||app.graph!==s.graph||key(identity())!==s.identity
            ||envelope?.subscription_id!==s.id||envelope.prompt_id!==s.prompt
            ||typeof s.canReceive!=='function'||!s.canReceive())return null;
        return s;
    }
    const allowed=['execution_start','execution_cached','executing','progress','progress_state','executed',
        'execution_success','execution_error','execution_interrupted'];
    function project(envelope,s) {
        if(!allowed.includes(envelope.event)||envelope.data?.prompt_id!==s.prompt||!s.canReceive())return false;
        // Project a verified backend event through the native API event format.
        // No values are invented; executing is narrowed exactly like api.ts.
        const detail=envelope.event==='executing'?(envelope.data.display_node??envelope.data.node):envelope.data;
        api.dispatchEvent(new CustomEvent(envelope.event,{detail}));
        return true;
    }
    function snapshotReceived(event) {
        const value=event.detail,s=currentSubscription(value);
        if(!s||s.initialized||!Number.isSafeInteger(value.watermark)||!Array.isArray(value.events))return;
        let previous=0;
        for(const item of value.events) {
            if(!Number.isSafeInteger(item.seq)||item.seq<=previous||item.seq>value.watermark
                ||!allowed.includes(item.event)||item.data?.prompt_id!==s.prompt) {
                subscription=null;return;
            }
            previous=item.seq;
        }
        for(const item of value.events)if(!project(item,s)){subscription=null;return;}
        s.seq=value.watermark;s.initialized=true;
    }
    function eventReceived(event) {
        const value=event.detail,s=currentSubscription(value);
        if(!s||!s.initialized||!Number.isSafeInteger(value.seq)||value.seq<=s.seq)return;
        if(value.seq!==s.seq+1){subscription=null;say('執行進度連線中斷，請重新開啟頁面接回。',true);return;}
        if(project(value,s))s.seq=value.seq;
    }
    host.addEventListener('pagehide',pagehide);
    host.addEventListener('pageshow',pageshow);
    host.addEventListener('compositionstart',compositionStart,true);
    host.addEventListener('compositionend',compositionEnd,true);
    api.addEventListener('pcs_background_event',eventReceived);
    api.addEventListener('pcs_background_snapshot',snapshotReceived);
    timer=setTimer(refresh,500);
    return {refresh,get receipt(){return receipt&&clone(receipt);},get pending(){return pending;},get blocked(){return blocked;},
        stop(){stopped=true;subscription=null;clearTimer(timer);host.removeEventListener('pagehide',pagehide);
            host.removeEventListener('pageshow',pageshow);
            host.removeEventListener('compositionstart',compositionStart,true);
            host.removeEventListener('compositionend',compositionEnd,true);
            api.removeEventListener('pcs_background_event',eventReceived);api.removeEventListener('pcs_background_snapshot',snapshotReceived);
            if(app.loadGraphData===load)app.loadGraphData=originalLoad;}};
}
