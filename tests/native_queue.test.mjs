import test from 'node:test';
import assert from 'node:assert/strict';
import {installNativeQueue,applyNativeBindings} from '../comfyui_prompt_studio/web/native_queue.js';
import {graphFingerprint} from '../comfyui_prompt_studio/web/graph_fingerprint.js';
import {captureManual} from '../comfyui_prompt_studio/web/state.js';

function fixture({busy=false,timeout=12000,capture=false,policy=null,manual=null,onRequest=null}={}) {
    globalThis.document=new EventTarget();
    const graph={id:'native-A',inputs:{width:1216,height:832,batch_size:2,seed:10},
        serialize(){return {id:this.id,nodes:[{id:1,type:'EmptyLatentImage',widgets_values:Object.values(this.inputs)}]};}};
    const sent=[],prepared=[],events=[],notices=[],diagnostics=[];
    const api=Object.assign(new EventTarget(),{clientId:'real-web-sid',async queuePrompt(...args){sent.push(args);return {prompt_id:'actual-prompt-id',node_errors:{}};}});
    const app={graph,rootGraph:graph,processingQueue:busy,queueItems:[],extensionManager:{workflow:{activeWorkflow:{path:'workflows/folder/A.json',activeState:{id:graph.id}}}},
        async graphToPrompt(){return {workflow:graph.serialize(),output:{'1':{class_type:'EmptyLatentImage',inputs:{...graph.inputs}}}};},
        async queuePrompt(number,count){
            events.push(['native',number,count]);this.processingQueue=true;
            try {
                if(policy){events.push(['before']);if(policy.timing==='before')policy.apply(graph.inputs);}
                else graph.inputs.seed++; // Native beforeQueued.
                const p=await this.graphToPrompt(this.rootGraph);
                const response=await api.queuePrompt(number,p,{previewMethod:'auto'});
                events.push(['storeJob',response.prompt_id]);
                if(policy){events.push(['after']);if(policy.timing==='after')policy.apply(graph.inputs);}
                else graph.inputs.seed++; // Native afterQueued.
                if(this.afterError)throw new Error('afterQueued failed');
                return true;
            } finally {this.processingQueue=false;}
        }};
    const request=async (route,value)=>{(route==='workflow/native/event'?diagnostics:prepared).push({route,value:structuredClone(value)});return onRequest?onRequest(route,value):route==='workflow/inputs/claim'&&manual?manual(value):{};};
    const seeds={queueHooks(){return {before(){graph.inputs.seed++;},after(){graph.inputs.seed++;}};}};
    const adapter=installNativeQueue(app,api,request,'tab-session',(...args)=>notices.push(args),timeout,undefined,undefined,capture?seeds:null);
    const command={id:'operation',epoch:0,identity:{workflow:'',path:'folder/A.json',frontend_id:'native-A'}};
    return {app,api,graph,sent,prepared,events,notices,diagnostics,adapter,command};
}

test('a lost poll response expires and a late command cannot run after the next poll',async()=>{
    let release,count=0;
    const f=fixture({timeout:25,onRequest:route=>{
        if(route==='workflow/native/poll'&&++count===1)return new Promise(resolve=>release=resolve);
        return {};
    }});
    try {
        await f.adapter.poll();assert.equal(f.sent.length,0);
        await f.adapter.poll();assert.equal(count,2);
        release({commands:[f.command]});await new Promise(resolve=>setImmediate(resolve));
        assert.equal(f.sent.length,0);assert.equal(f.adapter.busy,false);
    } finally {release?.({});f.adapter.stop();}
});

test('lost reply and unavailable diagnostics cannot lock polling or repeat a submitted command',async()=>{
    let poll=0,reply=0,release;
    const f=fixture({timeout:25,onRequest:route=>{
        if(route==='workflow/native/event')throw new Error('diagnostics unavailable');
        if(route==='workflow/native/poll')return {commands:[{...f.command,id:'command-'+(++poll)}]};
        if(route==='workflow/native/reply'&&++reply===1)return new Promise(resolve=>release=resolve);
        return {};
    }});
    try {
        await f.adapter.poll();assert.equal(f.sent.length,1);
        await f.adapter.poll();assert.equal(f.sent.length,2);
        release({});await new Promise(resolve=>setImmediate(resolve));
        assert.equal(f.sent.length,2);assert.equal(f.adapter.busy,false);
        assert.ok(f.diagnostics.some(e=>e.value.event.phase==='reply'&&e.value.event.state==='error'));
    } finally {release?.({});f.adapter.stop();}
});

test('Stage apply-only updates input without serializing or seed hooks; native Run stays independent',async()=>{
    const f=fixture(),widget={name:'text',value:'old',type:'text'};
    f.graph._nodes=[{id:6,type:'CLIPTextEncode',widgets:[widget]}];let serialized=0;
    const original=f.app.graphToPrompt;f.app.graphToPrompt=function(...args){serialized++;return original.apply(this,args);};
    try {
        const result=await f.adapter.execute({...f.command,action:'apply',texts:[{node:'6',field:'text',class_type:'CLIPTextEncode',text_source:'pcs',text:'new'}]});
        assert.equal(result.applied,true);assert.equal(widget.value,'new');assert.equal(f.graph.inputs.seed,10);
        assert.equal(serialized,0);assert.equal(f.sent.length,0);assert.ok(f.prepared.every(p=>p.route==='workflow/native/check_apply'));
        await f.app.queuePrompt(0,1);assert.equal(f.sent.length,1);assert.equal(f.graph.inputs.seed,12);
    } finally {f.adapter.stop();}
});

test('cancelled delivered apply rejects a late permission reply before any input mutation',async()=>{
    let permission,entered;const waiting=new Promise(resolve=>entered=resolve);
    const f=fixture({onRequest:async route=>{
        if(route==='workflow/native/check_apply'){entered();await new Promise(resolve=>permission=resolve);}return {};
    }}),widget={name:'text',value:'original'};
    f.graph._nodes=[{id:6,type:'CLIPTextEncode',widgets:[widget]}];
    try {
        const pending=f.adapter.execute({...f.command,action:'apply',texts:[{node:'6',field:'text',class_type:'CLIPTextEncode',text_source:'pcs',text:'late'}]});
        await waiting;
        f.api.dispatchEvent(new CustomEvent('prompt_studio_native_cancel',{detail:{...f.command,session:'tab-session',client_id:f.api.clientId}}));
        await new Promise(resolve=>setImmediate(resolve));
        assert.ok(f.prepared.some(p=>p.route==='workflow/native/cancel_applied'));
        permission();assert.match((await pending).error,/停止/);assert.equal(widget.value,'original');
        assert.equal(f.sent.length,0);await f.app.queuePrompt(0,1);assert.equal(f.sent.length,1);
    } finally {permission?.();f.adapter.stop();}
});

test('apply cancellation drains an entered image callback and prevents all later fields',async()=>{
    const f=fixture();let finish,entered;const started=new Promise(resolve=>entered=resolve);
    const first={name:'image',value:'first-old.png',callback:async()=>{entered();await new Promise(resolve=>finish=resolve);}};
    const second={name:'image',value:'second-old.png'};
    f.graph._nodes=[{id:10,type:'LoadImage',widgets:[first]},{id:11,type:'LoadImage',widgets:[second]}];
    try {
        const pending=f.adapter.execute({...f.command,action:'apply',images:[10,11].map(id=>({node:String(id),field:'image',class_type:'LoadImage',text:`new-${id}.png`}))});
        await started;
        f.api.dispatchEvent(new CustomEvent('prompt_studio_native_cancel',{detail:{...f.command,session:'tab-session',client_id:f.api.clientId}}));
        await new Promise(resolve=>setImmediate(resolve));
        assert.equal(f.prepared.some(p=>p.route==='workflow/native/cancel_applied'),false);
        finish();assert.match((await pending).error,/停止/);await new Promise(resolve=>setImmediate(resolve));
        assert.equal(first.value,'new-10.png');assert.equal(second.value,'second-old.png');
        assert.ok(f.prepared.some(p=>p.route==='workflow/native/cancel_applied'));assert.equal(f.sent.length,0);
        await f.app.queuePrompt(0,1);assert.equal(f.sent.length,1);
    } finally {finish?.();f.adapter.stop();}
});

test('backend rejects already cancelled late apply even when the cancel websocket was lost',async()=>{
    const f=fixture({onRequest:route=>{if(route==='workflow/native/check_apply')throw new Error('輸入套用已停止');return {};}});
    const widget={name:'text',value:'original'};f.graph._nodes=[{id:6,type:'CLIPTextEncode',widgets:[widget]}];
    try {
        const result=await f.adapter.execute({...f.command,action:'apply',texts:[{node:'6',field:'text',class_type:'CLIPTextEncode',text_source:'pcs',text:'late'}]});
        assert.match(result.error,/停止/);assert.equal(widget.value,'original');assert.equal(f.sent.length,0);
        await f.app.queuePrompt(0,1);assert.equal(f.sent.length,1);
    } finally {f.adapter.stop();}
});

test('cancelled image callback timeout never locks native Run or acknowledges an unfinished callback',async()=>{
    const f=fixture({timeout:20});let entered,finish;const started=new Promise(resolve=>entered=resolve);
    const image={name:'image',value:'old.png',callback:()=>{entered();return new Promise(resolve=>finish=resolve);}};
    f.graph._nodes=[{id:10,type:'LoadImage',widgets:[image]}];
    try {
        const pending=f.adapter.execute({...f.command,action:'apply',images:[{node:'10',field:'image',class_type:'LoadImage',text:'new.png'}]});
        await started;
        f.api.dispatchEvent(new CustomEvent('prompt_studio_native_cancel',{detail:{...f.command,session:'tab-session',client_id:f.api.clientId}}));
        assert.match((await pending).error,/逾時/);assert.equal(f.adapter.busy,false);
        assert.equal(f.prepared.some(p=>p.route==='workflow/native/cancel_applied'),false);
        await f.app.queuePrompt(0,1);assert.equal(f.sent.length,1);
        finish();await new Promise(resolve=>setImmediate(resolve));
        assert.ok(f.prepared.some(p=>p.route==='workflow/native/cancel_applied'));
    } finally {finish?.();f.adapter.stop();}
});

test('cancel acknowledgement precedes a delayed command and prevents that same command from starting',async()=>{
    const f=fixture();
    try {
        f.api.dispatchEvent(new CustomEvent('prompt_studio_native_cancel',{detail:{...f.command,session:'other-tab',client_id:f.api.clientId}}));
        await new Promise(resolve=>setImmediate(resolve));assert.equal(f.prepared.length,0);
        f.api.dispatchEvent(new CustomEvent('prompt_studio_native_cancel',{detail:{...f.command,session:'tab-session',client_id:f.api.clientId}}));
        await new Promise(resolve=>setImmediate(resolve));assert.equal(f.prepared[0].route,'workflow/native/cancel_applied');
        assert.match((await f.adapter.execute({...f.command,action:'apply'})).error,/取消/);
        assert.equal(f.prepared.length,1);assert.equal(f.sent.length,0);
        await f.app.queuePrompt(0,1);assert.equal(f.sent.length,1);
    } finally {f.adapter.stop();}
});

test('inspect reads actual serializer without bindings, queue, seed hooks, prepare or transport submission',async()=>{
    const f=fixture({capture:true}),before=structuredClone(f.graph.inputs);
    try {
        const result=await f.adapter.execute({...f.command,action:'inspect',texts:[{node:'missing',field:'text',text:'not applied'}]});
        assert.equal(result.error,undefined);assert.deepEqual(result.inspection.output['1'].inputs,before);
        assert.deepEqual(f.graph.inputs,before);assert.equal(f.events.length,0);assert.equal(f.sent.length,0);assert.equal(f.prepared.length,0);
    } finally {f.adapter.stop();}
});

test('websocket probe refreshes identity immediately and reports short submission busy separately',async()=>{
    const f=fixture({busy:true});
    try {
        f.api.dispatchEvent(new CustomEvent('prompt_studio_native_probe',{detail:{probe:'nonce-1'}}));
        await new Promise(resolve=>setImmediate(resolve));
        const first=f.prepared.find(p=>p.route==='workflow/native/poll').value;
        assert.equal(first.probe,'nonce-1');assert.equal(first.ready,false);assert.match(first.reason,/提交/);
        assert.equal(first.inspect_protocol,1);assert.equal(f.sent.length,0);
        f.app.processingQueue=false;
        f.api.dispatchEvent(new CustomEvent('prompt_studio_native_probe',{detail:{probe:'nonce-2'}}));
        await new Promise(resolve=>setImmediate(resolve));
        assert.equal(f.prepared.at(-1).value.probe,'nonce-2');assert.equal(f.prepared.at(-1).value.ready,true);
        f.adapter.stop();const count=f.prepared.length;
        f.api.dispatchEvent(new CustomEvent('prompt_studio_native_probe',{detail:{probe:'after-stop'}}));
        await new Promise(resolve=>setImmediate(resolve));assert.equal(f.prepared.length,count);
    } finally {f.adapter.stop();}
});

test('probe during owned serialization or native loading acknowledges busy without accepting a command',async()=>{
    const f=fixture();let release,entered;
    const waiting=new Promise(resolve=>entered=resolve),original=f.app.graphToPrompt;
    f.app.graphToPrompt=async()=>{entered();await new Promise(resolve=>release=resolve);return original.call(f.app);};
    try {
        const pending=f.adapter.execute({...f.command,action:'inspect'});await waiting;
        f.api.dispatchEvent(new CustomEvent('prompt_studio_native_probe',{detail:{probe:'during-inspect'}}));
        await new Promise(resolve=>setImmediate(resolve));
        assert.equal(f.prepared.at(-1).value.probe,'during-inspect');assert.equal(f.prepared.at(-1).value.ready,false);
        assert.equal(f.sent.length,0);release();assert.equal((await pending).error,undefined);
        f.app.configuringGraph=true;
        f.api.dispatchEvent(new CustomEvent('prompt_studio_native_probe',{detail:{probe:'during-load'}}));
        await new Promise(resolve=>setImmediate(resolve));
        assert.equal(f.prepared.at(-1).value.probe,'during-load');assert.equal(f.prepared.at(-1).value.ready,false);
        assert.equal(f.sent.length,0);
    } finally {release?.();f.adapter.stop();}
});

test('native Run always submits natively regardless of PCS scheduled head',async()=>{
    let claims=0;const f=fixture({manual:()=>{claims++;return {handled:true,item:'head-A',message:'PCS 接續'};}});
    try {
        await f.app.queuePrompt(0,1);await f.app.queuePrompt(0,1);
        assert.equal(claims,0);assert.equal(f.sent.length,2);assert.equal(f.graph.inputs.seed,14);
        await f.adapter.execute(f.command);assert.equal(claims,0);assert.equal(f.sent.length,3);
    } finally {f.adapter.stop();}
});

test('native Run for another workflow uses its original callback exactly once',async()=>{
    const f=fixture({manual:()=>({handled:false})});
    try {await f.app.queuePrompt(0,1);assert.equal(f.sent.length,1);assert.equal(f.events.filter(e=>e[0]==='native').length,1);}
    finally {f.adapter.stop();}
});

test('paused or disconnected PCS cannot lock native Run',async()=>{
    const f=fixture({manual:()=>{throw new Error('請先在 PCS 繼續');}});
    try {await f.app.queuePrompt(0,1);assert.equal(f.sent.length,1);assert.equal(f.graph.inputs.seed,12);}
    finally {f.adapter.stop();}
});

for(const timing of ['before','after']) for(const mode of ['fixed','randomize','increment','decrement']) {
    test(`live native ${mode} / ${timing} applies control once per submitted run`,async()=>{
        let calls=0;const policy={timing,apply(inputs){calls++;
            if(mode==='increment')inputs.seed++;
            if(mode==='decrement')inputs.seed--;
            if(mode==='randomize')inputs.seed=100+calls;
        }};
        const f=fixture({policy});
        try {
            for(let i=0;i<2;i++) {
                const start=f.graph.inputs.seed;
                const result=await f.adapter.execute({...f.command,id:'native-'+i});
                assert.equal(result.prompt_id,'actual-prompt-id');assert.equal(calls,i+1);
                const expected=timing==='after'?start:mode==='fixed'?start:mode==='increment'?start+1:mode==='decrement'?start-1:101+i;
                assert.equal(f.sent[i][1].output['1'].inputs.seed,expected);
            }
            assert.equal(f.sent.length,2);assert.equal(f.events.filter(e=>e[0]==='before').length,2);
            assert.equal(f.events.filter(e=>e[0]==='after').length,2);
        } finally {f.adapter.stop();}
    });
}

test('enqueue captures actual seed and batch without queuePrompt or HTTP submission',async()=>{
    const f=fixture({capture:true});f.command.action='capture';
    try {
        const result=await f.adapter.execute(f.command);
        assert.equal(result.captured,true);assert.equal(f.sent.length,0);assert.equal(f.events.length,0);
        const prepared=f.prepared.find(p=>p.route==='workflow/native/prepare');
        assert.equal(prepared.value.output['1'].inputs.seed,11);
        assert.equal(prepared.value.output['1'].inputs.batch_size,2);
        f.graph.inputs.seed=99;f.graph.inputs.width=4096;
        assert.equal(prepared.value.output['1'].inputs.seed,11);
        assert.equal(prepared.value.output['1'].inputs.width,1216);
    } finally {f.adapter.stop();}
});

test('capture fails closed during serialization edit and never queues a fallback',async()=>{
    const f=fixture({capture:true});f.command.action='capture';
    const original=f.app.graphToPrompt;
    f.app.graphToPrompt=async function(){const value=await original.call(this);f.graph.inputs.width++;return value;};
    try {assert.match((await f.adapter.execute(f.command)).error,/修改/);assert.equal(f.sent.length,0);assert.equal(f.prepared.length,0);}
    finally {f.adapter.stop();}
});

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

test('busy installation recovers when native submission drains without a page reload',async()=>{
    const f=fixture({busy:true});
    try {
        assert.equal((await f.adapter.execute(f.command)).uncertain,false);assert.equal(f.events.length,0);
        f.app.processingQueue=false;
        assert.equal((await f.adapter.execute(f.command)).prompt_id,'actual-prompt-id');assert.equal(f.sent.length,1);
    }
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

test('a native click during PCS serialization waits only for submission and then runs once',async()=>{
    const f=fixture(),serialize=f.app.graphToPrompt;let release;
    f.app.graphToPrompt=async function(...args){
        f.app.graphToPrompt=serialize;
        await new Promise(resolve=>{release=resolve;});
        return serialize.apply(this,args);
    };
    try {
        const pcs=f.adapter.execute(f.command);
        await new Promise(resolve=>setTimeout(resolve,0));
        const manual=f.app.queuePrompt(0,1);
        assert.equal(f.sent.length,0);release();
        assert.equal((await pcs).prompt_id,'actual-prompt-id');
        await manual;assert.equal(f.sent.length,2);
    } finally {f.adapter.stop();}
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

test('a permanently pending PCS serialization releases native Run after timeout',async()=>{
    const f=fixture({timeout:10}),original=f.app.graphToPrompt;let calls=0,late;
    f.app.graphToPrompt=function(...args){
        if(++calls===1)return new Promise(resolve=>{late=resolve;});
        return original.apply(this,args);
    };
    try {
        const pcs=f.adapter.execute(f.command);
        await new Promise(resolve=>setTimeout(resolve,1));
        const manual=f.app.queuePrompt(0,1);
        assert.match((await pcs).error,/逾時/);
        await manual;assert.equal(f.sent.length,1);assert.equal(f.app.processingQueue,false);
        late({workflow:f.graph.serialize(),output:{}});
        await new Promise(resolve=>setTimeout(resolve,0));assert.equal(f.sent.length,1);
    }finally{f.adapter.stop();}
});

test('chain retry records its execution seed once and preserves native after hook',async()=>{
    let calls=0;const f=fixture({policy:{timing:'after',apply(inputs){calls++;inputs.seed++;}}});
    const widget={name:'seed',get value(){return f.graph.inputs.seed;},set value(v){f.graph.inputs.seed=v;}};
    f.graph._nodes=[{id:1,type:'EmptyLatentImage',widgets:[widget]}];
    f.command.replay={'1':{class_type:'EmptyLatentImage',inputs:{seed:30}}};
    try {
        const result=await f.adapter.execute(f.command);assert.equal(result.prompt_id,'actual-prompt-id');
        assert.equal(f.sent[0][1].output['1'].inputs.seed,30);assert.equal(calls,1);assert.equal(widget.value,31);
    }finally{f.adapter.stop();}
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

test('three complete runs accept native serialization metadata and preview layout changes on every first click',async()=>{
    const f=fixture(),serialize=f.graph.serialize;
    f.graph.extra={};f.graph.layout={order:7,size:[210,80],inputs:[{name:'image',link:1,localized_name:'圖片'}]};
    f.graph.serialize=function(){const value=serialize.call(this);value.extra=this.extra;Object.assign(value.nodes[0],this.layout);return value;};
    const original=f.app.graphToPrompt;
    f.app.graphToPrompt=async function(){
        const value=await original.call(this);
        // Same mutations as executionUtil.graphToPrompt; returned workflow
        // metadata references the live graph. Completion changes preview size.
        value.workflow.extra.frontendVersion='1.43.18';
        delete value.workflow.nodes[0].inputs[0].localized_name;
        f.graph.layout.order=0;f.graph.layout.size=[500,700];
        return value;
    };
    try {
        for(let index=0;index<3;index++) {
            f.command.id='run-'+index;
            const result=await f.adapter.execute(f.command);
            assert.equal(result.error,undefined);assert.equal(result.prompt_id,'actual-prompt-id');
            assert.equal(f.sent.length,index+1);assert.equal(f.adapter.busy,false);
            f.graph.layout.order=7;f.graph.layout.size=[210,80];
            f.graph.layout.inputs[0].localized_name='圖片';
        }
        assert.deepEqual(f.sent.map(args=>args[1].output['1'].inputs.seed),[11,13,15]);
    } finally {f.adapter.stop();}
});

test('fingerprint retains execution inputs, arbitrary properties, links, modes and native identity',()=>{
    const value={id:'A',nodes:[{id:1,type:'KSampler',mode:0,widgets_values:[11,20],properties:{custom:{}},inputs:[{name:'model',link:4}]}],links:[[4,2,0,1,0,'MODEL']]};
    for(const mutate of [v=>v.id='B',v=>v.nodes[0].widgets_values[0]++,v=>v.nodes[0].mode=2,
        v=>v.nodes[0].properties.custom={x:1},v=>delete v.nodes[0].properties.custom,
        v=>v.nodes[0].inputs[0].link=3,v=>v.links[0][2]=1]) {
        const changed=structuredClone(value);mutate(changed);assert.notEqual(graphFingerprint(value),graphFingerprint(changed));
    }
});

test('legacy PCS manual bookkeeping happens before the guard without losing an empty manual draft',async()=>{
    const f=fixture(),serialize=f.graph.serialize;
    const node={id:6,type:'CLIPTextEncode',widgets:[{name:'text',value:''}],properties:{prompt_studio:{field:'text',
        snapshot:{state:{draft:null},generated_prompt:'generated',final_prompt:'old'}}}};
    f.graph._nodes=[node];f.graph.serialize=function(){const value=serialize.call(this);value.nodes.push({id:6,type:node.type,properties:node.properties,widgets_values:['']});return value;};
    const original=f.app.graphToPrompt;
    f.app.graphToPrompt=async function(){captureManual(node);return original.call(this);};
    try {
        const result=await f.adapter.execute(f.command);assert.equal(result.error,undefined);assert.equal(f.sent.length,1);
        assert.equal(node.properties.prompt_studio.snapshot.state.draft,'');
        assert.equal(node.properties.prompt_studio.snapshot.manual_draft,true);
    } finally {f.adapter.stop();}
});
