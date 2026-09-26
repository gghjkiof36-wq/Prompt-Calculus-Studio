// Small, testable binding layer. It never guesses another node or input.
export const KEY = 'prompt_studio';
export const clone = (value) => JSON.parse(JSON.stringify(value));

function recordMultiDraft(snapshot) {
    const data=snapshot.state.multi_output;
    if (!data || snapshot.state.selection_view==='list' && snapshot.state.settings?.separate_selections) return;
    const output=data.outputs[data.current_output]; const compiled=snapshot.outputs?.[data.current_output];
    if (output) {output.draft=snapshot.final_prompt;output.draft_base=snapshot.generated_prompt;}
    if (compiled) {compiled.final_prompt=snapshot.final_prompt;compiled.manual_draft=true;}
}

export function textWidget(node, field = 'text') {
    const input = node?.inputs?.find(i => i.name === field || i.widget?.name === field);
    if (input?.link != null) return {error: '文字由連線輸入，請改綁上游文字來源。'};
    const widget = node?.widgets?.find(w => w.name === field);
    if (!widget || typeof widget.value !== 'string' || widget.type === 'converted-widget')
        return {error: '此節點沒有可直接編輯的 text 欄位，請重新指定。'};
    return {widget};
}

export function captureManual(node) {
    const binding = node?.properties?.[KEY];
    if (!binding?.snapshot) return false;
    const {widget} = textWidget(node, binding.field);
    if (!widget) return false;
    const snapshot = binding.snapshot;
    if (widget.value === snapshot.final_prompt) return false;
    snapshot.state.draft = widget.value;
    snapshot.state.draft_base = snapshot.generated_prompt;
    snapshot.final_prompt = widget.value;
    snapshot.manual_draft = true;
    recordMultiDraft(snapshot);
    return true;
}

export function bind(node, snapshot, field = 'text') {
    const {widget, error} = textWidget(node, field);
    if (error) throw new Error(error);
    const copy = clone(snapshot);
    // Binding itself must not destroy text already in the graph.
    if (widget.value && widget.value !== copy.final_prompt) {
        copy.state.draft = widget.value;
        copy.state.draft_base = copy.generated_prompt;
        copy.final_prompt = widget.value;
        copy.manual_draft = true;
        recordMultiDraft(copy);
    }
    node.properties ??= {};
    node.properties[KEY] = {version: 1, field, snapshot: copy};
    return copy;
}

export function writeSnapshot(node, snapshot) {
    const binding = node?.properties?.[KEY];
    if (!binding) throw new Error('綁定已失效，請重新指定。');
    const {widget, error} = textWidget(node, binding.field);
    if (error) throw new Error(error);
    binding.snapshot = clone(snapshot);
    widget.value = snapshot.final_prompt;
    if (widget.inputEl) widget.inputEl.value = widget.value;
    widget.callback?.(widget.value);
}

export function mergeLibrary(state, library) {
    // Refresh candidates without silently replacing saved selections or text.
    const result = clone(state), mids = new Set(result.modules.map(m => m.id));
    for (const m of library.modules) if (!mids.has(m.id)) result.modules.push(clone(m));
    return result;
}

export function chooseItem(state, item, module) {
    // This picker is the list interface. An explicit list choice must not be
    // hidden by a snapshot that originated in the isolated desktop Canvas.
    if (state.settings?.separate_selections && state.selection_view === 'canvas') {
        state.view_drafts ??= {};
        state.view_drafts.canvas = {draft: state.draft, draft_base: state.draft_base ?? ''};
        const draft = state.view_drafts.list ?? {draft: null, draft_base: ''};
        Object.assign(state, clone(draft), {selection_view: 'list'});
    }
    const picks = state.selections[item.module] ??= [];
    const index = picks.indexOf(item.id);
    if (index >= 0) { picks.splice(index, 1); if (state.instances) delete state.instances[item.id]; return; }
    const savedModule = state.modules.find(m => m.id === item.module);
    if (!savedModule) state.modules.push(clone(module));
    state.items = state.items.filter(i => i.id !== item.id);
    state.items.push(clone(item));
    if (item.composition) state.version = Math.max(state.version, 2);
    if (state.version >= 2) {
        state.instances ??= {};
        state.instances[item.id] = clone(item.composition ?? {
            id: 'source:' + item.id, name: item.name, prompt: item.prompt,
            enabled: true, weight: 10, children: [], overlays: [], excludes: item.excludes ?? []
        });
    }
    if ((savedModule ?? module).mode === 'single') state.selections[item.module] = [item.id];
    else picks.push(item.id);
}

export function changeWorkspace(state, workspace, library) {
    const w = clone(workspace);
    state.workspaces = [w]; state.workspace = w.id;
    if (state.uses || w.uses) {
        state.uses ??= {};
        const removed = new Set();
        for (const [id, root] of Object.entries(state.uses)) if (w.fixed.includes(root.source_module) || w.fixed_uses?.includes(id)) {
            delete state.uses[id]; removed.add(id);
        }
        if (state.output_order) state.output_order = state.output_order.filter(id => !removed.has(id));
        for (const [id, root] of Object.entries(w.uses ?? {})) if (w.fixed.includes(root.source_module) || w.fixed_uses?.includes(id)) state.uses[id] = clone(root);
        if (Object.keys(state.uses).length) state.version = Math.max(state.version, 3);
    }
    for (const mid of w.fixed) {
        for (const item of state.items) if (item.module === mid && state.instances) delete state.instances[item.id];
        const m = library.modules.find(m => m.id === mid);
        if (!state.modules.some(m => m.id === mid) && m) state.modules.push(clone(m));
        state.selections[mid] = [...(w.picks[mid] ?? [])];
        state.weights ??= {};
        for (const id of state.selections[mid]) state.weights[id] = w.weights?.[id] ?? 10;
        for (const id of state.selections[mid]) if (w.instances?.[id]) {
            state.instances ??= {}; state.instances[id] = clone(w.instances[id]); state.version = Math.max(state.version, 2);
        }
    }
    const required = new Set(Object.values(w.picks).flat());
    for (const item of library.items) if (required.has(item.id)) {
        if (item.composition) state.version = Math.max(state.version, 2);
        if (w.fixed.includes(item.module) || !state.items.some(i => i.id === item.id)) {
            state.items = state.items.filter(i => i.id !== item.id);
            state.items.push(clone(item));
        }
    }
}
