import test from 'node:test';
import assert from 'node:assert/strict';
import {createSeedObserver} from '../comfyui_prompt_calculus_studio/web/native_seed.js';

function fixture(timing='before',provided=null) {
    const seed={name:'seed',value:10,options:{min:0,max:100,step2:1}};
    const a={name:'control_after_generate',value:'increment'},Ty=Symbol();
    const controlValueRunBefore=()=>timing==='before';
    const applyWidgetControl=()=>{seed.value++;};
    // Exact verified functions from the locally installed 1.43.18 bundle.
    a.beforeQueued=({isPartialExecution:e}={})=>{!e&&controlValueRunBefore()&&a[Ty]&&applyWidgetControl(),a[Ty]=!0};
    a.afterQueued=({isPartialExecution:e}={})=>{!e&&!controlValueRunBefore()&&applyWidgetControl()};
    const node={id:1,type:'KSampler',widgets:[seed,a]},graph={_nodes:[node]};
    const observer=provided??createSeedObserver(()=>timing);observer.observe(node);
    return {seed,control:a,node,graph,observer};
}
test('captures before-first state without invoking queue or consuming seed',()=>{
    const f=fixture();assert.equal(f.observer.capture(f.graph).hasExecuted,false);assert.equal(f.seed.value,10);
    f.control.beforeQueued();assert.equal(f.seed.value,10);assert.equal(f.observer.capture(f.graph).hasExecuted,true);
    f.control.beforeQueued();assert.equal(f.seed.value,11);
});

for(const timing of ['before','after'])test(`tab reconstruction preserves ${timing} lifecycle for multiple samplers, without a draw on load`,()=>{
    const observer=createSeedObserver(()=>timing),workflow={activeState:{id:'A'}};
    let samplers=[fixture(timing,observer),fixture(timing,observer)];samplers[1].node.id=2;
    const graph=()=>({id:'A',_nodes:samplers.map(f=>f.node)});
    const run=()=>{const hooks=observer.queueHooks(graph());hooks.before();const seeds=samplers.map(f=>f.seed.value);hooks.after();return seeds;};
    assert.deepEqual(run(),[10,10]);assert.deepEqual(run(),[11,11]);
    observer.remember(graph(),workflow);
    const values=samplers.map(f=>f.seed.value);
    samplers=values.map((value,i)=>{const f=fixture(timing,observer);f.node.id=i+1;f.seed.value=value;return f;});
    observer.resume(graph(),workflow);observer.resume(graph(),workflow);
    assert.deepEqual(samplers.map(f=>f.seed.value),values);
    assert.deepEqual(run(),[12,12]);
    const fresh=fixture(timing,observer);observer.resume({id:'A',_nodes:[fresh.node]},{activeState:{id:'A'}});
    fresh.control.beforeQueued();assert.equal(fresh.seed.value,10,'another workflow must keep its first-run semantics');
});

test('replaced sampler settings and unknown loaded state are never initialized as previously executed',()=>{
    const f=fixture(),workflow={activeState:{id:'A'}};f.graph.id='A';
    f.control.beforeQueued();f.observer.remember(f.graph,workflow);
    const next=fixture('before',f.observer);next.graph.id='A';next.seed.value=50;
    f.observer.resume(next.graph,workflow);next.control.beforeQueued();assert.equal(next.seed.value,50);
});

test('frozen queue supports image-only graphs and runs verified seed hooks exactly once',()=>{
    const f=fixture('after');const hooks=f.observer.queueHooks(f.graph);
    hooks.before();const frozen=f.seed.value;hooks.after();
    assert.equal(frozen,10);assert.equal(f.seed.value,11);
    const pure={_nodes:[{id:8,type:'ImageScale',widgets:[]}]};
    const imageHooks=f.observer.queueHooks(pure);imageHooks.before();imageHooks.after();
    pure._nodes[0].widgets.push({beforeQueued(){}});
    assert.throws(()=>f.observer.queueHooks(pure),/未支援/);
});
test('restore uses verified partial initializer once and preserves native next before transition',()=>{
    const f=fixture();f.observer.restore(f.graph,{node_id:'1',hasExecuted:true});
    assert.equal(f.seed.value,10);f.observer.restore(f.graph,{node_id:'1',hasExecuted:true});
    assert.equal(f.seed.value,10);f.control.beforeQueued();assert.equal(f.seed.value,11);
});
test('unknown or replaced native hooks are rejected, not interpreted as fixed seed',()=>{
    const f=fixture();f.control.afterQueued=()=>{};
    assert.throws(()=>f.observer.capture(f.graph),/回呼已變更/);
    const g=fixture();const unknown={...g.node};g.observer=createSeedObserver(()=>'after');
    assert.throws(()=>g.observer.capture({_nodes:[unknown]}),/尚未觀察/);
});
test('after timing keeps current execution seed and native after hook unchanged',()=>{
    const f=fixture('after');f.control.beforeQueued();assert.equal(f.seed.value,10);
    f.control.afterQueued();assert.equal(f.seed.value,11);assert.equal(f.observer.capture(f.graph).timing,'after');
});

test('a non-seed widget queue callback cannot be mistaken for supported background execution',()=>{
    for(const hook of ['beforeQueued','afterQueued']) {
        const f=fixture();const cfg={name:'cfg',value:7,[hook](){this.value=11;}};f.node.widgets.push(cfg);
        assert.throws(()=>f.observer.capture(f.graph),/未支援/);assert.equal(cfg.value,7);
    }
    const g=fixture();g.graph._nodes.push({id:2,type:'EmptyLatentImage',widgets:[{name:'width',beforeQueued(){}}]});
    assert.throws(()=>g.observer.capture(g.graph),/未支援/);
});
