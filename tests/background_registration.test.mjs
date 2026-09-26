import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {registerBackgroundJob} from '../comfyui_prompt_studio/web/background_registration.js';

const SHA = '1d870df50815a0faad3645f65a008e384337e67757364fb423dbb74c0b066ff7';
const ASSET = 'http://localhost:9000/assets/dialogService-DSBgqcNn.js';
const clone = value => structuredClone(value);

function fixture(t, {realHash = false, bytes = new Uint8Array([1, 2, 3])} = {}) {
    const visual = {id: 'workflow-A', nodes: [
        {id: 1, type: 'CheckpointLoaderSimple', inputs: [], widgets_values: ['example.safetensors']},
        {id: 2, type: 'KSampler', inputs: [{name: 'model', link: 10}], widgets_values: [42]},
    ], links: [[10, 1, 0, 2, 0, 'MODEL']]};
    const operation = {prompt_id: 'prompt-A', key: {frontend_id: 'workflow-A', path: 'folder/A.json'},
        revision: 4, digest: 'a'.repeat(64), visual: clone(visual), output: {
            '1': {class_type: 'CheckpointLoaderSimple', inputs: {ckpt_name: 'example.safetensors'}},
            '2': {class_type: 'KSampler', inputs: {model: ['1', 0], seed: 42}},
        }};
    const calls = {store: [], fetch: [], import: [], hash: [], forbidden: []}, hooks = {};
    const forbidden = name => () => { calls.forbidden.push(name); throw Error('unexpected ' + name); };
    const pinia = {_s: new Map()}, workflow = {path: 'workflows/folder/A.json', isLoaded: true,
        activeState: clone(visual), initialState: clone(visual)};
    const workflowStore = {$id: 'workflow', _p: pinia, activeWorkflow: workflow};
    // Reproduce the audited native storeJob contract. No fixture event handling:
    // registration must neither synthesize events nor change progress/activeJobId.
    const execution = {$id: 'execution', _p: pinia, activeJobId: null, queuedJobs: {},
        jobIdToWorkflowId: new Map(), jobIdToSessionWorkflowPath: new Map(),
        nodeProgressStates: {unrelated: {value: 8, max: 10}},
        bindExecutionEvents: forbidden('bind'), unbindExecutionEvents: forbidden('unbind'),
        storeJob({id, nodes, workflow: target}) {
            calls.store.push({id, nodes, workflow: target});
            const job = this.queuedJobs[id] ??= {nodes: {}};
            job.nodes = {...Object.fromEntries(nodes.map(id => [id, false])), ...job.nodes};
            job.workflow = target;
            this.jobIdToWorkflowId.set(String(id), String(target.activeState?.id ?? target.initialState?.id));
            this.jobIdToSessionWorkflowPath = new Map(this.jobIdToSessionWorkflowPath).set(String(id), target.path);
        }};
    pinia._s.set('execution', execution); pinia._s.set('workflow', workflowStore);
    const root = {id: visual.id, serialize: () => clone(visual)};
    const app = {graph: root, rootGraph: root, configuringGraph: false, extensionManager: {workflow: workflowStore},
        queuePrompt: forbidden('queue'), loadGraphData: forbidden('load'),
        api: {dispatchEvent: forbidden('dispatch'), queuePrompt: forbidden('api-queue')}};
    const listeners = new Set(), host = new EventTarget();
    host.app = app; host.location = {href: 'http://localhost:9000/?workflow=folder%2FA.json'};
    const add = host.addEventListener.bind(host), remove = host.removeEventListener.bind(host);
    host.addEventListener = (type, fn) => { listeners.add(fn); add(type, fn); };
    host.removeEventListener = (type, fn) => { listeners.delete(fn); remove(type, fn); };
    const module = {s: app, L: Object.assign(p => p._s.get('execution'), {$id: 'execution'}),
        N: Object.assign(p => p._s.get('workflow'), {$id: 'workflow'})};
    const response = {ok: true, redirected: false, url: ASSET,
        async arrayBuffer() { await hooks.body?.(); return bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength); }};
    const options = {host, async fetcher(...args) { calls.fetch.push(args); await hooks.fetch?.(); return response; },
        async importer(url) { calls.import.push(url); await hooks.import?.(); return module; }};
    // Portable behavior cases stub ONLY the version-hash boundary. The opt-in
    // installed-bundle case below uses real SHA-256 over the local bundle bytes.
    if (!realHash) t.mock.method(globalThis.crypto.subtle, 'digest', async (algorithm, buffer) => {
        calls.hash.push([algorithm, [...new Uint8Array(buffer)]]);
        await hooks.hash?.();
        return Buffer.from(SHA, 'hex');
    });
    return {operation, visual, calls, hooks, pinia, workflow, workflowStore, execution, root, app, host,
        listeners, module, response, options, run: () => registerBackgroundJob(app, operation, options)};
}

function unchanged(f) {
    assert.equal(f.calls.store.length, 0);
    assert.equal(f.listeners.size, 0);
    assert.deepEqual(f.calls.forbidden, []);
}

test('register exact submitted keys on existing native store; accepted display seed may have advanced', async t => {
    const f = fixture(t);
    f.visual.nodes[1].widgets_values[0] = 43;
    f.visual.nodes.push({id: 9, type: 'Note', inputs: [], widgets_values: ['manual note']});
    f.operation.visual.nodes.push(clone(f.visual.nodes[2]));
    f.workflow.activeState = clone(f.visual);
    const before = clone({visual: f.visual, draft: f.workflow.activeState, initial: f.workflow.initialState,
        operation: f.operation, progress: f.execution.nodeProgressStates});
    const ack = await f.run();
    assert.equal(ack.registered, true);
    assert.equal(ack.prompt_id, 'prompt-A'); assert.equal(ack.revision, 4); assert.equal(ack.digest, 'a'.repeat(64));
    assert.equal(Object.isFrozen(ack), true);
    assert.deepEqual(f.calls.store, [{id: 'prompt-A', nodes: ['1', '2'], workflow: f.workflow}]);
    assert.strictEqual(f.execution.queuedJobs['prompt-A'].workflow, f.workflow);
    assert.deepEqual(f.execution.queuedJobs['prompt-A'].nodes, {'1': false, '2': false});
    assert.equal(f.execution.jobIdToWorkflowId.get('prompt-A'), 'workflow-A');
    assert.equal(f.execution.jobIdToSessionWorkflowPath.get('prompt-A'), 'workflows/folder/A.json');
    assert.deepEqual({visual: f.visual, draft: f.workflow.activeState, initial: f.workflow.initialState,
        operation: f.operation, progress: f.execution.nodeProgressStates}, before);
    assert.equal(f.execution.activeJobId, null);
    assert.deepEqual(f.calls.fetch, [[ASSET, {cache: 'no-store', redirect: 'error'}]]);
    assert.deepEqual(f.calls.import, [ASSET]);
    assert.deepEqual(f.calls.hash, [['SHA-256', [1, 2, 3]]]);
    assert.deepEqual(f.calls.forbidden, []); assert.equal(f.listeners.size, 0);
    assert.equal(ack.canReceive(), true);
    f.execution.activeJobId = 'prompt-A'; assert.equal(ack.canReceive(), true);
    f.execution.activeJobId = 'other-native-job'; assert.equal(ack.canReceive(), false);
    assert.equal(f.execution.activeJobId, 'other-native-job');
    f.execution.activeJobId = null; f.pinia._s.set('execution', {}); assert.equal(ack.canReceive(), false);
});

test('same receipt is idempotent and preserves native completed-node flags', async t => {
    const f = fixture(t);
    await f.run(); f.execution.queuedJobs['prompt-A'].nodes['1'] = true;
    f.execution.activeJobId = 'prompt-A';
    await f.run();
    assert.deepEqual(f.execution.queuedJobs['prompt-A'].nodes, {'1': true, '2': false});
    assert.equal(f.calls.store.length, 2); assert.deepEqual(f.calls.forbidden, []);
    f.operation.revision++;
    await assert.rejects(f.run(), /同一任務的執行收據不同/);
    assert.equal(f.calls.store.length, 2);
});

test('completed-before-attach cleanup removes only the new never-started native registration',async t=>{
    const f=fixture(t),ack=await f.run();
    assert.equal(ack.dispose(),true);assert.equal('prompt-A' in f.execution.queuedJobs,false);
    assert.equal(f.execution.jobIdToWorkflowId.has('prompt-A'),false);
    assert.equal(f.execution.jobIdToSessionWorkflowPath.has('prompt-A'),false);
    assert.deepEqual(f.calls.forbidden,[]);
});

test('cleanup cannot remove an active or pre-existing native job',async t=>{
    const f=fixture(t),first=await f.run();f.execution.activeJobId='prompt-A';
    assert.equal(first.dispose(),false);f.execution.activeJobId=null;
    const second=await f.run();assert.equal(second.dispose(),false);
    assert.equal('prompt-A' in f.execution.queuedJobs,true);
    f.execution.queuedJobs['prompt-A'].nodes['1']=true;assert.equal(first.dispose(),false);
});

test('event receipt is invalid after a same-ID node changes type or connection',async t=>{
    const f=fixture(t),ack=await f.run();assert.equal(ack.canReceive(),true);
    f.visual.nodes[1].type='DifferentSampler';assert.equal(ack.canReceive(),false);
});

const badInput = {
    'missing prompt': f => { delete f.operation.prompt_id; },
    'prototype prompt key': f => { f.operation.prompt_id = '__proto__'; },
    'invalid digest': f => { f.operation.digest = 'unknown'; },
    'zero revision': f => { f.operation.revision = 0; },
    'unsafe revision': f => { f.operation.revision = Number.MAX_SAFE_INTEGER + 1; },
    'execution visual identity mismatch': f => { f.operation.visual.id = 'workflow-B'; },
    'different path': f => { f.operation.key.path = 'B.json'; },
    'missing output': f => { f.operation.output = {}; },
    'API node absent from visual': f => { f.operation.output['3'] = clone(f.operation.output['1']); },
    'different API node type': f => { f.operation.output['2'].class_type = 'Other'; },
    'different API edge': f => { f.operation.output['2'].inputs.model[1] = 1; },
    'API edge missing': f => { delete f.operation.output['2'].inputs.model; },
    'unmapped array input': f => { f.operation.output['2'].inputs.seed = [1, 2, 3]; },
    'duplicate visual node': f => { f.operation.visual.nodes.push(clone(f.operation.visual.nodes[0])); },
    'duplicate visual edge': f => { f.operation.visual.links.push(clone(f.operation.visual.links[0])); },
    'edge absent from visual': f => { f.operation.visual.links = []; },
    'unmapped bypass execution node': f => { f.operation.visual.nodes[1].mode = 4; },
    'subgraph': f => { f.operation.visual.definitions = {subgraphs: [{}]}; },
};
for (const [name, mutate] of Object.entries(badInput)) test(`refuse ${name} before native writes`, async t => {
    const f = fixture(t); mutate(f);
    await assert.rejects(f.run()); unchanged(f);
});

const badContext = {
    'another app': f => { f.host.app = {}; },
    'subgraph view': f => { f.app.graph = {}; },
    'loading graph': f => { f.app.configuringGraph = true; },
    'unloaded workflow': f => { f.workflow.isLoaded = false; },
    'active ID differs': f => { f.workflow.activeState.id = 'workflow-B'; },
    'root ID differs': f => { f.root.id = 'workflow-B'; },
    'native path differs': f => { f.workflow.path = 'workflows/B.json'; },
    'no existing execution store': f => { f.pinia._s.delete('execution'); },
    'workflow not registered in Pinia': f => { f.pinia._s.delete('workflow'); },
    'foreign execution Pinia': f => { f.execution._p = {}; },
    'another native active job': f => { f.execution.activeJobId = 'other-job'; },
    'live edge differs': f => { f.visual.links[0][2] = 2; },
    'live type differs': f => { f.visual.nodes[0].type = 'Other'; },
    'prior workflow ID': f => { f.execution.jobIdToWorkflowId.set('prompt-A', 'workflow-B'); },
    'prior workflow path': f => { f.execution.jobIdToSessionWorkflowPath.set('prompt-A', 'workflows/B.json'); },
    'prior workflow object': f => { f.execution.queuedJobs['prompt-A'] = {workflow: {...f.workflow}, nodes: {'1': false, '2': false}}; },
    'prior node set': f => { f.execution.queuedJobs['prompt-A'] = {workflow: f.workflow, nodes: {'3': false}}; },
};
for (const [name, mutate] of Object.entries(badContext)) test(`refuse ${name} before asset import`, async t => {
    const f = fixture(t); mutate(f);
    await assert.rejects(f.run()); unchanged(f);
    assert.deepEqual(f.calls.import, []);
});

// Every async boundary can outlive the selected workflow. A stale callback
// must not attach its job to whatever happens to be current when it resumes.
const races = {
    'root replacement': f => { f.app.rootGraph = f.app.graph = {...f.root}; },
    'workflow replacement with identical IDs': f => { f.workflowStore.activeWorkflow = {...f.workflow}; },
    'draft replacement with identical IDs': f => { f.workflow.activeState = clone(f.workflow.activeState); },
    'store replacement': f => { f.pinia._s.set('execution', {...f.execution}); },
    'store method replacement': f => { f.execution.storeJob = () => {}; },
    'active native job': f => { f.execution.activeJobId = 'native-other'; },
    'page unload': f => { f.host.dispatchEvent(new Event('pagehide')); },
    'receipt edit': f => { f.operation.output['2'].inputs.seed++; },
    'topology edit': f => { f.visual.links[0][2]++; },
};
for (const phase of ['fetch', 'body', 'hash', 'import']) {
    for (const [name, mutate] of Object.entries(races)) test(`${phase} wait: reject ${name}`, async t => {
        const f = fixture(t); f.hooks[phase] = () => mutate(f);
        await assert.rejects(f.run()); unchanged(f);
    });
}

test('HTTP failure, redirect, or different response URL never import an unverified module', async t => {
    const f = fixture(t);
    for (const response of [{ok: false}, {redirected: true}, {url: ASSET + '?copy=1'}]) {
        Object.assign(f.response, {ok: true, redirected: false, url: ASSET}, response);
        await assert.rejects(f.run(), /bundle/); unchanged(f);
    }
    assert.deepEqual(f.calls.import, []);
});

test('wrong real SHA-256 refuses before importer', async t => {
    const f = fixture(t, {realHash: true});
    await assert.rejects(f.run(), /版本內容尚未驗證/);
    unchanged(f); assert.deepEqual(f.calls.import, []);
});

for (const [name, mutate] of Object.entries({
    'different exported app': f => { f.module.s = {}; },
    'missing factory ID': f => { delete f.module.L.$id; },
    'different workflow store': f => { f.module.N = Object.assign(() => ({}), {$id: 'workflow'}); },
    'different execution store': f => { f.module.L = Object.assign(() => ({}), {$id: 'execution'}); },
})) test(`canonical module: reject ${name}`, async t => {
    const f = fixture(t); mutate(f);
    await assert.rejects(f.run(), /匯出/); unchanged(f);
});

test('missing or wrong native mapping after storeJob produces no successful acknowledgement', async t => {
    const f = fixture(t);
    const store = f.execution.storeJob;
    f.execution.storeJob = function(value) {
        store.call(this, value);
        this.jobIdToSessionWorkflowPath.delete(value.id);
    };
    await assert.rejects(f.run(), /登記未完成/);
    assert.equal(f.calls.store.length, 1); assert.deepEqual(f.calls.forbidden, []);
    assert.equal(f.listeners.size, 0);
    // No speculative rollback: native code may already have committed a job.
    assert.ok(f.execution.queuedJobs['prompt-A']);
});

for (const phase of ['fetch', 'body', 'hash', 'import']) test(`${phase} failure cleans up page listener`, async t => {
    const f = fixture(t), error = Error('loader failed'); f.hooks[phase] = () => { throw error; };
    await assert.rejects(f.run(), value => value === error); unchanged(f);
});

test('installed audited bundle bytes satisfy the real SHA gate (offline; mocked module/store)',
    {skip: !process.env.PCS_TEST_FRONTEND_BUNDLE}, async t => {
        const bytes = await readFile(process.env.PCS_TEST_FRONTEND_BUNDLE);
        assert.equal(createHash('sha256').update(bytes).digest('hex'), SHA);
        const f = fixture(t, {realHash: true, bytes});
        assert.equal((await f.run()).registered, true);
        assert.deepEqual(f.calls.import, [ASSET]);
    });
