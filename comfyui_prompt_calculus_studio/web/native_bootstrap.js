// ComfyUI frontend 1.43.18 cold-start adapter, not a general readiness event.
// GraphCanvas awaits app.setup() (including all extension setup promises),
// then assigns window.app. afterConfigureGraph alone is not that barrier.
// Create and start synchronously inside extension.setup; never await here.
const startedApps = new WeakSet();

export function createNativeBootstrap({
    app, install, host = globalThis, pollMs = 50, onStatus = () => {},
    setTimer = (fn, ms) => globalThis.setTimeout(fn, ms),
    clearTimer = id => globalThis.clearTimeout(id),
}) {
    // Even an existing undefined property cannot establish a fresh cold start.
    const initialMarkerPresent = 'app' in host;
    let state = 'idle', reason = 'not_started', timer = null, listening = false;
    let reportedState = state, reportedReason = reason;
    const status = () => ({state, reason});

    function report() {
        if (state === reportedState && reason === reportedReason) return;
        reportedState = state;
        reportedReason = reason;
        // Observers receive only fixed reason codes, never app data or errors.
        // Reporting failure must not strand bootstrap or repeat installation.
        try { onStatus(status()); } catch {}
    }

    function cleanup() {
        if (timer !== null) clearTimer(timer);
        timer = null;
        if (listening) host.removeEventListener('pagehide', pagehide);
        listening = false;
    }

    function finish(nextState, nextReason) {
        state = nextState;
        reason = nextReason;
        cleanup();
        report();
        return status();
    }

    function stop() {
        // Stopping only cancels bootstrap. The caller owns installed cleanup.
        if (state === 'installed' || state === 'rejected' || state === 'stopped') return status();
        return finish('stopped', 'stopped');
    }

    function pagehide() { finish('stopped', 'page_hidden'); }

    function schedule() { timer = setTimer(check, pollMs); }

    function blockedReason() {
        if (!('app' in host)) return 'waiting_setup';
        if (host.app !== app) return 'app_marker_mismatch';
        if (typeof app.processingQueue !== 'boolean' || !Array.isArray(app.queueItems)) return 'queue_state_unknown';
        if (typeof app.queuePrompt !== 'function') return 'queue_method_missing';
        if (app.processingQueue !== false || app.queueItems.length !== 0) return 'waiting_queue_idle';
        return null;
    }

    function waitOrReject(blocked) {
        if (blocked === 'waiting_setup' || blocked === 'waiting_queue_idle') {
            state = 'waiting';
            reason = blocked;
            report();
            if (state === 'waiting') schedule();
        } else finish('rejected', blocked);
    }

    function check() {
        timer = null;
        if (state !== 'waiting') return;
        // Never call queuePrompt to probe: the native method queues before
        // returning false when busy. Unknown fields do not mean idle.
        const blocked = blockedReason();
        if (blocked) { waitOrReject(blocked); return; }
        // No await between the checks and install. The installer captures the
        // current OUTERMOST queuePrompt, including third-party setup wrappers.
        state = 'installing';
        reason = 'installing';
        report();
        if (state !== 'installing') return; // Observer may synchronously stop.
        const changed = blockedReason();
        if (changed) { waitOrReject(changed); return; }
        cleanup();
        try {
            const result = install();
            if (result && typeof result.then === 'function') {
                // Async installers violate the contract; never retry them.
                Promise.resolve(result).catch(() => {});
                if (state === 'installing') finish('rejected', 'install_must_be_synchronous');
            } else if (state === 'installing') finish('installed', 'installed');
        } catch {
            if (state === 'installing') finish('rejected', 'install_failed');
        }
    }

    function start() {
        if (state !== 'idle') return status();
        if (!app || typeof app !== 'object' || typeof install !== 'function' ||
            typeof onStatus !== 'function' ||
            typeof host.addEventListener !== 'function' || typeof host.removeEventListener !== 'function' ||
            !Number.isFinite(pollMs) || pollMs <= 0) return finish('rejected', 'invalid_bootstrap_options');
        if (initialMarkerPresent) return finish('rejected', 'app_marker_already_present');
        // A controller created early but started after setup is ambiguous too.
        if ('app' in host) return finish('rejected', 'start_after_setup');
        if (startedApps.has(app)) return finish('rejected', 'bootstrap_already_started');
        startedApps.add(app);
        state = 'waiting';
        reason = 'waiting_setup';
        host.addEventListener('pagehide', pagehide);
        listening = true;
        schedule();
        report();
        return status();
    }

    return {start, stop, status};
}
