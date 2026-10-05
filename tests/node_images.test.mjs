import test from 'node:test';
import assert from 'node:assert/strict';
import {imageNodeState,watchImageNodes} from '../comfyui_prompt_calculus_studio/web/node_images.js';

test('only named workflow image nodes publish; current selection and relative filenames survive',()=>{
    globalThis.location=new URL('http://127.0.0.1:8188/');
    const graph={extra:{prompt_studio_v08:{id:'A'}},_nodes:[
        {id:1,type:'LoadImage',widgets:[{name:'image',value:'folder/input.png'}]},
        {id:2,type:'PreviewImage',imgs:[{src:'/view?filename=one.png&type=temp'},{src:'/view?filename=two.png&type=temp'}],imageIndex:1},
        {id:3,type:'CLIPTextEncode',widgets:[{name:'text',value:'private prompt'}]}
    ]};
    const value=imageNodeState(graph);
    assert.deepEqual(Object.keys(value.nodes),['1','2']); assert.equal(value.nodes['2'].selected,1);
    assert.equal(value.nodes['1'].images[0],'folder/input.png'); assert.equal(value.nodes['2'].images[1].type,'temp');
    graph.extra={}; assert.equal(imageNodeState(graph),null); assert.deepEqual(graph.extra,{});
});

test('selection during an outstanding publish is sent afterward without generating',async()=>{
    globalThis.document={addEventListener(){}};
    const graph={extra:{prompt_studio_v08:{id:'A'}},_nodes:[{id:1,type:'LoadImage',widgets:[{name:'image',value:'one.png'}]}]};
    let resolve; const calls=[];
    const publish=watchImageNodes({graph},{addEventListener(){}},async(route,value)=>{
        calls.push({route,value}); if(calls.length===1) await new Promise(r=>resolve=r);
    });
    const first=publish(); graph._nodes[0].widgets[0].value='two.png'; await publish(); resolve(); await first;
    await new Promise(r=>setImmediate(r));
    assert.equal(calls.length,2); assert.equal(calls[1].value.nodes['1'].images[0],'two.png');
    assert.ok(calls.every(v=>v.route==='workflow/images'));
    await publish(); assert.equal(calls.length,2);
});

test('native load lease prevents publishing target images under the previous workflow identity',async()=>{
    globalThis.document={addEventListener(){}};
    const graph={_nodes:[{id:1,type:'LoadImage',widgets:[{name:'image',value:'B.png'}]}]};
    let paused=true,current='A';const calls=[];
    const app={graph,rootGraph:graph};
    const publish=watchImageNodes(app,new EventTarget(),async(route,value)=>calls.push(value),()=>({path:current+'.json',frontend_id:current}),()=>paused);
    await publish();assert.equal(calls.length,0);
    current='B';paused=false;await publish();assert.equal(calls[0].path,'B.json');
    assert.equal(calls[0].nodes['1'].images[0],'B.png');
});

test('unsaved native identity is preserved and oversized sets cannot look like complete batches',()=>{
    globalThis.location=new URL('http://127.0.0.1:8188/');
    const node={id:9,type:'PreviewImage',imgs:Array.from({length:65},(_,i)=>({src:'/view?filename='+i+'.png&type=temp'}))};
    const graph={extra:{prompt_studio_v08:{id:'A'}},_nodes:[node]};
    const state=imageNodeState(graph,{frontend_id:'native-A'});
    assert.equal(state.frontend_id,'native-A');assert.equal(state.nodes['9'].overflow,true);
    assert.equal(state.nodes['9'].images.length,0);assert.equal(state.nodes['9'].selected,null);
    node.imgs.pop();assert.equal(imageNodeState(graph).nodes['9'].images.length,64);
});
