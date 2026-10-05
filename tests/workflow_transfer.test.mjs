import test from 'node:test';
import assert from 'node:assert/strict';
import {editableFields,transferSettings,assignOutput,exportTransfer} from '../comfyui_prompt_calculus_studio/web/workflow_transfer.js';
const graph=()=>({_nodes:[{id:6,type:'Positive',widgets:[{name:'text',value:'original'}],inputs:[]},
    {id:7,type:'Negative',widgets:[{name:'text',value:''},{name:'text_g',value:'a'},{name:'text_l',value:'linked'}],inputs:[{name:'text_l',link:1}]}]});
test('all editable text fields are listed; linked text is excluded',()=>assert.equal(editableFields(graph()._nodes).length,3));
test('multiple outputs bind explicitly without changing original text',()=>{
    const g=graph();assignOutput(g,'positive','6','text');assignOutput(g,'negative','7','text');
    assert.equal(transferSettings(g).bindings.length,2);assert.equal(g._nodes[0].widgets[0].value,'original');
    assert.throws(()=>assignOutput(g,'spare','7','text'),/占用/);assert.throws(()=>assignOutput(g,'spare','7','text_l'),/已接線/);
});
test('export is immutable and identifies one workflow',()=>{
    const g=graph();assignOutput(g,'positive','6','text'); const profile=exportTransfer(g,{'6':{inputs:{text:'source'}}},{nodes:[]});
    assert.equal(profile.bindings[0].workflow,profile.id);g.extra.prompt_studio_v08.bindings=[];assert.equal(profile.bindings.length,1);
    const other=graph();assert.notEqual(transferSettings(other).id,profile.id);
});
test('removed target blocks an export instead of silently skipping it',()=>{
    const g=graph();assignOutput(g,'negative','7','text');g._nodes.pop();assert.throws(()=>exportTransfer(g,{},{}),/失效/);
});
