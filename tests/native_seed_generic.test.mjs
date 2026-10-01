// Synthetic native-INT factory contract. Installed frontend source-backed
// lifecycle/serializer evidence is verified separately, without a service/GPU.
import test from 'node:test';
import assert from 'node:assert/strict';
import {createSeedObserver} from '../comfyui_prompt_studio/web/native_seed.js';
import {createParameterCapabilities} from '../comfyui_prompt_studio/web/native_parameter_capabilities.js';
import {describeParameters,parameterTransaction} from '../comfyui_prompt_studio/web/native_parameters.js';

const liveSeed=370680149938299;
function fixture({type='UltimateSDUpscale',field='seed',timing='after',install=true,observe=true}={}){
    const counts={before:0,after:0,updates:0,draws:0};
    const factories={INT(node,name){
        const seed={name,type:'number',value:liveSeed,options:{min:0,max:2**50,step2:1},callback(){}};
        const control={name:'control_after_generate',type:'combo',value:'randomize',options:{},callback(){}};
        let executed=false;
        const update=()=>{counts.updates++;if(control.value==='increment')seed.value++;
            else if(control.value==='decrement')seed.value--;
            else if(control.value==='randomize')seed.value=2000+ ++counts.draws;
            seed.callback(seed.value);};
        control.beforeQueued=({isPartialExecution}={})=>{counts.before++;if(!isPartialExecution&&timing==='before'&&executed)update();executed=true;};
        control.afterQueued=({isPartialExecution}={})=>{counts.after++;if(!isPartialExecution&&timing==='after')update();};
        seed.linkedWidgets=[control];node.widgets.push(seed,control);return {widget:seed};
    }};
    const observer=createSeedObserver(()=>timing);if(install)observer.installFactory(factories);
    const capabilities=createParameterCapabilities({extensions:[]},factories);
    let constructorType;
    class RegisteredNode {
        static type=type;
        constructor(){this.type='';this.id=7;this.widgets=[];this.inputs=[];
            factories.INT(this,field);constructorType=this.type;if(observe)observer.observe(this);}
    }
    const node=new RegisteredNode();node.type=type;
    const [seed,control]=node.widgets,graph={id:'custom-seed',_nodes:[node],setDirtyCanvas(){}},app={rootGraph:graph};
    const definitions={[type]:{input:{required:{[field]:['INT',{default:0,min:0,max:2**64-1}]}}}};
    const output=()=>({'7':{class_type:type,inputs:{[field]:seed.value}}});
    const describe=()=>describeParameters(app,output(),definitions,observer,capabilities);
    const patch=(mode,value=10,resolved)=>({...describe().nodes[0].fields.find(f=>f.field===field),node:'7',path:['7'],
        class_type:type,base:seed.value,value,seed_mode:mode,...(resolved===undefined?{}:{resolved})});
    const transaction=p=>parameterTransaction(app,{identity:{frontend_id:graph.id,path:'custom.json'},parameters:{version:1,
        identity:{frontend_id:graph.id,origin:{path:'custom.json'}},patches:[p]}},describe(),()=>{},observer,()=>{},capabilities);
    return {node,seed,control,graph,app,observer,factories,capabilities,describe,patch,transaction,output,counts,field,constructorType};
}

for(const [type,field]of [['UltimateSDUpscale','seed'],['AnotherNativeSampler','seed'],['AnotherNoiseNode','noise_seed']])
test(`${type} uses factory-proven ${field} control before instance type assignment`,()=>{
    const f=fixture({type,field});assert.equal(f.constructorType,'');
    assert.equal(f.observer.parameterControl(f.node,field),f.control);
    const description=f.describe().nodes[0].fields;
    assert.equal(description.length,1);assert.equal(description[0].editable,true);assert.equal(description[0].seed_mode,'randomize');
    assert.equal(description[0].value,liveSeed);assert.equal(f.seed.value,liveSeed);
    const before=f.control.beforeQueued;f.observer.observe(f.node,{loaded:true});assert.equal(f.control.beforeQueued,before);
    assert.deepEqual(f.counts,{before:0,after:0,updates:0,draws:0});
});

for(const timing of ['before','after'])for(const mode of ['fixed','increment','decrement','randomize'])
test(`generic seed transaction preserves native ${timing}/${mode} and restores live state`,()=>{
    const f=fixture({timing}),before=f.control.beforeQueued,after=f.control.afterQueued;
    let next;
    for(let round=0;round<2;round++){
        const tx=f.transaction(f.patch(mode,10,next));tx.apply();f.control.beforeQueued();
        tx.enterSerialize();const payload=f.output();tx.leaveSerialize();tx.evidence(payload);f.control.afterQueued();
        const evidence=tx.finish()[0];tx.restore();next=evidence.next_value;
        const expected=mode==='fixed'?10:mode==='increment'?10+round:mode==='decrement'?10-round:round===0?10:2001;
        assert.equal(evidence.actual,expected);assert.equal(f.seed.value,liveSeed);assert.equal(f.control.value,'randomize');
        assert.equal(f.control.beforeQueued,before);assert.equal(f.control.afterQueued,after);
        assert.equal(f.observer.parameterControl(f.node,f.field),f.control);
    }
    assert.equal(f.counts.before,2);assert.equal(f.counts.after,2);
    assert.equal(f.counts.updates,timing==='after'?2:1);
    assert.equal(f.counts.draws,mode==='randomize'?(timing==='after'?2:1):0);
});

test('custom controls without creation provenance are not observed or promoted later',()=>{
    const f=fixture({install:false,observe:false}),before=f.control.beforeQueued;
    f.observer.observe(f.node);assert.equal(f.control.beforeQueued,before);assert.equal(f.observer.parameterControl(f.node,'seed'),null);
    f.observer.installFactory(f.factories);f.observer.observe(f.node);
    assert.equal(f.control.beforeQueued,before);assert.equal(f.describe().nodes[0].fields[0].editable,false);
    assert.deepEqual(f.counts,{before:0,after:0,updates:0,draws:0});
});

for(const phase of ['before','after'])test(`ambiguous or changed native pair is rejected ${phase} observation`,()=>{
    for(const mutation of ['second-seed','second-control','copied-links','replaced-link','extra-link','missing-control',
        'before-hook','after-hook','seed-callback','control-callback','seed-accessor','control-accessor']){
        const f=fixture({observe:phase==='after'});
        if(mutation==='second-seed')f.factories.INT(f.node,'noise_seed');
        if(mutation==='second-control')f.node.widgets.push({...f.control});
        if(mutation==='copied-links')f.seed.linkedWidgets=[f.control];
        if(mutation==='replaced-link')f.seed.linkedWidgets[0]={...f.control};
        if(mutation==='extra-link')f.seed.linkedWidgets.push({name:'other'});
        if(mutation==='missing-control')f.node.widgets.splice(1,1);
        if(mutation==='before-hook')f.control.beforeQueued=()=>{};
        if(mutation==='after-hook')f.control.afterQueued=()=>{};
        if(mutation==='seed-callback')f.seed.callback=()=>{};
        if(mutation==='control-callback')f.control.callback=()=>{};
        if(mutation==='seed-accessor')Object.defineProperty(f.seed,'value',{get:()=>liveSeed,set(){},configurable:true});
        if(mutation==='control-accessor')Object.defineProperty(f.control,'value',{get:()=> 'randomize',set(){},configurable:true});
        f.observer.observe(f.node);
        assert.equal(f.observer.parameterControl(f.node,'seed'),null,phase+': '+mutation);
        assert.equal(f.describe().nodes[0].fields.find(v=>v.field==='seed').editable,false,mutation);
        assert.equal(f.seed.value,liveSeed);assert.equal(f.counts.before,0);assert.equal(f.counts.after,0);
    }
});

test('native controls cannot be moved to another node or retain changed field/type/constructor identity',()=>{
    const source=fixture({observe:false});
    const other={id:8,type:'Other',widgets:source.node.widgets,inputs:[]};
    source.observer.observe(other);assert.equal(source.observer.parameterControl(other,'seed'),null);
    for(const mutation of ['field','type','constructor']){
        const f=fixture();
        if(mutation==='field')f.seed.name='noise_seed';
        if(mutation==='type')f.node.type='Changed';
        if(mutation==='constructor')f.node.constructor=class Changed {};
        assert.equal(f.observer.parameterControl(f.node,f.seed.name),null,mutation);
    }
});

test('generic parameter support does not admit custom nodes to legacy background execution',()=>{
    const f=fixture();
    assert.throws(()=>f.observer.capture(f.graph),/一個標準 KSampler/);
    assert.throws(()=>f.observer.queueHooks(f.graph),/未支援/);
    assert.equal(f.seed.value,liveSeed);assert.equal(f.counts.before,0);assert.equal(f.counts.after,0);
});

test('unsupported range, policy and serializer remain read-only for proven native custom seeds',()=>{
    for(const mutation of ['range','policy','serializer','linked-input']){
        const f=fixture();
        if(mutation==='range')f.seed.value=2**50+1;
        if(mutation==='policy')f.control.value='unknown';
        if(mutation==='serializer')f.seed.serializeValue=()=>f.seed.value;
        if(mutation==='linked-input')f.node.inputs.push({name:'seed',link:1});
        assert.equal(f.describe().nodes[0].fields[0].editable,false,mutation);
        assert.throws(()=>f.transaction(f.patch('fixed')),/特殊序列化|欄位定義/);
        assert.equal(f.counts.before,0);assert.equal(f.counts.after,0);
    }
});
