import test from 'node:test';
import assert from 'node:assert/strict';
import {createNativeBootstrap} from '../comfyui_prompt_calculus_studio/web/native_bootstrap.js';

function clock() {
    let next = 0;
    const pending = new Map();
    return {
        setTimer(fn) { const id = ++next; pending.set(id, fn); return id; },
        clearTimer(id) { pending.delete(id); },
        tick() {
            const batch = [...pending];
            for (const [id, fn] of batch) if (pending.delete(id)) fn();
        },
        get size() { return pending.size; },
        first() { return pending.values().next().value; },
    };
}

function fixture({app = {}, install, marker, hasMarker = false, onStatus} = {}) {
    const time = clock(), host = new EventTarget();
    let installs = 0, queues = 0;
    const nativeApp = {processingQueue: false, queueItems: [], queuePrompt() { queues++; }, ...app};
    if (hasMarker) host.app = marker;
    const options = {app: nativeApp, host, onStatus, setTimer: time.setTimer, clearTimer: time.clearTimer,
        install: install ?? (() => { installs++; })};
    const bootstrap = createNativeBootstrap(options);
    return {app: nativeApp, host, time, options, bootstrap,
        get installs() { return installs; }, get queues() { return queues; }};
}

function deferred() {
    let resolve;
    const promise = new Promise(done => { resolve = done; });
    return {promise, resolve};
}

for (const order of ['PCS first', 'third-party first']) {
    test(`${order}: install after all setup, retaining wrapper this, arguments, result and rejection`, async () => {
        const time = clock(), host = new EventTarget(), calls = [];
        const failure = new Error('native failure'), receiver = {name: 'receiver'};
        const app = {processingQueue: false, queueItems: [],
            async queuePrompt(...args) {
                calls.push(['native', this, args]);
                if (args[0] === 'fail') throw failure;
                return args[0];
            }};
        let bootstrap, installs = 0, captured;
        const pcsSetup = () => {
            bootstrap = createNativeBootstrap({app, host, setTimer: time.setTimer, clearTimer: time.clearTimer,
                install() {
                    installs++;
                    captured = app.queuePrompt;
                    app.queuePrompt = function(...args) { return captured.apply(this, args); };
                }});
            // No promise returned to ComfyApp.setup: waiting here would deadlock.
            assert.deepEqual(bootstrap.start(), {state: 'waiting', reason: 'waiting_setup'});
        };
        const thirdSetup = () => {
            const previous = app.queuePrompt;
            app.queuePrompt = async function(...args) {
                calls.push(['third', this, args]);
                await Promise.resolve();
                return previous.apply(this, args);
            };
        };
        await Promise.all((order === 'PCS first' ? [pcsSetup, thirdSetup] : [thirdSetup, pcsSetup]).map(fn => fn()));
        const outer = app.queuePrompt;
        time.tick();
        assert.equal(installs, 0);
        // GraphCanvas sets this only after app.setup() returns.
        host.app = app;
        time.tick();
        assert.equal(installs, 1);
        assert.equal(captured, outer);
        assert.equal(calls.length, 0); // No queue probe during installation.
        const argument = {batch: 2};
        assert.equal(await app.queuePrompt.call(receiver, argument, 7), argument);
        await assert.rejects(app.queuePrompt.call(receiver, 'fail', argument), error => error === failure);
        assert.deepEqual(calls.map(entry => entry[0]), ['third', 'native', 'third', 'native']);
        for (const entry of calls) assert.equal(entry[1], receiver);
        assert.deepEqual(calls[0][2], [argument, 7]);
        assert.deepEqual(calls[2][2], ['fail', argument]);
        const laterWrapper = () => {};
        app.queuePrompt = laterWrapper;
        bootstrap.start(); time.tick();
        assert.equal(installs, 1);
        assert.equal(app.queuePrompt, laterWrapper); // Never reacquire ownership.
        assert.equal(time.size, 0);
    });
}

test('pending asynchronous setup and its early afterConfigureGraph cannot install', async () => {
    const f = fixture(), gate = deferred();
    let configured = 0;
    const afterConfigureGraph = () => { configured++; };
    const startup = (async () => {
        await Promise.all([
            (async () => { f.bootstrap.start(); })(),
            (async () => { afterConfigureGraph(); await gate.promise; })(),
        ]);
        f.host.app = f.app;
    })();
    await Promise.resolve();
    for (let i = 0; i < 3; i++) f.time.tick();
    assert.equal(configured, 1);
    assert.equal(f.installs, 0);
    assert.equal(f.bootstrap.status().reason, 'waiting_setup');
    gate.resolve(); await startup; f.time.tick();
    assert.equal(f.installs, 1);
});

test('known busy queue drains naturally before installation, without probing or changing it', () => {
    const queued = {id: 'ordinary'}, items = [queued];
    const f = fixture({app: {processingQueue: true, queueItems: items}});
    f.bootstrap.start(); f.host.app = f.app; f.time.tick();
    assert.equal(f.bootstrap.status().reason, 'waiting_queue_idle');
    assert.equal(f.installs, 0); assert.equal(f.queues, 0);
    assert.equal(f.app.queueItems, items); assert.deepEqual(items, [queued]);
    f.app.processingQueue = false; f.time.tick();
    assert.equal(f.installs, 0); // queued work is still outstanding.
    items.pop(); f.app.processingQueue = true; f.time.tick();
    assert.equal(f.installs, 0); // processing still outstanding with an empty array.
    f.app.processingQueue = false; f.time.tick();
    assert.equal(f.installs, 1); assert.equal(f.queues, 0);
});

for (const [name, app] of [
    ['missing processing', {processingQueue: undefined}],
    ['null processing', {processingQueue: null}],
    ['numeric processing', {processingQueue: 0}],
    ['missing items', {queueItems: undefined}],
    ['array-like items', {queueItems: {length: 0}}],
]) {
    test(`unknown queue state rejects permanently: ${name}`, () => {
        const f = fixture({app});
        f.bootstrap.start(); f.host.app = f.app; f.time.tick();
        assert.deepEqual(f.bootstrap.status(), {state: 'rejected', reason: 'queue_state_unknown'});
        f.app.processingQueue = false; f.app.queueItems = [];
        f.bootstrap.start(); f.time.tick();
        assert.equal(f.installs, 0); assert.equal(f.queues, 0); assert.equal(f.time.size, 0);
    });
}

test('missing queue method rejects without installation', () => {
    const f = fixture({app: {queuePrompt: undefined}});
    f.bootstrap.start(); f.host.app = f.app; f.time.tick();
    assert.equal(f.bootstrap.status().reason, 'queue_method_missing');
    assert.equal(f.installs, 0); assert.equal(f.time.size, 0);
});

for (const marker of [undefined, {}, 'other']) {
    test(`preexisting marker (${typeof marker}) rejects HMR even if later removed`, () => {
        const f = fixture({hasMarker: true, marker});
        delete f.host.app;
        assert.equal(f.bootstrap.start().reason, 'app_marker_already_present');
        assert.equal(f.time.size, 0); assert.equal(f.installs, 0);
    });
}

test('preexisting marker matching app also rejects', () => {
    const f = fixture(); f.host.app = f.app;
    const later = createNativeBootstrap(f.options);
    assert.equal(later.start().reason, 'app_marker_already_present');
});

test('controller must start during setup, not after the marker appears', () => {
    const f = fixture(); f.host.app = f.app;
    assert.equal(f.bootstrap.start().reason, 'start_after_setup');
    assert.equal(f.installs, 0); assert.equal(f.time.size, 0);
});

test('marker for another app rejects instead of attaching to a replacement', () => {
    const f = fixture(); f.bootstrap.start(); f.host.app = {}; f.time.tick();
    assert.equal(f.bootstrap.status().reason, 'app_marker_mismatch');
    f.host.app = f.app; f.bootstrap.start(); f.time.tick();
    assert.equal(f.installs, 0); assert.equal(f.time.size, 0);
});

test('repeated starts share one timer; stop is final, including an already-dispatched timer', () => {
    const f = fixture();
    f.bootstrap.start(); f.bootstrap.start();
    assert.equal(f.time.size, 1);
    const dispatched = f.time.first();
    f.bootstrap.stop(); f.bootstrap.stop(); f.bootstrap.start();
    f.host.app = f.app; dispatched(); f.time.tick();
    assert.equal(f.time.size, 0); assert.equal(f.installs, 0);
    assert.deepEqual(f.bootstrap.status(), {state: 'stopped', reason: 'stopped'});
});

test('stop before start never arms a timer', () => {
    const f = fixture(); f.bootstrap.stop(); f.bootstrap.start();
    assert.equal(f.time.size, 0); assert.equal(f.installs, 0);
});

test('pagehide cancels waiting setup and waiting queue; back-forward restore cannot restart it', () => {
    for (const busy of [false, true]) {
        const f = fixture({app: {processingQueue: busy}});
        f.bootstrap.start();
        if (busy) { f.host.app = f.app; f.time.tick(); }
        f.host.dispatchEvent(new Event('pagehide'));
        f.host.app = f.app; f.app.processingQueue = false;
        f.host.dispatchEvent(new Event('pageshow')); f.bootstrap.start(); f.time.tick();
        assert.deepEqual(f.bootstrap.status(), {state: 'stopped', reason: 'page_hidden'});
        assert.equal(f.time.size, 0); assert.equal(f.installs, 0);
    }
});

test('a second controller for the same app cannot double-install or retry a stopped controller', () => {
    const f = fixture(), second = createNativeBootstrap(f.options);
    f.bootstrap.start();
    assert.equal(second.start().reason, 'bootstrap_already_started');
    f.bootstrap.stop();
    const third = createNativeBootstrap(f.options);
    assert.equal(third.start().reason, 'bootstrap_already_started');
    assert.equal(f.time.size, 0); assert.equal(f.installs, 0);
});

test('installer exception is terminal and never retries a partially installed wrapper', () => {
    let calls = 0;
    const f = fixture({install() { calls++; throw new Error('private error'); }});
    f.bootstrap.start(); f.host.app = f.app; f.time.tick();
    assert.deepEqual(f.bootstrap.status(), {state: 'rejected', reason: 'install_failed'});
    f.bootstrap.start(); f.time.tick();
    assert.equal(calls, 1); assert.equal(f.time.size, 0);
});

test('async installer violates the contract and is never retried', async () => {
    let calls = 0;
    const f = fixture({async install() { calls++; throw new Error('async failure'); }});
    f.bootstrap.start(); f.host.app = f.app; f.time.tick();
    assert.equal(f.bootstrap.status().reason, 'install_must_be_synchronous');
    await Promise.resolve(); f.bootstrap.start(); f.time.tick();
    assert.equal(calls, 1); assert.equal(f.time.size, 0);
});

test('installed controller releases its pagehide listener and does not own adapter cleanup', () => {
    const f = fixture(); f.bootstrap.start(); f.host.app = f.app; f.time.tick();
    f.host.dispatchEvent(new Event('pagehide')); f.bootstrap.stop(); f.bootstrap.start();
    assert.deepEqual(f.bootstrap.status(), {state: 'installed', reason: 'installed'});
    assert.equal(f.installs, 1); assert.equal(f.time.size, 0);
    const snapshot = f.bootstrap.status(); snapshot.state = 'waiting';
    assert.equal(f.bootstrap.status().state, 'installed');
});

test('status observer reports state/reason transitions once, without polling duplicates or private data', () => {
    const reports = [];
    const f = fixture({onStatus(value) { reports.push(value); }});
    f.bootstrap.start(); f.bootstrap.start(); f.time.tick(); f.time.tick();
    f.host.app = f.app; f.app.processingQueue = true;
    f.time.tick(); f.time.tick();
    f.app.processingQueue = false; f.time.tick(); f.time.tick();
    f.bootstrap.stop(); f.bootstrap.start();
    assert.deepEqual(reports, [
        {state: 'waiting', reason: 'waiting_setup'},
        {state: 'waiting', reason: 'waiting_queue_idle'},
        {state: 'installing', reason: 'installing'},
        {state: 'installed', reason: 'installed'},
    ]);
    assert.equal(f.installs, 1);
});

test('terminal rejection notifies once and observer exceptions cannot strand cleanup', () => {
    const reports = [];
    const f = fixture({app: {processingQueue: undefined}, onStatus(value) {
        reports.push(value); throw new Error('observer failed');
    }});
    f.bootstrap.start(); f.host.app = f.app; f.time.tick();
    f.bootstrap.start(); f.time.tick();
    assert.deepEqual(reports, [
        {state: 'waiting', reason: 'waiting_setup'},
        {state: 'rejected', reason: 'queue_state_unknown'},
    ]);
    assert.equal(f.time.size, 0); assert.equal(f.installs, 0);
});

test('observer can stop synchronously during waiting or installing without a leaked timer or install', () => {
    for (const stopAt of ['waiting', 'installing']) {
        const f = fixture({onStatus(value) { if (value.state === stopAt) f.bootstrap.stop(); }});
        f.bootstrap.start(); f.host.app = f.app; f.time.tick();
        assert.equal(f.bootstrap.status().state, 'stopped');
        assert.equal(f.time.size, 0); assert.equal(f.installs, 0);
    }
});

test('queue becoming busy in an observer is rechecked before installation', () => {
    let first = true;
    const f = fixture({onStatus(value) {
        if (value.state === 'installing' && first) { first = false; f.app.processingQueue = true; }
    }});
    f.bootstrap.start(); f.host.app = f.app; f.time.tick();
    assert.equal(f.installs, 0);
    assert.equal(f.bootstrap.status().reason, 'waiting_queue_idle');
    f.app.processingQueue = false; f.time.tick();
    assert.equal(f.installs, 1); assert.equal(f.time.size, 0);
});
