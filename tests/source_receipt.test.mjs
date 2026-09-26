import test from 'node:test';
import assert from 'node:assert/strict';
import {watchWorkflowRuns} from '../comfyui_prompt_studio/web/workflow_sync.js';

const A='a'.repeat(64),B='b'.repeat(64);
const clone=value=>structuredClone(value);
function fixture(t,{text='A'}={}) {
    const previous=globalThis.document;
    globalThis.document=new EventTarget();document.hidden=false;
    const element={},widget={name:'text',value:text,element};
    const node={id:1,type:'CLIPTextEncode',widgets:[widget],inputs:[]};
    const graph={id:'native',_nodes:[node]},workflow={path:'workflows/A.json',activeState:{id:'native'}};
    const app={graph,rootGraph:graph,extensionManager:{workflow:{activeWorkflow:workflow}}};
    const key={library_id:'library',workspace_id:'workspace',workflow_id:'flow',frontend_id:'native',path:'A.json'};
    let value,lookup=null;
    const set=(text,source_revision=A,owner='library/workspace/flow')=>{
        value={live:{owner,source_revision,name:'flow',texts:[
            {node:'1',field:'text',class_type:'CLIPTextEncode',text,binding:'clip/output'}]}};
    };
    set('A');
    const watcher=watchWorkflowRuns(app,new EventTarget(),()=>lookup?lookup():Promise.resolve(clone(value)),()=>{});
    t.after(()=>{watcher.stop();globalThis.document=previous;});
    const edit=(text,changes={})=>{widget.value=text;return watcher.markEdited({type:'input',isTrusted:true,isComposing:false,target:element,...changes});};
    return {app,graph,node,widget,workflow,key,watcher,set,edit,element,
        live:()=>clone(value),lookup:fn=>{lookup=fn;}};
}

test('A graph never receives B receipt until the B response is actually applied',async t=>{
    const f=fixture(t);assert.equal(f.watcher.sourceReceipt(),null);
    await f.watcher.refresh();assert.equal(f.watcher.sourceReceipt().source_revision,A);
    f.set('B',B);
    assert.equal(f.widget.value,'A');assert.equal(f.watcher.sourceReceipt().source_revision,A);
    let resolve;f.lookup(()=>new Promise(r=>resolve=r));
    const pending=f.watcher.refresh();
    assert.equal(f.watcher.sourceReceipt().source_revision,A);
    resolve(f.live());await pending;
    assert.equal(f.widget.value,'B');assert.equal(f.watcher.sourceReceipt().source_revision,B);
    assert.equal(f.watcher.sourceReceipt().texts[0].mode,'pcs');
});

test('initial mismatch is preserved but cannot be certified as manual',async t=>{
    const f=fixture(t,{text:'unknown saved text'});
    await f.watcher.refresh();assert.equal(f.watcher.sourceReceipt(),null);
    f.set('B',B);await f.watcher.refresh();
    assert.equal(f.widget.value,'unknown saved text');assert.equal(f.watcher.sourceReceipt(),null);
});

test('trusted field input proves manual ownership including empty text',async t=>{
    const f=fixture(t,{text:'unknown'});await f.watcher.refresh();
    assert.equal(f.edit(''),true);assert.equal(f.watcher.sourceReceipt(),null);
    await f.watcher.refresh();
    assert.deepEqual(f.watcher.sourceReceipt(),{owner:'library/workspace/flow',source_revision:A,
        texts:[{node:'1',field:'text',class_type:'CLIPTextEncode',text:'',mode:'manual'}]});
    f.set('B',B);await f.watcher.refresh();
    assert.equal(f.widget.value,'');assert.equal(f.watcher.sourceReceipt().source_revision,B);
    const copy=f.watcher.sourceReceipt();copy.texts[0].text='tamper';
    assert.equal(f.watcher.sourceReceipt().texts[0].text,'');
});

for(const [name,changes] of Object.entries({synthetic:{isTrusted:false},composition:{isComposing:true},
    unrelated:{target:{}},wrongEvent:{type:'change'}})) {
    test(`${name} input cannot certify initial mismatch`,async t=>{
        const f=fixture(t,{text:'unknown'});await f.watcher.refresh();
        assert.equal(f.edit('different',changes),false);await f.watcher.refresh();
        assert.equal(f.watcher.sourceReceipt(),null);
    });
}

test('unannounced change during lookup stays unknown even on later identical polls',async t=>{
    const f=fixture(t);await f.watcher.refresh();
    let resolve;f.lookup(()=>new Promise(r=>resolve=r));
    f.set('B',B);const pending=f.watcher.refresh();f.widget.value='unproven edit';
    assert.equal(f.watcher.sourceReceipt(),null);resolve(f.live());await pending;
    f.lookup(null);await f.watcher.refresh();
    assert.equal(f.widget.value,'unproven edit');assert.equal(f.watcher.sourceReceipt(),null);
});

test('manual field cannot bless a different unknown bound field',async t=>{
    const f=fixture(t,{text:'unknown'});
    const second={id:2,type:'CLIPTextEncode',widgets:[{name:'text',value:'other unknown',element:{}}],inputs:[]};
    f.graph._nodes.push(second);
    const live=f.live();live.live.texts.push({node:'2',field:'text',class_type:'CLIPTextEncode',text:'PCS negative',binding:'clip/negative'});
    f.lookup(async()=>clone(live));await f.watcher.refresh();f.edit('manual');await f.watcher.refresh();
    assert.equal(f.watcher.sourceReceipt(),null);assert.equal(second.widgets[0].value,'other unknown');
});

test('a shared input target cannot certify multiple widgets as manual',async t=>{
    const f=fixture(t,{text:'unknown'});await f.watcher.refresh();
    f.node.widgets.push({name:'other',value:'other unknown',element:f.element});
    assert.equal(f.edit('manual'),false);await f.watcher.refresh();assert.equal(f.watcher.sourceReceipt(),null);
});

test('missing root graph never returns a background source receipt',async t=>{
    const f=fixture(t);await f.watcher.refresh();delete f.app.rootGraph;
    assert.equal(f.watcher.sourceReceipt(),null);assert.equal(f.edit('unknown'),false);
});

test('owner change does not inherit another library manual ownership',async t=>{
    const f=fixture(t);await f.watcher.refresh();f.edit('manual');await f.watcher.refresh();
    f.set('B',B,'different/workspace/flow');await f.watcher.refresh();
    assert.equal(f.widget.value,'manual');assert.equal(f.watcher.sourceReceipt(),null);
});

test('a changed binding cannot inherit manual ownership on the same widget',async t=>{
    const f=fixture(t);await f.watcher.refresh();f.edit('manual');await f.watcher.refresh();
    const live=f.live();live.live.texts[0].binding='different/output';live.live.source_revision=B;
    f.lookup(async()=>clone(live));await f.watcher.refresh();assert.equal(f.watcher.sourceReceipt(),null);
});

for(const [name,mutate] of Object.entries({
    graph:f=>{f.app.graph={...f.graph};f.app.rootGraph=f.app.graph;},
    workflow:f=>{f.app.extensionManager.workflow.activeWorkflow={...f.workflow};},
    identity:f=>{f.workflow.path='workflows/B.json';},
    node:f=>{f.graph._nodes[0]={...f.node};},
    widget:f=>{f.node.widgets[0]={...f.widget};},
    linked:f=>{f.node.inputs=[{name:'text',link:3}];},
    value:f=>{f.widget.value='changed';},
    stopped:f=>{f.watcher.stop();},
    generation:f=>{f.watcher.changed();},
})) test(`${name} change invalidates the synchronous receipt`,async t=>{
    const f=fixture(t);await f.watcher.refresh();assert.ok(f.watcher.sourceReceipt());
    mutate(f);assert.equal(f.watcher.sourceReceipt(),null);
});

test('late response after same-ID workflow replacement cannot write or acknowledge',async t=>{
    const f=fixture(t);await f.watcher.refresh();
    let resolve;f.lookup(()=>new Promise(r=>resolve=r));f.set('B',B);
    const pending=f.watcher.refresh();f.app.extensionManager.workflow.activeWorkflow={...f.workflow};
    resolve(f.live());await pending;
    assert.equal(f.widget.value,'A');assert.equal(f.watcher.sourceReceipt(),null);
});

test('missing or failed live source clears acknowledgement; unbind acknowledges an empty set',async t=>{
    const f=fixture(t);await f.watcher.refresh();
    f.lookup(async()=>({live:null}));await f.watcher.refresh();assert.equal(f.watcher.sourceReceipt(),null);
    f.lookup(null);await f.watcher.refresh();
    f.lookup(async()=>{throw Error('offline');});await f.watcher.refresh();assert.equal(f.watcher.sourceReceipt(),null);
    f.lookup(async()=>({live:{owner:'library/workspace/flow',source_revision:B,texts:[]}}));
    await f.watcher.refresh();assert.deepEqual(f.watcher.sourceReceipt().texts,[]);
});

test('restored manual receipt needs the same live owner and source before acknowledgement',async t=>{
    const f=fixture(t,{text:'restored manual'});
    const receipt={owner:'library/workspace/flow',source_revision:A,texts:[
        {node:'1',field:'text',class_type:'CLIPTextEncode',text:'restored manual',mode:'manual'}]};
    assert.equal(f.watcher.restoreSourceReceipt(receipt,f.key),true);
    assert.equal(f.watcher.sourceReceipt(),null);
    await f.watcher.refresh();assert.deepEqual(f.watcher.sourceReceipt(),receipt);
    f.set('B',B);await f.watcher.refresh();
    assert.equal(f.widget.value,'restored manual');assert.equal(f.watcher.sourceReceipt().source_revision,B);
});

test('restored ownership cannot certify a changed offline source without reconfirmation',async t=>{
    const f=fixture(t,{text:'restored manual'});
    const receipt={owner:'library/workspace/flow',source_revision:A,texts:[
        {node:'1',field:'text',class_type:'CLIPTextEncode',text:'restored manual',mode:'manual'}]};
    f.watcher.restoreSourceReceipt(receipt,f.key);f.set('B',B);await f.watcher.refresh();
    assert.equal(f.watcher.sourceReceipt(),null);assert.equal(f.widget.value,'restored manual');
    f.edit('new explicit manual');await f.watcher.refresh();
    assert.equal(f.watcher.sourceReceipt().source_revision,B);
});

test('restoration rejects wrong original identity, owner, value and duplicate fields',async t=>{
    const f=fixture(t);
    const receipt={owner:'library/workspace/flow',source_revision:A,texts:[
        {node:'1',field:'text',class_type:'CLIPTextEncode',text:'A',mode:'pcs'}]};
    assert.equal(f.watcher.restoreSourceReceipt(receipt,{...f.key,path:'B.json'}),false);
    assert.equal(f.watcher.restoreSourceReceipt({...receipt,owner:'wrong'},f.key),false);
    assert.equal(f.watcher.restoreSourceReceipt({...receipt,texts:[{...receipt.texts[0],text:'wrong'}]},f.key),false);
    assert.equal(f.watcher.restoreSourceReceipt({...receipt,texts:[receipt.texts[0],receipt.texts[0]]},f.key),false);
    assert.equal(f.watcher.sourceReceipt(),null);
});
