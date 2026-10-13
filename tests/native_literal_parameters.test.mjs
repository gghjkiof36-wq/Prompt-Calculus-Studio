import test from 'node:test';
import assert from 'node:assert/strict';
import {describeParameters, parameterTransaction} from '../comfyui_prompt_calculus_studio/web/native_parameters.js';
import {createParameterCapabilities} from '../comfyui_prompt_calculus_studio/web/native_parameter_capabilities.js';

const enabled = !!process.env.PCS_FRONTEND_ASSETS;
const native = enabled ? await import('./fixtures/frontend_1536_runtime.mjs') : null;
const options = {skip: !enabled && 'Set PCS_FRONTEND_ASSETS to the installed supported frontend assets'};
const definitions = {CLIPTextEncode: {input: {required: {text: ['STRING', {multiline: true, dynamicPrompts: true}]}}}};

async function fixture(value = '原生文字, plain text') {
    const runtime = native.createRuntime();
    const capabilities = createParameterCapabilities(runtime.app, runtime.widgets);
    runtime.service.registerExtension({name: 'PCS.literal-fixture', getCustomWidgets: () => capabilities.getCustomWidgets()});
    await Promise.resolve();
    const node = runtime.node('CLIPTextEncode');
    const widget = runtime.registry.widgets.get('STRING')(node, 'text', ['STRING', {default: value, multiline: true, dynamicPrompts: true}]).widget;
    for (const extension of runtime.app.extensions) extension.nodeCreated?.(node);
    runtime.addToGraph(node, 11);
    const graph = runtime.app.rootGraph;
    graph._nodes = [node];
    graph.serialize = () => ({nodes: [{id: node.id, ...runtime.serializeWidgets(node.widgets)}]});
    const output = text => ({11: {class_type: node.type, inputs: {text}}});
    const serialized = widget.serializeValue(undefined, 0);
    const description = describeParameters(runtime.app, output(serialized), definitions, null, capabilities);
    const field = description.nodes[0].fields[0];
    const command = text => ({target: {frontend_id: 'offline'}, parameters: {version: 1, identity: {frontend_id: 'offline'},
        patches: [{node: '11', path: ['11'], class_type: node.type, field: 'text', schema: field.schema, base: field.value, value: text}]}});
    const transaction = cmd => parameterTransaction(runtime.app, cmd, description, () => {}, null, () => {}, capabilities);
    return {runtime, node, widget, graph, output, description, field, command, transaction};
}

for (const value of ['新的內容, blue sky', '']) test(`native DynamicPrompts literal override preserves reactive original: ${JSON.stringify(value)}`, options, async () => {
    const f = await fixture();
    assert.equal(f.field.editable, true); assert.equal(f.description.projection_reason, '');
    const original = f.widget.value, serializer = f.widget.serializeValue, serialize = f.graph.serialize;
    const tx = f.transaction(f.command(value));
    tx.apply();
    let proof;
    try {
        tx.enterSerialize();
        const text = f.runtime.serializeWidgets(f.node.widgets).widgets_values_named.text;
        assert.equal(text, value);
        assert.equal(f.graph.serialize().nodes[0].widgets_values_named.text, value);
        proof = tx.evidence(f.output(text));
        assert.equal(proof[0].actual, value);
    } finally { tx.leaveSerialize(); tx.restore(); }
    assert.equal(f.widget.value, original);
    assert.equal(f.runtime.valueStore.getWidget('offline-graph:11:text').value, original);
    assert.equal(f.widget.serializeValue, serializer); assert.equal(f.graph.serialize, serialize);
    assert.deepEqual(tx.finish(), proof);
});

for (const value of ['{red|blue}', 'red /* comment */', 'red // comment', String.raw`\{literal\}`]) test(`native transformation refuses exact-value override: ${JSON.stringify(value)}`, options, async () => {
    const f = await fixture(), original = f.widget.value, serializer = f.widget.serializeValue;
    const tx = f.transaction(f.command(value));
    tx.apply();
    try {
        tx.enterSerialize();
        const text = f.runtime.serializeWidgets(f.node.widgets).widgets_values_named.text;
        assert.throws(() => tx.evidence(f.output(text)), /動態提示詞展開或註解處理/);
    } finally { tx.restore(); }
    assert.equal(f.widget.value, original); assert.equal(f.widget.serializeValue, serializer);
    assert.equal(f.runtime.valueStore.getWidget('offline-graph:11:text').value, original);
    assert.deepEqual(tx.finish(), []);
});

test('native dynamic templates stay readonly and existing PCS text binding owns the field', options, async () => {
    const template = await fixture('{red|blue}');
    assert.equal(template.field.editable, false); assert.match(template.field.reason, /動態提示詞/);
    const f = await fixture(), command = f.command('changed');
    command.texts = [{node: '11', field: 'text'}];
    assert.throws(() => f.transaction(command), /由 PCS 輸入供應/);
    assert.equal(f.widget.value, '原生文字, plain text');
});

test('later native serializer replacement cannot use an earlier editable description', options, async () => {
    const f = await fixture(), tx = f.transaction(f.command('changed'));
    f.widget.serializeValue = () => 'foreign';
    assert.throws(() => tx.apply(), /控制項已變更/);
    tx.restore(); assert.equal(f.widget.value, '原生文字, plain text');
});
