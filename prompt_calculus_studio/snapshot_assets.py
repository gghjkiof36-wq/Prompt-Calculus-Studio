"""Report unresolved snapshot image references without substituting other files."""
import hashlib
from pathlib import Path


def missing_images(snapshot, directory):
    state=snapshot['state'];sources=[]
    source=state.get('generation',{}).get('source')
    if source:sources.append(source)
    sources.extend(v.get('source') for v in state.get('canvas_functions',{}).get('images',{}).values() if v.get('source'))
    for canvas in state.get('multi_output',{}).get('canvases',{}).values():
        for layer in (canvas.get('image') or {}).get('layers',[]):
            if layer.get('type')=='image' and layer.get('source'):sources.append(layer['source'])
    missing=[];seen=set();directory=Path(directory).resolve()
    for source in sources:
        key=source.get('relative','')
        if key in seen:continue
        seen.add(key)
        try:
            path=(directory/key).resolve()
            valid=bool(key) and path.is_relative_to(directory) and path.is_file() and path.stat().st_size<=25*1024*1024
            if valid and source.get('sha256'):valid=hashlib.sha256(path.read_bytes()).hexdigest()==source['sha256']
        except (OSError,ValueError):valid=False
        if not valid:missing.append(source.get('name') or key or '未命名圖片')
    return missing
