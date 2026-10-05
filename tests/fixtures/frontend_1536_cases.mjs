import assert from 'node:assert/strict';
import { readFileSync, writeFileSync } from 'node:fs';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { createRuntime, sourceEvidence, vue, assets } from './frontend_1536_runtime.mjs';
const productPath = fileURLToPath(new URL('../../comfyui_prompt_calculus_studio/web/native_parameter_capabilities.js', import.meta.url));
const checkout = fileURLToPath(new URL('../../', import.meta.url)).replace(/[\\/]$/, '');
const baselineRef = process.argv.find(arg => arg.startsWith('--baseline-ref='))?.slice('--baseline-ref='.length);
const baseline = !!baselineRef;
const productSource = baseline ? execFileSync('git', ['-c', `safe.directory=${checkout}`, 'show', `${baselineRef}:comfyui_prompt_studio/web/native_parameter_capabilities.js`], { cwd: checkout, encoding: 'utf8' }) : readFileSync(productPath, 'utf8');
const { createParameterCapabilities } = await import(baseline ? `data:text/javascript;base64,${Buffer.from(productSource).toString('base64')}` : pathToFileURL(productPath));
const { createSeedObserver } = await import('../../comfyui_prompt_calculus_studio/web/native_seed.js');
const { describeParameters, parameterTransaction } = await import('../../comfyui_prompt_calculus_studio/web/native_parameters.js');

const cases = [];
for (const primed of [false, true]) {
    const runtime = createRuntime();
    if (primed) void runtime.registry.widgets;
    const seeds = baseline ? null : createSeedObserver(() => 'after');
    seeds?.installFactory(runtime.widgets);
    const capabilities = createParameterCapabilities(runtime.app, runtime.widgets);
    runtime.service.registerExtension({ name: 'PCS.offline-capabilities', getCustomWidgets: () => capabilities.getCustomWidgets() });
    await Promise.resolve();
    const sampler = runtime.node('KSampler');
    const clip = runtime.node('CLIPTextEncode');
    const seed = seeds ? runtime.registry.widgets.get('INT')(sampler, 'seed', ['INT', { default: 17, min: 0, max: 2 ** 50 }]).widget : null;
    const cfg = runtime.registry.widgets.get('FLOAT')(sampler, 'cfg', ['FLOAT', { default: 4, min: 0, max: 100 }]).widget;
    const text = runtime.registry.widgets.get('STRING')(clip, 'text', ['STRING', { default: '{one|two}', multiline: true, dynamicPrompts: true }]).widget;
    for (const extension of runtime.app.extensions) { extension.nodeCreated?.(sampler); extension.nodeCreated?.(clip); }
    seeds?.observe(sampler);
    const graph = { _nodes: [sampler, clip] };
    const before = { primitiveCFG: capabilities.primitive(cfg), primitiveText: capabilities.primitive(text), trusted: capabilities.trusted(text), reason: capabilities.reason(graph) };
    assert.deepEqual(before, { primitiveCFG: true, primitiveText: true, trusted: true, reason: '' });
    const originals = { cfg: cfg.options, text: text.options, cfgCallback: cfg.callback,
        getValue: text.options.getValue, setValue: text.options.setValue, serializeValue: text.serializeValue };
    const rawKeys = Reflect.ownKeys(originals.text);
    runtime.addToGraph(sampler, 5); runtime.addToGraph(clip, 11);
    const after = { primitiveCFG: capabilities.primitive(cfg), primitiveText: capabilities.primitive(text), trusted: capabilities.trusted(text), reason: capabilities.reason(graph) };
    const identity = { cfgChanged: cfg.options !== originals.cfg, textChanged: text.options !== originals.text,
        cfgSameRaw: vue.toRaw(cfg.options) === originals.cfg, textSameRaw: vue.toRaw(text.options) === originals.text,
        callbackSame: cfg.callback === originals.cfgCallback, getValueSame: text.options.getValue === originals.getValue,
        setValueSame: text.options.setValue === originals.setValue, serializerSame: text.serializeValue === originals.serializeValue };
    assert.ok(Object.values(identity).every(Boolean));
    assert.deepEqual(Reflect.ownKeys(originals.text), rawKeys);
    // An explicit baseline revision independently reproduces the regression.
    if (!baseline) assert.deepEqual(after, before);
    else { assert.equal(after.primitiveCFG, false); assert.equal(after.primitiveText, false); assert.equal(after.trusted, false); assert.match(after.reason, /特殊序列化/); }
    const workflowNode = { widgets_values: [text.value] };
    const resolved = text.serializeValue(workflowNode, 0);
    assert.ok(['one', 'two'].includes(resolved));
    assert.equal(workflowNode.widgets_values[0], resolved);
    assert.equal(text.value, '{one|two}');
    cases.push({ name: `native_setNodeId_${primed ? 'primed' : 'unprimed'}`, before, after, identity, dynamicProviderResolved: true, templateUnchanged: true });
    if (!baseline) {
        assert.ok(capabilities.primitive(seed));
        assert.ok(seeds.parameterControl(sampler, 'seed'));
        // Repeat real registration (native configure/graph transitions can do so).
        runtime.addToGraph(sampler, 5); runtime.addToGraph(clip, 11);
        assert.equal(capabilities.reason(graph), '');
        assert.ok(seeds.parameterControl(sampler, 'seed'));
        runtime.app.rootGraph._nodes = graph._nodes;
        // These inputs are a projection fixture, never a constructed submission.
        const description = describeParameters(runtime.app, {
            5: { class_type: 'KSampler', inputs: { cfg: 4, seed: 17 } },
            11: { class_type: 'CLIPTextEncode', inputs: { text: resolved } },
        }, {
            KSampler: { input: { required: { cfg: ['FLOAT', { min: 0, max: 100 }], seed: ['INT', { min: 0, max: 2 ** 50 }] } } },
            CLIPTextEncode: { input: { required: { text: ['STRING', { multiline: true, dynamicPrompts: true }] } } },
        }, seeds, capabilities);
        assert.equal(description.projection_reason, '');
        const samplerFields = description.nodes.find(node => node.id === '5').fields;
        assert.ok(samplerFields.find(field => field.field === 'cfg').editable);
        assert.ok(samplerFields.find(field => field.field === 'seed').editable);
        assert.equal(description.nodes.find(node => node.id === '11').fields[0].editable, false);
        cases.push({ name: `seed_and_projection_${primed ? 'primed' : 'unprimed'}`, seedPairRetained: true,
            repeatRegistrationRetained: true, cfgEditable: true, seedEditable: true, dynamicTextRemainsReadonly: true });

        const rootGraph = runtime.app.rootGraph;
        rootGraph.serialize = () => ({ nodes: rootGraph._nodes.map(node => ({ id: node.id, ...runtime.serializeWidgets(node.widgets) })) });
        const originalSerialize = rootGraph.serialize, originalDynamic = text.serializeValue;
        const control = seeds.parameterControl(sampler, 'seed');
        const controlHooks = [control.beforeQueued, control.afterQueued];
        const patches = samplerFields.map(field => ({ node: '5', path: ['5'], class_type: 'KSampler',
            field: field.field, schema: field.schema, base: field.value,
            value: field.field === 'cfg' ? 7.25 : 31,
            ...(field.field === 'seed' ? { seed_mode: 'fixed', seed_timing: 'after' } : {}),
        }));
        const transaction = parameterTransaction(runtime.app, { target: { frontend_id: 'offline' },
            parameters: { version: 1, identity: { frontend_id: 'offline' }, patches } }, description, () => {}, seeds, () => {}, capabilities);
        transaction.apply();
        try {
            transaction.enterSerialize();
            // This is the exact native LGraphNode widget-value serialization
            // helper, not an API prompt construction or native queue request.
            const live = runtime.serializeWidgets(sampler.widgets).widgets_values_named;
            assert.equal(live.cfg, 7.25); assert.equal(live.seed, 31);
            const foldedText = runtime.serializeWidgets(clip.widgets).widgets_values_named.text;
            assert.ok(['one', 'two'].includes(foldedText));
            const metadata = rootGraph.serialize();
            assert.equal(metadata.nodes.find(node => node.id === '11').widgets_values_named.text, '{one|two}');
            assert.equal(text.value, foldedText);
        } finally { transaction.leaveSerialize(); transaction.restore(); }
        assert.equal(rootGraph.serialize, originalSerialize); assert.equal(text.serializeValue, originalDynamic);
        assert.equal(cfg.value, 4); assert.equal(seed.value, 17); assert.equal(text.value, '{one|two}');
        assert.equal(runtime.valueStore.getWidget('offline-graph:5:cfg').value, 4);
        assert.equal(runtime.valueStore.getWidget('offline-graph:5:seed').value, 17);
        assert.equal(runtime.valueStore.getWidget('offline-graph:11:text').value, '{one|two}');
        assert.deepEqual([control.beforeQueued, control.afterQueued], controlHooks);
        assert.deepEqual(Reflect.ownKeys(originals.text), rawKeys);
        assert.equal(capabilities.reason(graph), '');
        cases.push({ name: `transaction_native_serialization_${primed ? 'primed' : 'unprimed'}`,
            cfgSerialized: 7.25, seedSerialized: 31, dynamicProviderFolded: true,
            metadataPreservedRawTemplate: true, liveAndPiniaValuesRestored: true, hooksAndSerializersRestored: true, probeKeysRemoved: true });

        const realOptions = text.options;
        const forwarding = new Proxy(realOptions, { get(target, key) {
            if (key === '__v_raw') return originals.text;
            if (key === '__v_isReactive') return true;
            if (key === '__v_isReadonly') return false;
            return Reflect.get(target, key);
        } });
        const foreignObjects = [
            ['forwarding Proxy', forwarding],
            ['claimed raw clone', { ...originals.text, __v_raw: originals.text, __v_isReactive: true }],
            ['new Vue proxy of clone', vue.reactive({ ...originals.text })],
        ];
        const ownOptions = Object.getOwnPropertyDescriptor(text, 'options');
        for (const [name, options] of foreignObjects) {
            Object.defineProperty(text, 'options', { configurable: true, get: () => options });
            assert.equal(capabilities.trusted(text), false);
            assert.match(capabilities.reason(graph), /特殊序列化/);
            if (name === 'forwarding Proxy') {
                text.setNodeId(clip.id);
                assert.equal(capabilities.trusted(text), false);
            }
            if (ownOptions) Object.defineProperty(text, 'options', ownOptions); else delete text.options;
            assert.equal(capabilities.trusted(text), true);
            cases.push({ name: `late_${name}_${primed ? 'primed' : 'unprimed'}`, rejected: true });
        }
        for (const [owner, name] of [[text, 'callback'], [originals.text, 'setValue'], [text, 'serializeValue'], [text, 'setNodeId']]) {
            const previous = owner[name]; owner[name] = () => undefined;
            assert.equal(capabilities.trusted(text), false); assert.match(capabilities.reason(graph), /特殊序列化/);
            owner[name] = previous; assert.equal(capabilities.trusted(text), true);
        }
        let changes = 0;
        capabilities.observeCallback(text, () => changes++);
        text.callback('manual-observation');
        assert.equal(changes, 1); assert.equal(capabilities.trusted(text), true);
        assert.deepEqual(Reflect.ownKeys(originals.text), rawKeys);
        cases.push({ name: `delegates_and_PCS_observer_${primed ? 'primed' : 'unprimed'}`, foreignChangesRejected: true, ownedObserverRetained: true, probeKeysRemoved: true });
    }
}
const result = { frontend: '1.53.6', assets, mode: baseline ? 'baseline-reproduction' : 'fixed',
    coverage: 'Installed production Vue/Pinia bundle; exact source-map widgetValueStore, widgetStore, BaseWidget/concrete classes, LGraphNode.addWidget/addCustomWidget, DOMWidget own accessors, core STRING/FLOAT factories, DynamicPrompts provider and processor, extensionService registration.' + (baseline ? '' : ' Fixed-source checks additionally exercise the INT/control factory and production parameterTransaction with native serialiseWidgetValues, restoring actual reactive backing state.'),
    boundaries: 'Offline only. Node shell/graph serializer wrapper are adapters; addToGraph invokes native setNodeId directly. Native serialiseWidgetValues executes, not full graphToPrompt/LGraph.serialize/add/configure. DOM creation/registration and unrelated settings/menu infrastructure are adapters. INT control hooks are actual factory closures, but nextValueForLinkedTarget is fail-fast unavailable and no seed policy hook executes. No API prompt, network, submission or GPU.',
    product_sha256: createHash('sha256').update(productSource).digest('hex'), product_source: baseline ? `git ${baselineRef} native_parameter_capabilities.js` : 'working-tree native_parameter_capabilities.js',
    additional_product_hashes: baseline ? [] : ['native_seed.js', 'native_parameters.js'].map(name => ({ name,
        sha256: createHash('sha256').update(readFileSync(new URL(`../../comfyui_prompt_calculus_studio/web/${name}`, import.meta.url))).digest('hex') })),
    vue_bundle_sha256: createHash('sha256').update(readFileSync(`${assets}/vendor-vue-core-DwKKv_Jy.js`)).digest('hex'), sources: sourceEvidence(), cases };
const output = process.argv.find(arg => arg.startsWith('--evidence='))?.slice('--evidence='.length);
if (output) writeFileSync(output, JSON.stringify(result, null, 2));
console.log(JSON.stringify({ frontend: result.frontend, mode: result.mode, cases, ...(output ? { evidence: output } : {}) }, null, 2));
