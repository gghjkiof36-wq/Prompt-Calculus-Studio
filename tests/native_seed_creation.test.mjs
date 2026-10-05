// Native construction order: ComfyNode.nodeCreated runs before createNode sets
// the instance type. Source-backed concrete factory verification is separate.
import test from 'node:test';
import assert from 'node:assert/strict';
import {createSeedObserver} from '../comfyui_prompt_calculus_studio/web/native_seed.js';
import {createParameterCapabilities} from '../comfyui_prompt_calculus_studio/web/native_parameter_capabilities.js';
import {describeParameters} from '../comfyui_prompt_calculus_studio/web/native_parameters.js';

function fixture({type='KSampler',timing='after',observe=true,proven=true}={}){
    const seedName=type==='KSampler'?'seed':'noise_seed';let before=0,after=0,draws=0,seenType;
    const widgets={INT(node,name){
        const seed={name,type:'number',value:76642911565795,options:{min:0,max:2**50},callback(){}};
        const control={name:'control_after_generate',type:'combo',value:'randomize',options:{},callback(){}};
        let ran=false;
        const update=()=>{draws++;if(control.value==='increment')seed.value++;};
        control.beforeQueued=()=>{before++;if(timing==='before'&&ran)update();ran=true;};
        control.afterQueued=()=>{after++;if(timing==='after')update();};
        seed.linkedWidgets=[control];node.widgets.push(seed,control);return {widget:seed};
    }};
    const seeds=createSeedObserver(()=>timing);if(proven)seeds.installFactory(widgets);
    const cap=createParameterCapabilities({extensions:[]},widgets);
    class ComfyNode {
        static type=type;
        constructor(){this.type='';this.widgets=[];this.id=5;this.inputs=[];
            widgets.INT(this,seedName);seenType=this.type;if(observe)seeds.observe(this);
        }
    }
    const node=new ComfyNode();node.type=type;
    const graph={id:'A',_nodes:[node]},app={rootGraph:graph};
    const seed=node.widgets[0],control=node.widgets[1];
    const defs={[type]:{input:{required:{[seedName]:['INT',{min:0,max:2**50,step:1}]}}}};
    const description=()=>describeParameters(app,{'5':{class_type:type,inputs:{[seedName]:seed.value}}},defs,seeds,cap);
    return {node,graph,seed,control,seeds,cap,description,defs,seedName,seenType,counts:()=>({before,after,draws})};
}

for(const type of ['KSampler','KSamplerAdvanced','RandomNoise'])test(`registered ${type} constructor is observed before instance type assignment`,()=>{
    const f=fixture({type});assert.equal(f.seenType,'');
    assert.equal(f.seeds.parameterControl(f.node,f.seedName),f.control);
    const fields=f.description().nodes[0].fields;assert.equal(fields.length,1);
    assert.equal(fields[0].editable,true);assert.equal(fields[0].seed_control,true);
    assert.equal(fields[0].seed_mode,'randomize');assert.equal(fields[0].seed_timing,'after');
    assert.equal(fields[0].value,76642911565795);assert.deepEqual(f.counts(),{before:0,after:0,draws:0});
});

for(const timing of ['before','after'])test(`construction and loaded values retain native ${timing} first-run semantics`,()=>{
    const f=fixture({timing});f.seed.value=41;f.control.value='increment';
    const original=f.control.beforeQueued;f.seeds.observe(f.node,{loaded:true});
    assert.equal(f.control.beforeQueued,original);assert.equal(f.seed.value,41);
    f.control.beforeQueued();assert.equal(f.seed.value,41);f.control.afterQueued();
    assert.equal(f.seed.value,timing==='after'?42:41);
    f.control.beforeQueued();assert.equal(f.seed.value,42);f.control.afterQueued();
    assert.equal(f.seed.value,timing==='after'?43:42);
    assert.deepEqual(f.counts(),{before:2,after:2,draws:timing==='after'?2:1});
});

for(const mutation of ['type','constructor','before','after','seed-access','control-access'])test(`changed ${mutation} cannot retain constructor-time seed authority`,()=>{
    const f=fixture();
    if(mutation==='type')f.node.type='KSamplerAdvanced';
    if(mutation==='constructor')f.node.constructor=class Other {};
    if(mutation==='before')f.control.beforeQueued=()=>{};
    if(mutation==='after')f.control.afterQueued=()=>{};
    if(mutation==='seed-access')f.seed.callback=()=>{};
    if(mutation==='control-access')Object.defineProperty(f.control,'value',{get:()=> 'fixed',set(){}});
    assert.equal(f.seeds.parameterControl(f.node,'seed'),null);
});

test('unobserved and unknown policies remain merged read-only seed controls',()=>{
    for(const mode of ['unobserved','unknown-policy','unknown-timing']){
        const f=fixture({observe:mode!=='unobserved',timing:mode==='unknown-timing'?'unknown':'after'});
        if(mode==='unknown-policy')f.control.value='foreign-policy';
        const fields=f.description().nodes[0].fields;
        assert.equal(fields.length,1);const seed=fields[0];
        assert.equal(seed.editable,false);assert.equal(seed.seed_control,true);
        assert.equal(seed.seed_mode,undefined);assert.equal(seed.seed_timing,undefined);
        assert.equal(seed.seed_control_mode,mode==='unknown-policy'?'foreign-policy':'randomize');
        assert.match(seed.seed_control_reason,/種子生命週期/);
        assert.deepEqual(f.counts(),{before:0,after:0,draws:0});
    }
});

test('late factory installation never proves a pre-existing constructor-time control',()=>{
    const f=fixture({observe:false,proven:false});
    f.seeds.installFactory({INT(){}});f.seeds.observe(f.node);
    assert.equal(f.seeds.parameterControl(f.node,'seed'),null);
    assert.equal(f.description().nodes[0].fields[0].editable,false);
});

test('a backend-declared policy name is not merged or hidden as a native companion',()=>{
    const f=fixture();f.defs.KSampler.input.required.control_after_generate=['STRING',{}];
    const fields=f.description().nodes[0].fields;
    assert.equal(fields.length,2);
    assert.equal(fields[0].editable,false);
    assert.equal(fields[0].seed_control,undefined);assert.equal(fields[0].seed_mode,undefined);
    assert.equal(fields[1].field,'control_after_generate');assert.equal(fields[1].editable,false);
});
