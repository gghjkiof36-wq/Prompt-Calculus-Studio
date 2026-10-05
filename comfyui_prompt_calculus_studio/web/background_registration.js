// Private adapter for the audited ComfyUI frontend 1.43.18 bundle only.
// Registration never queues, restores a graph, or synthesizes execution events.
const ASSET = '/assets/dialogService-DSBgqcNn.js';
const SHA256 = '1d870df50815a0faad3645f65a008e384337e67757364fb423dbb74c0b066ff7';
// Follow native queued-job lifetime; completed jobs must not retain graph copies.
const receipts = new WeakMap();
const own = (value, key) => Object.hasOwn(value, key);
const record = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const fail = message => { throw new Error('背景任務登記已停止：' + message); };
const require = (condition, message) => { if (!condition) fail(message); };
const safeId = value => typeof value === 'string' && value.length > 0 && value.length <= 256 &&
    !own(Object.prototype, value) && value !== '__proto__';
const nodeId = value => {
    if (Number.isSafeInteger(value) && value >= 0) return String(value);
    require(safeId(value), '節點識別碼無效。');
    return value;
};
const inputOf = operation => ({prompt_id: operation.prompt_id, output: operation.output,
    visual: operation.visual, key: operation.key, revision: operation.revision, digest: operation.digest});

function topology(visual) {
    require(record(visual) && Array.isArray(visual.nodes) && Array.isArray(visual.links), '缺少原生圖節點或連線。');
    require(!visual.definitions?.subgraphs?.length && !visual.subgraphs?.length, '尚未支援子圖的執行節點對應。');
    const nodes = new Map();
    for (const node of visual.nodes) {
        require(record(node) && typeof node.type === 'string' && node.type.length > 0, '節點類型無效。');
        const id = nodeId(node.id);
        require(!nodes.has(id), '原生圖有重複節點。');
        require(node.inputs === undefined || Array.isArray(node.inputs), '節點輸入欄位無效。');
        require(Number.isInteger(node.mode ?? 0), '節點模式無效。');
        nodes.set(id, {id, type: node.type, mode: node.mode ?? 0,
            inputs: (node.inputs ?? []).map(input => {
                require(record(input) && typeof input.name === 'string', '節點輸入名稱無效。');
                return input.name;
            })});
    }
    const edges = [], ids = new Set(), targets = new Set();
    for (const raw of visual.links) {
        require(Array.isArray(raw) ? raw.length === 6 : record(raw), '原生連線格式無效。');
        const [id, origin, originSlot, target, targetSlot, type] = Array.isArray(raw) ? raw :
            [raw.id, raw.origin_id, raw.origin_slot, raw.target_id, raw.target_slot, raw.type];
        const source = nodeId(origin), destination = nodeId(target), link = nodeId(id);
        require(!ids.has(link) && nodes.has(source) && nodes.has(destination), '原生連線節點缺失或識別碼重複。');
        require(Number.isSafeInteger(originSlot) && originSlot >= 0 && Number.isSafeInteger(targetSlot) && targetSlot >= 0,
            '原生連線插槽無效。');
        require(typeof type === 'string' || typeof type === 'number', '原生連線類型無效。');
        const field = nodes.get(destination).inputs[targetSlot], slot = JSON.stringify([destination, targetSlot]);
        require(typeof field === 'string' && !targets.has(slot), '原生連線輸入缺失或重複。');
        ids.add(link); targets.add(slot);
        edges.push([source, originSlot, destination, targetSlot, type]);
    }
    const signature = JSON.stringify({nodes: [...nodes.values()].sort((a, b) => a.id.localeCompare(b.id)),
        edges: edges.map(edge => JSON.stringify(edge)).sort()});
    return {nodes, edges, signature};
}

function executionNodes(output, graph) {
    require(record(output) && Object.keys(output).length > 0, '缺少真正提交的 API 圖。');
    const ids = Object.keys(output), links = [];
    for (const id of ids) {
        const node = output[id], visual = graph.nodes.get(id);
        require(safeId(id) && record(node) && record(node.inputs) && visual && visual.type === node.class_type && visual.mode === 0,
            '執行節點與原生圖類型不符，或需要未支援的節點轉換。');
        for (const [field, value] of Object.entries(node.inputs)) {
            if (!Array.isArray(value)) continue;
            require(value.length === 2 && typeof value[0] === 'string' && own(output, value[0]) &&
                Number.isSafeInteger(value[1]) && value[1] >= 0, 'API 連線無法唯一對應原生節點。');
            const slot = visual.inputs.indexOf(field);
            require(slot >= 0 && visual.inputs.lastIndexOf(field) === slot, 'API 輸入無法唯一對應原生插槽。');
            links.push(JSON.stringify([value[0], value[1], id, slot]));
        }
    }
    const visualLinks = graph.edges.filter(edge => own(output, edge[2])).map(edge => JSON.stringify(edge.slice(0, 4)));
    require(JSON.stringify(links.sort()) === JSON.stringify(visualLinks.sort()), 'API 與原生圖連線不符。');
    return ids;
}

function receipt(operation) {
    require(record(operation) && safeId(operation.prompt_id), '缺少真正任務識別碼。');
    require(Number.isSafeInteger(operation.revision) && operation.revision > 0 &&
        typeof operation.digest === 'string' && /^[0-9a-f]{64}$/i.test(operation.digest), '執行收據版本或摘要無效。');
    require(record(operation.key) && safeId(operation.key.frontend_id) && typeof operation.key.path === 'string', '工作流綁定身分無效。');
    require(operation.visual?.id === operation.key.frontend_id, '執行圖與原件識別碼不符。');
    // Core owns digest verification and accepted display R. Do not re-hash the
    // execution visual against mutable widget values (e.g. afterQueued seed).
    const value = structuredClone(inputOf(operation));
    const graph = topology(value.visual), nodes = executionNodes(value.output, graph);
    return {value, nodes, topology: graph.signature, signature: JSON.stringify(value)};
}

function current(app, host, value) {
    require(app && host.app === app && app.graph === app.rootGraph && app.rootGraph && !app.configuringGraph,
        '目前頁面或主畫布無法確認。');
    const workflowStore = app.extensionManager?.workflow, workflow = workflowStore?.activeWorkflow;
    require(workflow && workflow.isLoaded === true && typeof workflow.path === 'string' && workflow.path.startsWith('workflows/'),
        '目前原生工作流尚未載入。');
    require(workflow.path.slice(10) === value.key.path && workflow.activeState?.id === value.key.frontend_id &&
        app.rootGraph.id === value.key.frontend_id, '目前工作流與執行原件不符。');
    const pinia = workflowStore._p, store = pinia?._s?.get?.('execution');
    require(workflowStore.$id === 'workflow' && pinia?._s instanceof Map && pinia._s.get('workflow') === workflowStore &&
        store && store.$id === 'execution' && store._p === pinia && typeof store.storeJob === 'function', '找不到同頁既有的原生任務 store。');
    require(record(store.queuedJobs) && store.jobIdToWorkflowId instanceof Map && store.jobIdToSessionWorkflowPath instanceof Map,
        '原生任務 store 格式不符。');
    require(store.activeJobId === null || store.activeJobId === value.prompt_id, '原生頁面正在追蹤另一筆任務。');
    require(typeof app.rootGraph.serialize === 'function', '無法讀取目前原生圖。');
    return {workflowStore, workflow, draft: workflow.activeState, pinia, store, storeJob: store.storeJob, root: app.rootGraph,
        topology: topology(app.rootGraph.serialize()).signature};
}

function compatible(store, workflow, value, nodes) {
    const id = value.prompt_id, path = workflow.path;
    require(!store.jobIdToWorkflowId.has(id) || store.jobIdToWorkflowId.get(id) === value.key.frontend_id, '任務已綁定另一個原生 ID。');
    require(!store.jobIdToSessionWorkflowPath.has(id) || store.jobIdToSessionWorkflowPath.get(id) === path, '任務已綁定另一個原生路徑。');
    if (!own(store.queuedJobs, id)) return;
    const job = store.queuedJobs[id];
    require(record(job) && job.workflow === workflow && record(job.nodes), '任務已登記其他原生工作流物件。');
    require(JSON.stringify(Object.keys(job.nodes).sort()) === JSON.stringify([...nodes].sort()) &&
        Object.values(job.nodes).every(value => typeof value === 'boolean'), '任務已登記不同執行節點。');
}

// Caller supplies an authenticated frozen execution receipt and has already
// restored the core-accepted display revision. Scalar/widget values may differ
// afterQueued; this adapter checks identity and topology, not receipt integrity.
// Unsupported virtual/bypass/subgraph expansions and array-valued literals are
// rejected rather than guessed. A rejected post-write check does not roll back
// native state: callers must stop event delivery on ANY registration failure.
export async function registerBackgroundJob(app, operation, {host = window, fetcher = fetch, importer = url => import(url)} = {}) {
    const captured = receipt(operation), value = captured.value;
    const asset = new URL(ASSET, host.location.href);
    require(['http:', 'https:'].includes(asset.protocol) && !asset.username && !asset.password &&
        typeof fetcher === 'function' && typeof importer === 'function' &&
        typeof host.addEventListener === 'function' && typeof host.removeEventListener === 'function', '頁面或載入器不受支援。');
    const first = current(app, host, value);
    require(first.topology === captured.topology, '目前原生圖的節點或連線已改變。');
    let hidden = false;
    const pagehide = () => { hidden = true; };
    const check = () => {
        require(!hidden && new URL(ASSET, host.location.href).href === asset.href &&
            JSON.stringify(inputOf(operation)) === captured.signature, '頁面或執行收據已改變。');
        const now = current(app, host, value);
        for (const field of ['workflowStore', 'workflow', 'draft', 'pinia', 'store', 'storeJob', 'root'])
            require(now[field] === first[field], '等待期間原生工作流或 store 已切換。');
        require(now.topology === captured.topology, '等待期間原生連線已改變。');
        compatible(now.store, now.workflow, value, captured.nodes);
        const prior = receipts.get(now.store.queuedJobs[value.prompt_id]);
        require(prior === undefined || prior === captured.signature, '同一任務的執行收據不同。');
        return now;
    };
    host.addEventListener('pagehide', pagehide);
    try {
        check();
        const response = await fetcher(asset.href, {cache: 'no-store', redirect: 'error'});
        check();
        require(response?.ok === true && !response.redirected && response.url === asset.href, '原生 bundle 無法確認或已重導。');
        const bytes = await response.arrayBuffer();
        check();
        const digest = await globalThis.crypto.subtle.digest('SHA-256', bytes);
        check();
        require(Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('') === SHA256,
            '原生前端版本內容尚未驗證。');
        // Import the same canonical module the native page uses, not a copied,
        // evaluated, blob, or cache-busted module that could create another app.
        const module = await importer(asset.href);
        const now = check();
        require(module?.s === app && module.L?.$id === 'execution' && module.N?.$id === 'workflow' &&
            typeof module.L === 'function' && typeof module.N === 'function', '原生 bundle 匯出不符。');
        require(module.N(now.pinia) === now.workflowStore && module.L(now.pinia) === now.store, '匯出不屬於同頁原生 store。');
        check();
        const created = !own(now.store.queuedJobs, value.prompt_id);
        now.store.storeJob({id: value.prompt_id, nodes: [...captured.nodes], workflow: now.workflow});
        check();
        require(now.store.jobIdToWorkflowId.get(value.prompt_id) === value.key.frontend_id &&
            now.store.jobIdToSessionWorkflowPath.get(value.prompt_id) === now.workflow.path &&
            own(now.store.queuedJobs, value.prompt_id), '原生任務登記未完成。');
        receipts.set(now.store.queuedJobs[value.prompt_id], captured.signature);
        const registeredJob = now.store.queuedJobs[value.prompt_id];
        const dispose = () => {
            // Remove only this adapter's new, never-started registration. A
            // real active/other native job belongs to the native lifecycle.
            if (!created || now.store.activeJobId === value.prompt_id ||
                now.store.queuedJobs[value.prompt_id] !== registeredJob ||
                Object.values(registeredJob.nodes).some(Boolean)) return false;
            delete now.store.queuedJobs[value.prompt_id];
            if (now.store.jobIdToWorkflowId.get(value.prompt_id) === value.key.frontend_id)
                now.store.jobIdToWorkflowId.delete(value.prompt_id);
            if (now.store.jobIdToSessionWorkflowPath.get(value.prompt_id) === now.workflow.path)
                now.store.jobIdToSessionWorkflowPath.delete(value.prompt_id);
            receipts.delete(registeredJob);return true;
        };
        // Caller also owns workflow/load-generation/subscription/sequence guards.
        // Check this synchronously before EACH native event, including snapshots.
        const canReceive = () => {
            try {
                const live = current(app, host, value);
                return live.root === now.root && live.workflow === now.workflow && live.store === now.store &&
                    live.topology === captured.topology;
            } catch { return false; }
        };
        return Object.freeze({registered: true, prompt_id: value.prompt_id, revision: value.revision,
            digest: value.digest, canReceive, dispose});
    } finally { host.removeEventListener('pagehide', pagehide); }
}
