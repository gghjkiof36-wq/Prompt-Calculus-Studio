// Registry-cache regression with actual PCS lifecycle hooks. The source-backed
// Vue/Pinia reproduction is recorded separately; this fixture has no browser.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createParameterCapabilities} from '../comfyui_prompt_studio/web/native_parameter_capabilities.js';
import {createSeedObserver} from '../comfyui_prompt_studio/web/native_seed.js';

const source=readFileSync(new URL('../comfyui_prompt_studio/web/prompt_studio.js',import.meta.url),'utf8');
function fixture({cached=false}={}){
    let callbackCalls=0,changes=0;
    const widgets=Object.fromEntries(['INT','FLOAT','BOOLEAN','COMBO','STRING'].map(type=>[type,(node,name,value)=>{
        const widget={name,type,value,options:{},callback(){callbackCalls++;return this.value;}};
        node.widgets.push(widget);
        if(name==='seed'){
            const control={name:'control_after_generate',value:'fixed',beforeQueued(){},afterQueued(){}};
            widget.linkedWidgets=[control];node.widgets.push(control);
        }
        return {widget};
    }]));
    let cachedMap;
    const custom=new Map(),registry={get widgets(){return cachedMap??=new Map([...custom,...Object.entries(widgets)]);},
        registerCustomWidgets(values){for(const pair of Object.entries(values))custom.set(...pair);cachedMap=undefined;}};
    if(cached)void registry.widgets;
    const app={extensions:[{name:'Comfy.DynamicPrompts',nodeCreated(node){for(const w of node.widgets)if(w.dynamicPrompts)w.serializeValue=()=>w.value;}}]};
    const seeds=createSeedObserver(()=> 'after');seeds.installFactory(widgets);
    const cap=createParameterCapabilities(app,widgets);
    const observe=Function('observed','syncing','captureManual','desktopNode','graphChanged','active','render','parameterCapabilities',
        source.slice(source.indexOf('function observe(node)'),source.indexOf('function captureAll()'))+';return observe;')(
        new WeakSet(),false,()=>{changes++;return false;},null,()=>{},null,()=>{},cap);
    let registration;
    app.registerExtension=ext=>{
        app.extensions.push(ext);
        registration=(async()=>registry.registerCustomWidgets(await ext.getCustomWidgets(app)))();
    };
    const register=()=>{Function('app','seedObserver','parameterCapabilities','observe','root',
        source.slice(source.lastIndexOf('app.registerExtension({')).replaceAll('import.meta.url',JSON.stringify(import.meta.url)))(app,seeds,cap,observe,null);return registration;};
    function construct(){
        const clip={id:11,type:'CLIPTextEncode',widgets:[]},sampler={id:5,type:'KSampler',widgets:[]};
        const text=registry.widgets.get('STRING')(clip,'text','{one|two}').widget;text.dynamicPrompts=true;
        const seed=registry.widgets.get('INT')(sampler,'seed',17).widget;
        const cfg=registry.widgets.get('FLOAT')(sampler,'cfg',4).widget;
        for(const ext of app.extensions){ext.nodeCreated?.(clip);ext.nodeCreated?.(sampler);}
        return {clip,sampler,text,seed,cfg,graph:{_nodes:[clip,sampler]}};
    }
    return {app,widgets,registry,seeds,cap,observe,register,construct,counts:()=>({callbackCalls,changes})};
}

for(const cached of [false,true])test(`official registration and PCS nodeCreated keep native fields editable, primed cache=${cached}`,async()=>{
    const f=fixture({cached}),before=f.registry.widgets;
    await f.register();assert.notEqual(f.registry.widgets,before);
    const n=f.construct();
    assert.ok(f.cap.primitive(n.cfg));assert.ok(f.cap.primitive(n.seed));assert.ok(f.cap.primitive(n.text));
    assert.ok(f.cap.trusted(n.text));assert.ok(f.seeds.parameterControl(n.sampler,'seed'));assert.equal(f.cap.reason(n.graph),'');
    const callback=n.text.callback;f.observe(n.clip);assert.equal(n.text.callback,callback);
    assert.equal(n.text.callback.call(n.text,'changed'),'{one|two}');
    assert.deepEqual(f.counts(),{callbackCalls:1,changes:1});
    const unrelated=()=>{};f.registry.registerCustomWidgets({OTHER:unrelated});
    assert.equal(f.registry.widgets.get('OTHER'),unrelated);assert.equal(f.registry.widgets.get('INT'),f.widgets.INT);
    assert.equal(f.cap.reason(n.graph),'');
});

test('nodes constructed through an old cached factory are not retrospectively promoted',async()=>{
    const f=fixture({cached:true}),old=f.construct();
    assert.equal(f.cap.primitive(old.cfg),false);assert.match(f.cap.reason(old.graph),/特殊序列化/);
    await f.register();f.observe(old.clip);
    assert.equal(f.cap.primitive(old.text),false);assert.equal(f.seeds.parameterControl(old.sampler,'seed'),null);
    assert.equal(f.cap.reason(f.construct().graph),'');
});

for(const mutation of ['callback','accessor','options','serializer'])test(`PCS callback observer cannot bless foreign ${mutation}`,async()=>{
    const f=fixture();await f.register();const n=f.construct();
    if(mutation==='callback')n.text.callback=()=>{};
    if(mutation==='accessor')Object.defineProperty(n.text,'value',{get:()=> 'foreign',set(){},configurable:true});
    if(mutation==='options')n.text.options={setValue(){}};
    if(mutation==='serializer')n.text.serializeValue=()=> 'foreign';
    f.cap.observeCallback(n.text,()=>{});
    assert.equal(f.cap.trusted(n.text),false);assert.match(f.cap.reason(n.graph),/特殊序列化/);
});

test('registration exports captured wrappers without adopting a later factory replacement',()=>{
    const f=fixture();const initial=f.cap.getCustomWidgets();
    const n={widgets:[]};const widget=f.widgets.FLOAT(n,'cfg',4).widget;
    initial.FLOAT=()=>{};assert.notEqual(f.cap.getCustomWidgets().FLOAT,initial.FLOAT);
    f.widgets.FLOAT=()=>{};assert.equal(f.cap.primitive(widget),false);
    assert.notEqual(f.cap.getCustomWidgets().FLOAT,f.widgets.FLOAT);
});
