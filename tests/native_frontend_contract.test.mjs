// Optional source-backed offline check. Executes the installed frontend's
// ChangeTracker class, with DOM/stores/diff services isolated. This is neither
// a running browser nor a GPU acceptance test. No installed file is modified.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import * as nodeModule from 'node:module';
import {isDeepStrictEqual} from 'node:util';
import {installNativeQueue} from '../comfyui_prompt_studio/web/native_queue.js';
import {nativeIdentity} from '../comfyui_prompt_studio/web/native_open.js';

const sourceMap=process.env.PCS_FRONTEND_SOURCE_MAP;
test('installed native ChangeTracker survives inspect A to B, unsaved edits, switch back, undo and next native submission',
    {skip:!sourceMap},async()=>{
    const map=JSON.parse(readFileSync(sourceMap,'utf8'));
    const source=map.sourcesContent[map.sources.findIndex(name=>name.endsWith('/scripts/changeTracker.ts'))];
    assert.ok(source?.includes('export class ChangeTracker'));
    // Keep the real class implementation, removing only imports/type syntax.
    assert.equal(typeof nodeModule.stripTypeScriptTypes,'function','Source-backed check requires Node.js with TypeScript stripping support');
    const plain=nodeModule.stripTypeScriptTypes(source.replace(/^import[\s\S]*? from ['"][^'"]+['"]\s*$/gm,''),{mode:'transform'}).replace('export class ChangeTracker','class ChangeTracker');
    const copy=value=>structuredClone(value),store={workflows:[],activeWorkflow:null,getWorkflowByPath(path){return this.workflows.find(w=>w.path===path);}};
    const graph={state:null,get id(){return this.state.id;},serialize(){return copy(this.state);}};
    const app={graph,rootGraph:graph,canvas:{ds:{scale:1,offset:[0,0]},setGraph(g){app.graph=g;}},extensionManager:{workflow:store},processingQueue:false,queueItems:[]};
    globalThis.document=new EventTarget();globalThis.window=new EventTarget();
    const sent=[],api=Object.assign(new EventTarget(),{clientId:'live-session',dispatchCustomEvent(name,value){this.dispatchEvent(new CustomEvent(name,{detail:value}));},
        async queuePrompt(_number,payload){sent.push(copy(payload));return {prompt_id:'job-'+sent.length};}});
    const log={getLogger(){return {setLevel(){},debug(){},getLevel(){return 1;},levels:{DEBUG:0}};}};
    const lodash={isEqual:isDeepStrictEqual,isEqualWith:isDeepStrictEqual,omit(value,keys){return Object.fromEntries(Object.entries(value).filter(([key])=>!keys.includes(key)));}};
    const outputs={snapshotOutputs:()=>({oldPreview:'kept'}),restoreOutputs(){}},navigation={exportState:()=>[],restoreState(){}};
    const Tracker=Function('app','api','useWorkflowStore','useNodeOutputStore','useSubgraphNavigationStore','_','log',plain+';return ChangeTracker;')(
        app,api,()=>store,()=>outputs,()=>navigation,lodash,log);
    for(const name of ['A','B']){
        const w={path:'workflows/'+name+'.json',isLoaded:true,get activeState(){return this.changeTracker.activeState;}};
        w.changeTracker=new Tracker(w,{id:'native-'+name,nodes:[{id:1,type:'CLIPTextEncode',widgets_values:['saved '+name]}]});
        w.changeTracker.activeState=copy(w.activeState);w.activeState.nodes[0].widgets_values=['unsaved '+name];
        store.workflows.push(w);
    }
    const [a,b]=store.workflows;store.activeWorkflow=a;graph.state=copy(a.activeState);
    let adapter;
    app.loadGraphData=async(state,clean,restore,destination)=>{
        store.activeWorkflow.changeTracker.store();
        if(clean)graph.state={id:state.id,nodes:[]};
        await Promise.resolve(); // Native clean happens before its await/configure.
        Tracker.isLoadingGraph=true;app.configuringGraph=true;
        try {
            graph.state=copy(state);adapter.changed();
            // Actual lifecycle: afterConfigureGraph precedes activeWorkflow.
            store.activeWorkflow.changeTracker.checkState();
            store.activeWorkflow=destination;destination.changeTracker.reset(copy(state));
            if(restore)destination.changeTracker.restore();
        } finally {app.configuringGraph=false;Tracker.isLoadingGraph=false;}
    };
    app.graphToPrompt=async()=>({workflow:graph.serialize(),output:{'1':{class_type:'CLIPTextEncode',inputs:{text:graph.state.nodes[0].widgets_values[0]}}}});
    app.queuePrompt=async()=>{app.processingQueue=true;try{return await api.queuePrompt(0,await app.graphToPrompt());}finally{app.processingQueue=false;}};
    const requests=[];
    adapter=installNativeQueue(app,api,async(route,value)=>{requests.push({route,value});return {};},'tab');
    try {
        graph.state.nodes.push({id:2,type:'CLIPTextEncode',widgets_values:['new unsaved CLIP']});
        const read=await adapter.execute({id:'read-B',epoch:0,identity:nativeIdentity(a),target:nativeIdentity(b),action:'inspect'});
        assert.equal(read.error,undefined);assert.equal(read.inspection.output['1'].inputs.text,'unsaved B');assert.equal(sent.length,0);
        assert.equal(a.activeState.nodes.length,2);assert.equal(a.changeTracker.undoQueue.length,1);
        assert.equal(a.changeTracker.initialState.nodes[0].widgets_values[0],'saved A');
        assert.equal(b.changeTracker.initialState.nodes[0].widgets_values[0],'saved B');
        const run=await adapter.execute({id:'run-A',epoch:1,identity:nativeIdentity(b),target:nativeIdentity(a)});
        assert.equal(run.prompt_id,'job-1');assert.equal(sent[0].workflow.nodes.length,2);
        await a.changeTracker.undo();assert.equal(graph.state.nodes.length,1);
        assert.equal(a.changeTracker.redoQueue.length,1);
        assert.equal(requests.filter(r=>r.route==='workflow/native/prepare').length,1);
        await app.queuePrompt();assert.equal(sent.length,2);
    } finally {adapter.stop();}
});
