// Publish only image references from a workflow that already has a PCS identity.
export function imageNodeState(graph,identity={}) {
    const workflow=identity.workflow||graph?.extra?.prompt_studio_v08?.id;
    if (!workflow&&!identity.path) return null;
    const nodes={};
    for (const node of graph._nodes??[]) {
        if (!['LoadImage','PreviewImage','SaveImage'].includes(node.type)) continue;
        let images=[];
        if (node.type==='LoadImage') {
            const value=node.widgets?.find(w=>w.name==='image')?.value;
            if (typeof value==='string'&&value) images=[value];
        } else {
            for (const image of node.imgs??[]) {
                try {
                    const url=new URL(image.src,location.href);
                    if (url.origin!==location.origin||!url.searchParams.get('filename')) continue;
                    images.push({filename:url.searchParams.get('filename'),subfolder:url.searchParams.get('subfolder')??'',type:url.searchParams.get('type')??'output'});
                } catch {}
            }
        }
        nodes[String(node.id)]=images.length>64?{type:node.type,images:[],selected:null,overflow:true}:
            {type:node.type,images,selected:images.length===1?0:Number.isInteger(node.imageIndex)&&node.imageIndex>=0&&node.imageIndex<images.length?node.imageIndex:null};
    }
    return {workflow:workflow||identity.path,nodes,frontend_id:identity.frontend_id??'',...(identity.path?{path:identity.path}:{})};
}

export function watchImageNodes(app,api,request,identity=()=>({}),paused=()=>false,publisher='') {
    let pending=false,dirty=false,last='';
    async function publish() {
        if(paused()||app.configuringGraph||app.rootGraph&&app.graph!==app.rootGraph)return;
        if (pending) { dirty=true; return; }
        const value=imageNodeState(app.graph,identity()); if (!value) return;
        if(publisher)value.publisher=publisher;
        const signature=JSON.stringify(value); if (signature===last) return;
        pending=true;
        try { await request('workflow/images',value); last=signature; } catch {} finally {
            pending=false;
            if (dirty) { dirty=false; queueMicrotask(publish); }
        }
    }
    // Comfy UI selection and actual outputs are distinct events. No graph writes
    // or generation requests occur when publishing a changed reference.
    document.addEventListener('pointerup',()=>setTimeout(publish,0));
    document.addEventListener('change',()=>setTimeout(publish,0));
    // Comfy sets output thumbnails asynchronously after the executed event.
    // Capturing image load also covers a selection made while publishing.
    document.addEventListener('load',event=>{if (event.target?.tagName==='IMG') publish();},true);
    api.addEventListener('executed',()=>setTimeout(publish,0));
    api.addEventListener('execution_success',()=>setTimeout(publish,0));
    return publish;
}
