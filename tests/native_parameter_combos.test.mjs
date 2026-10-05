import test from 'node:test';
import assert from 'node:assert/strict';
import {describeParameters,parameterTransaction} from '../comfyui_prompt_calculus_studio/web/native_parameters.js';
import {createParameterCapabilities} from '../comfyui_prompt_calculus_studio/web/native_parameter_capabilities.js';

const models=['4xNomos8kDAT.pth','other.pth'];
function fixture({type='UpscaleModelLoader',name='model_name',spec=['COMBO',{options:models,multiselect:false}],value=models[0]}={}){
    const node={id:27,type,title:type,widgets:[],inputs:[]};let callbacks=0;
    const graph={id:'combo-workflow',_nodes:[node],setDirtyCanvas(){}};
    const app={rootGraph:graph,extensions:[]};
    const factories={COMBO(n,field){const widget={name:field,type:'combo',value,options:{values:models},callback(){callbacks++;}};
        n.widgets.push(widget);return {widget};}};
    const capabilities=createParameterCapabilities(app,factories),widget=factories.COMBO(node,name).widget;
    const definitions={[type]:{python_module:'test.nodes',input:{required:{[name]:spec}}}};
    const output=()=>({'27':{class_type:type,inputs:{[name]:widget.value}}});
    const describe=()=>describeParameters(app,output(),definitions,null,capabilities);
    const field=()=>describe().nodes[0].fields[0];
    const patch=(next)=>({...field(),node:'27',path:['27'],class_type:type,base:widget.value,value:next});
    const command=(patches)=>({identity:{frontend_id:graph.id,path:'combo.json'},parameters:{version:1,
        identity:{frontend_id:graph.id,origin:{path:'combo.json'}},patches}});
    const transaction=(patches,description=describe())=>parameterTransaction(app,command(patches),description,()=>{},null,()=>{},capabilities);
    return {node,graph,widget,definitions,describe,field,patch,transaction,output,capabilities,callbacks:()=>callbacks};
}

for(const [type,name,spec]of [
    ['UpscaleModelLoader','model_name',['COMBO',{options:models,multiselect:false}]],
    ['CheckpointLoaderSimple','ckpt_name',[models,{tooltip:'checkpoint'}]],
    ['LoraLoader','lora_name',[models,{tooltip:'LoRA'}]],
    ['UnlistedNativeNode','selection',['COMBO',{options:models}]],
])test(`${type} exposes the same static choice contract and restores transaction values`,()=>{
    const f=fixture({type,name,spec});const field=f.field();
    assert.equal(field.type,'ENUM');assert.equal(field.editable,true);assert.deepEqual(field.choices,models);
    const tx=f.transaction([f.patch(models[1])]);tx.apply();assert.equal(f.widget.value,models[0]);
    tx.enterSerialize();const payload=f.output();tx.leaveSerialize();
    assert.equal(payload['27'].inputs[name],models[1]);assert.equal(f.widget.value,models[0]);
    assert.equal(tx.evidence(payload)[0].actual,models[1]);tx.restore();
    assert.equal(f.widget.value,models[0]);assert.equal(f.callbacks(),0);
});

test('new and legacy spellings keep the same persisted ENUM schema',()=>{
    const modern=fixture(),legacy=fixture({spec:[models,{}]});
    assert.equal(modern.field().schema,legacy.field().schema);
    assert.equal(legacy.field().schema,'pcs-parameters-v1:'+JSON.stringify(['UpscaleModelLoader','test.nodes','model_name','ENUM',models,{},'combo','undefined',false,null]));
    const tx=modern.transaction([legacy.patch(models[1])]);tx.apply();tx.enterSerialize();
    assert.equal(modern.widget.value,models[1]);tx.restore();assert.equal(modern.widget.value,models[0]);
});

test('numeric choices preserve float/number/string types without coercion',()=>{
    const choices=[1,1.5,'1',''];const f=fixture({spec:['COMBO',{options:choices}],value:1});
    assert.equal(f.field().editable,true);assert.deepEqual(f.field().choices,choices);
    for(const next of choices){const tx=f.transaction([f.patch(next)]);tx.apply();tx.enterSerialize();
        assert.equal(f.widget.value,next);tx.evidence(f.output());tx.restore();assert.equal(f.widget.value,1);}
    for(const next of [true,null,{},[1],2,NaN,Infinity])assert.throws(()=>f.transaction([f.patch(next)]),/參數值或範圍/);
    const onlyNumber=fixture({spec:['COMBO',{options:[1]}],value:1});
    assert.throws(()=>onlyNumber.transaction([onlyNumber.patch('1')]),/參數值或範圍/);
});

test('changed choices or input type invalidate queued intentions before any mutation',()=>{
    for(const replacement of [['COMBO',{options:[...models,'later.pth']}],['COMBO',{options:[...models].reverse()}],['STRING',{}]]){
        const f=fixture(),patch=f.patch(models[1]);f.definitions.UpscaleModelLoader.input.required.model_name=replacement;
        assert.throws(()=>f.transaction([patch]),/欄位定義、連線或序列化已變更/);
        assert.equal(f.widget.value,models[0]);assert.equal(f.callbacks(),0);
    }
});

test('unsupported combo metadata and nonprimitive options stay read-only',()=>{
    const specs=[['COMBO'],['COMBO',{}],['COMBO',{options:[]}],['COMBO',{options:'a,b'}],
        ['COMBO',{options:()=>models}],['COMBO',{options:[true,false]}],['COMBO',{options:[{value:'a'}]}],
        ['COMBO',{options:[null]}],['COMBO',{options:[NaN]}],['COMBO',{options:[Infinity]}],
        ['COMBO',{options:models,remote:{route:'/models'}}],['COMBO',{options:models,remote:false}],
        ['COMBO',{options:models,multiselect:true}],['COMBO',{options:models,multi_select:{}}],
        ['COMBO',{options:models,control_after_generate:'increment'}],['COMFY_DYNAMICCOMBO_V3',{options:models}]];
    for(const spec of specs){const f=fixture({spec}),field=f.field();
        assert.equal(field.editable,false,JSON.stringify(spec));assert.match(field.reason,/尚未適配/);
        assert.equal(field.choices,undefined);assert.throws(()=>f.transaction([f.patch(models[1])]),/欄位定義/);
        assert.equal(f.widget.value,models[0]);assert.equal(f.callbacks(),0);
    }
});

test('serializer, provenance, linked input and submission mismatch protections apply to new combos',()=>{
    for(const mutation of ['serializer','callback','accessor','linked']){
        const f=fixture();
        if(mutation==='serializer')f.widget.serializeValue=()=>models[0];
        if(mutation==='callback')f.widget.callback=()=>{};
        if(mutation==='accessor')Object.defineProperty(f.widget,'value',{get:()=>models[0],set(){},configurable:true});
        if(mutation==='linked')f.node.inputs.push({name:'model_name',link:1});
        assert.equal(f.field().editable,false,mutation);
        assert.throws(()=>f.transaction([f.patch(models[1])]),/特殊序列化|欄位定義/);
    }
    const f=fixture(),tx=f.transaction([f.patch(models[1])]);tx.apply();tx.enterSerialize();
    const wrong=f.output();wrong['27'].inputs.model_name=models[0];tx.leaveSerialize();
    assert.throws(()=>tx.evidence(wrong),/序列化與套用值不同/);tx.restore();assert.equal(f.widget.value,models[0]);
});

test('serialization failure restores the projected choice and preserves later external edits',()=>{
    const f=fixture();let tx=f.transaction([f.patch(models[1])]);tx.apply();
    try{tx.enterSerialize();throw new Error('serialization failed');}catch(error){assert.equal(error.message,'serialization failed');}
    finally{tx.restore();}assert.equal(f.widget.value,models[0]);
    tx=f.transaction([f.patch(models[1])]);tx.apply();tx.enterSerialize();f.widget.value='external.pth';tx.restore();
    assert.equal(f.widget.value,'external.pth');assert.equal(f.callbacks(),0);
});
