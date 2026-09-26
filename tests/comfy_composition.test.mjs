import test from 'node:test';
import assert from 'node:assert/strict';
import { chooseItem, changeWorkspace } from '../comfyui_prompt_studio/web/state.js';

const node = (name, prompt) => ({id: name, name, prompt, enabled: true, weight: 10, children: [], overlays: [], excludes: []});
const module = {id:'m', name:'自訂', mode:'multiple'};
const item = {id:'a', module:'m', name:'複合素材', prompt:'old', composition:node('root','current')};
const initial = () => ({version:1, modules:[module], items:[], selections:{}, workspaces:[], workspace:'', draft:'manual'});

test('choosing a composite upgrades the state and copies the prototype', () => {
    const s = initial(); chooseItem(s,item,module);
    assert.equal(s.version,2); assert.equal(s.instances.a.prompt,'current');
    s.instances.a.prompt='edited'; assert.equal(item.composition.prompt,'current');
    chooseItem(s,item,module); assert.equal(s.instances.a,undefined);
    chooseItem(s,item,module); assert.equal(s.instances.a.prompt,'current');
    assert.equal(s.draft,'manual');
});

test('fixed workspace restores its instances and weights, preserving others and draft', () => {
    const s = initial(); s.instances={other:node('other','untouched')};
    const w = {id:'w',name:'saved',fixed:['m'],picks:{m:['a']},weights:{a:13},instances:{a:node('root','saved')}};
    changeWorkspace(s,w,{modules:[module],items:[item]});
    assert.equal(s.version,2); assert.equal(s.instances.a.prompt,'saved');
    assert.equal(s.instances.other.prompt,'untouched'); assert.equal(s.weights.a,13);
    s.instances.a.prompt='new edit'; assert.equal(w.instances.a.prompt,'saved');
    assert.equal(s.draft,'manual');
});

test('version 3 usages survive library choice and fixed workspace restore', () => {
    const s=initial(); s.version=3;
    s.uses={a:{...node('girl','edited'),source_module:'m'},b:node('raw','keep')};
    s.output_order=['a','b'];
    chooseItem(s,item,module); assert.equal(s.version,3); assert.equal(s.uses.a.prompt,'edited');
    const saved={...node('girl','saved'),source_module:'m'};
    const w={id:'w',name:'saved',fixed:['m'],picks:{m:['a']},uses:{a:saved},instances:{a:node('root','saved list')}};
    changeWorkspace(s,w,{modules:[module],items:[item]});
    assert.equal(s.version,3); assert.equal(s.uses.a.prompt,'saved'); assert.equal(s.uses.b.prompt,'keep');
    s.uses.a.prompt='later'; assert.equal(w.uses.a.prompt,'saved'); assert.equal(s.draft,'manual');
});

test('an isolated Canvas snapshot accepts an explicit list choice in the list scope', () => {
    const s=initial(); s.settings={separate_selections:true}; s.selection_view='canvas'; s.draft='canvas draft';
    s.uses={girl:node('girl','1girl')};
    chooseItem(s,item,module);
    assert.equal(s.selection_view,'list'); assert.equal(s.draft,null);
    assert.equal(s.view_drafts.canvas.draft,'canvas draft'); assert.equal(s.uses.girl.prompt,'1girl');
    assert.deepEqual(s.selections.m,['a']);
});
