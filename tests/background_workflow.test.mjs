import test from 'node:test';
import assert from 'node:assert/strict';
import {createBackgroundWorkflow} from '../comfyui_prompt_studio/web/background_workflow.js';

function deferred(){let resolve;const promise=new Promise(r=>resolve=r);return {promise,resolve};}
function fixture(){
    const host=new EventTarget(),api=new EventTarget();host.location={hash:''};api.clientId='browser';
    const visual={id:'native',nodes:[{id:1,type:'KSampler',widgets_values:[42]}]};
    const graph={id:'native',serialize:()=>structuredClone(visual)};
    const active={path:'workflows/A.json',activeState:visual};
    const app={graph,rootGraph:graph,processingQueue:false,queueItems:[],
        extensionManager:{workflow:{activeWorkflow:active}},async loadGraphData(){},
        async graphToPrompt(){return {workflow:structuredClone(visual),output:{'1':{class_type:'KSampler',inputs:{seed:42}}}};}};
    const calls=[],gate={capture:null},seeds={capture:()=>({node_id:'1',seed:42,timing:'after',hasExecuted:false})};
    let revision=0;
    const request=async(path,data,options)=>{
        calls.push({path,data:structuredClone(data),options});
        if(path.endsWith('/context'))return {key:{frontend_id:'native',path:'A.json'},revision};
        if(path.endsWith('/lease'))return {lease_id:'lease',lease_epoch:1,server_epoch:'server',revision};
        if(path.endsWith('/capture')){
            if(gate.reject){const error=new Error('capture rejected');if(gate.reject==='known')error.uncommittedOperation=data.op_id;throw error;}
            if(gate.capture)await gate.capture.promise;return {committed:true,revision:++revision,digest:'a'.repeat(64),edit_seq:data.edit_seq};}
        if(path.endsWith('/release'))return {released:true};
    };
    const controller=createBackgroundWorkflow({app,api,host,request,session:'s',seeds,sourceReceipt:()=>({owner:'test',source_revision:gate.source??'A',texts:[]}),setTimer:()=>1,clearTimer:()=>{}});
    const close=persisted=>{const e=new Event('pagehide');e.persisted=persisted;host.dispatchEvent(e);};
    return {controller,calls,app,visual,gate,close,request,host};
}
test('normal close releases only an acknowledged identical live version via small keepalive receipt',async()=>{
    const f=fixture();await f.controller.refresh();f.close(false);
    const release=f.calls.at(-1);assert.match(release.path,/release$/);assert.equal(release.options.keepalive,true);
    assert.equal(release.data.revision,1);assert.equal(release.data.edit_seq,1);assert.equal('visual' in release.data,false);
});
test('last edit after ack cannot release the older version',async()=>{
    const f=fixture();await f.controller.refresh();f.visual.nodes[0].widgets_values[0]=43;f.close(false);
    assert.equal(f.calls.some(c=>c.path.endsWith('/release')),false);
});
test('closing during capture or before any acknowledgement never releases',async()=>{
    const f=fixture();f.gate.capture=deferred();const pending=f.controller.refresh();await new Promise(r=>setImmediate(r));
    f.close(false);f.gate.capture.resolve();await pending;
    assert.equal(f.calls.some(c=>c.path.endsWith('/release')),false);
});
test('back-forward cache is not a clean close',async()=>{
    const f=fixture();await f.controller.refresh();f.close(true);
    assert.equal(f.calls.some(c=>c.path.endsWith('/release')),false);
    const show=new Event('pageshow');show.persisted=true;f.host.dispatchEvent(show);
    assert.match(f.controller.blocked,/重新整理/);
});

test('unfinished IME composition cannot release a previously acknowledged graph',async()=>{
    const f=fixture();await f.controller.refresh();f.host.dispatchEvent(new Event('compositionstart'));
    f.close(false);assert.equal(f.calls.some(c=>c.path.endsWith('/release')),false);
    const g=fixture();await g.controller.refresh();g.host.dispatchEvent(new Event('compositionstart'));
    g.host.dispatchEvent(new Event('compositionend'));g.visual.nodes[0].widgets_values[0]=44;
    g.close(false);assert.equal(g.calls.some(c=>c.path.endsWith('/release')),false);
});
test('editing during an accepted capture advances receipt but requires another capture before close',async()=>{
    const f=fixture();f.gate.capture=deferred();const pending=f.controller.refresh();await new Promise(r=>setImmediate(r));
    f.visual.nodes[0].widgets_values[0]=43;f.gate.capture.resolve();await pending;
    assert.equal(f.controller.receipt.revision,1);f.gate.capture=null;await f.controller.refresh();
    assert.equal(f.calls.filter(c=>c.path.endsWith('/capture')).at(-1).data.base_revision,1);
    f.close(false);assert.match(f.calls.at(-1).path,/release$/);
});
test('no graphToPrompt or network work occurs during a native queue submission',async()=>{
    const f=fixture();f.app.processingQueue=true;await f.controller.refresh();assert.equal(f.calls.length,0);f.controller.stop();
});

function coldFixture({attachFailure=false,leaseFailure=false}={}) {
    const host=new EventTarget(),api=new EventTarget();host.location={hash:'#native'};api.clientId='browser';
    const baseline={id:'native',nodes:[{id:2,type:'EmptyLatentImage',widgets_values:[832,1216,1]}]};
    const accepted=structuredClone(baseline);accepted.nodes[0].widgets_values=[1216,832,2];
    let visual=structuredClone(baseline),canReceive=true;
    const graph={id:'native',serialize:()=>structuredClone(visual)};
    const target={path:'workflows/A.json',originalContent:JSON.stringify(baseline),activeState:null,
        async load(){},changeTracker:{checkState(){}}};
    const store={activeWorkflow:null,getWorkflowByPath:p=>p===target.path?target:null};
    const loaded=[],restoredSeeds=[],calls=[],delivered=[];
    const app={rootGraph:graph,graph,processingQueue:false,queueItems:[],extensionManager:{workflow:store},
        async loadGraphData(value,a,b,workflow){visual=structuredClone(value);store.activeWorkflow=workflow;target.activeState=visual;loaded.push(value);},
        async graphToPrompt(){return {workflow:structuredClone(visual),output:{}};}};
    const operation={op_id:'op',prompt_id:'real-prompt',revision:7,digest:'a'.repeat(64),key:{frontend_id:'native',path:'A.json'}};
    const context={key:operation.key,server_epoch:'server',revision:8,accepted:{visual:accepted,seed:{hasExecuted:true}},operation};
    const request=async(path,data)=>{
        calls.push({path,data});
        if(path.endsWith('/context'))return context;
        if(path.endsWith('/lease')){if(leaseFailure)throw new Error('background_revision_conflict');return {lease_id:'lease',lease_epoch:1,server_epoch:'server',revision:8};}
        if(path.endsWith('/capture'))return {committed:true,revision:9,digest:'b'.repeat(64)};
        if(path.endsWith('/attach')){if(attachFailure)throw new Error('receipt mismatch');return {finished:false};}
    };
    for(const name of ['execution_start','progress','executing','executed'])
        api.addEventListener(name,e=>delivered.push({event:name,data:e.detail}));
    const controller=createBackgroundWorkflow({app,api,host,request,session:'s',seeds:{capture:()=>({hasExecuted:true}),restore:(g,s)=>restoredSeeds.push(s)},
        sourceReceipt:()=>({owner:'test',source_revision:'source',texts:[]}),
        registerJob:async()=>({canReceive:()=>canReceive}),setTimer:()=>1,clearTimer:()=>{}});
    const emit=(name,detail)=>api.dispatchEvent(new CustomEvent(name,{detail}));
    const envelope=()=>({prompt_id:'real-prompt',subscription_id:calls.find(c=>c.path.endsWith('/attach')).data.subscription_id});
    return {app,controller,target,baseline,accepted,loaded,restoredSeeds,calls,delivered,emit,envelope,
        disallow:()=>{canReceive=false;}};
}

test('cold reopen loads accepted unsaved dimensions into the same native file and preserves its saved baseline',async()=>{
    const f=coldFixture();await f.app.loadGraphData(f.baseline);
    assert.equal(f.app.extensionManager.workflow.activeWorkflow,f.target);
    assert.deepEqual(f.loaded[0].nodes[0].widgets_values,[1216,832,2]);
    assert.deepEqual(f.target.changeTracker.initialState.nodes[0].widgets_values,[832,1216,1]);
    assert.equal(f.target.isModified,true);assert.deepEqual(f.restoredSeeds,[{hasExecuted:true}]);
    assert.equal(f.calls.some(c=>c.path.endsWith('/lease')),true);
    await f.controller.refresh();
    const attach=f.calls.find(c=>c.path.endsWith('/attach')).data;
    assert.equal(attach.revision,7);assert.equal(attach.op_id,'op');
    f.controller.stop();
});

test('late join uses only its real ordered events and stops on changed native ownership',async()=>{
    const f=coldFixture();await f.app.loadGraphData(f.baseline);await f.controller.refresh();const e=f.envelope();
    f.emit('pcs_background_snapshot',{...e,watermark:5,events:[
        {seq:1,event:'execution_start',data:{prompt_id:e.prompt_id}},
        {seq:5,event:'progress',data:{prompt_id:e.prompt_id,value:9,max:28}}]});
    assert.equal(f.delivered.length,2);assert.equal(f.delivered[1].data.value,9);
    f.emit('pcs_background_event',{...e,seq:6,event:'executing',data:{prompt_id:e.prompt_id,node:'1'}});
    assert.deepEqual(f.delivered.at(-1),{event:'executing',data:'1'});
    f.emit('pcs_background_event',{...e,prompt_id:'wrong',seq:7,event:'progress',data:{prompt_id:'wrong'}});
    assert.equal(f.delivered.length,3);
    f.disallow();f.emit('pcs_background_event',{...e,seq:7,event:'progress',data:{prompt_id:e.prompt_id,value:10,max:28}});
    assert.equal(f.delivered.length,3);f.controller.stop();
});

test('event gaps stop delivery and an invalid snapshot does not partially replay',async()=>{
    const f=coldFixture();await f.app.loadGraphData(f.baseline);await f.controller.refresh();const e=f.envelope();
    f.emit('pcs_background_snapshot',{...e,watermark:2,events:[{seq:1,event:'execution_start',data:{prompt_id:e.prompt_id}},
        {seq:2,event:'made_up',data:{prompt_id:e.prompt_id}}]});
    assert.equal(f.delivered.length,0);f.controller.stop();
    const g=coldFixture();await g.app.loadGraphData(g.baseline);await g.controller.refresh();const ge=g.envelope();
    g.emit('pcs_background_snapshot',{...ge,watermark:0,events:[]});
    g.emit('pcs_background_event',{...ge,seq:2,event:'execution_start',data:{prompt_id:ge.prompt_id}});
    g.emit('pcs_background_event',{...ge,seq:1,event:'execution_start',data:{prompt_id:ge.prompt_id}});
    assert.equal(g.delivered.length,0);g.controller.stop();
});

test('failed join cannot silently continue capture or claim a ready native entry',async()=>{
    const f=coldFixture({attachFailure:true});await f.app.loadGraphData(f.baseline);await f.controller.refresh();
    assert.match(f.controller.blocked,/receipt mismatch/);
    assert.equal(f.calls.some(c=>c.path.endsWith('/capture')),false);f.controller.stop();
});

test('cold claim refusal remains blocked if native persistence retries its saved graph',async()=>{
    const f=coldFixture({leaseFailure:true});await assert.rejects(f.app.loadGraphData(f.baseline),/revision_conflict/);
    assert.match(f.controller.blocked,/revision_conflict/);assert.equal(f.loaded.length,0);
    await f.app.loadGraphData(f.baseline);assert.equal(f.loaded.length,1);
    assert.match(f.controller.blocked,/revision_conflict/);f.controller.stop();
});

test('proven uncommitted source rejection allows a new capture after B has been applied',async()=>{
    const f=fixture();f.gate.reject='known';await f.controller.refresh();
    f.gate.reject=null;f.gate.source='B';await f.controller.refresh();
    const captures=f.calls.filter(c=>c.path.endsWith('/capture'));
    assert.equal(captures.length,2);assert.notEqual(captures[0].data.op_id,captures[1].data.op_id);
    assert.equal(captures[1].data.source_receipt.source_revision,'B');assert.equal(f.controller.receipt.revision,1);
    f.controller.stop();
});

test('unknown capture failure retains the same operation before advancing to B',async()=>{
    const f=fixture();f.gate.reject='unknown';await f.controller.refresh();
    f.gate.reject=null;f.gate.source='B';await f.controller.refresh();
    const captures=f.calls.filter(c=>c.path.endsWith('/capture'));
    assert.equal(captures.length,3);assert.equal(captures[0].data.op_id,captures[1].data.op_id);
    assert.notEqual(captures[1].data.op_id,captures[2].data.op_id);
    assert.equal(captures[2].data.source_receipt.source_revision,'B');f.controller.stop();
});
