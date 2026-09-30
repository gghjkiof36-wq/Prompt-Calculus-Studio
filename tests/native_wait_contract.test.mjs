// Execute the installed frontend method, with only external services isolated.
// This is a source-backed contract check, not live browser or GPU validation.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {stripTypeScriptTypes} from 'node:module';
import {installNativeQueue} from '../comfyui_prompt_studio/web/native_queue.js';
const mapPath=process.env.PCS_FRONTEND_SOURCE_MAP;
const tick=()=>new Promise(resolve=>setImmediate(resolve));

function fixture(waitAt,bound=false) {
    const map=JSON.parse(readFileSync(mapPath,'utf8'));
    const source=map.sourcesContent[map.sources.findIndex(name=>name.endsWith('/scripts/app.ts'))];
    assert.ok(source.includes('await useAuthStore().getAuthToken()'));
    const method=source.slice(source.indexOf('  async queuePrompt('),source.indexOf('  showErrorOnFileLoad('));
    const plain=stripTypeScriptTypes('class Original {\n'+method+'\n}',{mode:'transform'});
    const graph={id:'native-A',_nodes:[],serialize(){return {id:this.id,nodes:[]};}};
    let seed=0,after=0,finish,authCalls=0,updates=0;
    graph._nodes=[{widgets:[{beforeQueued(){seed++;},afterQueued(){after++;}}]}];
    const sent=[],jobs=[],events=[];
    const api=Object.assign(new EventTarget(),{clientId:'sid',dispatchCustomEvent(name,value){events.push({name,value});},
        async queuePrompt(_number,p){sent.push(p);return {prompt_id:'job-'+sent.length};}});
    const app={rootGraph:graph,graph,queueItems:[],processingQueue:false,nextQueueRequestId:1,
        extensionManager:{workflow:{activeWorkflow:{path:'workflows/A.json',activeState:{id:graph.id}}}},
        canvas:{draw(){}},ui:{queue:{update(){return ++updates===1&&waitAt==='ui'?new Promise(resolve=>finish=resolve):Promise.resolve();}}},
        async graphToPrompt(){return {workflow:graph.serialize(),output:{'1':{class_type:'Test',inputs:{seed}}}};}};
    const errors={clearAllErrors(){}};
    const deps={api,useExecutionStore:()=>({storeJob:j=>jobs.push(j)}),useExecutionErrorStore:()=>errors,
        useAuthStore:()=>({getAuthToken(){return ++authCalls===1&&waitAt==='auth'?new Promise(resolve=>finish=resolve):Promise.resolve();}}),
        useApiKeyAuthStore:()=>({getApiKey(){}}),useSettingStore:()=>({get(){}}),useWorkspaceStore:()=>({workflow:app.extensionManager.workflow}),
        forEachNode:(g,fn)=>g._nodes.forEach(fn),collectAllNodes:g=>g._nodes,
        executeWidgetsCallback:(nodes,hook,arg)=>nodes.forEach(n=>n.widgets.forEach(w=>w[hook]?.(arg))),
        PromptExecutionError:class extends Error{},useDialogService:()=>({showErrorDialog(){}}),t:x=>x};
    app.queuePrompt=Function(...Object.keys(deps),plain+';return Original.prototype.queuePrompt;')(...Object.values(deps));
    if(bound)app.queuePrompt=app.queuePrompt.bind(app);
    globalThis.document=new EventTarget();
    const diagnostics=[];
    const adapter=installNativeQueue(app,api,async(route,value)=>{
        if(route==='workflow/native/event')diagnostics.push(value.event);
        return {};
    },'tab',()=>{},25);
    return {app,adapter,sent,jobs,events,diagnostics,get seed(){return seed;},get after(){return after;},finish:()=>finish?.(),
        command:{id:'old-operation',epoch:0,identity:{workflow:'',path:'A.json',frontend_id:graph.id}}};
}

for(const waitAt of ['auth','ui'])test(`original native ${waitAt} await cannot hold PCS or consume a later native Run`,{skip:!mapPath},async()=>{
    const f=fixture(waitAt);
    try {
        const result=await f.adapter.execute(f.command);
        assert.match(result.error,/逾時/);assert.equal(f.adapter.busy,false);assert.equal(f.app.processingQueue,false);
        assert.equal(f.app.queueItems.length,0);
        assert.equal(f.sent.length,waitAt==='auth'?0:1);
        await f.app.queuePrompt(0,1);
        const count=waitAt==='auth'?1:2;
        assert.equal(f.sent.length,count);assert.equal(f.seed,count);assert.equal(f.after,count);
        f.finish();await tick();await tick();
        assert.equal(f.sent.length,count,'late auth must not submit another job');
        assert.equal(f.seed,count);assert.equal(f.after,count);assert.equal(f.jobs.length,count);
        assert.equal(f.app.processingQueue,false);assert.equal(f.app.nextQueueRequestId,3);
        assert.ok(f.diagnostics.some(e=>e.state==='timeout'&&e.phase===(waitAt==='auth'?'native_wait':'native_finish')));
    } finally {f.finish();f.adapter.stop();}
});

test('a third-party bound queue cannot bypass guards through a late auth continuation', {skip:!mapPath},async()=>{
    const f=fixture('auth',true);
    try {
        const result=await f.adapter.execute(f.command);assert.match(result.error,/重新整理/);
        assert.equal(f.adapter.busy,true);assert.equal(f.sent.length,0);
        f.finish();await tick();await tick();
        assert.equal(f.sent.length,0,'unguarded transport must remain unavailable to the old call');
        assert.equal(f.adapter.busy,false);assert.equal(f.app.processingQueue,false);
        await f.app.queuePrompt(0,1);assert.equal(f.sent.length,1);
    } finally {f.finish();f.adapter.stop();}
});
