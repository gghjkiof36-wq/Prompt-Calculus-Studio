import test from 'node:test';
import assert from 'node:assert/strict';
import {describeParameters} from '../comfyui_prompt_studio/web/native_parameters.js';
import {installNativeQueue} from '../comfyui_prompt_studio/web/native_queue.js';
import {createSeedObserver} from '../comfyui_prompt_studio/web/native_seed.js';
import {createParameterCapabilities} from '../comfyui_prompt_studio/web/native_parameter_capabilities.js';

const defs={KSampler:{python_module:'nodes',input:{required:{seed:['INT',{min:0,max:2**64-1}],steps:['INT',{min:1,max:10000,step:1}],cfg:['FLOAT',{min:0,max:100}],sampler_name:[['euler','dpmpp_2m']],scheduler:[['normal','karras']],denoise:['FLOAT',{min:0,max:1}]} }},
    EmptyLatentImage:{python_module:'nodes',input:{required:{width:['INT',{min:16,max:4096,step:8}],height:['INT',{min:16,max:4096,step:8}],batch_size:['INT',{min:1,max:4096,step:1}]}}}};

function fixture(timing='after',{hang=false,dropEarly=false,withClip=false,unknownSerializer=false}={}){
    globalThis.document=new EventTarget();let hasExecuted=false,before=0,after=0,draws=0;
    const seed={name:'seed',type:'number',value:500,options:{min:0,max:2**64-1}};
    const control={name:'control_after_generate',type:'combo',value:'fixed',
        beforeQueued(o={}){before++;if(timing==='before'&&hasExecuted&&!o.isPartialExecution)update();hasExecuted=true;},
        afterQueued(){after++;if(timing==='after')update();}};
    function update(){if(control.value==='increment')seed.value++;if(control.value==='decrement')seed.value--;if(control.value==='randomize')seed.value=900+ ++draws;seed.value=Math.min(2**50,Math.max(0,seed.value));}
    const node={id:35,type:'KSampler',title:'Sampler A',widgets:[seed,control,{name:'steps',type:'number',value:20},{name:'cfg',type:'number',value:8},
        {name:'sampler_name',type:'combo',value:'euler'},{name:'scheduler',type:'combo',value:'normal'},{name:'denoise',type:'number',value:1}],inputs:[{name:'latent_image',link:1}]};
    const latent={id:12,type:'EmptyLatentImage',title:'Shared latent',widgets:[{name:'width',type:'number',value:512},{name:'height',type:'number',value:768},{name:'batch_size',type:'number',value:1}]};
    const graph={id:'native-A',_nodes:[node,latent],serialize(){return {id:this.id,nodes:this._nodes.map(n=>({id:n.id,type:n.type,widgets_values:n.widgets.map(w=>w.value)}))};},setDirtyCanvas(){}};
    const payload=()=>Object.fromEntries(graph._nodes.map(n=>[String(n.id),{class_type:n.type,inputs:{...Object.fromEntries(n.widgets.filter(w=>w.name!=='control_after_generate').map(w=>[w.name,w.value])),...(n===node?{latent_image:['12',0]}:{})}}]));
    const sent=[],requests=[];let release;
    const api=Object.assign(new EventTarget(),{clientId:'web-client',async getNodeDefs(){return defs;},async queuePrompt(...args){sent.push(structuredClone(args));return {prompt_id:'prompt-'+sent.length};}});
    let dynamicCalls=0;
    const app={graph,rootGraph:graph,extensions:[{name:'Comfy.DynamicPrompts',nodeCreated(node){for(const w of node.widgets??[])if(w.dynamicPrompts)
        w.serializeValue=()=>{dynamicCalls++;return 'resolved text';};}}],processingQueue:false,queueItems:[],extensionManager:{workflow:{activeWorkflow:{path:'workflows/A.json',activeState:{id:'native-A'}}}},
        async graphToPrompt(){const workflow=graph.serialize(),output=payload();for(const n of graph._nodes)for(const [i,w] of n.widgets.entries())
            if(w.name!=='control_after_generate')output[n.id].inputs[w.name]=w.serializeValue?await w.serializeValue(undefined,i):w.value;
            return {workflow,output};},
        async queuePrompt(n,count){this.queueItems.push({n,count});this.processingQueue=true;try{this.queueItems.shift();for(const node of this.rootGraph._nodes)for(const w of node.widgets)w.beforeQueued?.call(w);
            const p=await this.graphToPrompt(this.rootGraph);await api.queuePrompt(n,p);
            for(const node of this.rootGraph._nodes)for(const w of node.widgets)w.afterQueued?.call(w);
            if(hang)await new Promise(r=>release=r);return true;
        }finally{this.processingQueue=false;}}};
    const seeds=createSeedObserver(()=>timing);seeds.observe(node);
    const factories=Object.fromEntries(['INT','FLOAT','BOOLEAN','COMBO','STRING'].map(name=>[name,(node,field)=>({widget:node.widgets.find(w=>w.name===field)})]));
    const capabilities=createParameterCapabilities(app,factories);
    for(const n of graph._nodes)for(const w of n.widgets){
        if(w.name==='control_after_generate')continue;
        const decl=defs[n.type]?.input.required[w.name];const kind=Array.isArray(decl?.[0])?'COMBO':decl?.[0];factories[kind]?.(n,w.name);
    }
    if(withClip){const clip={id:6,type:'CLIPTextEncode',widgets:[{name:'text',type:'customtext',dynamicPrompts:true,value:'{raw|template}'}]};
        factories.STRING(clip,'text');app.extensions[0].nodeCreated(clip);
        capabilities.observeCallback(clip.widgets[0],()=>{});graph._nodes.unshift(clip);}
    if(unknownSerializer)graph._nodes.unshift({id:99,type:'Unknown',widgets:[{name:'data',value:'unchanged',serializeValue:async()=> 'unchanged'}]});
    const adapter=installNativeQueue(app,api,async(route,value)=>{requests.push({route,value:structuredClone(value)});
        if(dropEarly&&route==='workflow/native/reply')throw new Error('receipt transport lost');return {};},'session',()=>{},hang?25:1000,undefined,undefined,seeds,capabilities);
    const description=describeParameters(app,payload(),defs,seeds,capabilities);
    function patch(field,value,mode){const f=description.nodes.find(n=>n.id==='35').fields.find(f=>f.field===field);return {...f,node:'35',path:['35'],class_type:'KSampler',base:f.value,value,...(mode?{seed_mode:mode}:{} )};}
    function command(patches,id='operation'){return {id,epoch:0,identity:{workflow:'',path:'A.json',frontend_id:'native-A'},target:{workflow:'A',path:'A.json',frontend_id:'native-A'},
        parameters:{version:1,identity:{workflow:'A',frontend_id:'native-A',origin:{path:'A.json'}},patches}};}
    return {adapter,app,api,graph,node,seed,control,sent,requests,description,patch,command,capabilities,factories,getCounts:()=>({before,after,draws,dynamicCalls}),release:()=>release?.()};
}

test('two queued CFG intentions preserve live dimensions, other sampler and native Run',async()=>{
    const f=fixture();try{
        f.graph._nodes.push({id:58,type:'KSampler',title:'Sampler B',widgets:[{name:'cfg',type:'number',value:9}]});
        f.graph._nodes[1].widgets[0].value=1024;
        for(const value of [3,4]){
            const r=await f.adapter.execute(f.command([f.patch('cfg',value)],'cfg-'+value));assert.ok(r.prompt_id);
            assert.equal(f.sent.at(-1)[1].output['35'].inputs.cfg,value);assert.equal(f.sent.at(-1)[1].output['58'].inputs.cfg,9);
            assert.equal(f.sent.at(-1)[1].output['12'].inputs.width,1024);assert.equal(f.node.widgets.find(w=>w.name==='cfg').value,8);
        }
        await f.app.queuePrompt(0,1);assert.equal(f.sent.at(-1)[1].output['35'].inputs.cfg,8);
    }finally{f.adapter.stop();}
});

for(const timing of ['before','after'])for(const mode of ['fixed','increment','decrement','randomize'])test(`native ${timing}/${mode} hook projection and Stage sequence are isolated`,async()=>{
    const f=fixture(timing);try{
        let next=10;
        for(let round=0;round<3;round++){
            const seed=f.patch('seed',10,mode);if(round)seed.resolved=next;
            const result=await f.adapter.execute(f.command([seed],'seed-'+round));assert.ok(result.prompt_id,JSON.stringify(result));
            const used=f.sent.at(-1)[1].output['35'].inputs.seed;
            if(mode==='fixed')assert.equal(used,10);
            else if(mode==='increment')assert.equal(used,10+round);
            else if(mode==='decrement')assert.equal(used,10-round);
            next=result.parameter_evidence[0].next_value;
            assert.equal(f.seed.value,500);assert.equal(f.control.value,'fixed');
        }
        assert.equal(f.getCounts().before,3);assert.equal(f.getCounts().after,3);
        await f.app.queuePrompt(0,1);assert.equal(f.sent.at(-1)[1].output['35'].inputs.seed,500);
    }finally{f.adapter.stop();}
});

test('after-hook receipt is sent and native values restored before a hung queue UI releases',async()=>{
    const f=fixture('after',{hang:true});try{
        const r=await f.adapter.execute(f.command([f.patch('seed',10,'increment')]));assert.ok(r.prompt_id);assert.match(r.error,/逾時/);
        const early=f.requests.find(r=>r.route==='workflow/native/reply'&&r.value.parameter_evidence);
        assert.equal(early.value.parameter_evidence[0].actual,10);assert.equal(early.value.parameter_evidence[0].next_value,11);
        assert.equal(f.seed.value,500);assert.equal(f.control.value,'fixed');assert.equal(f.adapter.busy,false);
        f.release();await new Promise(r=>setImmediate(r));assert.equal(f.seed.value,500);
    }finally{f.release();f.adapter.stop();}
});

test('all targets validate before projection and external edits survive failed serialization',async()=>{
    const f=fixture();try{
        const bad=f.patch('steps',-1);let r=await f.adapter.execute(f.command([f.patch('cfg',3),bad]));assert.ok(r.error);assert.equal(f.sent.length,0);assert.equal(f.node.widgets.find(w=>w.name==='cfg').value,8);
        const native=f.app.graphToPrompt;let reads=0;
        f.app.graphToPrompt=async function(){if(++reads===2)f.node.widgets.find(w=>w.name==='cfg').value=6;return native.call(this);};
        r=await f.adapter.execute(f.command([f.patch('cfg',4)]));assert.ok(r.error);assert.equal(f.sent.length,0);assert.equal(f.node.widgets.find(w=>w.name==='cfg').value,6);
    }finally{f.adapter.stop();}
});

test('real definitions distinguish large seed, connected source, subgraph and special serializer',()=>{
    const f=fixture();try{
        f.seed.value=2**50+1;
        let d=describeParameters(f.app,{'35':{class_type:'KSampler',inputs:{seed:f.seed.value}}},defs,{parameterControl:()=>f.control,parameterTiming:()=> 'after'},f.capabilities);
        assert.match(d.nodes.find(n=>n.id==='35').fields.find(v=>v.field==='seed').reason,/範圍/);
        f.graph._nodes.push({id:30,type:'OpenPoseStudio',title:'Canvas',widgets:[{name:'pose_json',type:'custom',value:'{}',serializeValue(){return '{}';}}]});
        const extra={...defs,OpenPoseStudio:{python_module:'custom_nodes.comfyui-openpose-studio',input:{required:{pose_json:['STRING',{}]}}}};
        const output={...Object.fromEntries(f.description.nodes.map(n=>[n.id,{class_type:n.class_type,inputs:Object.fromEntries(n.fields.map(v=>[v.field,v.value]))}])),
            '30':{class_type:'OpenPoseStudio',inputs:{pose_json:'{}'}}};output['35'].inputs.seed=f.seed.value;
        d=describeParameters(f.app,output,extra,{parameterControl:()=>f.control,parameterTiming:()=> 'after'});
        assert.match(d.nodes.find(n=>n.id==='30').fields[0].reason,/特殊/);
        assert.equal(d.nodes.find(n=>n.id==='35').fields.find(v=>v.field==='cfg').editable,false);
    }finally{f.adapter.stop();}
});

test('CLIP before sampler folds trusted native serialization without exposing Stage values to queued microtasks',async()=>{
    const f=fixture('after',{withClip:true});try{
        const native=f.app.graphToPrompt;const observed=[];
        f.app.graphToPrompt=function(...args){const p=native.apply(this,args);queueMicrotask(()=>observed.push(f.node.widgets.find(w=>w.name==='cfg').value));return p;};
        const r=await f.adapter.execute(f.command([f.patch('cfg',3)]));assert.ok(r.prompt_id,JSON.stringify(r));
        assert.equal(f.sent[0][1].output['35'].inputs.cfg,3);assert.equal(f.sent[0][1].output['6'].inputs.text,'resolved text');
        assert.equal(f.sent[0][1].workflow.nodes.find(n=>n.id===6).widgets_values[0],'{raw|template}');
        assert.deepEqual(observed,[8,8]);assert.equal(f.graph._nodes[0].widgets[0].value,'{raw|template}');
        assert.equal(f.getCounts().dynamicCalls,2); // native baseline plus actual serialization, once each
        assert.equal(f.capabilities.trusted(f.graph._nodes[0].widgets[0]),true);
    }finally{f.adapter.stop();}
});

test('unknown graph serializer and later replaced trusted reference refuse every override before native hooks',async()=>{
    for(const unknown of [true,false]){
        const f=fixture('after',{withClip:!unknown,unknownSerializer:unknown});try{
            if(!unknown)f.graph._nodes[0].widgets[0].serializeValue=async()=> 'other';
            const r=await f.adapter.execute(f.command([f.patch('cfg',3)]));assert.ok(r.error);assert.equal(f.sent.length,0);
            assert.equal(f.getCounts().before,0);assert.equal(f.node.widgets.find(w=>w.name==='cfg').value,8);
        }finally{f.adapter.stop();}
    }
});

test('failed early receipt transport plus hung native UI returns the already observed next seed after timeout',async()=>{
    const f=fixture('after',{hang:true,dropEarly:true});try{
        const r=await f.adapter.execute(f.command([f.patch('seed',10,'increment')]));assert.match(r.error,/逾時/);
        assert.equal(r.parameter_evidence[0].actual,10);assert.equal(r.parameter_evidence[0].next_value,11);
        assert.equal(f.getCounts().after,1);assert.equal(f.seed.value,500);
    }finally{f.release();f.adapter.stop();}
});

test('primitive capability belongs to original factory refs, including absent callbacks and DOM delegates',()=>{
    const node={id:7,type:'UltimateSDUpscale',widgets:[]};
    const factories=Object.fromEntries(['INT','FLOAT','BOOLEAN','COMBO','STRING'].map(type=>[type,(n,name,value)=>{
        const widget={name,value,type,callback(){}};n.widgets.push(widget);return {widget};
    }]));
    const app={rootGraph:{_nodes:[node]},extensions:[]},cap=createParameterCapabilities(app,factories);
    const inputs={steps:['INT',{min:1,max:10000}],cfg:['FLOAT',{min:0,max:100}],tiled_decode:['BOOLEAN',{}],
        mode:[['Linear','Chess']],label:['STRING',{}]},values={steps:20,cfg:8,tiled_decode:true,mode:'Linear',label:''};
    for(const [name,spec] of Object.entries(inputs))factories[Array.isArray(spec[0])?'COMBO':spec[0]](node,name,values[name]);
    const descriptions=()=>describeParameters(app,{'7':{class_type:node.type,inputs:values}},
        {[node.type]:{python_module:'custom_nodes.ComfyUI_UltimateSDUpscale',input:{required:inputs}}},null,cap);
    assert.ok(descriptions().nodes[0].fields.every(f=>f.editable));
    const cfg=node.widgets.find(w=>w.name==='cfg');cfg.callback=undefined;
    let actual=8;Object.defineProperty(cfg,'value',{get:()=>actual,set:v=>actual=v,configurable:true});
    assert.equal(cap.primitive(cfg),false);assert.equal(descriptions().nodes[0].fields.find(f=>f.field==='cfg').editable,false);
    assert.equal(descriptions().nodes[0].fields.find(f=>f.field==='steps').editable,true);
    const domFactory={STRING(n,name,value){let stored=value;const options={getValue:()=>stored,setValue:v=>stored=v};
        const widget={name,type:'string',options};Object.defineProperty(widget,'value',{get:()=>options.getValue(),set:v=>options.setValue(v)});
        n.widgets.push(widget);return {widget};}};
    const domCap=createParameterCapabilities(app,domFactory),dom=domFactory.STRING(node,'text','neutral').widget;
    assert.equal(domCap.primitive(dom),true);dom.options.setValue=()=>{};assert.equal(domCap.primitive(dom),false);
});

test('asynchronous seed hooks cannot be promoted to editable native parameter controls',()=>{
    const seed={name:'seed',value:500},control={name:'control_after_generate',async beforeQueued(){seed.value=777;},afterQueued(){}};
    const node={type:'KSampler',widgets:[seed,control]},observer=createSeedObserver(()=> 'after');observer.observe(node);
    assert.equal(observer.parameterControl(node,'seed'),null);assert.equal(seed.value,500);
});

test('unchanged DynamicPrompts serializer cannot authorize a replaced text setter',async()=>{
    const f=fixture('after',{withClip:true});try{
        const clip=f.graph._nodes[0].widgets[0];let raw=clip.value;
        Object.defineProperty(clip,'value',{get:()=>raw,set:v=>raw=v,configurable:true});
        assert.equal(f.capabilities.trusted(clip),false);
        const result=await f.adapter.execute(f.command([f.patch('cfg',3)]));
        assert.ok(result.error);assert.equal(f.sent.length,0);assert.equal(f.getCounts().before,0);
        assert.equal(f.node.widgets.find(w=>w.name==='cfg').value,8);
    }finally{f.adapter.stop();}
});

test('seed policies require original control accessors and delegates, not just seed hook refs',()=>{
    for(const mutate of ['accessor','delegate','inherited']){
        const seed={name:'seed',value:500},proto={};
        const control=Object.create(proto);Object.assign(control,{name:'control_after_generate',value:'fixed',beforeQueued(){},afterQueued(){}});
        if(mutate==='inherited'){
            delete control.value;let current='fixed';Object.defineProperty(proto,'value',{get:()=>current,set:v=>current=v,configurable:true});
        }
        seed.linkedWidgets=[control];const factories={INT(node){node.widgets.push(seed,control);return {widget:seed};}};
        const node={id:35,type:'KSampler',widgets:[]},observer=createSeedObserver(()=> 'after');observer.installFactory(factories);
        factories.INT(node);observer.observe(node);assert.equal(observer.parameterControl(node,'seed'),control);
        if(mutate==='delegate')control.options={getValue:()=> 'fixed',setValue(){}};
        else if(mutate==='inherited')Object.defineProperty(proto,'value',{get:()=> 'fixed',set(){},configurable:true});
        else Object.defineProperty(control,'value',{get:()=> 'fixed',set(){},configurable:true});
        assert.equal(observer.parameterControl(node,'seed'),null);assert.equal(seed.value,500);
    }
});
