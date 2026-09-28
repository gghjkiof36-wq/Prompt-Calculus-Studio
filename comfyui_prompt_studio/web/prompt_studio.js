import {app} from '../../scripts/app.js';
import {api} from '../../scripts/api.js';
import {KEY, textWidget, captureManual, bind, writeSnapshot} from './state.js';

import {DesktopHandoff, workflowTarget} from './desktop_binding.js';
import {transferSettings, exportTransfer} from './workflow_transfer.js';
import {watchImageNodes} from './node_images.js';
import {watchWorkflowRuns,workflowIdentity} from './workflow_sync.js';
import {installNativeQueue} from './native_queue.js';
import {createNativeBootstrap} from './native_bootstrap.js';
import {createSeedObserver} from './native_seed.js';

const observed = new WeakSet();
let root, token, config = {destinations: {}}, library, active = null, mode = 'binding', publishImages, workflowRuns, nativeQueue;
const seedObserver=createSeedObserver(()=>app.extensionManager.setting.get('Comfy.WidgetControlMode'));
let filter = '', moduleFilter = '', page = 0, resultPage = 0, results = [], busy = false;
let noticeText = '', noticeError = false, syncing = false, refreshTimer, searchTimer;
const desktopSession = crypto.randomUUID();
let desktopLease = '', desktopNode = null, desktopExecuting = false;
let desktopCancelled = false, workflowVersion = 0, workflowDownload = null;
const desktop = new DesktopHandoff(
    async node => (await request('desktop/claim', {session:desktopSession,target:`${node.title || node.type} · #${node.id} / text`})).lease,
    () => request('desktop/release', {session:desktopSession,reason:'已切換桌面控制。'}),
    current => {
        desktopLease=current?.lease ?? ''; desktopNode=current?.node ?? null;
        if (current) { notify('桌面控制已就緒。'); desktopLoop(current.lease,current.node); }
        else desktopCancelled=true;
        render();
    });
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
async function request(path, data, options={}) {
    if(data!==undefined&&!token)token=(await request('config')).token;
    const response = await api.fetchApi('/prompt_studio/' + path, data === undefined ? {} : {
        method: 'POST', headers: {'Content-Type': 'application/json', 'X-Prompt-Studio': token}, body: JSON.stringify(data),...options
    });
    let result; try { result = await response.json(); } catch { throw new Error('ComfyUI 擴充未載入或連線中斷，請確認已重新啟動後端。'); }
    if (!response.ok) {
        const error=new Error(result.error ?? '操作失敗，請重新整理頁面後再試。');
        if(response.status===400&&typeof result.capture_uncommitted==='string')error.uncommittedOperation=result.capture_uncommitted;
        throw error;
    }
    return result;
}
async function connect() {
    const data = await request('config'); token = data.token; config = data.settings;
    try { library = await request('library'); notify('已讀取桌面輸出。'); }
    catch (error) { notify(error.message, true); }
    render(); await restoreDesktop(); await workflowRuns?.refresh();
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
        if (desktopNode === node) stopDesktop('綁定節點已刪除，請重新指定。', false);
        if (String(node.id) === String(active)) setTimeout(render, 0);
        return result;
    };
    const connected = node.onConnectionsChange;
    node.onConnectionsChange = function(...args) {
        const result = connected?.apply(this, args);
        if (desktopNode === node && textWidget(node).error) stopDesktop(textWidget(node).error, false);
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
async function bindSelected(id, automatic = false) {
    const version=workflowVersion;
    const node = nodes().find(n => String(n.id) === id);
    if (!node) throw new Error('節點已不存在，請重新選擇。');
    const issue = textWidget(node).error; if (issue) throw new Error(issue);
    if (!node.properties?.[KEY]) {
        if (!library) throw new Error('請先連接桌面素材庫。');
        const data = await compose(library.state, library);
        if (version!==workflowVersion || !nodes().includes(node)) return;
        bind(node, data);
        if (!textWidget(node).widget.value) {
            syncing = true; try { writeSnapshot(node, node.properties[KEY].snapshot); } finally { syncing = false; }
        }
    }
    if (version!==workflowVersion || !nodes().includes(node)) return;
    markActive(node);
    if (!automatic || app.graph.extra.prompt_studio_desktop !== false) await startDesktop(node);
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
function download(name,value) {
    if (workflowDownload) URL.revokeObjectURL(workflowDownload.url);
    const url=URL.createObjectURL(new Blob([JSON.stringify(value,null,2)],{type:'application/json'}));
    workflowDownload={name,url}; notify('已準備工作流檔案。若瀏覽器沒有開始下載，可按下方下載連結。'); render();
    root?.querySelector('a.ps-workflow-download')?.click();
}
async function exportWorkflow() {
    const version=workflowVersion, graph=app.graph;
    const value=await app.graphToPrompt();
    if (version!==workflowVersion||graph!==app.graph) throw new Error('工作流已切換，請重新匯出目前工作流。');
    const transfer=exportTransfer(app.graph,value.output,value.workflow,false);
    await publishImages?.();
    if (!library?.library_id) throw new Error('請先連接桌面素材庫。');
    const delivery=await request('workflow/export',{id:crypto.randomUUID(),library:library.library_id,value:transfer});
    notify('已送往桌面，連線中的桌面程式會自動匯入。');
    for (let i=0;i<12;i++) {
        await new Promise(resolve=>setTimeout(resolve,1500));
        const status=await request('workflow/export/'+encodeURIComponent(delivery.id));
        if (status.status==='error') throw new Error('桌面未匯入：'+status.error);
        if (status.status==='done') { notify('桌面已匯入，可在「設定 → ComfyUI → 工作流管理」查看。'); return; }
    }
    notify('已保留待匯入工作流，桌面程式重新連線後會自動接收。');
}
function importWorkflow() {
    const input=el('input'); input.type='file'; input.accept='.json';
    input.hidden=true; document.body.append(input);
    input.addEventListener('cancel',()=>input.remove(),{once:true});
    input.onchange=async()=>{
        try {
            const file=input.files?.[0]; if (!file) return;
            if (file.size>2*1024*1024) throw new Error('工作流超過 2 MB。');
            const value=JSON.parse(await file.text());
            const workflow=value.format==='prompt_studio_workflow'?value.workflow:value;
            if (!Array.isArray(workflow?.nodes)) throw new Error('請選擇含畫布資料的 ComfyUI 工作流。');
            await stopDesktop('正在匯入工作流…',false); await app.loadGraphData(workflow);
            notify('已匯入工作流。'); render();
        } catch(error) { notify(error.message,true); }
        finally { input.remove(); }
    }; input.click();
}
function render() {
    if (!root?.isConnected) return;
    root.replaceChildren(); root.className='prompt-studio';
    root.append(el('h2','Prompt Calculus Studio · v0.82 Alpha 1 Repair 5（0927-2）'),el('p','工作流傳送與執行同步','ps-subtitle'));
    root.append(el('div',noticeText,'ps-notice'+(noticeError?' error':'')));
    const transfer=section('工作流匯入／匯出'); const settings=transferSettings(app.graph);
    const name=el('input'); name.value=settings.name; name.setAttribute('aria-label','工作流名稱');
    name.onchange=()=>{settings.name=name.value.trim()||'桌面工作流'; graphChanged();};
    transfer.append(name,line(button('匯入工作流',importWorkflow),button('匯出到桌面',exportWorkflow),button('另存檔案',async()=>{
        const graph=app.graph,version=workflowVersion,value=await app.graphToPrompt();
        if (graph!==app.graph||version!==workflowVersion) throw new Error('工作流已切換，請重新另存。');
        download(transferSettings(graph).name+'.prompt-studio.json',exportTransfer(graph,value.output,value.workflow,false));
        await publishImages?.();
    })));
    if (workflowDownload) {
        const link=el('a','下載：'+workflowDownload.name,'ps-workflow-download'); link.href=workflowDownload.url; link.download=workflowDownload.name; transfer.append(link);
    }
    transfer.append(el('p','匯出後自動傳到已連接的桌面素材庫。','ps-muted')); root.append(transfer);
    const setup=el('details','','ps-section'); setup.append(el('summary','桌面資料連接'));
    setup.append(el('p',config.library||'尚未指定桌面資料','ps-path'));
    setup.append(button('選擇資料夾',()=>pickFolder('選擇包含 studio.sqlite3 的資料夾','',async path=>{
        config=await request('config',{library:path});await connect();
    })),button('重新讀取桌面輸出',connect)); root.append(setup);
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

async function restoreDesktop() {
    if (library?.state?.multi_output?.version>=4)return;
    if (!token || app.graph?.extra?.prompt_studio_desktop === false) return;
    if (app.graph?.extra?.prompt_studio_v08 && app.graph.extra.prompt_studio_desktop !== true) return;
    const node=workflowTarget(app.graph);
    if (node) await bindSelected(String(node.id),true);
}
async function startDesktop(node) {
    if (busy || !nodes().includes(node) || textWidget(node).error) throw new Error('請先完成文字欄綁定。');
    app.graph.extra ??= {}; app.graph.extra.prompt_studio_desktop=true; graphChanged();
    await desktop.select(node);
}
async function stopDesktop(reason = '已停止桌面控制，保留目前文字。', remember = true) {
    if (remember) { app.graph.extra ??= {}; app.graph.extra.prompt_studio_desktop=false; graphChanged(); }
    try { await desktop.select(null); } catch { /* The backend lease also expires. */ }
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
                if (busy || desktopExecuting || app.processingQueue) throw new Error('ComfyUI 正在提交其他操作，請完成後再生成。');
                desktopExecuting = true;
                syncing = true;
                try { writeSnapshot(node, command.snapshot); } finally { syncing = false; }
                graphChanged(); render();
                if (command.kind === 'run') {
                    desktopCancelled = false;
                    const originalQueue = api.queuePrompt;
                    api.queuePrompt = async function(...args) {
                        // Verify the actual graph immediately before the request is sent.
                        if (desktopLease!==lease || !nodes().includes(node) || args[1]?.output?.[String(node.id)]?.inputs?.text !== command.snapshot.final_prompt)
                            throw new Error('提交前文字或工作流改變，已停止這次生成。');
                        const response = await originalQueue.apply(this, args);
                        promptId = response.prompt_id ?? ''; submitted++; return response;
                    };
                    try {
                        // Keep native widget/seed callbacks for every submission, while
                        // stopping between jobs without surfacing an intentional stop as
                        // a ComfyUI execution-error dialog.
                        for (let i = 0; i < (command.count ?? 1) && !desktopCancelled && desktopLease===lease; i++) {
                            const before = submitted;
                            await app.queuePrompt(0, 1);
                            if (submitted === before) break;
                        }
                    } finally { api.queuePrompt = originalQueue; }
                    if (desktopLease !== lease) throw new Error('工作流已切換，其餘次數已取消。');
                    if (desktopCancelled) {
                        // A request already in flight at the first stop can arrive after
                        // the queue was cleared. Drain it once submission has settled.
                        await request('desktop/interrupt', {clear_pending: true});
                        throw new Error(`已停止運行，提交了 ${submitted} 次，其餘已取消。`);
                    }
                    if (!promptId) throw new Error('ComfyUI 未接受生成，請查看工作流中的錯誤提示。');
                    if (submitted !== (command.count ?? 1)) throw new Error(`已提交 ${submitted}/${command.count} 次；其餘未提交，請查看 ComfyUI 錯誤提示。`);
                }
                notify(command.kind === 'run' ? '已從桌面提交生成。' : '已同步桌面組合。');
            } catch (exc) { error = exc.message; notify(error, true); }
            finally { desktopExecuting = false; }
            await request('desktop/ack', {session: desktopSession, lease, id: command.id, error, prompt_id: promptId});
        }
    } catch (error) {
        if (desktopLease === lease) await stopDesktop('桌面控制已暫停：' + error.message, false);
    }
}

app.registerExtension({
    name: 'PromptStudio.DesktopWorkflow',
    nodeCreated(node) { seedObserver.observe(node); observe(node); if (root?.isConnected) setTimeout(render, 0); },
    afterConfigureGraph() {
        workflowVersion++;
        nativeQueue?.changed();
        workflowRuns?.changed();
        if (workflowDownload) { URL.revokeObjectURL(workflowDownload.url); workflowDownload=null; }
        stopDesktop('正在切換工作流…', false);
        active = app.graph.extra?.prompt_studio_active ?? null;
        captureAll(); for (const node of nodes()) observe(node); render();
        restoreDesktop().catch(e=>notify(e.message,true));
    },
    async setup() {
        for(const node of nodes())seedObserver.observe(node,{loaded:true});
        publishImages=watchImageNodes(app,api,request,()=>workflowIdentity(app),()=>nativeQueue?.busy,desktopSession);
        workflowRuns=watchWorkflowRuns(app,api,request,notify,fn=>{syncing=true;try{return fn();}finally{syncing=false;}},()=>nativeQueue?.busy);
        // ComfyUI runs extension setup concurrently. Its cold-start window.app
        // marker is published only after all setup promises complete, so capture
        // the final extension queue chain there, never partway through setup.
        const bootstrap=createNativeBootstrap({app,host:window,
            install(){nativeQueue=installNativeQueue(app,api,request,desktopSession,notify,undefined,()=>{workflowRuns.refresh();publishImages();},undefined,seedObserver);},
            onStatus({state}){if(state==='rejected')notify('無法確認原生執行入口的啟動狀態，PCS 未提交；請重新整理 ComfyUI 頁面。',true);}
        });
        bootstrap.start(); // Do not await the completion marker from inside setup.
        window.addEventListener('pagehide',()=>{bootstrap.stop();workflowRuns.stop();nativeQueue?.stop();},{once:true});
        connect().catch(e=>notify(e.message,true));
        setTimeout(publishImages,0);
        api.addEventListener('prompt_studio_cancel', () => { desktopCancelled = true; nativeQueue?.changed(); });
        const css = el('link'); css.rel = 'stylesheet'; css.href = new URL('./style.css', import.meta.url).href; document.head.append(css);
        app.extensionManager.registerSidebarTab({id: 'prompt-studio', icon: 'pi pi-sparkles', title: 'Prompt Calculus Studio',
            tooltip: '工作流傳送與執行同步', type: 'custom', render(container) { root = container; render(); connect().catch(e => notify(e.message, true)); }});
        for (const node of nodes()) observe(node);
        // Capture late/programmatic edits before serialization. Preserve Comfy's method and arguments.
        const serialize = app.graphToPrompt;
        app.graphToPrompt = async function(...args) {
            if (busy) throw new Error('Prompt Calculus Studio 正在更新文字，請完成後再生成。');
            captureAll(); return serialize.apply(this, args);
        };
        // DOM-based text editors can be mounted after nodeCreated in newer frontends.
        document.addEventListener('input', event => {
            if (!syncing && !root?.contains(event.target)) queueMicrotask(() => {
                let changed = false, desktopChanged = false;
                for (const node of nodes()) {
                    const edited = captureManual(node); changed = edited || changed;
                    if (edited && node === desktopNode) desktopChanged = true;
                }
                workflowRuns?.markEdited(event);
                if (changed) {
                    if (desktopLease && desktopChanged && !desktopExecuting)
                        stopDesktop('ComfyUI 文字已手動改動，保留文字並暫停桌面控制。');
                    graphChanged(); render();
                }
            });
        }, true);
    }
});
