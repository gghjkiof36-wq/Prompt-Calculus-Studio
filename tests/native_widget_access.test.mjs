import test from 'node:test';
import assert from 'node:assert/strict';
import {captureWidgetAccess,matchesWidgetAccess} from '../comfyui_prompt_calculus_studio/web/native_parameter_capabilities.js';

// A focused forwarding fixture; actual Vue/Pinia + BaseWidget sources are
// exercised separately by the source-backed frontend compatibility harness.
const reactive=raw=>new Proxy(raw,{get(target,key,receiver){
    if(key==='__v_raw')return target;
    if(key==='__v_isReactive')return true;
    if(key==='__v_isReadonly')return false;
    return Reflect.get(target,key,receiver);
}});
function fixture(){
    const raw={min:0,max:100,step:1,getValue(){return 1;},setValue(){}};
    const widget={value:1,options:raw,callback(){},setNodeId(){this.options=reactive(raw);}};
    return {raw,widget,access:captureWidgetAccess(widget)};
}

test('native options can acquire a live reactive view without losing factory identity',()=>{
    const {raw,widget,access}=fixture(),before=Object.getOwnPropertyDescriptors(raw);
    widget.setNodeId();
    for(let i=0;i<3;i++)assert.equal(matchesWidgetAccess(widget,access),true);
    assert.deepEqual(Object.getOwnPropertyDescriptors(raw),before);
    widget.options=raw;assert.equal(matchesWidgetAccess(widget,access),true);
});

for(const replacement of ['copy','claimed raw','other reactive target','readonly','throwing proxy'])
    test(`foreign options remain rejected: ${replacement}`,()=>{
        const {raw,widget,access}=fixture(),before=Reflect.ownKeys(raw);
        widget.options=replacement==='copy'?{...raw}:
            replacement==='claimed raw'?{...raw,__v_isReactive:true,__v_raw:raw}:
            replacement==='other reactive target'?reactive({...raw}):
            replacement==='readonly'?new Proxy(raw,{get(target,key){
                if(key==='__v_isReadonly'||key==='__v_isReactive')return true;
                if(key==='__v_raw')return raw;return target[key];
            }}):new Proxy(raw,{get(target,key){if(typeof key==='symbol')throw Error('no probe');
                if(key==='__v_raw')return raw;if(key==='__v_isReactive')return true;return target[key];}});
        assert.equal(matchesWidgetAccess(widget,access),false);
        assert.deepEqual(Reflect.ownKeys(raw),before);
    });

for(const mutation of ['callback','accessor','getValue','setValue'])
    test(`reactivity never blesses a changed ${mutation}`,()=>{
        const {raw,widget,access}=fixture();widget.setNodeId();
        if(mutation==='callback')widget.callback=()=>{};
        else if(mutation==='accessor')Object.defineProperty(widget,'value',{get:()=>1,set(){}});
        else raw[mutation]=()=>{};
        assert.equal(matchesWidgetAccess(widget,access),false);
    });

test('non-extensible or unreadable options do not gain a new access capability',()=>{
    const {raw,widget,access}=fixture();Object.preventExtensions(raw);
    widget.setNodeId();assert.equal(matchesWidgetAccess(widget,access),false);
    widget.options=raw;assert.equal(matchesWidgetAccess(widget,access),true);
});

test('a later forwarding proxy cannot claim native registration provenance',()=>{
    const {raw,widget,access}=fixture();widget.setNodeId();
    assert.equal(matchesWidgetAccess(widget,access),true);
    widget.options=reactive(raw);
    assert.equal(matchesWidgetAccess(widget,access),false);
    // Calling the original registration with an already foreign access path
    // also cannot restore a lost capability.
    widget.setNodeId();assert.equal(matchesWidgetAccess(widget,access),false);
});

test('replacement native registration method does not acquire the factory capability',()=>{
    const {raw,widget,access}=fixture();widget.setNodeId=()=>{widget.options=reactive(raw);};
    widget.setNodeId();assert.equal(matchesWidgetAccess(widget,access),false);
});

test('registration does not adopt changed delegates during the transition',()=>{
    const raw={},widget={value:1,options:raw,setNodeId(){this.options=reactive(raw);this.callback=()=>{};}};
    const access=captureWidgetAccess(widget);widget.setNodeId();
    widget.callback=undefined;assert.equal(matchesWidgetAccess(widget,access),false);
});

test('unreadable foreign delegates do not prevent the original registration call',()=>{
    let calls=0;const result={},raw={},widget={value:1,options:raw,setNodeId(id){calls++;assert.equal(id,42);return result;}};
    const access=captureWidgetAccess(widget);
    Object.defineProperty(raw,'getValue',{get(){throw Error('unsupported delegate');}});
    assert.equal(widget.setNodeId(42),result);assert.equal(calls,1);
    assert.equal(matchesWidgetAccess(widget,access),false);
});

test('unreadable observation results preserve the exact native return and exception',()=>{
    const result={get then(){throw Error('opaque result');}};
    const raw={},widget={value:1,options:raw,setNodeId(){this.options=reactive(raw);return result;}};
    const access=captureWidgetAccess(widget);
    assert.equal(widget.setNodeId(),result);assert.equal(matchesWidgetAccess(widget,access),false);
    const originalError=Error('native error');
    const failing={value:1,options:{},setNodeId(){throw originalError;}};captureWidgetAccess(failing);
    assert.throws(()=>failing.setNodeId(),error=>error===originalError);
});

test('a thenable registration return stays unchanged and grants no synchronous capability',()=>{
    const result=Promise.resolve(),raw={},widget={value:1,options:raw,setNodeId(){this.options=reactive(raw);return result;}};
    const access=captureWidgetAccess(widget);
    assert.equal(widget.setNodeId(),result);assert.equal(matchesWidgetAccess(widget,access),false);
});

test('a throwing foreign lifecycle getter is refused without throwing from the capability check',()=>{
    const {widget,access}=fixture();widget.setNodeId();
    Object.defineProperty(widget,'setNodeId',{get(){throw Error('foreign lifecycle');}});
    assert.equal(matchesWidgetAccess(widget,access),false);
});
