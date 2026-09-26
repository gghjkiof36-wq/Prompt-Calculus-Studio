// ComfyUI frontend 1.43.18 workflow objects, not PCS graph copies.
export function nativeIdentity(workflow) {
    const state=workflow?.activeState,path=workflow?.path;
    return {workflow:state?.extra?.prompt_studio_v08?.id??'',
        path:typeof path==='string'&&path.startsWith('workflows/')?path.slice(10):'',
        frontend_id:state?.id??''};
}

export function nativeCatalog(app) {
    const workflows=app.extensionManager?.workflow?.workflows;
    // Only loaded native objects have a confirmed ID and draft. Unknown files
    // must first be opened natively; never infer identity from a display name.
    return Array.isArray(workflows)?workflows.filter(w=>w.isLoaded&&w.activeState?.id).slice(0,256).map(nativeIdentity):[];
}

function sameTarget(actual,target) {
    return !!target.frontend_id&&actual.frontend_id===target.frontend_id&&
        (target.path?actual.path===target.path:!!target.workflow&&actual.workflow===target.workflow);
}

export function confirmNativeWorkflow(app,target,guard) {
    const store=app.extensionManager?.workflow,source=store?.activeWorkflow,graph=app.rootGraph;
    const workflows=store?.workflows;
    if(!Array.isArray(workflows)||!source||!graph||app.graph!==graph||app.configuringGraph)
        throw new Error('無法確認原生工作流管理介面，請先回到 ComfyUI 主畫布。');
    const candidates=workflows.filter(w=>w.isLoaded&&sameTarget(nativeIdentity(w),target));
    if(candidates.length!==1)throw new Error('找不到唯一的已載入原生工作流，請先在 ComfyUI 打開綁定的原件。');
    const destination=candidates[0];
    // Native switching is deliberately unavailable until its draft/undo and
    // failed-load lifecycle is verified. Confirmation is entirely read-only.
    if(destination!==source)throw new Error('請先在 ComfyUI 切到已綁定的工作流；PCS 尚未開放自動切換。');
    guard();
    if(store.activeWorkflow!==source||app.rootGraph!==graph||app.graph!==graph||graph.id!==target.frontend_id||!sameTarget(nativeIdentity(source),target))
        throw new Error('無法確認目前原生工作流，請重新選擇後再試。');
    return nativeIdentity(source);
}
