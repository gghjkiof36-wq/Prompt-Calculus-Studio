import test from 'node:test';
import assert from 'node:assert/strict';
import {captureFields,applyLiveState} from '../comfyui_prompt_studio/web/workflow_sync.js';

function fixture() {
    const widget={name:'text',type:'text',value:'original'};
    const node={id:1,type:'CLIPTextEncode',widgets:[widget]};
    return {app:{graph:{_nodes:[node]}},node,widget,record:{}};
}
const live=(text,owner='A',binding='clip-A')=>({owner,name:owner,texts:[{node:'1',field:'text',class_type:'CLIPTextEncode',binding,text}]});
function apply(f,value,before=captureFields(f.app.graph)){return applyLiveState(f.app,value,before,f.record);}

test('owner and binding changes do not release manual edits of the same actual field',()=>{
    const f=fixture();apply(f,live('controlled'));f.widget.value='manual';
    assert.match(apply(f,live('B text','B','clip-B')),/保留/);
    apply(f,live('C text','C','clip-C'));assert.equal(f.widget.value,'manual');
});
test('unedited field follows new owner and observed empty unbind still releases it',()=>{
    const f=fixture();apply(f,live('A'));apply(f,live('B','B','clip-B'));assert.equal(f.widget.value,'B');
    f.widget.value='manual';apply(f,{owner:'B',texts:[]});apply(f,live('rebound','C'));assert.equal(f.widget.value,'rebound');
});
test('manual edit during owner switch stays protected in later polls',()=>{
    const f=fixture();apply(f,live('A'));const before=captureFields(f.app.graph);f.widget.value='concurrent';
    apply(f,live('B','B'),before);apply(f,live('B later','B'));assert.equal(f.widget.value,'concurrent');
});
test('recreated nodes are distinct and stale lookup cannot write into replacement widget',()=>{
    const f=fixture();apply(f,live('A'));f.widget.value='manual';const before=captureFields(f.app.graph);
    const node={id:1,type:'CLIPTextEncode',widgets:[{name:'text',value:'manual'}]};f.app.graph._nodes=[node];
    assert.match(apply(f,live('B','B'),before),/保留/);assert.equal(node.widgets[0].value,'manual');
    apply(f,live('B','B'));assert.equal(node.widgets[0].value,'B');assert.equal(f.widget.value,'manual');
});
test('linked and converted fields are not overwritten after owner changes',()=>{
    for(const converted of [false,true]) {
        const f=fixture();apply(f,live('A'));f.widget.value='manual';
        if(converted)f.widget.type='converted-widget';else f.node.inputs=[{name:'text',link:2}];
        assert.match(apply(f,live('B','B')),/保留/);assert.equal(f.widget.value,'manual');
    }
});
