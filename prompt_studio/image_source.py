"""Finite image sources and conservative positive-prompt metadata reading."""
import copy
import hashlib
import re
import uuid
from pathlib import Path
from .pnginfo import png_metadata
from .snapshots import image_snapshots

EXTENSIONS = {'.png', '.jpg', '.jpeg', '.webp', '.bmp'}


def natural_key(path):
    return [(0,int(p)) if p.isdigit() else (1,p.casefold()) for p in re.split(r'(\d+)', Path(path).name)]


def folder_files(folder):
    return sorted((p for p in Path(folder).iterdir() if p.is_file() and p.suffix.lower() in EXTENSIONS), key=natural_key)


def positive_fields(graph):
    """Trace actual positive conditioning; never concatenate negative text."""
    if not isinstance(graph,dict):return []
    starts = []
    for node in graph.values():
        inputs=node.get('inputs',{}) if isinstance(node,dict) else {}
        link = inputs.get('positive') if isinstance(inputs,dict) else None
        if isinstance(link,list) and len(link)==2:
            starts.append(str(link[0]))
    found, seen = [], set()
    while starts:
        key = starts.pop()
        if key in seen:
            continue
        seen.add(key)
        node = graph.get(key,{})
        inputs = node.get('inputs',{}) if isinstance(node,dict) else {}
        if not isinstance(inputs,dict):continue
        for field, content in inputs.items():
            if field in ('text','text_g','text_l','prompt') and isinstance(content,str):
                found.append((key,field,content))
            elif isinstance(content,list) and len(content)==2 and str(content[0]) in graph:
                starts.append(str(content[0]))
    return sorted(set(found))


def read_content(path, choice=None):
    metadata = png_metadata(path)
    raw = metadata.get('raw',{})
    fields = positive_fields(raw.get('prompt',{}))
    if choice:
        fields = [f for f in fields if list(f[:2])==list(choice)]
    texts = list(dict.fromkeys(f[2] for f in fields))
    if len(texts)>1:
        raise ValueError('圖片有多個正面提示詞來源，請在圖片來源選擇要使用的欄位。')
    positive = texts[0] if texts else None
    targets = {tuple(f[:2]) for f in fields}
    marker = raw.get('prompt_studio',{})
    if not isinstance(marker,dict):marker={}
    texts=marker.get('texts',[])
    linked = [t for t in texts if isinstance(t,dict) and isinstance(t.get('field'),str) and (str(t.get('node')),t['field']) in targets] if isinstance(texts,list) else []
    captures=marker.get('input_values',{})
    for captured in captures.values() if isinstance(captures,dict) else []:
        if not isinstance(captured,dict) or not isinstance(captured.get('origin',{}),dict):continue
        origin=captured.get('origin',{})
        saved=origin.get('composition') if captured.get('type')=='clip' and origin.get('output') in {t.get('output') for t in linked} else None
        if isinstance(saved,dict) and positive is not None and saved.get('text')==positive:
            from .flow_data import validate_value
            try:validate_value(dict(type='content',value=saved))
            except ValueError:continue
            return copy.deepcopy(saved)
    for binding in image_snapshots(metadata):
        snapshot = binding['snapshot']; state = snapshot['state']; data = state.get('multi_output')
        output_ids = {t.get('output') for t in linked if t.get('output')}
        if data:
            from .clip_flow import source
            for target in data.get('bindings',[]):
                if (str(target['node']),target['field']) in targets:
                    out = source(state,target['clip']) if 'clip' in target else target.get('output')
                    if out in data['outputs']:output_ids.add(out)
            if not output_ids and len(snapshot.get('outputs',{}))==1:
                output_ids=set(snapshot['outputs'])
            candidates = [snapshot['outputs'][k] for k in output_ids if k in snapshot.get('outputs',{})
                          and (positive is None or snapshot['outputs'][k]['final_prompt']==positive)]
            if len(candidates)!=1:continue
            compiled=candidates[0]; out=data['outputs'][compiled['output']]
            from .flow_data import canvas_ids
            roots=[copy.deepcopy(state['uses'][key]) for cid in canvas_ids(out) for key in data['canvases'][cid]['members']]
            text=compiled['final_prompt']; manual=compiled['manual_draft'] or any(data['canvases'][cid].get('raw_prompt') is not None for cid in canvas_ids(out))
        else:
            text=snapshot['final_prompt']
            if positive is not None and text!=positive:continue
            roots=[copy.deepcopy(state['uses'][key]) for key in state.get('output_order',[]) if key in state.get('uses',{})]
            manual=snapshot['manual_draft']
        if roots:
            return dict(kind='modules',roots=roots,text=text,manual_text=text if manual else None)
        if positive is None:positive=text
    if positive is None and isinstance(raw.get('parameters'),str):
        parameters=raw['parameters']
        positive=re.split(r'\nNegative prompt:|\nSteps:',parameters,maxsplit=1)[0]
    if positive is None:
        raise ValueError('這張圖片沒有可辨識的正面提示詞或 PCS 文字模塊；請選擇提示詞來源或使用其他畫布。')
    return dict(kind='raw',text=positive)


def select(state, key, index, directory):
    source = state['canvas_functions']['images'][key]
    items = source.get('items') or ([source['source']] if source.get('source') else [])
    if type(index) is not int or not 0<=index<len(items):
        raise ValueError('圖片來源沒有下一張；本批已結束，不會自動循環。')
    image = copy.deepcopy(items[index])
    path = (Path(directory)/image['relative']).resolve()
    if not path.is_relative_to(Path(directory).resolve()) or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=image['sha256']:
        raise ValueError('來源圖片遺失或內容已變更，已停止這一項。')
    source.update(source=image,index=index)
    source.pop('content',None); source.pop('content_error',None)
    try:source['content']=read_content(path,source.get('prompt_choice'))
    except (ValueError,OSError) as exc:source['content_error']=str(exc)
    from .flow_data import materialize
    materialize(state)
    return state


def set_items(state, key, items, directory, iterate=True):
    if not items:raise ValueError('沒有選擇可用圖片。')
    source=state['canvas_functions']['images'][key]
    source.update(items=copy.deepcopy(items),iterate=iterate,index=0,batch_id=uuid.uuid4().hex)
    source.pop('binding',None); source.pop('selection',None)
    return select(state,key,0,directory)
