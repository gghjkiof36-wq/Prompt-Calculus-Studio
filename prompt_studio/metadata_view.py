"""Readable summaries; raw ComfyUI graphs remain available separately."""
from .snapshots import image_snapshots
from .core import output_groups, TEMPORARY_GROUP

FIELD_NAMES={"text":"提示詞","ckpt_name":"CKPT","unet_name":"Diffusion 模型","lora_name":"LoRA",
    "strength_model":"模型權重","strength_clip":"CLIP 權重","seed":"Seed","noise_seed":"Noise seed",
    "steps":"步數","cfg":"CFG","sampler_name":"採樣器","scheduler":"調度器","denoise":"Denoise",
    "ckpt":"CKPT／Diffusion","loras":"LoRA 與權重","sampler":"採樣器","notes":"備註"}

def readable_metadata(record):
    meta=record.get("metadata",{})
    lines=["（此為圖片的內嵌資料）",""]
    for node in meta.get("nodes",[]):
        kind=node.get("type","")
        if "Sampler" in kind: title="採樣器"
        elif "TextEncode" in kind: title="提示詞"
        elif "Lora" in kind: title="LoRA"
        elif "Loader" in kind: title="載入模型"
        else: title="生成設定"
        lines.append(f"{title} · 節點 {node.get('node','')}")
        for key,value in node.get("values",{}).items(): lines.append(f"{FIELD_NAMES.get(key,key)}：{value}")
        lines.append("")
    raw=meta.get("raw",{})
    if not meta.get("nodes") and isinstance(raw,dict) and isinstance(raw.get("parameters"),str): lines.append(raw["parameters"][:12000])
    bindings=image_snapshots(meta)
    if bindings:
        lines.extend(['Prompt Studio 模組快照', ''])
        for binding in bindings:
            snapshot=binding['snapshot']; state=snapshot['state']
            lines.append(f"工作區：{state['workspaces'][0]['name']} · 文字節點 {binding.get('node_id','')}")
            items={i['id']:i for i in state['items']}; modules={m['id']:m for m in state['modules']}
            for mid in output_groups(state):
                if mid==TEMPORARY_GROUP: lines.append('臨時片段：'+'、'.join(state['temporary']))
                else: lines.append(modules[mid]['name']+'：'+'、'.join(items[i]['name'] for i in state['selections'].get(mid,[])))
            lines.append('正在使用手動版本' if snapshot['manual_draft'] else '自動模組組合')
            lines.append('當次最終 Prompt：\n'+snapshot['final_prompt']+'\n')
    elif isinstance(raw,dict) and 'prompt_studio' in raw:
        lines.append('模組快照無法驗證或版本不支援；可查看原始生成資料。')
    if record.get("notes"): lines.extend(["","圖片備註",record["notes"]])
    return "\n".join(lines)
