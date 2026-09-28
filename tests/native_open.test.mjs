import test from 'node:test';
import assert from 'node:assert/strict';
import {installNativeQueue} from '../comfyui_prompt_studio/web/native_queue.js';
import {nativeCatalog,nativeIdentity} from '../comfyui_prompt_studio/web/native_open.js';

function fixture() {
    globalThis.document=new EventTarget();
    const a={path:'workflows/A.json',isLoaded:true,activeState:{id:'native-A',nodes:[{id:1,widgets_values:['saved A']}]},
        changeTracker:{undoQueue:[{id:'older'}],checkState(){throw new Error('confirmation must not save a draft');}}};
    const b={path:'workflows/B.json',isLoaded:true,activeState:{id:'native-B',nodes:[]}};
    const graph={id:'native-A',nodes:[{id:1,widgets_values:['unsaved A']}]};
    const calls=[],store={activeWorkflow:a,workflows:[a,b]};
    const app={graph,rootGraph:graph,extensionManager:{workflow:store},processingQueue:false,queueItems:[],
        async loadGraphData(){calls.push('load');throw new Error('must never enter native load');},
        async queuePrompt(){calls.push('queue');}};
    const adapter=installNativeQueue(app,{clientId:'sid'},async()=>({}),'tab');
    return {a,b,graph,app,store,calls,adapter,command:(target,epoch=0)=>({id:'confirm',action:'open',epoch,identity:nativeIdentity(store.activeWorkflow),target:nativeIdentity(target)})};
}

test('current workflow confirmation is read-only and preserves native dirty state and history',async()=>{
    const f=fixture(),before=JSON.stringify([f.a.activeState,f.a.changeTracker.undoQueue,f.graph]),load=f.app.loadGraphData;
    try {
        assert.deepEqual((await f.adapter.execute(f.command(f.a))).opened_identity,nativeIdentity(f.a));
        assert.equal(JSON.stringify([f.a.activeState,f.a.changeTracker.undoQueue,f.graph]),before);
        assert.equal(f.app.loadGraphData,load);assert.deepEqual(f.calls,[]);assert.equal(f.adapter.busy,false);
        const event=new Event('keydown',{cancelable:true});document.dispatchEvent(event);assert.equal(event.defaultPrevented,false);
    } finally {f.adapter.stop();}
});

test('unknown tracker lifecycle refuses switching before any load or draft mutation',async()=>{
    const f=fixture(),before=JSON.stringify([f.a.activeState,f.a.changeTracker.undoQueue,f.graph]);
    try {
        const result=await f.adapter.execute(f.command(f.b));
        assert.match(result.error,/不支援安全切換/);assert.equal(result.uncertain,false);assert.equal(result.opened_identity,undefined);
        assert.deepEqual(f.calls,[]);assert.equal(f.store.activeWorkflow,f.a);assert.equal(f.adapter.busy,false);
        assert.equal(JSON.stringify([f.a.activeState,f.a.changeTracker.undoQueue,f.graph]),before);
    } finally {f.adapter.stop();}
});

test('missing, duplicate, wrong-ID, and unloaded native targets cannot be confirmed by name',async()=>{
    const f=fixture();
    try {
        const wrong=f.command(f.a);wrong.target.frontend_id='A-copy';assert.match((await f.adapter.execute(wrong)).error,/原件/);
        f.a.isLoaded=false;assert.equal(nativeCatalog(f.app).length,1);assert.match((await f.adapter.execute(f.command(f.a))).error,/原件/);
        f.a.isLoaded=true;f.store.workflows.push({...f.a});assert.match((await f.adapter.execute(f.command(f.a))).error,/唯一/);
        assert.deepEqual(f.calls,[]);
    } finally {f.adapter.stop();}
});

test('wrong live root, subgraph, configuring graph and stale session refuse confirmation',async()=>{
    const f=fixture();
    try {
        f.graph.id='native-copy';assert.match((await f.adapter.execute(f.command(f.a))).error,/確認/);
        f.graph.id='native-A';f.app.graph={id:'subgraph'};assert.match((await f.adapter.execute(f.command(f.a))).error,/切換/);
        f.app.graph=f.graph;f.app.configuringGraph=true;assert.match((await f.adapter.execute(f.command(f.a))).error,/切換/);
        f.app.configuringGraph=false;f.adapter.changed();assert.match((await f.adapter.execute(f.command(f.a))).error,/切換/);
        assert.deepEqual(f.calls,[]);
    } finally {f.adapter.stop();}
});
