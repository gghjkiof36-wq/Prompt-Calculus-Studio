import {app} from '../../scripts/app.js';
import {api} from '../../scripts/api.js';
import {KEY, clone, textWidget, captureManual, bind, writeSnapshot, mergeLibrary, chooseItem, changeWorkspace} from './state.js';

const observed = new WeakSet();
const collected = new Map();
let root, token, config = {destinations: {}}, library, active = null, mode = 'builder';
let filter = '', moduleFilter = '', page = 0, resultPage = 0, results = [], busy = false;
let noticeText = '', noticeError = false, syncing = false, refreshTimer, searchTimer;
const desktopSession = crypto.randomUUID();
let desktopLease = '', desktopNode = null, desktopExecuting = false;
let desktopCancelled = false;
const el = (tag, text = '', cls = '') => { const e = document.createElement(tag); e.textContent = text; if (cls) e.className = cls; return e; };
const button = (text, action, cls = '') => {
    const b = el('button', text, cls); b.type = 'button';
    b.onclick = () => Promise.resolve().then(action).catch(error => notify(error.message, true));
    return b;
};
const nodes = () => app.graph?._nodes ?? [];
const target = () => nodes().find(n => String(n.id) === String(active));
const snapshot = () => target()?.properties?.[KEY]?.snapshot;
function graphChanged() { app.graph?.change?.(); app.graph?.setDirtyCanvas?.(true, true); }
function notify(message, error = false) {
    noticeText = message; noticeError = error;
    const box = root?.querySelector('.ps-notice');
    if (box) { box.textContent = message; box.classList.toggle('error', error); }
}
async function request(path, data) {
    const response = await api.fetchApi('/prompt_studio/' + path, data === undefined ? {} : {
        method: 'POST', headers: {'Content-Type': 'application/json', 'X-Prompt-Studio': token}, body: JSON.stringify(data)
    });
    let result; try { result = await response.json(); } catch { throw new Error('ComfyUI 擴充未載入或連線中斷，請確認已重新啟動後端。'); }
    if (!response.ok) throw new Error(result.error ?? '操作失敗，請重新整理頁面後再試。');
    return result;
}
async function connect() {
    const data = await request('config'); token = data.token; config = data.settings;
    try { library = await request('library'); notify('素材庫已連接；既有組合保持原文字。'); }
    catch (error) { notify(error.message, true); }
    render();
}
function observe(node) {
    if (!node || observed.has(node)) return;
    observed.add(node);
    const changed = () => {
        if (!syncing && captureManual(node)) {
            if (desktopNode === node) stopDesktop('ComfyUI 的文字已手動修改，保留文字並暫停桌面控制。');
            graphChanged(); if (String(node.id) === String(active)) render();
        }
    };
    for (const w of node.widgets ?? []) {
        if (w.name !== 'text') continue;
        const old = w.callback;
        w.callback = function(...args) { const result = old?.apply(this, args); changed(); return result; };
        w.inputEl?.addEventListener('input', () => { w.value = w.inputEl.value; changed(); });
    }
    const old = node.onWidgetChanged;
    node.onWidgetChanged = function(...args) { const result = old?.apply(this, args); changed(); return result; };
    const removed = node.onRemoved;
    node.onRemoved = function(...args) {
        const result = removed?.apply(this, args);
        if (desktopNode === node) stopDesktop('綁定節點已刪除，請重新指定。');
        if (String(node.id) === String(active)) setTimeout(render, 0);
        return result;
    };
    const connected = node.onConnectionsChange;
    node.onConnectionsChange = function(...args) {
        const result = connected?.apply(this, args);
        if (desktopNode === node && textWidget(node).error) stopDesktop(textWidget(node).error);
        if (String(node.id) === String(active)) setTimeout(render, 0);
        return result;
    };
}
function captureAll() { for (const node of nodes()) captureManual(node); }
function markActive(node) {
    active = String(node.id); app.graph.extra ??= {}; app.graph.extra.prompt_studio_active = active;
    observe(node); graphChanged(); render();
}
async function compose(state, source = snapshot() ?? library) {
    return request('compose', {state, library_id: source?.library_id, library_revision: source?.library_revision});
}
async function update(action, clear = false) {
    if (busy) return;
    const node = target();
    if (!node?.properties?.[KEY]) throw new Error('請先綁定工作流的文字節點。');
    captureManual(node);
    if (textWidget(node).error) throw new Error(textWidget(node).error);
    const original = node.properties[KEY].snapshot;
    const originalText = original.final_prompt;
    if (original.manual_draft && !clear) throw new Error('正在使用手動版本；清除手動內容後才能調整組合。');
    const state = clone(original.state); action(state);
    busy = true; render();
    try {
        const next = await compose(state, original);
        // A user may type in the graph or switch workflows during the request.
        if (!nodes().includes(node) || node.properties[KEY]?.snapshot !== original)
            throw new Error('工作流或組合已切換，此次更新未套用。');
        captureManual(node);
        if (textWidget(node).widget?.value !== originalText)
            throw new Error('文字欄已手動變更，保留手動版本。');
        syncing = true; try { writeSnapshot(node, next); } finally { syncing = false; }
        graphChanged(); notify(clear ? '已回到模組組合。' : '已更新工作流文字欄。');
    } finally { busy = false; render(); }
}
async function bindSelected(id) {
    if (desktopLease) await stopDesktop('已切換綁定，請重新啟用桌面控制。');
    const node = nodes().find(n => String(n.id) === id);
    if (!node) throw new Error('節點已不存在，請重新選擇。');
    const issue = textWidget(node).error; if (issue) throw new Error(issue);
    if (!node.properties?.[KEY]) {
        if (!library) throw new Error('請先連接桌面素材庫。');
        const data = await compose(library.state, library);
        bind(node, data);
        if (!textWidget(node).widget.value) {
            syncing = true; try { writeSnapshot(node, node.properties[KEY].snapshot); } finally { syncing = false; }
        }
    }
    markActive(node);
    notify(snapshot().manual_draft ? '已綁定並保留原文字；清除手動內容後使用模組組合。' : '已綁定，選擇模組即可更新文字。');
}
function section(title) { const box = el('section', '', 'ps-section'); box.append(el('h3', title)); return box; }
function line(...children) { const row = el('div', '', 'ps-row'); row.append(...children); return row; }
function select(options, value, action) {
    const control = el('select');
    for (const [id, title] of options) { const item = el('option', title); item.value = id; control.append(item); }
    control.value = value;
    control.onchange = () => Promise.resolve(action(control.value)).catch(e => { notify(e.message, true); render(); });
    return control;
}
function render() {
    if (!root?.isConnected) return;
    root.replaceChildren(); root.className = 'prompt-studio';
    root.append(el('h2', 'Prompt Studio'), el('p', '選模組 → 照常生圖 → 喜歡就收藏', 'ps-subtitle'));
    root.append(line(button('模組組合', () => { mode = 'builder'; render(); }, mode === 'builder' ? 'primary' : ''),
        button('挑圖收藏', async () => { mode = 'results'; await loadResults(); render(); }, mode === 'results' ? 'primary' : '')));
    root.append(el('div', noticeText, 'ps-notice' + (noticeError ? ' error' : '')));
    if (mode === 'results') renderResults(); else renderBuilder();
    const setup = el('details', '', 'ps-section'); setup.append(el('summary', '連接與資料設定'));
    setup.append(el('p', config.library || '尚未指定桌面素材庫', 'ps-path'));
    setup.append(button('選擇素材資料夾', () => pickFolder('選擇包含 studio.sqlite3 的資料夾', '', async path => {
        config = await request('config', {library: path}); await connect();
    })), button('重新讀取素材庫', connect));
    setup.append(el('p', '素材編輯仍在桌面版完成；重新讀取不覆蓋工作流已保存的文字。', 'ps-muted'));
    root.append(setup);
}
function renderBuilder() {
    const binding = section('1 · 綁定文字欄');
    const candidates = nodes().filter(n => n.widgets?.some(w => w.name === 'text') || n.inputs?.some(i => i.name === 'text'));
    const picker = select([['', '選擇工作流中的文字節點'], ...candidates.map(n => [String(n.id), `${n.title || n.type} · #${n.id}`])], active ?? '', () => {});
    picker.setAttribute('aria-label', '要控制的文字節點');
    binding.append(picker, button('綁定／切換', () => bindSelected(picker.value)));
    const current = target();
    if (active && !current) binding.append(el('p', '綁定節點已刪除，請重新指定。', 'error'));
    if (current) binding.append(el('p', `目前控制：${current.title || current.type} · #${current.id} / text`, 'ps-muted'));
    const issue = current && textWidget(current).error;
    if (issue) binding.append(el('p', issue, 'error'));
    root.append(binding);
    if (current?.properties?.[KEY] && !issue) {
        binding.append(button(desktopLease ? '停止桌面版控制' : '交給桌面版控制', async () => {
            if (desktopLease) await stopDesktop(); else await startDesktop(current);
        }, desktopLease ? '' : 'primary'));
        binding.append(el('p', desktopLease ? '已交給桌面版：在桌面選模組、直接生成與挑圖收藏。' : '啟用後由桌面版完整接管此文字欄，原文字會隨桌面組合更新。', 'ps-muted'));
    }
    const snap = snapshot();
    if (!snap?.state) { root.append(el('p', '先選擇文字節點，再載入素材與組合。', 'ps-empty')); return; }
    const state = snap.state;
    const disabled = busy || !!issue || snap.manual_draft || !!desktopLease;
    const box = section('2 · 選擇與組合');
    const workspaces = library?.state.workspaces ?? state.workspaces;
    const options = workspaces.map(w => [w.id, w.name]);
    if (!options.some(([id]) => id === state.workspace)) options.unshift([state.workspace, state.workspaces[0]?.name + ' · 歷史快照']);
    const work = select(options, state.workspace, id => update(s => changeWorkspace(s, workspaces.find(w => w.id === id), library?.state ?? state)));
    work.disabled = disabled; box.append(work);
    if (snap.manual_draft) box.append(el('p', '正在使用手動版本', 'ps-manual'));
    if (!library) box.append(el('p', '素材庫未連接，已保存的組合仍可使用。', 'ps-muted'));
    const fieldset = el('fieldset'); fieldset.disabled = disabled; fieldset.classList.toggle('ps-dim', snap.manual_draft);
    const source = library?.state ?? state;
    const search = el('input'); search.placeholder = '搜尋名稱、Prompt、中文別名'; search.value = filter;
    search.setAttribute('aria-label', search.placeholder);
    search.oninput = () => { filter = search.value; page = 0; clearTimeout(searchTimer); searchTimer = setTimeout(() => renderItems(list, source, state), 160); };
    const modules = select([['', '全部模組'], ...source.modules.map(m => [m.id, m.name])], moduleFilter, id => {
        moduleFilter = id; page = 0; renderItems(list, source, state);
    });
    fieldset.append(search, modules);
    const list = el('div', '', 'ps-items'); fieldset.append(list); renderItems(list, source, state);
    const temporary = el('textarea'); temporary.rows = 2; temporary.placeholder = '臨時片段：本次使用，不必先存入素材庫';
    temporary.setAttribute('aria-label', '臨時片段');
    fieldset.append(temporary, button('＋ 加入片段', () => {
        const text = temporary.value.trim(); if (text) return update(s => s.temporary.push(text));
    }));
    const chosen = el('details'); chosen.open = true; chosen.append(el('summary', '目前組合 · 調整順序'));
    renderOrder(chosen, state); fieldset.append(chosen);
    box.append(fieldset); root.append(box);
    const preview = section('3 · 最終 Prompt');
    preview.append(el('p', '可直接在工作流文字欄手動編輯。', 'ps-muted'));
    const text = el('textarea'); text.rows = 6; text.value = snap.final_prompt; text.readOnly = true;
    text.setAttribute('aria-label', '最終 Prompt 預覽');
    preview.append(text);
    const clear = button('清除手動內容', async () => {
        if (!window.confirm('清除手動內容，回到已選模組與臨時片段的組合？')) return;
        await update(s => { s.draft = null; s.draft_base = ''; }, true);
    }, 'danger');
    clear.disabled = !snap.manual_draft || busy || !!issue || !!desktopLease; preview.append(clear);
    root.append(preview);
}
function renderItems(container, source, state) {
    container.replaceChildren();
    const terms = filter.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
    const matches = source.items.filter(i => (!moduleFilter || i.module === moduleFilter)
        && terms.every(term => [i.name, i.prompt, i.notes, ...(i.aliases ?? [])].join(' ').toLocaleLowerCase().includes(term)));
    const maxPage = Math.max(0, Math.ceil(matches.length / 30) - 1); page = Math.min(page, maxPage);
    for (const item of matches.slice(page * 30, page * 30 + 30)) {
        const chosen = state.selections[item.module]?.includes(item.id);
        const b = button('', () => update(s => chooseItem(s, item, source.modules.find(m => m.id === item.module))), 'ps-item' + (chosen ? ' selected' : ''));
        b.append(el('strong', (chosen ? '✓ ' : '') + item.name), el('span', item.prompt));
        b.title = item.prompt; b.setAttribute('aria-pressed', String(!!chosen)); container.append(b);
    }
    if (!matches.length) container.append(el('p', '沒有符合的素材，可改用臨時片段。', 'ps-empty'));
    const prev = button('上一頁', () => { page--; renderItems(container, source, state); }); prev.disabled = page === 0;
    const next = button('下一頁', () => { page++; renderItems(container, source, state); }); next.disabled = page === maxPage;
    container.append(line(prev, el('span', `${matches.length} 項 · ${page + 1}/${maxPage + 1}`), next));
}
function renderOrder(parent, state) {
    const order = state.output_order ?? [], temp = '__temporary_group__';
    function moveGroup(id, delta) {
        return update(s => {
            const at = order.indexOf(id), to = at + delta; if (to < 0 || to >= order.length) return;
            const changed = [...order]; [changed[at], changed[to]] = [changed[to], changed[at]];
            s.output_order = changed;
        });
    }
    function moveItem(mid, at, delta) {
        return update(s => { const entries = mid === temp ? s.temporary : s.selections[mid];
            const to = at + delta; if (to >= 0 && to < entries.length) [entries[at], entries[to]] = [entries[to], entries[at]]; });
    }
    for (const mid of order) {
        const isTemp = mid === temp, entries = isTemp ? state.temporary : state.selections[mid] ?? [];
        if (!entries.length) continue;
        const group = el('div', '', 'ps-group');
        const name = isTemp ? '臨時片段' : state.modules.find(m => m.id === mid).name;
        group.append(line(el('strong', name), button('↑', () => moveGroup(mid, -1)), button('↓', () => moveGroup(mid, 1))));
        entries.forEach((entry, at) => {
            const label = isTemp ? entry : state.items.find(i => i.id === entry)?.name ?? '歷史片段';
            group.append(line(el('span', label, 'ps-grow'), button('↑', () => moveItem(mid, at, -1)), button('↓', () => moveItem(mid, at, 1)),
                button('×', () => update(s => (isTemp ? s.temporary : s.selections[mid]).splice(at, 1)))));
        });
        parent.append(group);
    }
}
async function loadResults() { results = await request('results'); resultPage = Math.min(resultPage, Math.max(0, Math.ceil(results.length / 12) - 1)); }
function destinationControl(parent, workspace, name = '') {
    parent.append(el('p', `${name ? name + ' · ' : ''}${config.destinations?.[workspace] ?? '尚未設定收藏資料夾'}`, 'ps-path'));
    parent.append(button('更換收藏資料夾', () => pickFolder('選擇收藏資料夾', config.destinations?.[workspace] ?? '', async path => {
        config = await request('config', {workspace, destination: path}); render(); notify('已記住收藏資料夾。');
    })));
}
function renderResults() {
    root.append(el('p', '只收藏喜歡的成品。原始 PNG 直接複製，保留已有元資料。', 'ps-muted'));
    root.append(button('重新整理結果', async () => { await loadResults(); render(); }));
    if (!results.length) root.append(el('p', '尚無 PNG 結果。照常生成後，圖片會出現在這裡。', 'ps-empty'));
    for (const r of results.slice(resultPage * 12, resultPage * 12 + 12)) {
        const card = el('article', '', 'ps-result');
        const image = el('img'); image.loading = 'lazy'; image.alt = r.image.filename;
        const params = new URLSearchParams({...r.image, preview: 'webp;75'});
        image.src = api.apiURL('/view?' + params.toString()); card.append(image);
        card.append(el('strong', r.image.filename), el('p', `結果節點 #${r.node_id} · ${r.prompt_id.slice(0, 8)}`, 'ps-muted'));
        const options = r.workspaces.length ? r.workspaces : [{id: '', name: '預設收藏'}];
        r.workspace ??= options[0].id;
        if (options.length > 1) card.append(select(options.map(w => [w.id, w.name]), r.workspace, value => { r.workspace = value; render(); }));
        destinationControl(card, r.workspace, options.find(w => w.id === r.workspace)?.name);
        const key = JSON.stringify([r.prompt_id, r.image, config.destinations?.[r.workspace]]);
        const showStatus = result => {
            save.textContent = '✓ 已收藏'; save.disabled = true;
            status.textContent = `${result.has_generation ? '含生成資料' : '缺少生成資料'} · ${result.has_snapshot ? '含模組快照' : '缺少模組快照'}`;
            status.title = result.path;
        };
        const save = button('收藏到指定資料夾', async () => {
            save.disabled = true; save.textContent = '正在收藏…';
            try {
                const result = await request('collect', {image: r.image, prompt_id: r.prompt_id, workspace: r.workspace});
                collected.set(key, result); showStatus(result);
                notify('已收藏：' + result.path);
            } catch (error) { save.textContent = '重試收藏'; save.disabled = false; throw error; }
        }, 'primary');
        save.disabled = !config.destinations?.[r.workspace];
        const status = el('p', '', 'ps-muted'); card.append(save, status); root.append(card);
        if (collected.has(key)) showStatus(collected.get(key));
    }
    const max = Math.max(0, Math.ceil(results.length / 12) - 1);
    const prev = button('上一頁', () => { resultPage--; render(); }); prev.disabled = !resultPage;
    const next = button('下一頁', () => { resultPage++; render(); }); next.disabled = resultPage >= max;
    root.append(line(prev, el('span', `${resultPage + 1}/${max + 1}`), next));
}
async function pickFolder(title, start, done) {
    const dialog = el('dialog', '', 'prompt-studio ps-dialog');
    dialog.append(el('h2', title));
    const input = el('input'); input.placeholder = '輸入路徑，或點選下方資料夾'; input.value = start;
    input.setAttribute('aria-label', '資料夾路徑');
    const list = el('div', '', 'ps-folders'), status = el('p', '', 'ps-muted');
    let current = '';
    const load = async value => {
        try {
            const data = await request('folders?path=' + encodeURIComponent(value)); current = data.path; input.value = current;
            list.replaceChildren(button('↑ 上一層／磁碟', () => load(data.parent)));
            for (const path of data.directories) list.append(button(path.split(/[\\/]/).filter(Boolean).pop() || path, () => load(path)));
            use.disabled = !current; status.textContent = current || '選擇磁碟';
        } catch (error) { status.textContent = error.message; use.disabled = true; }
    };
    const use = button('使用這個資料夾', async () => {
        use.disabled = true;
        try { await done(current); dialog.close(); } catch (error) { status.textContent = error.message; use.disabled = false; }
    }, 'primary');
    input.onkeydown = event => { if (event.key === 'Enter') { event.preventDefault(); load(input.value); } };
    input.oninput = () => { use.disabled = true; status.textContent = '按「前往」確認輸入的路徑。'; };
    dialog.append(line(input, button('前往', () => load(input.value))), status, list,
        line(button('取消', () => dialog.close()), use));
    dialog.addEventListener('close', () => dialog.remove()); document.body.append(dialog); dialog.showModal(); await load(start);
}

async function startDesktop(node) {
    if (busy || !nodes().includes(node) || textWidget(node).error) throw new Error('請先完成文字欄綁定。');
    const status = await request('desktop/claim', {session: desktopSession, target: `${node.title || node.type} · #${node.id} / text`});
    desktopLease = status.lease; desktopNode = node;
    notify('桌面控制已就緒；請在桌面版按「連接 ComfyUI」。'); render();
    desktopLoop(desktopLease, node);
}
async function stopDesktop(reason = '已停止桌面控制，保留目前文字。') {
    if (!desktopLease) return;
    desktopLease = ''; desktopNode = null;
    try { await request('desktop/release', {session: desktopSession, reason}); }
    catch { /* A lost backend connection also expires the target lease. */ }
    notify(reason, true); render();
}
async function desktopLoop(lease, node) {
    try {
        while (desktopLease === lease) {
            const command = await request('desktop/wait', {session: desktopSession, lease});
            if (desktopLease !== lease) return;
            if (!command) continue;
            let error = '', promptId = '', submitted = 0;
            try {
                if (!nodes().includes(node) || textWidget(node).error) throw new Error('文字綁定已失效，未執行生成。');
                if (busy || app.processingQueue) throw new Error('ComfyUI 正在提交其他操作，請完成後再生成。');
                desktopExecuting = true;
                syncing = true;
                try { writeSnapshot(node, command.snapshot); } finally { syncing = false; }
                graphChanged(); render();
                if (command.kind === 'run') {
                    desktopCancelled = false;
                    const originalQueue = api.queuePrompt;
                    api.queuePrompt = async function(...args) {
                        if (desktopCancelled) throw new Error('已停止運行。');
                        // Verify the actual graph immediately before the request is sent.
                        if (!nodes().includes(node) || args[1]?.output?.[String(node.id)]?.inputs?.text !== command.snapshot.final_prompt)
                            throw new Error('提交前文字或工作流改變，已停止這次生成。');
                        const response = await originalQueue.apply(this, args);
                        promptId = response.prompt_id ?? ''; submitted++; return response;
                    };
                    try { await app.queuePrompt(0, command.count ?? 1); } finally { api.queuePrompt = originalQueue; }
                    if (!promptId) throw new Error('ComfyUI 未接受生成，請查看工作流中的錯誤提示。');
                    if (submitted !== (command.count ?? 1)) throw new Error(`已提交 ${submitted}/${command.count} 次；其餘未提交，請查看 ComfyUI 錯誤提示。`);
                }
                notify(command.kind === 'run' ? '已從桌面提交生成。' : '已同步桌面組合。');
            } catch (exc) { error = exc.message; notify(error, true); }
            finally { desktopExecuting = false; }
            await request('desktop/ack', {session: desktopSession, lease, id: command.id, error, prompt_id: promptId});
        }
    } catch (error) {
        if (desktopLease === lease) await stopDesktop('桌面控制已暫停：' + error.message);
    }
}

app.registerExtension({
    name: 'PromptStudio.ModularCollector',
    nodeCreated(node) { observe(node); if (root?.isConnected) setTimeout(render, 0); },
    afterConfigureGraph() {
        if (desktopLease) stopDesktop('工作流已切換，請重新指定桌面控制的文字節點。');
        active = app.graph.extra?.prompt_studio_active ?? null;
        captureAll(); for (const node of nodes()) observe(node); render();
    },
    async setup() {
        api.addEventListener('prompt_studio_cancel', () => { desktopCancelled = true; });
        const css = el('link'); css.rel = 'stylesheet'; css.href = new URL('./style.css', import.meta.url).href; document.head.append(css);
        app.extensionManager.registerSidebarTab({id: 'prompt-studio', icon: 'pi pi-sparkles', title: 'Prompt Studio',
            tooltip: '模組組合與挑圖收藏', type: 'custom', render(container) { root = container; render(); connect().catch(e => notify(e.message, true)); }});
        for (const node of nodes()) observe(node);
        // Capture late/programmatic edits before serialization. Preserve Comfy's method and arguments.
        const serialize = app.graphToPrompt;
        app.graphToPrompt = async function(...args) {
            if (busy) throw new Error('Prompt Studio 正在更新文字，請完成後再生成。');
            captureAll(); return serialize.apply(this, args);
        };
        for (const event of ['executed', 'execution_success']) api.addEventListener(event, () => {
            clearTimeout(refreshTimer); refreshTimer = setTimeout(() => {
                if (mode === 'results' && root?.isConnected) loadResults().then(render).catch(e => notify(e.message, true));
            }, 300);
        });
        // DOM-based text editors can be mounted after nodeCreated in newer frontends.
        document.addEventListener('input', event => {
            if (!syncing && !root?.contains(event.target)) queueMicrotask(() => {
                let changed = false, desktopChanged = false;
                for (const node of nodes()) {
                    const edited = captureManual(node); changed = edited || changed;
                    if (edited && node === desktopNode) desktopChanged = true;
                }
                if (changed) {
                    if (desktopLease && desktopChanged && !desktopExecuting)
                        stopDesktop('ComfyUI 文字已手動改動，保留文字並暫停桌面控制。');
                    graphChanged(); render();
                }
            });
        }, true);
    }
});
