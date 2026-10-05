import {test} from 'node:test';
import assert from 'node:assert/strict';
import {KEY, bind, captureManual, textWidget, writeSnapshot, chooseItem, mergeLibrary} from '../comfyui_prompt_calculus_studio/web/state.js';

const snapshot = () => ({final_prompt: 'auto', generated_prompt: 'auto', manual_draft: false, state: {draft: null, draft_base: ''}});
const node = (text = '') => ({id: 4, widgets: [{name: 'text', value: text}], inputs: [], properties: {}});

test('binding preserves existing text and serializes to workflow properties', () => {
    const n = node('existing'); bind(n, snapshot());
    assert.equal(n.widgets[0].value, 'existing'); assert.equal(n.properties[KEY].snapshot.state.draft, 'existing');
    const restored = JSON.parse(JSON.stringify(n)); assert.equal(restored.properties[KEY].field, 'text');
});
test('direct and programmatic edits become manual including empty text', () => {
    const n = node(); bind(n, snapshot()); writeSnapshot(n, snapshot());
    n.widgets[0].value = ''; assert.ok(captureManual(n)); assert.equal(n.properties[KEY].snapshot.state.draft, '');
    n.widgets[0].value = 'new'; captureManual(n); assert.equal(n.properties[KEY].snapshot.final_prompt, 'new');
});
test('linked and missing fields are never written and other nodes are untouched', () => {
    const n = node(), other = node('keep'); bind(n, snapshot());
    n.inputs = [{name: 'text', link: 5}]; assert.ok(textWidget(n).error);
    assert.throws(() => writeSnapshot(n, snapshot())); assert.equal(other.widgets[0].value, 'keep');
    assert.ok(textWidget({widgets: []}).error);
});
test('independent bindings do not share mutable snapshots', () => {
    const a = node(), b = node(); const s = snapshot(); bind(a, s); bind(b, s);
    a.widgets[0].value = 'manual'; captureManual(a);
    assert.equal(b.properties[KEY].snapshot.final_prompt, 'auto');
});
test('refresh preserves historical selection and explicit reselection adopts current item', () => {
    const s = {modules:[{id:'m',mode:'single'}],items:[{id:'a',module:'m',prompt:'old'}],selections:{m:['a']}};
    const fresh = {modules:s.modules,items:[{id:'a',module:'m',prompt:'new'}]};
    const merged = mergeLibrary(s, fresh); assert.equal(merged.items[0].prompt, 'old');
    chooseItem(merged, fresh.items[0], s.modules[0]); assert.deepEqual(merged.selections.m, []);
    chooseItem(merged, fresh.items[0], s.modules[0]); assert.equal(merged.items[0].prompt, 'new');
});
