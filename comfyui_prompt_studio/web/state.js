// Small, testable binding layer. It never guesses another node or input.
export const KEY = 'prompt_studio';
export const clone = (value) => JSON.parse(JSON.stringify(value));

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
    const picks = state.selections[item.module] ??= [];
    const index = picks.indexOf(item.id);
    if (index >= 0) { picks.splice(index, 1); return; }
    const savedModule = state.modules.find(m => m.id === item.module);
    if (!savedModule) state.modules.push(clone(module));
    state.items = state.items.filter(i => i.id !== item.id);
    state.items.push(clone(item));
    if ((savedModule ?? module).mode === 'single') state.selections[item.module] = [item.id];
    else picks.push(item.id);
}

export function changeWorkspace(state, workspace, library) {
    const w = clone(workspace);
    state.workspaces = [w]; state.workspace = w.id;
    for (const mid of w.fixed) {
        const m = library.modules.find(m => m.id === mid);
        if (!state.modules.some(m => m.id === mid) && m) state.modules.push(clone(m));
        state.selections[mid] = [...(w.picks[mid] ?? [])];
    }
    const required = new Set(Object.values(w.picks).flat());
    for (const item of library.items) if (required.has(item.id)) {
        if (w.fixed.includes(item.module) || !state.items.some(i => i.id === item.id)) {
            state.items = state.items.filter(i => i.id !== item.id);
            state.items.push(clone(item));
        }
    }
}
