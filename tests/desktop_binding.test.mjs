import {test} from 'node:test';
import assert from 'node:assert/strict';
import {DesktopHandoff,workflowTarget} from '../comfyui_prompt_calculus_studio/web/desktop_binding.js';
import {KEY} from '../comfyui_prompt_calculus_studio/web/state.js';

const tick=()=>new Promise(resolve=>setImmediate(resolve));
const node=id=>({id,widgets:[{name:'text',value:'existing'}],inputs:[],properties:{[KEY]:{}}});
test('each workflow restores its own target and only positive paths are inferred',()=>{
    const a=node(1),negative=node(2),b=node(3);
    const sampler={inputs:[{name:'positive'},{name:'negative'}],getInputNode:i=>[a,negative][i]};
    assert.equal(workflowTarget({_nodes:[negative,a,sampler]}),a);
    assert.equal(workflowTarget({_nodes:[b],extra:{prompt_studio_active:3}}),b);
    assert.equal(workflowTarget({_nodes:[a,b]}),null);
    const sampler2={inputs:[{name:'positive'}],getInputNode:()=>b};
    assert.equal(workflowTarget({_nodes:[a,b,sampler,sampler2]}),null);
    assert.equal(workflowTarget({_nodes:[a,b,sampler,sampler2],extra:{prompt_studio_active:3}}),b);
});
test('a late claim from workflow A is released before B becomes active',async()=>{
    let finishA,owner=null; const events=[];
    const bridge=new DesktopHandoff(async n=>{
        events.push('claim '+n); if(n==='A' && !finishA) await new Promise(r=>finishA=r);
        owner=n; return 'lease-'+n;
    },async()=>{events.push('release '+owner); owner=null;},c=>events.push('active '+(c?.node??'-')));
    const a=bridge.select('A'); await tick(); const b=bridge.select('B'); finishA(); await Promise.all([a,b]);
    assert.equal(owner,'B'); assert.equal(bridge.current.node,'B');
    assert.ok(!events.includes('active A')); assert.ok(events.indexOf('release A')<events.indexOf('claim B'));
    await bridge.select('A'); assert.equal(owner,'A'); await bridge.select(null); assert.equal(owner,null);
});
test('rapid switches coalesce and explicit stop invalidates an in-flight claim',async()=>{
    let finish,owner=null; const activated=[];
    const bridge=new DesktopHandoff(async n=>{await new Promise(r=>finish=r); owner=n; return n;},async()=>{owner=null;},c=>{if(c)activated.push(c.node);});
    const a=bridge.select('A'); const b=bridge.select('B'); const c=bridge.select('C'); await tick();
    const stopped=bridge.select(null); finish(); await Promise.all([a,b,c,stopped]);
    assert.equal(owner,null); assert.deepEqual(activated,[]);
});
test('foreign ownership failures do not release another session and can be retried',async()=>{
    let fail=true,releases=0;
    const bridge=new DesktopHandoff(async()=>{if(fail)throw new Error('another browser'); return 'lease';},async()=>releases++,()=>{});
    await assert.rejects(bridge.select('A'),/another browser/); assert.equal(releases,0);
    fail=false; await bridge.select('A'); assert.equal(bridge.current.node,'A');
});
