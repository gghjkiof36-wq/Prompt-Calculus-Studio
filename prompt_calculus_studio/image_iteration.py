"""Finite ordered image inputs and explicit per-image prompt mapping."""
from .pnginfo import png_metadata
from .snapshots import image_snapshots


def prompt_choices(metadata):
    marker=metadata.get('raw',{}).get('prompt_studio',{})
    if not isinstance(marker,dict):return []
    choices=[]
    for text in marker.get('texts',[]):
        if isinstance(text,dict) and isinstance(text.get('text'),str):
            choices.append((f"實際文字 #{text.get('node','')} / {text.get('field','')}",('text',text.get('node'),text.get('field'))))
    for index,binding in enumerate(image_snapshots(metadata)):
        choices.append((f'來源組合 {index+1} 的最終 Prompt',('snapshot',index)))
    return choices


def image_prompt(metadata, choice):
    if not choice:
        raise ValueError('請明確指定圖片中的 Prompt 來源。')
    snapshots=image_snapshots(metadata)
    if choice[0]=='text':
        marker=metadata.get('raw',{}).get('prompt_studio',{})
        texts=marker.get('texts',[]) if isinstance(marker,dict) else []
        matches=[t['text'] for t in texts if isinstance(t,dict) and (t.get('node'),t.get('field'))==tuple(choice[1:]) and isinstance(t.get('text'),str)]
        if len(matches)!=1:raise ValueError('圖片缺少指定的 Prompt 來源；可改用整批固定 Prompt。')
        return matches[0],snapshots[0]['snapshot'] if snapshots else None
    if choice[0]=='snapshot' and len(choice)==2 and type(choice[1]) is int and 0<=choice[1]<len(snapshots):
        snapshot=snapshots[choice[1]]['snapshot']
        return snapshot['final_prompt'],snapshot
    raise ValueError('圖片缺少指定的模組快照；可改用整批固定 Prompt。')


def plan_inputs(directory, sources, mode='none', target=None, choice=None, fixed=''):
    if not 1<=len(sources)<=100:
        raise ValueError('一次請選擇 1–100 張圖片。')
    result=[]
    for source in sources:
        entry=dict(source=source)
        if mode!='none':
            if not target:raise ValueError('請選擇接收 Prompt 的工作流欄位。')
            if mode=='metadata':
                value,snapshot=image_prompt(png_metadata(directory/source['relative']),choice)
                if snapshot:entry['input_snapshot']=snapshot
            elif mode=='fixed':value=fixed
            else:raise ValueError('圖片 Prompt 來源無效。')
            entry['text']=dict(node=target[0],field=target[1],value=value)
        result.append(entry)
    return result
