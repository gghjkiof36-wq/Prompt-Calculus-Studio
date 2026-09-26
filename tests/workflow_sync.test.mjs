import test from 'node:test';
import assert from 'node:assert/strict';
import {workflowIdentity,captureFields,applyRunState,watchWorkflowRuns} from '../comfyui_prompt_studio/web/workflow_sync.js';

function fixture() {
    const app={nodeOutputs:{untouched:{images:[]}},extensionManager:{workflow:{activeWorkflow:{path:'workflows/folder/A.json',activeState:{id:'native'}}}},graph:{_nodes:[
        {id:1,type:'CLIPTextEncode',widgets:[{name:'text',value:'before'}]},
        {id:2,type:'CLIPTextEncode',widgets:[{name:'text',value:'negative'}]},
        {id:3,type:'PreviewImage'}]}};
    const value={prompt_id:'p',workflow:'A',name:'A',phase:'complete',texts:[{node:'1',field:'text',class_type:'CLIPTextEncode',text:'submitted'}],outputs:{'3':{class_type:'PreviewImage',images:[{filename:'result.png',type:'temp',subfolder:''}]}}};
    return {app,value};
}

test('explicit PCS mode applies first binding and future changes; Web mode preserves edits until switched back',()=>{
    const {app}=fixture(),record={};
    const live={owner:'owner',name:'A',texts:[{node:'1',field:'text',class_type:'CLIPTextEncode',text_source:'pcs',text:'red dress'}]};
    applyRunState(app,{live},captureFields(app.graph),record);
    assert.equal(app.graph._nodes[0].widgets[0].value,'red dress');
    live.texts[0].text='red dress, street';applyRunState(app,{live},captureFields(app.graph),record);
    assert.equal(app.graph._nodes[0].widgets[0].value,'red dress, street');
    live.texts[0].text_source='web';app.graph._nodes[0].widgets[0].value='';
    applyRunState(app,{live},captureFields(app.graph),record);assert.equal(app.graph._nodes[0].widgets[0].value,'');
    live.texts[0].text_source='pcs';applyRunState(app,{live},captureFields(app.graph),record);
    assert.equal(app.graph._nodes[0].widgets[0].value,'red dress, street');
    assert.equal(app.graph._nodes[1].widgets[0].value,'negative');
});

test('identity uses full saved path; history never edits live widgets or outputs',()=>{
    const {app,value}=fixture();
    assert.equal(workflowIdentity(app).path,'folder/A.json');
    const record={}; applyRunState(app,value,captureFields(app.graph),record);
    assert.equal(app.graph._nodes[0].widgets[0].value,'before');
    assert.equal(app.graph._nodes[1].widgets[0].value,'negative');
    assert.equal(app.nodeOutputs['3'],undefined);
    assert.ok(app.nodeOutputs.untouched);
    app.graph._nodes[0].widgets[0].value='manual afterward';
    applyRunState(app,value,captureFields(app.graph),record);
    assert.equal(app.graph._nodes[0].widgets[0].value,'manual afterward');
});

test('edits during lookup and newly connected text targets are preserved',()=>{
    const {app,value}=fixture(),before=captureFields(app.graph),record={};
    app.graph._nodes[0].widgets[0].value='changed while waiting';
    applyRunState(app,value,before,record);
    assert.equal(app.graph._nodes[0].widgets[0].value,'changed while waiting');
    value.prompt_id='next';app.graph._nodes[0].inputs=[{name:'text',link:22}];
    applyRunState(app,value,captureFields(app.graph),record);
    assert.equal(app.graph._nodes[0].widgets[0].value,'changed while waiting');
});

test('late lookup after graph switch cannot write into another workflow',async()=>{
    globalThis.document={hidden:false,addEventListener(){}};
    const {app,value}=fixture(); let resolve;
    const watcher=watchWorkflowRuns(app,{addEventListener(){}},()=>new Promise(r=>resolve=r),()=>{});
    try {
        const pending=watcher.refresh();const original=app.graph;
        app.graph=fixture().app.graph;resolve(value);await pending;
        assert.equal(app.graph._nodes[0].widgets[0].value,'before');
        assert.equal(original._nodes[0].widgets[0].value,'before');
    } finally {watcher.stop();}
});

test('history cannot inject executed events or replace a native two-image result',()=>{
    const {app,value}=fixture(),api=new EventTarget(),displayed=new Map(),events=[];
    // In 1.43 the legacy map is separate; rendering is updated by the executed
    // handler's setNodeOutputsByExecutionId. Mutating that map cannot pass this.
    api.addEventListener('executed',({detail})=>{events.push(detail);displayed.set(detail.display_node,detail.output);});
    const native={display_node:'3',prompt_id:'native',output:{images:[{filename:'one.png'},{filename:'two.png'}]}};
    api.dispatchEvent(new CustomEvent('executed',{detail:native}));
    const record={};applyRunState(app,value,captureFields(app.graph),record,api);
    assert.deepEqual(displayed.get('3'),native.output);
    assert.equal(events.length,1);
    applyRunState(app,value,captureFields(app.graph),record,api);assert.equal(events.length,1);
    app.graph._nodes[2]={id:3,type:'PreviewImage'};
    applyRunState(app,value,captureFields(app.graph),record,api);assert.equal(events.length,1);
    value.prompt_id='next';value.outputs['3'].images[0].filename='next.png';
    applyRunState(app,value,captureFields(app.graph),record,api);
    assert.deepEqual(displayed.get('3'),native.output);assert.equal(events.length,1);
});

function live(text,owner='library/workspace/A') {
    return {live:{owner,name:'A',revision:text,texts:[{node:'1',field:'text',class_type:'CLIPTextEncode',binding:'clip/output',text}]}};
}

test('live desktop text follows add/remove/empty without a job and never rewrites saved job text',()=>{
    const {app,value}=fixture(),record={};
    app.graph._nodes[0].widgets[0].value='dress';
    applyRunState(app,live('dress'),captureFields(app.graph),record);
    applyRunState(app,live('dress, standing'),captureFields(app.graph),record);
    assert.equal(app.graph._nodes[0].widgets[0].value,'dress, standing');
    const frozen=structuredClone(value);
    applyRunState(app,{...value,...live('')},captureFields(app.graph),record);
    assert.equal(app.graph._nodes[0].widgets[0].value,'');
    assert.equal(app.graph._nodes[1].widgets[0].value,'negative');assert.deepEqual(value,frozen);
    applyRunState(app,{...value,live:null},captureFields(app.graph),record);
    assert.equal(app.graph._nodes[0].widgets[0].value,'');
});

test('live preserves web edits during and after polling, unbind releases ownership',()=>{
    const {app}=fixture(),record={},widget=app.graph._nodes[0].widgets[0];
    const before=captureFields(app.graph);widget.value='web edit during wait';
    assert.match(applyRunState(app,live('dress'),before,record),/保留/);
    applyRunState(app,live('dress, standing'),captureFields(app.graph),record);
    assert.equal(widget.value,'web edit during wait');
    applyRunState(app,{live:{owner:'library/workspace/A',texts:[]}},captureFields(app.graph),record);
    applyRunState(app,live('rebound'),captureFields(app.graph),record);assert.equal(widget.value,'web edit during wait');
    widget.value='later edit';
    applyRunState(app,live('desktop later'),captureFields(app.graph),record);assert.equal(widget.value,'later edit');
});

test('hidden pages, open subgraphs and stopped watchers cannot perform lookups',async()=>{
    globalThis.document={hidden:true,addEventListener(){}};
    const {app}=fixture();let count=0;
    const watcher=watchWorkflowRuns(app,new EventTarget(),async()=>{count++;return live('new');},()=>{});
    await watcher.refresh();assert.equal(count,0);
    document.hidden=false;app.rootGraph={};await watcher.refresh();assert.equal(count,0);
    delete app.rootGraph;
    watcher.stop();document.hidden=false;await watcher.refresh();assert.equal(count,0);
});

test('native submission or navigation lease blocks polling and discards already pending text replies',async()=>{
    globalThis.document={hidden:false,addEventListener(){}};
    const {app}=fixture();let paused=false,resolve,calls=0;
    const widget=app.graph._nodes[0].widgets[0];widget.value='dress';
    const watcher=watchWorkflowRuns(app,new EventTarget(),()=>{calls++;return new Promise(r=>resolve=r);},()=>{},fn=>fn(),()=>paused);
    try {
        let pending=watcher.refresh();resolve(live('dress'));await pending;
        pending=watcher.refresh();paused=true;resolve(live('stale source text'));await pending;
        assert.equal(widget.value,'dress');await watcher.refresh();assert.equal(calls,2);
        paused=false;pending=watcher.refresh();resolve(live('new owned edit'));await pending;
        assert.equal(widget.value,'new owned edit');
    } finally {watcher.stop();}
});
