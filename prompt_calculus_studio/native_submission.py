"""Prepare this run's typed inputs before asking the real frontend to submit."""
from .flow_data import incoming,resolve
from .snapshots import make_snapshot


def prepare(client,state,workflow,valid,submit,failed,image_values=None):
    from .composition_image import freeze_image_source
    from .workflow_runner import image_sources
    directory=client.window.store.directory
    profile=next(p for p in state['generation']['profiles'] if p['id']==workflow)
    images=[]
    for key,target in state['multi_output'].get('image_inputs',{}).items():
        if image_values is not None:break
        source=incoming(state,key,'image')
        if target.get('workflow')==workflow and source:
            content=(freeze_image_source(state,directory,source) if source in state['multi_output']['canvases']
                     else resolve(state,source,'image')['value'])
            images.append((target['node'],content))
    if image_values is None and not images:
        for key in image_sources(state,profile):
            source=freeze_image_source(state,directory,key)
            if not profile.get('image'):raise ValueError('請用圖片輸入選擇接收圖片的 LoadImage 節點。')
            images.append((profile['image'],source))
    if image_values is not None:images=list(image_values)
    snapshot=make_snapshot(state,getattr(client.window.store,'library_id',''))
    uploaded=[]
    def next_upload():
        if not valid():return
        if not images:submit(snapshot,uploaded);return
        node,image=images.pop(0)
        def done(path):
            if valid():uploaded.append(dict(node=node,image=path));next_upload()
        client.queue.upload(image,done,lambda error:failed(error) if valid() else None)
    next_upload()
