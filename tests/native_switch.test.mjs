// Offline model of the v1.43.18 changeTracker/loadGraphData lifecycle. Includes
// its clean-before-await gap and reset of initialState; not real frontend QA.
import test from 'node:test';
import assert from 'node:assert/strict';
import {installNativeQueue} from '../comfyui_prompt_studio/web/native_queue.js';
import {nativeIdentity} from '../comfyui_prompt_studio/web/native_open.js';
const copy=value=>JSON.parse(JSON.stringify(value));

function fixture(timeout=12000) {
    globalThis.document=new EventTarget();globalThis.window=new EventTarget();
    let adapter;
    const graph={state:null,get id(){return this.state.id;},serialize(){return copy(this.state);}};
    const store={workflows:[],activeWorkflow:null};
    function workflow(name,text) {
        const state={id:'native-'+name,nodes:[{id:1,type:'CLIPTextEncode',widgets_values:[text]}]};
        const w={path:'workflows/'+name+'.json',isLoaded:true,get activeState(){return this.changeTracker.activeState;}};
        const t=w.changeTracker={activeState:copy(state),initialState:copy(state),changeCount:0,undoQueue:[],redoQueue:[],
            checkState(){const value=graph.serialize();if(JSON.stringify(value)!==JSON.stringify(this.activeState)){this.undoQueue.push(this.activeState);this.activeState=value;this.redoQueue.length=0;}},
            store(){this.ds={scale:1,offset:[0,0]};this.nodeOutputs={preview:'kept-'+name};},
            updateModified(){w.isModified=JSON.stringify(this.initialState)!==JSON.stringify(this.activeState);},
            async updateState(source,target){const previous=source.pop();if(previous){target.push(this.activeState);this._restoringState=true;try{await app.loadGraphData(previous,false,false,w);this.activeState=previous;}finally{this._restoringState=false;}}},
            async undo(){await this.updateState(this.undoQueue,this.redoQueue);}};
        t.undoQueue.push({...copy(state),nodes:[]});
        store.workflows.push(w);return w;
    }
    const a=workflow('A','unsaved A'),b=workflow('B','');
    a.changeTracker.initialState.nodes[0].widgets_values=['saved A'];
    b.changeTracker.initialState.nodes[0].widgets_values=['saved B'];
    graph.state=copy(a.activeState);store.activeWorkflow=a;
    const sent=[],requests=[],loads=[];
    const api={clientId:'sid',async queuePrompt(_number,payload){sent.push(copy(payload));return {prompt_id:'job-'+sent.length};}};
    const app={graph,rootGraph:graph,extensionManager:{workflow:store},processingQueue:false,queueItems:[],
        async loadGraphData(state,clean,restore,destination,options){
            loads.push(destination.path);store.activeWorkflow.changeTracker.store();
            if(clean)graph.state={id:state.id,nodes:[]};
            await app.onClean?.(destination);
            app.configuringGraph=true;graph.state=copy(state);adapter.changed();
            store.activeWorkflow=destination;
            destination.changeTracker.activeState=copy(state);destination.changeTracker.initialState=copy(state);
            app.configuringGraph=false;
        },
        async graphToPrompt(){return {workflow:graph.serialize(),output:{'1':{class_type:'CLIPTextEncode',inputs:{text:graph.state.nodes[0].widgets_values[0]}}}};},
        async queuePrompt(){this.processingQueue=true;try{return await api.queuePrompt(0,await this.graphToPrompt());}finally{this.processingQueue=false;}}};
    const request=async(route,value)=>{if(route!=='workflow/native/event')requests.push({route,value:copy(value)});return {};};
    adapter=installNativeQueue(app,api,request,'tab',()=>{},timeout);
    const command={id:'switch',action:'open',identity:nativeIdentity(a),epoch:0,target:nativeIdentity(b)};
    return {app,api,graph,store,a,b,adapter,command,sent,requests,loads};
}

for(const action of ['open','queue'])test(`hung ${action} load releases page controls, protects drafts and fences a late load until native recovery`,async()=>{
    const f=fixture(25),source=copy(f.a.activeState),target=copy(f.b.activeState);
    let finish;
    f.app.onClean=()=>new Promise(resolve=>finish=resolve);
    try {
        const result=await f.adapter.execute({...f.command,action});
        assert.match(result.error,/逾時/);
        await new Promise(resolve=>setTimeout(resolve,10));
        assert.equal(f.adapter.busy,false);assert.equal(f.sent.length,0);
        const pointer=new Event('pointerdown',{cancelable:true});window.dispatchEvent(pointer);assert.equal(pointer.defaultPrevented,false);
        f.a.changeTracker.checkState();await f.a.changeTracker.undo();
        assert.deepEqual(f.a.activeState,source);assert.deepEqual(f.b.activeState,target);
        await assert.rejects(f.app.loadGraphData(source,true,true,f.a),/重新整理/);
        assert.equal(f.loads.length,1,'no overlapping rollback while the native loader is pending');
        f.app.onClean=null;finish();await new Promise(resolve=>setImmediate(resolve));
        assert.deepEqual(f.a.activeState,source);assert.deepEqual(f.b.activeState,target);
        assert.equal(f.sent.length,0,'late load cannot continue the cancelled submission');
        await f.app.loadGraphData(source,true,true,f.a);
        await f.app.queuePrompt();assert.equal(f.sent.length,1);
    } finally {finish?.();f.adapter.stop();}
});

test('inspect switches through the same native owner, returns unsaved draft, and never invokes queue or seeds',async()=>{
    const f=fixture(),source=copy(f.a.activeState),aUndo=copy(f.a.changeTracker.undoQueue),bUndo=copy(f.b.changeTracker.undoQueue);
    try {
        const result=await f.adapter.execute({...f.command,action:'inspect',texts:[{node:'1',field:'text',text:'must not apply',text_source:'pcs',class_type:'CLIPTextEncode'}]});
        assert.equal(result.error,undefined);assert.equal(result.inspection.workflow.id,'native-B');
        assert.equal(result.inspection.output['1'].inputs.text,'');
        assert.equal(result.inspection.identity.frontend_id,'native-B');assert.equal(f.sent.length,0);
        assert.deepEqual(f.requests.map(r=>r.route),['workflow/native/activate']);
        assert.deepEqual(f.a.activeState,source);assert.deepEqual(f.a.changeTracker.undoQueue,aUndo);assert.deepEqual(f.b.changeTracker.undoQueue,bUndo);
        const generated=await f.adapter.execute({...f.command,action:'queue',epoch:1,identity:nativeIdentity(f.b)});
        assert.equal(generated.prompt_id,'job-1');assert.equal(f.sent.length,1);
    } finally {f.adapter.stop();}
});

test('switch uses exact native draft, retains both saved baselines and undo, and queue uses the selected target',async()=>{
    const f=fixture(),source=copy(f.a.activeState),aUndo=copy(f.a.changeTracker.undoQueue),bUndo=copy(f.b.changeTracker.undoQueue);
    try {
        const result=await f.adapter.execute({...f.command,action:'queue'});
        assert.equal(result.error,undefined);assert.equal(result.prompt_id,'job-1');
        assert.equal(f.store.activeWorkflow,f.b);assert.deepEqual(f.a.activeState,source);
        assert.deepEqual(f.a.changeTracker.undoQueue,aUndo);assert.deepEqual(f.b.changeTracker.undoQueue,bUndo);
        assert.equal(f.a.changeTracker.initialState.nodes[0].widgets_values[0],'saved A');
        assert.equal(f.b.changeTracker.initialState.nodes[0].widgets_values[0],'saved B');
        assert.equal(f.a.isModified,true);assert.equal(f.b.isModified,true);
        assert.equal(f.sent[0].output['1'].inputs.text,'');
        assert.deepEqual(f.requests.map(r=>r.route),['workflow/native/activate','workflow/native/prepare']);
        assert.equal(f.requests[0].value.opened_epoch,1);
        assert.equal(f.requests[1].value.workflow.id,'native-B');
        assert.equal(f.adapter.busy,false);
    } finally {f.adapter.stop();}
});

test('N03 queued window checkState and undo during clean cannot overwrite either draft or move undo entries',async()=>{
    const f=fixture(),source=copy(f.a.activeState),undo=copy(f.a.changeTracker.undoQueue);
    let release,entered;
    const cleaned=new Promise(resolve=>entered=resolve);
    f.app.onClean=async()=>{entered();await new Promise(resolve=>release=resolve);};
    const loader=f.app.loadGraphData;
    try {
        const pending=f.adapter.execute(f.command);await cleaned;
        assert.equal(f.graph.state.nodes.length,0);
        // An already queued native window callback runs after the input guard.
        f.a.changeTracker.checkState();await f.a.changeTracker.undo();
        assert.deepEqual(f.a.activeState,source);assert.deepEqual(f.a.changeTracker.undoQueue,undo);
        assert.equal(f.a.changeTracker.redoQueue.length,0);
        await assert.rejects(async()=>f.app.loadGraphData(source,true,true,f.a),/等候/);
        const event=new Event('keydown',{cancelable:true});window.dispatchEvent(event);assert.equal(event.defaultPrevented,true);
        assert.equal(f.adapter.busy,true);release();
        const result=await pending;assert.equal(result.error,undefined);assert.equal(result.opened_identity.frontend_id,'native-B');
        assert.equal(f.app.loadGraphData,loader);assert.equal(f.sent.length,0);
        const after=new Event('keydown',{cancelable:true});window.dispatchEvent(after);assert.equal(after.defaultPrevented,false);
        f.app.onClean=null;await f.b.changeTracker.undo();assert.equal(f.b.changeTracker.undoQueue.length,0);
    } finally {f.adapter.stop();}
});

test('failed native target load restores source draft and leaves both undo stacks usable without generating',async()=>{
    const f=fixture(),source=copy(f.a.activeState),target=copy(f.b.activeState),loader=f.app.loadGraphData;
    f.app.onClean=async destination=>{if(destination===f.b)throw new Error('target load failed');};
    try {
        const result=await f.adapter.execute({...f.command,action:'queue'});
        assert.match(result.error,/target load failed/);assert.equal(f.sent.length,0);assert.equal(f.requests.length,0);
        assert.equal(f.store.activeWorkflow,f.a);assert.deepEqual(f.graph.serialize(),source);
        assert.deepEqual(f.a.activeState,source);assert.deepEqual(f.b.activeState,target);
        assert.equal(f.app.loadGraphData,loader);assert.equal(f.adapter.busy,false);
        assert.equal(f.a.changeTracker.undoQueue.length,1);assert.equal(f.b.changeTracker.undoQueue.length,1);
    } finally {f.adapter.stop();}
});

for(const action of ['queue','open'])test(`failed ${action} rollback protects drafts while releasing native controls and allows native recovery`,async()=>{
    const f=fixture(),source=copy(f.a.activeState),target=copy(f.b.activeState),loader=f.app.loadGraphData;
    f.app.onClean=async()=>{throw new Error('loader failed');};
    try {
        const result=await f.adapter.execute({...f.command,action});
        assert.match(result.error,/重新整理/);assert.equal(f.adapter.busy,false);
        f.a.changeTracker.checkState();await f.a.changeTracker.undo();
        assert.deepEqual(f.a.activeState,source);assert.deepEqual(f.b.activeState,target);
        assert.equal(f.a.changeTracker.undoQueue.length,1);assert.equal(f.sent.length,0);
        assert.match((await f.adapter.execute(f.command)).error,/重新開啟工作流/);
        const click=new Event('click',{cancelable:true});window.dispatchEvent(click);assert.equal(click.defaultPrevented,false);
        // Native Run reaches its own serializer, even while the partial graph
        // still needs loading. It never waits for PCS pause/reconciliation.
        const serialize=f.app.graphToPrompt;let called=false;
        f.app.graphToPrompt=async()=>{called=true;throw new Error('native graph is incomplete');};
        await assert.rejects(f.app.queuePrompt(),/native graph is incomplete/);assert.equal(called,true);
        f.app.graphToPrompt=serialize;f.app.onClean=null;
        await f.app.loadGraphData(source,true,true,f.a);
        assert.equal(f.app.loadGraphData,loader);
        await f.app.queuePrompt();assert.equal(f.sent.length,1);
        await f.a.changeTracker.undo();assert.equal(f.a.changeTracker.undoQueue.length,0);
    } finally {f.adapter.stop();}
});

test('a native load already awaiting validation cannot be mistaken for an idle graph or captured into a draft',async()=>{
    const f=fixture(),source=copy(f.a.activeState);let release,entered;
    const cleaned=new Promise(resolve=>entered=resolve);
    f.app.onClean=async()=>{entered();await new Promise(resolve=>release=resolve);};
    try {
        const ordinary=f.app.loadGraphData(copy(f.b.activeState),true,true,f.b);await cleaned;
        const result=await f.adapter.execute(f.command);
        assert.match(result.error,/正在載入/);assert.deepEqual(f.a.activeState,source);
        assert.equal(f.loads.length,1);assert.equal(f.sent.length,0);release();await ordinary;
    } finally {f.adapter.stop();}
});
