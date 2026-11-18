import test from 'node:test';
import assert from 'node:assert/strict';
import {createBridgeRequests} from '../comfyui_prompt_calculus_studio/web/bridge_request.js';

const reply = (status, value) => ({status, ok: status >= 200 && status < 300, json: async () => value});
const forbidden = () => new Response('Forbidden', {status: 403});

function fixture() {
    const api = new EventTarget();
    const state = {token: 'first', calls: []};
    api.fetchApi = async (url, opts) => {
        const route = url.slice('/prompt_studio/'.length);
        state.calls.push({route, opts});
        if (route === 'config') {
            assert.equal(opts.cache, 'no-store');
            return state.config ? state.config() : reply(200, {token: state.token, settings: {}});
        }
        if (state.respond) return state.respond(route, opts);
        return opts.headers['X-Prompt-Studio'] === state.token ? reply(200, {}) : forbidden();
    };
    return {api, state, ...createBridgeRequests(api)};
}

test('the original page negotiates the restarted backend once for simultaneous native polls', async () => {
    const {api, state, request} = fixture();
    await request('workflow/native/poll', {});
    state.token = 'second';
    api.dispatchEvent(new Event('reconnected'));
    await Promise.all([request('workflow/native/poll', {}), request('workflow/run-state', {})]);
    assert.equal(state.calls.filter(c => c.route === 'config').length, 2);
    assert.deepEqual(state.calls.filter(c => c.opts.method === 'POST').map(c => c.opts.headers['X-Prompt-Studio']),
        ['first', 'second', 'second']);
});

test('a missed reconnect invalidates a plain-text 403 without replaying the command', async () => {
    const {state, request} = fixture();
    await request('workflow/native/poll', {});
    state.token = 'second';
    await assert.rejects(request('desktop/ack', {id: 'sent-once'}));
    await request('workflow/native/poll', {});
    assert.deepEqual(state.calls.map(c => c.route), ['config', 'workflow/native/poll', 'desktop/ack', 'config', 'workflow/native/poll']);
    assert.equal(state.calls.at(-1).opts.headers['X-Prompt-Studio'], 'second');
});

test('a delayed old reply cannot invalidate the fresh credential', async () => {
    const {api, state, request} = fixture();
    await request('workflow/native/poll', {});
    const old = Promise.withResolvers();
    state.respond = route => route === 'desktop/ack' ? old.promise : reply(200, {});
    const sent = request('desktop/ack', {});
    api.dispatchEvent(new Event('reconnected'));
    state.token = 'second';
    await request('workflow/native/poll', {});
    old.resolve(forbidden());
    await assert.rejects(sent);
    await request('workflow/run-state', {});
    assert.equal(state.calls.filter(c => c.route === 'config').length, 2);
    assert.equal(state.calls.at(-1).opts.headers['X-Prompt-Studio'], 'second');
});

test('a reconnect during config lookup leaves the pending write unsent', async () => {
    const {api, state, request} = fixture();
    const first = Promise.withResolvers();
    state.config = () => first.promise;
    const command = request('desktop/claim', {});
    api.dispatchEvent(new Event('reconnected'));
    state.config = null;
    state.token = 'second';
    await request('workflow/native/poll', {});
    first.resolve(reply(200, {token: 'first', settings: {}}));
    await assert.rejects(command, /尚未送出/);
    assert.equal(state.calls.filter(c => c.route === 'desktop/claim').length, 0);
    assert.equal(state.calls.at(-1).opts.headers['X-Prompt-Studio'], 'second');
});

test('a failed config read is recoverable and an uncommitted capture keeps its operation ID', async () => {
    const {state, request} = fixture();
    state.config = () => reply(200, {});
    await assert.rejects(request('workflow/native/poll', {}), /憑證/);
    state.config = null;
    state.respond = () => reply(400, {error: 'capture rejected', capture_uncommitted: 'same-operation'});
    await assert.rejects(request('workflow/capture', {}), e => e.uncommittedOperation === 'same-operation');
    assert.equal(state.calls.filter(c => c.opts.method === 'POST').length, 1);
});
