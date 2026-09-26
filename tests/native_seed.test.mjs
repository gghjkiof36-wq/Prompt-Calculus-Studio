import test from 'node:test';
import assert from 'node:assert/strict';
import {createSeedObserver} from '../comfyui_prompt_studio/web/native_seed.js';

function fixture(timing='before') {
    const seed={name:'seed',value:10,options:{min:0,max:100,step2:1}};
    const a={name:'control_after_generate',value:'increment'},Ty=Symbol();
    const controlValueRunBefore=()=>timing==='before';
    const applyWidgetControl=()=>{seed.value++;};
    // Exact verified functions from the locally installed 1.43.18 bundle.
    a.beforeQueued=({isPartialExecution:e}={})=>{!e&&controlValueRunBefore()&&a[Ty]&&applyWidgetControl(),a[Ty]=!0};
    a.afterQueued=({isPartialExecution:e}={})=>{!e&&!controlValueRunBefore()&&applyWidgetControl()};
    const node={id:1,type:'KSampler',widgets:[seed,a]},graph={_nodes:[node]};
    const observer=createSeedObserver(()=>timing);observer.observe(node);
    return {seed,control:a,node,graph,observer};
}
test('captures before-first state without invoking queue or consuming seed',()=>{
    const f=fixture();assert.equal(f.observer.capture(f.graph).hasExecuted,false);assert.equal(f.seed.value,10);
    f.control.beforeQueued();assert.equal(f.seed.value,10);assert.equal(f.observer.capture(f.graph).hasExecuted,true);
    f.control.beforeQueued();assert.equal(f.seed.value,11);
});
test('restore uses verified partial initializer once and preserves native next before transition',()=>{
    const f=fixture();f.observer.restore(f.graph,{node_id:'1',hasExecuted:true});
    assert.equal(f.seed.value,10);f.observer.restore(f.graph,{node_id:'1',hasExecuted:true});
    assert.equal(f.seed.value,10);f.control.beforeQueued();assert.equal(f.seed.value,11);
});
test('unknown or replaced native hooks are rejected, not interpreted as fixed seed',()=>{
    const f=fixture();f.control.afterQueued=()=>{};
    assert.throws(()=>f.observer.capture(f.graph),/擴充修改/);
    const g=fixture();const unknown={...g.node};g.observer=createSeedObserver(()=>'after');
    assert.throws(()=>g.observer.capture({_nodes:[unknown]}),/擴充修改/);
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
