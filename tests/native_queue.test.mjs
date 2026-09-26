import test from 'node:test';
import assert from 'node:assert/strict';
import {installNativeQueue,applyNativeBindings} from '../comfyui_prompt_studio/web/native_queue.js';

function fixture({busy=false,timeout=12000}={}) {
    globalThis.document=new EventTarget();
    const graph={id:'native-A',inputs:{width:1216,height:832,batch_size:2,seed:10},
        serialize(){return {id:this.id,nodes:[{id:1,type:'EmptyLatentImage',widgets_values:Object.values(this.inputs)}]};}};
    const sent=[],prepared=[],events=[],notices=[];
    const api={clientId:'real-web-sid',async queuePrompt(...args){sent.push(args);return {prompt_id:'actual-prompt-id',node_errors:{}};}};
    const app={graph,rootGraph:graph,processingQueue:busy,queueItems:[],extensionManager:{workflow:{activeWorkflow:{path:'workflows/folder/A.json',activeState:{id:graph.id}}}},
        async graphToPrompt(){return {workflow:graph.serialize(),output:{'1':{class_type:'EmptyLatentImage',inputs:{...graph.inputs}}}};},
        async queuePrompt(number,count){
            events.push(['native',number,count]);this.processingQueue=true;
            try {
                graph.inputs.seed++; // Native beforeQueued.
                const p=await this.graphToPrompt(this.rootGraph);
                const response=await api.queuePrompt(number,p,{previewMethod:'auto'});
                events.push(['storeJob',response.prompt_id]);
                graph.inputs.seed++; // Native afterQueued.
                if(this.afterError)throw new Error('afterQueued failed');
                return true;
            } finally {this.processingQueue=false;}
        }};
    const request=async (route,value)=>{prepared.push({route,value:structuredClone(value)});return {};};
    const adapter=installNativeQueue(app,api,request,'tab-session',(...args)=>notices.push(args),timeout);
    const command={id:'operation',epoch:0,identity:{workflow:'',path:'folder/A.json',frontend_id:'native-A'}};
    return {app,api,graph,sent,prepared,events,notices,adapter,command};
}

test('PCS execute writes clicked text and image on native nodes before serialization; other values stay live',async()=>{
    const f=fixture();
    const positive={id:6,type:'CLIPTextEncode',widgets:[{name:'text',value:'old positive'}]};
    const negative={id:7,type:'CLIPTextEncode',widgets:[{name:'text',value:''}]};
    const image={id:20,type:'LoadImage',widgets:[{name:'image',value:'old.png'}]};
    f.graph._nodes=[positive,negative,image];
    const original=f.app.graphToPrompt;
    f.app.graphToPrompt=async function(...args){
        const value=await original.apply(this,args);
        for(const node of f.graph._nodes)value.output[node.id]={class_type:node.type,inputs:{[node.widgets[0].name]:node.widgets[0].value}};
        return value;
    };
    f.command.texts=[{node:'6',field:'text',class_type:'CLIPTextEncode',text_source:'pcs',text:'red dress, night street'},
        {node:'7',field:'text',class_type:'CLIPTextEncode',text_source:'web',text:'do not overwrite manual empty'}];
    f.command.images=[{node:'20',field:'image',class_type:'LoadImage',text:'pcs/new.png'}];
    try {
        const result=await f.adapter.execute(f.command);
        assert.equal(result.prompt_id,'actual-prompt-id');
        const output=f.sent[0][1].output;
        assert.equal(output['6'].inputs.text,'red dress, night street');
        assert.equal(output['7'].inputs.text,'');assert.equal(output['20'].inputs.image,'pcs/new.png');
        assert.deepEqual(output['1'].inputs,{width:1216,height:832,batch_size:2,seed:11});
        assert.equal(positive.widgets[0].value,output['6'].inputs.text);
    } finally {f.adapter.stop();}
});

test('invalid later binding prevents any earlier field mutation or submission',async()=>{
    const f=fixture(),widget={name:'text',value:'keep'};
    f.graph._nodes=[{id:6,type:'CLIPTextEncode',widgets:[widget]}];
    f.command.texts=[{node:'6',field:'text',class_type:'CLIPTextEncode',text_source:'pcs',text:'new'},
        {node:'7',field:'text',class_type:'CLIPTextEncode',text_source:'pcs',text:'missing'}];
    try {assert.match((await f.adapter.execute(f.command)).error,/綁定節點/);assert.equal(widget.value,'keep');assert.equal(f.sent.length,0);}
    finally {f.adapter.stop();}
});

test('bound empty PCS text is deliberate and linked widgets are refused',async()=>{
    const node={id:6,type:'CLIPTextEncode',widgets:[{name:'text',value:'old'}]},app={rootGraph:{_nodes:[node]}};
    const command={texts:[{node:'6',field:'text',class_type:'CLIPTextEncode',text_source:'pcs',text:''}]};
    await applyNativeBindings(app,command);assert.equal(node.widgets[0].value,'');
    node.inputs=[{name:'text',link:1}];node.widgets[0].value='from link';
    await assert.rejects(applyNativeBindings(app,command),/接線供值/);assert.equal(node.widgets[0].value,'from link');
});

test('same native graph supplies all dimensions and batch; original queue owns hooks and real job registration',async()=>{
    const f=fixture();
    try {
        const result=await f.adapter.execute(f.command);
        assert.equal(result.prompt_id,'actual-prompt-id');assert.equal(f.sent.length,1);
        const [number,p,options]=f.sent[0];
        assert.equal(number,0);assert.deepEqual(options,{previewMethod:'auto'});
        assert.deepEqual(p.output['1'].inputs,{width:1216,height:832,batch_size:2,seed:11});
        assert.equal(f.graph.inputs.seed,12);
        assert.deepEqual(f.events,[['native',0,1],['storeJob','actual-prompt-id']]);
        assert.equal(p.workflow.extra.pcs_native_operation,'operation');
        assert.equal(f.graph.extra,undefined); // No metadata pasted into live graph.
        assert.equal(f.prepared[0].value.client_id,'real-web-sid');
        assert.equal(result.payload.client_id,undefined); // No transport credentials.
    } finally {f.adapter.stop();}
});

test('busy at installation is refused before native push and stays unknown',async()=>{
    const f=fixture({busy:true});f.app.processingQueue=false;
    try {const result=await f.adapter.execute(f.command);assert.equal(result.uncertain,false);assert.equal(f.events.length,0);}
    finally {f.adapter.stop();}
});

test('poll and execution both refuse unknown queue fields without calling native push',async()=>{
    for(const [field,value] of [['processingQueue',undefined],['processingQueue',0],['queueItems',undefined],['queueItems',{length:0}]]) {
        const f=fixture();f.app[field]=value;
        try {
            await f.adapter.poll();await f.adapter.poll();
            assert.equal(f.prepared[0].value.ready,false);
            assert.match((await f.adapter.execute(f.command)).error,/無法辨識/);
            assert.equal(f.notices.length,1);assert.equal(f.events.length,0);assert.equal(f.sent.length,0);
        } finally {f.adapter.stop();}
    }
});

test('a later wrapper is not captured again and receives no PCS submission',async()=>{
    const f=fixture();const gate=f.app.queuePrompt;let lateCalls=0;
    const late=async function(...args){lateCalls++;return gate.apply(this,args);};f.app.queuePrompt=late;
    try {
        await f.adapter.poll();
        assert.equal(f.prepared[0].value.ready,false);
        assert.match((await f.adapter.execute(f.command)).error,/其他擴充/);
        assert.equal(f.app.queuePrompt,late);assert.equal(lateCalls,0);assert.equal(f.sent.length,0);
    } finally {f.adapter.stop();}
    assert.equal(f.app.queuePrompt,late);
});

test('a subgraph is unavailable in both poll and direct command acceptance',async()=>{
    const f=fixture();f.app.graph={...f.graph};
    try {
        await f.adapter.poll();
        assert.equal(f.prepared[0].value.ready,false);
        assert.match((await f.adapter.execute(f.command)).error,/主畫布/);
        assert.equal(f.sent.length,0);
    } finally {f.adapter.stop();}
});

test('different native identity and A-B-A epoch invalidate even with the same graph object',async()=>{
    const f=fixture();
    try {
        const wrong=structuredClone(f.command);wrong.identity.frontend_id='A-copy';
        assert.match((await f.adapter.execute(wrong)).error,/工作流/);assert.equal(f.sent.length,0);
        f.app.graphToPrompt=async()=>{f.adapter.changed();return {workflow:f.graph.serialize(),output:{}};};
        assert.match((await f.adapter.execute(f.command)).error,/切換/);assert.equal(f.sent.length,0);
    } finally {f.adapter.stop();}
});

test('asynchronous serialization edit is rejected without submitting or restoring old values',async()=>{
    const f=fixture();
    f.app.graphToPrompt=async()=>{await Promise.resolve();f.graph.inputs.width=1000;return {workflow:f.graph.serialize(),output:{}};};
    try {assert.match((await f.adapter.execute(f.command)).error,/被修改/);assert.equal(f.sent.length,0);assert.equal(f.graph.inputs.width,1000);}
    finally {f.adapter.stop();}
});

test('a lost HTTP response stays uncertain and is never retried',async()=>{
    const f=fixture();let attempts=0;
    f.api.queuePrompt=async()=>{attempts++;throw new Error('connection lost');};
    try {const result=await f.adapter.execute(f.command);assert.equal(result.uncertain,true);assert.equal(attempts,1);assert.ok(result.payload);}
    finally {f.adapter.stop();}
});

test('real prompt id survives an afterQueued failure; no second submission',async()=>{
    const f=fixture();f.app.afterError=true;
    try {const result=await f.adapter.execute(f.command);assert.equal(result.prompt_id,'actual-prompt-id');assert.match(result.error,/afterQueued/);assert.equal(result.uncertain,false);assert.equal(f.sent.length,1);}
    finally {f.adapter.stop();}
});

test('concurrent user queue is blocked before native queueItems can be appended',async()=>{
    const f=fixture();const serialize=f.app.graphToPrompt;let blocked;
    f.app.graphToPrompt=async function(...args){blocked=await this.queuePrompt(0,1).catch(e=>e.message);return serialize.apply(this,args);};
    try {const result=await f.adapter.execute(f.command);assert.equal(result.prompt_id,'actual-prompt-id');assert.match(blocked,/等候/);assert.equal(f.events.filter(e=>e[0]==='native').length,1);}
    finally {f.adapter.stop();}
});

test('timeout does not lock native editing; a late serialization still cannot submit unguarded',async()=>{
    const f=fixture({timeout:10});let release;
    f.app.graphToPrompt=()=>new Promise(resolve=>{release=()=>resolve({workflow:f.graph.serialize(),output:{}});});
    try {
        const result=await f.adapter.execute(f.command);assert.match(result.error,/逾時/);assert.equal(f.sent.length,0);
        const during=new Event('pointerdown',{cancelable:true});document.dispatchEvent(during);assert.equal(during.defaultPrevented,false);
        release();await new Promise(resolve=>setTimeout(resolve,0));assert.equal(f.sent.length,0);
        const after=new Event('pointerdown',{cancelable:true});document.dispatchEvent(after);assert.equal(after.defaultPrevented,false);
    } finally {f.adapter.stop();}
});

test('command received after an A-B-A round trip cannot start on a newer epoch',async()=>{
    const f=fixture();f.adapter.changed();f.adapter.changed();
    try {assert.match((await f.adapter.execute(f.command)).error,/切換/);assert.equal(f.events.length,0);}
    finally {f.adapter.stop();}
});


test('native undo retains its loader and history; a pre-submit change refuses PCS transport',async()=>{
    const f=fixture();const original={...f.graph.inputs},undo=[original],redo=[];
    const loader=async function(state){f.graph.inputs={...state};f.adapter.changed();};
    f.app.loadGraphData=loader;
    const serialize=f.app.graphToPrompt;
    f.app.graphToPrompt=async()=>{
        const state=undo.pop();redo.push({...f.graph.inputs});
        await f.app.loadGraphData(state);
        return serialize.call(f.app);
    };
    try {
        assert.match((await f.adapter.execute(f.command)).error,/切換/);
        assert.equal(f.app.loadGraphData,loader);assert.deepEqual(f.graph.inputs,original);
        assert.equal(undo.length,0);assert.equal(redo.length,1);assert.equal(f.sent.length,0);
        assert.equal(f.prepared.length,0);
    } finally {f.adapter.stop();}
});
