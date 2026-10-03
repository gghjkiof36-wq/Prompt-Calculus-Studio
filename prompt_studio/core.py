"""Text rules and persistence, independent of the Qt user interface."""
import copy
import json
import re
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit
from .exclusions import filter_tags, exclusion_rules, tag_key
from .composition import validate_compositions, root_for, render, effective_nodes


DEFAULT_SETTINGS = dict(ui_size=11, prompt_size=12, density="comfortable",
                        context_sidebar_width=240, gallery_columns=12,
                        separate_selections=True, interface_mode='ask', connection_style='curve',reduce_motion=False,
                        material="mica", visual_palette="graphite", accent="neutral", online=True, search_cache=True,
                        acrylic_transparency=61, mica_transparency=61, menu_transparency=6, confirm_clear_draft=True,
                        formatter="spaces", artist_prefix=True, translator="dictionary",
                        font_family="Microsoft JhengHei UI", model_root="", model_categories=["畫風", "角色", "背景", "其他"])
DEFAULT_DICTIONARY = {
    "白色的牆壁": "white wall", "白色牆壁": "white wall", "白牆": "white wall",
    "一個女孩": "1girl", "女孩": "1girl", "紅色衣服": "red clothes",
    "簡單背景": "simple background", "看鏡頭": "looking at viewer",
    "微笑": "smile", "站立": "standing",
}


def uid():
    return uuid.uuid4().hex


def initial_state():
    modules = [dict(id=uid(), name=n, mode=mode) for n, mode in
               [("LoRA", "multiple"), ("Artist", "multiple"), ("角色", "single"),
                ("動作", "single"), ("表情", "multiple"), ("場景", "single")]]
    seed = [
        (2, "紅色洋裝女孩", "1girl, red dress", ["紅裙", "紅色衣服", "女孩"]),
        (2, "白襯衫女孩", "1girl, white shirt", ["白衣", "女孩"]),
        (3, "自然站立", "standing, hands behind back", ["站立", "手放身後"]),
        (3, "坐姿", "sitting", ["坐下", "坐著"]),
        (4, "微笑", "smile", ["微笑", "笑容"]),
        (4, "看向鏡頭", "looking at viewer", ["看鏡頭", "看向觀眾"]),
        (5, "夜晚街道", "street, night, city lights", ["夜景", "街道"]),
    ]
    items = [dict(id=uid(), module=modules[m]["id"], name=n, prompt=p, aliases=a, notes="可修改或刪除的範例") for m,n,p,a in seed]
    workspace = dict(id=uid(), name="預設工作區", fixed=[m["id"] for m in modules[:2]], picks={})
    return dict(version=1, modules=modules, items=items, workspaces=[workspace],
                workspace=workspace["id"], selections={}, draft=None, draft_base="",
                temporary=[], settings=copy.deepcopy(DEFAULT_SETTINGS), dictionary=copy.deepcopy(DEFAULT_DICTIONARY))


def local_address(value):
    if not isinstance(value,str): raise ValueError('ComfyUI 網址須為文字。')
    parts=urlsplit(value.strip())
    if parts.scheme not in ('http','https') or parts.hostname not in ('localhost','127.0.0.1','::1') or parts.username or parts.password or parts.path not in ('','/') or parts.query or parts.fragment:
        raise ValueError('請填本機 ComfyUI 網址，例如 http://127.0.0.1:8188。')
    if parts.port is not None and not 1<=parts.port<=65535: raise ValueError('ComfyUI 連接埠無效。')
    return value.strip().rstrip('/')


def validate_state(state):
    if not isinstance(state, dict) or type(state.get("version")) is not int or state.get("version") not in (1, 2, 3, 4):
        raise ValueError("不支援的備份版本。")
    ids = set()
    for name in ("modules", "items", "workspaces"):
        if not isinstance(state.get(name), list):
            raise ValueError(f"缺少 {name} 清單。")
        for entry in state[name]:
            if not isinstance(entry, dict) or not isinstance(entry.get("id"), str) or not entry["id"] or entry["id"] in ids:
                raise ValueError("項目 ID 缺少或重複。")
            ids.add(entry["id"])
            if not isinstance(entry.get("name"), str) or not entry["name"].strip():
                raise ValueError("名稱不能空白。")
    modules = {m["id"]: m for m in state["modules"]}
    items = {i["id"]: i for i in state["items"]}
    for m in modules.values():
        if m.get("mode") not in ("single", "multiple"):
            raise ValueError("選擇模式必須是 single 或 multiple。")
        if type(m.get('canvas_library',False)) is not bool:
            raise ValueError('Canvas 素材分類格式無效。')
    for item in items.values():
        if item.get("module") not in modules or not isinstance(item.get("prompt"), str) or (not item["prompt"].strip() and 'composition' not in item):
            raise ValueError("提示詞內容或所屬模組無效。")
        if not isinstance(item.get("aliases"), list) or any(not isinstance(a,str) for a in item["aliases"]) or not isinstance(item.get("notes"), str):
            raise ValueError("別名或備註格式無效。")
        if not isinstance(item.get('excludes',[]),list) or any(not isinstance(t,str) or not t.strip() for t in item.get('excludes',[])):
            raise ValueError('自動停用 Tag 清單格式無效。')
    def selections_valid(picks):
        if not isinstance(picks, dict):
            raise ValueError("選擇內容必須是物件。")
        for mid, chosen in picks.items():
            if mid not in modules or not isinstance(chosen, list) or any(not isinstance(i, str) for i in chosen):
                raise ValueError("選擇引用不存在的模組。")
            if len(chosen) != len(set(chosen)) or any(i not in items or items[i]["module"] != mid for i in chosen):
                raise ValueError("選擇內容重複或引用錯誤的項目。")
            if modules[mid]["mode"] == "single" and len(chosen) > 1:
                raise ValueError("單選模組不能保存多個選擇。")
    selections_valid(state.get("selections"))
    weights = state.get("weights", {})
    if not isinstance(weights, dict) or any(not isinstance(k, str) or type(v) is not int or not 0 <= v <= 1000 for k, v in weights.items()):
        raise ValueError("素材權重格式無效。")
    for w in state["workspaces"]:
        owners=w.get('canvas_owners',{})
        if not isinstance(owners,dict) or any(not isinstance(k,str) or (v is not None and not isinstance(v,str)) for k,v in owners.items()): raise ValueError('固定模組的畫布歸屬格式無效。')
        if not isinstance(w.get('weights',{}),dict) or any(not isinstance(k,str) or type(v) is not int or not 0<=v<=1000 for k,v in w.get('weights',{}).items()):
            raise ValueError('工作區權重格式無效。')
        if not isinstance(w.get("fixed"), list) or any(not isinstance(m,str) or m not in modules for m in w["fixed"]):
            raise ValueError("工作區固定模組無效。")
        selections_valid(w.get("picks"))
    if not state["workspaces"] or state.get("workspace") not in {w["id"] for w in state["workspaces"]}:
        raise ValueError("目前工作區不存在。")
    if state.get("draft") is not None and not isinstance(state["draft"], str):
        raise ValueError("手動稿格式錯誤。")
    if not isinstance(state.get("draft_base", ""), str) or not isinstance(state.get("settings"), dict):
        raise ValueError("設定格式錯誤。")
    if not isinstance(state.get("dictionary"), dict) or any(not isinstance(k,str) or not isinstance(v,str) or not k.strip() or not v.strip() for k,v in state["dictionary"].items()):
        raise ValueError("中文字典必須是非空白的文字對照。")
    if not isinstance(state.get("temporary", []), list) or any(not isinstance(v, str) for v in state.get("temporary", [])):
        raise ValueError("臨時片段格式無效。")
    before=state.get("temporary_before")
    if before is not None and (not isinstance(before,str) or before not in modules):
        raise ValueError("臨時片段的排序位置無效。")
    settings = state["settings"]
    if state.get('prompt_layout','compact') not in ('compact','paragraphs'):
        raise ValueError('Prompt 分段格式無效。')
    if state.get('selection_view','list') not in ('list','canvas'): raise ValueError('組合介面無效。')
    drafts=state.get('view_drafts',{})
    if not isinstance(drafts,dict) or any(k not in ('list','canvas','shared') or not isinstance(v,dict) or (v.get('draft') is not None and not isinstance(v['draft'],str)) or not isinstance(v.get('draft_base',''),str) for k,v in drafts.items()):
        raise ValueError('介面手動稿格式無效。')
    if not isinstance(state.get('uses',{}),dict): raise ValueError('模組使用內容格式無效。')
    order=state.get("output_order")
    if "output_order" in state and (not isinstance(order,list) or
            any(not isinstance(mid,str) or mid not in set(modules)|set(state.get('uses',{}))|{TEMPORARY_GROUP} for mid in order) or
            len(order)!=len(set(order))):
        raise ValueError("輸出群組順序無效。")
    for key, allowed in {"density":("comfortable","compact"), "material":("solid","mica","acrylic"),
                         "accent":("neutral","blue","green"), "visual_palette":('graphite','mist','paper'), "formatter":("spaces","original"),
                         "translator":("dictionary","google"), "interface_mode":('ask','list','canvas'),
                         "connection_style":('curve','straight','orthogonal')}.items():
        if settings.get(key, DEFAULT_SETTINGS[key]) not in allowed:
            raise ValueError(f"設定 {key} 無效。")
    for key in ("ui_size", "prompt_size"):
        value = settings.get(key, DEFAULT_SETTINGS[key])
        if type(value) is not int or not 9 <= value <= 22:
            raise ValueError("字體大小必須介於 9 和 22 pt。")
    sidebar_width=settings.get('context_sidebar_width',DEFAULT_SETTINGS['context_sidebar_width'])
    if type(sidebar_width) is not int or not 220<=sidebar_width<=480:
        raise ValueError('側欄寬度必須介於 220 和 480。')
    gallery_columns=settings.get('gallery_columns',DEFAULT_SETTINGS['gallery_columns'])
    if type(gallery_columns) is not int or not 8<=gallery_columns<=20:
        raise ValueError('圖片橫排數量必須介於 8 和 20。')
    count=settings.get('comfy_count',1)
    if 'comfy_url' in settings: local_address(settings['comfy_url'])
    if type(settings.get('comfy_enabled',False)) is not bool: raise ValueError('ComfyUI 連線設定必須是布林值。')
    if type(count) is not int or not 1<=count<=100: raise ValueError('運行次數須介於 1–100。')
    for material in ('acrylic','mica'):
        transparency=settings.get(material+'_transparency',DEFAULT_SETTINGS[material+'_transparency'])
        if type(transparency) is not int or not 0 <= transparency <= 100:
            raise ValueError("透明度必須介於 0 和 100%。")
    menu_transparency=settings.get('menu_transparency',DEFAULT_SETTINGS['menu_transparency'])
    if type(menu_transparency) is not int or not 0<=menu_transparency<=40:
        raise ValueError('選單透明度必須介於 0 和 40%。')
    for key in ("online", "artist_prefix", "confirm_clear_draft", "separate_selections", "search_cache"):
        if type(settings.get(key, DEFAULT_SETTINGS[key])) is not bool:
            raise ValueError(f"設定 {key} 必須是布林值。")
    palette=settings.get('canvas_palette',{})
    if not isinstance(palette,dict): raise ValueError('素材面板尺寸無效。')
    for key,length,minimum in (('size',2,1),('columns',3,0)):
        if key in palette and (not isinstance(palette[key],list) or len(palette[key])!=length or any(type(v) is not int or not minimum<=v<=100000 for v in palette[key])):
            raise ValueError('素材面板尺寸無效。')
    position=settings.get('execution_bar_position',[.5,1.])
    if not isinstance(position,list) or len(position)!=2 or any(type(v) not in (int,float) or not 0<=v<=1 for v in position): raise ValueError('執行列位置無效。')
    for key in ("font_family", "model_root"):
        if not isinstance(settings.get(key, DEFAULT_SETTINGS[key]), str):
            raise ValueError(f"設定 {key} 必須是文字。")
    categories = settings.get("model_categories", DEFAULT_SETTINGS["model_categories"])
    if not isinstance(categories,list) or any(not isinstance(v,str) or not v.strip() for v in categories):
        raise ValueError("模型分類格式無效。")
    scopes=settings.get('model_category_types',{})
    if not isinstance(scopes,dict) or any(not isinstance(k,str) or not isinstance(v,list) or any(not isinstance(t,str) for t in v) for k,v in scopes.items()):
        raise ValueError('模型分類適用類型格式無效。')
    for item in state["items"]:
        if not isinstance(item.get("preview",""),str): raise ValueError("預覽圖片路徑格式無效。")
    for workspace in state["workspaces"]:
        if not isinstance(workspace.get("parameters",{}),dict) or any(not isinstance(v,str) for v in workspace.get("parameters",{}).values()):
            raise ValueError("工作區建議參數格式無效。")
        history=workspace.get("history",[])
        if not isinstance(history,list) or any(not isinstance(v,dict) or not isinstance(v.get("parameters"),dict) or not isinstance(v.get("time"),(int,float)) or not isinstance(v.get("note",""),str) for v in history):
            raise ValueError("工作區參數歷史格式無效。")
    validate_compositions(state)
    if 'generation' in state:
        from .generation import validate_generation
        validate_generation(state['generation'])
    if 'canvas_functions' in state:
        from .generation import validate_canvas_functions
        validate_canvas_functions(state['canvas_functions'])
    if 'multi_output' in state:
        from .multi_output import validate_multi
        if state['version']<4: raise ValueError('多畫布需要第 4 版資料格式。')
        validate_multi(state)
    if 'workspace_scenes' in state:
        from .workspace_scene import validate as validate_scenes
        validate_scenes(state)
    return state


TEMPORARY_GROUP = "__temporary_group__"


def separate_selections(state):
    # Missing on a historical snapshot means the original shared composition.
    return state['settings'].get('separate_selections',False)


def activate_selection_view(state,view):
    previous=state.get('selection_view','list')
    if 'multi_output' in state and previous!=view:
        from .multi_output import capture_current,active_output
        capture_current(state); state['selection_view']=view; output=active_output(state)
        if output:
            saved=output.get('list_draft',{}) if view=='list' and separate_selections(state) else output
            state.update(draft=saved.get('draft'),draft_base=saved.get('draft_base',''))
        return
    if previous!=view and separate_selections(state):
        drafts=state.setdefault('view_drafts',{})
        drafts[previous]=dict(draft=state['draft'],draft_base=state.get('draft_base',''))
        state.update(copy.deepcopy(drafts.get(view,dict(draft=None,draft_base=''))))
    state['selection_view']=view


def set_selection_separation(state,enabled):
    if enabled==separate_selections(state): return
    mode=state.get('selection_view','list')
    drafts=state.setdefault('view_drafts',{})
    current=dict(draft=state['draft'],draft_base=state.get('draft_base',''))
    drafts['shared' if enabled else mode]=current
    target=drafts.get(mode,dict(draft=None,draft_base='')) if enabled else drafts.get('shared',current)
    state.update(copy.deepcopy(target))
    state['settings']['separate_selections']=enabled


def output_groups(state, include_hidden=False):
    """Keep output independent; new groups enter at their sidebar position."""
    modules=[m["id"] for m in state["modules"]]
    active={mid for mid in modules if state["selections"].get(mid)}
    uses = list(state.get('uses', {})); active.update(uses)
    if state.get("temporary"): active.add(TEMPORARY_GROUP)
    def visible(order):
        if not include_hidden and 'multi_output' in state and state.get('selection_view')=='canvas':
            from .multi_output import active_output
            output=active_output(state)
            canvas=state['multi_output']['canvases'].get(output['canvas'] if output else None,{})
            members=[key for key in canvas.get('members',[]) if key in active]
            return members if separate_selections(state) else members+[key for key in order if key not in state.get('uses',{})]
        if include_hidden or not separate_selections(state): return order
        canvas=state.get('selection_view','list')=='canvas'
        return [key for key in order if (key in state.get('uses',{}))==canvas]
    if "output_order" not in state:
        order=list(modules); before=state.get("temporary_before")
        order.insert(order.index(before) if before in order else len(order),TEMPORARY_GROUP)
        return visible([mid for mid in order if mid in active] + uses)
    order=[mid for mid in state["output_order"] if mid in active]
    for index,mid in enumerate(modules):
        if mid in active and mid not in order: order.insert(min(index,len(order)),mid)
    if TEMPORARY_GROUP in active and TEMPORARY_GROUP not in order: order.append(TEMPORARY_GROUP)
    order.extend(ident for ident in uses if ident not in order)
    return visible(order)


def reorder_output(state, source, target, after=False):
    """Move groups, or entries within the same group. Never reclassify items."""
    kind,ident=source; other,other_id=target
    if source==target: return source
    if kind in ('group','use') and other in ('group','use'):
        if 'multi_output' in state and kind==other=='use':
            from .multi_output import owner
            canvas=owner(state,ident)
            if canvas is None or canvas!=owner(state,other_id): raise ValueError('請在同一畫布內調整輸出排序。')
            members=state['multi_output']['canvases'][canvas]['members']
            members.remove(ident); members.insert(members.index(other_id)+int(after),ident)
            return source
        order=output_groups(state,include_hidden=True)
        if ident not in order or other_id not in order: raise ValueError("排序群組不存在。")
        order.remove(ident); order.insert(order.index(other_id)+int(after),ident)
        state["output_order"]=order
        return source
    if kind==other=="temporary":
        entries=state["temporary"]
        if not (type(ident) is int and type(other_id) is int and 0<=ident<len(entries) and 0<=other_id<len(entries)):
            raise ValueError("臨時片段不存在。")
        destination=other_id+int(after)-int(ident<other_id)
        value=entries.pop(ident); entries.insert(destination,value)
        return (kind,destination)
    if kind==other=="item":
        items={i["id"]:i for i in state["items"]}
        if ident not in items or other_id not in items or items[ident]["module"]!=items[other_id]["module"]:
            raise ValueError("提示詞請在原本的模組內排序。")
        entries=state["selections"].get(items[ident]["module"],[])
        if ident not in entries or other_id not in entries: raise ValueError("排序項目未選取。")
        entries.remove(ident); entries.insert(entries.index(other_id)+int(after),ident)
        return source
    raise ValueError("群組可上下排序；提示詞請在同一群組內移動。")


def item_prompt(state, item, text=None, *, grouped=False):
    """Wrap the intact source once; integer tenths avoid cumulative rounding."""
    text = item['prompt'].strip() if text is None else text.strip()
    if not text: return ''
    weight = state.get('weights', {}).get(item['id'], 10)
    return text if weight == 10 and not grouped else f'({text}:{weight / 10:.1f})'


def compose_details(state):
    items = {i["id"]: i for i in state["items"]}
    groups = []; paragraphs=[]; affected={}; rules={}; canvas_groups=False
    for mid in output_groups(state):
        roots = ([state['uses'][mid]] if mid in state.get('uses', {}) else
                 [root_for(state, items[i]) for i in state['selections'].get(mid, []) if i in items])
        for root in roots:
            for part in effective_nodes(root):
                for tag in part['excludes']:
                    rules.setdefault(tag_key(tag), []).append(part['name'])
    def fragment(ident,text):
        filtered,removed=filter_tags(text,rules)
        if removed:
            prior=affected.setdefault(ident,dict(tags=[],by=[]))
            prior['tags']=list(dict.fromkeys(prior['tags']+removed))
            prior['by']=list(dict.fromkeys(prior['by']+[name for tag in removed for name in rules.get(tag_key(tag),[])]))
        return filtered
    for mid in output_groups(state):
        if mid in state.get('uses', {}):
            clean = render(state['uses'][mid], lambda text: fragment(mid, text))
            if clean: groups.append(clean); paragraphs.append(clean); canvas_groups = True
            continue
        if mid==TEMPORARY_GROUP:
            temporary=[clean for i,v in enumerate(state.get('temporary',[])) if (clean:=fragment('temporary:'+str(i),v).strip())]
            groups.extend(temporary); paragraphs.extend(temporary)
            continue
        fragments = []
        for ident in state['selections'].get(mid, []):
            if ident not in items: continue
            root = root_for(state, items[ident]); grouped = root.get('grouped', False)
            text = render(root, lambda text: fragment(ident, text), grouped=False)
            clean = item_prompt(state, items[ident], text, grouped=grouped)
            if clean:
                fragments.append(clean); paragraphs.append(clean); canvas_groups = canvas_groups or grouped
        if fragments:
            groups.append(", ".join(fragments))
    if canvas_groups and state.get('prompt_layout')=='paragraphs':
        return ",\n\n".join(paragraphs),affected
    return (", " if canvas_groups else ",\n").join(groups),affected


def build_prompt(state):
    return compose_details(state)[0]


def apply_workspace(state, workspace_id):
    if 'workspace_scenes' in state:
        from .workspace_scene import switch
        return switch(state,workspace_id)
    return apply_workspace_legacy(state,workspace_id)


def apply_workspace_legacy(state, workspace_id):
    w = next(w for w in state["workspaces"] if w["id"] == workspace_id)
    prior_owners={key:cid for cid,c in state.get('multi_output',{}).get('canvases',{}).items() for key in c['members']}
    if 'uses' in state or 'uses' in w:
        from .composition import remove_usage
        for ident, root in list(state.get('uses', {}).items()):
            if root.get('source_module') in w['fixed'] or ident in w.get('fixed_uses',[]): remove_usage(state, ident)
        state.setdefault('uses', {}).update({k:copy.deepcopy(v) for k,v in w.get('uses', {}).items() if v.get('source_module') in w['fixed'] or k in w.get('fixed_uses',[])})
    if 'multi_output' in state:
        from .multi_output import reconcile,assign,active_output,owner
        reconcile(state); output=active_output(state); current=output.get('canvas') if output else None
        saved=w.get('canvas_owners',{})
        for key,root in w.get('uses',{}).items():
            if key not in state['uses'] or (root.get('source_module') not in w['fixed'] and key not in w.get('fixed_uses',[])): continue
            target=saved.get(key,prior_owners.get(key,current))
            if target not in state['multi_output']['canvases']: target=None
            if owner(state,key)!=target: assign(state,key,target)
    for mid in w["fixed"]:
        fixed_items = {i['id'] for i in state['items'] if i['module'] == mid}
        for ident in fixed_items:
            state.get('instances', {}).pop(ident, None)
        state["selections"][mid] = list(w["picks"].get(mid, []))
        for ident in state['selections'][mid]: state.setdefault('weights',{})[ident]=w.get('weights',{}).get(ident,10)
        for ident in state['selections'][mid]:
            if ident in w.get('instances', {}):
                state.setdefault('instances', {})[ident] = copy.deepcopy(w['instances'][ident])
    state["workspace"] = workspace_id


@dataclass(frozen=True)
class Token:
    start: int
    end: int
    query: str
    artist: bool


def current_token(text, position):
    """Complete one comma-delimited fragment; leave unknown syntax untouched."""
    start = max(text.rfind(c, 0, position) for c in (",", "，", "\n")) + 1
    while start < position and text[start].isspace():
        start += 1
    ends = [p for c in (",", "，", "\n") if (p := text.find(c, position)) >= 0]
    end = min(ends) if ends else len(text)
    segment = text[start:end].strip()
    query = text[start:position].strip()
    if not query or len(query) > 160 or any(c in segment for c in "()<>:\\[]{}"):
        return None
    artist = query.startswith("@")
    if artist:
        query = query[1:].strip()
    if len(query) < (1 if contains_chinese(query) else 2):
        return None
    return Token(start, end, query, artist)


def contains_chinese(text):
    return bool(re.search(r"[\u3400-\u9fff]", text))


def insertion(text, token, value):
    """Return replacement span/text. Consume an existing separator only once."""
    end = token.end
    if end < len(text) and text[end] in ",，":
        end += 1
        while end < len(text) and text[end] in " \t":
            end += 1
    return token.start, end, value.rstrip(",， ") + ", "


def format_candidate(candidate, settings, artist=False):
    value = candidate["value"]
    if candidate["source"] == "Danbooru":
        if settings.get("formatter", "spaces") == "spaces" and not value.startswith("score_"):
            value = value.replace("_", " ")
        value = value.replace("(", r"\(").replace(")", r"\)")
    if candidate.get("category") == 1 and (artist or settings.get("artist_prefix", True)):
        value = "@" + value.lstrip("@")
    return value


class Storage:
    CACHE_LIMIT=5000
    CACHE_BYTES=16*1024*1024
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.directory / "studio.sqlite3")
        self.db.execute("CREATE TABLE IF NOT EXISTS document (id INTEGER PRIMARY KEY CHECK(id=1), body TEXT NOT NULL)")
        self.db.execute("CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, body TEXT NOT NULL, saved REAL NOT NULL)")
        # Keep the historical cache table's three-column shape compatible.
        self.db.execute('CREATE TABLE IF NOT EXISTS cache_access (key TEXT PRIMARY KEY, accessed REAL NOT NULL)')
        self.cache_epoch=0; self.cache_enabled=True
        self.db.execute("CREATE TABLE IF NOT EXISTS usage (workspace TEXT, tag TEXT, count INTEGER, last REAL, PRIMARY KEY(workspace,tag))")
        self.db.execute("CREATE TABLE IF NOT EXISTS accepted (workspace TEXT, tag TEXT, body TEXT, last REAL, PRIMARY KEY(workspace,tag))")
        self.db.commit()

    def load(self):
        row = self.db.execute("SELECT body FROM document WHERE id=1").fetchone()
        state = validate_state(json.loads(row[0])) if row else initial_state()
        state["settings"] = {**copy.deepcopy(DEFAULT_SETTINGS), **state["settings"]}
        state.setdefault("temporary", [])
        return state

    def save(self, state):
        if 'multi_output' in state:
            from .multi_output import capture_current
            capture_current(state)
        from .workspace_scene import capture
        capture(state)
        validate_state(state)
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO document VALUES (1,?)", (json.dumps(state, ensure_ascii=False),))

    def load_current(self, *, multi=False):
        """Desktop load boundary; keep raw load for historical inspection/export."""
        from .state_loading import prepare_state
        original=self.load(); candidate=prepare_state(original,multi=multi)
        if candidate!=original and self.db.execute('SELECT 1 FROM document WHERE id=1').fetchone():
            self.backup(); self.save(candidate)
        return candidate

    def commit_workflow_update(self, state, workflow_id, operation):
        """Return a new document only after graph, revision and receipt commit."""
        from .generation import accept_effective_profile
        profile=next((p for p in state.get('generation',{}).get('profiles',[]) if p['id']==workflow_id),None)
        if profile is None: raise ValueError('工作流不存在。')
        scope=operation.get('scope',{}) if isinstance(operation,dict) else {}
        library=str(uuid.uuid5(uuid.NAMESPACE_URL,str((self.directory/'studio.sqlite3').resolve()).casefold()))
        if scope.get('library')!=library or scope.get('workspace')!=state['workspace']:
            raise ValueError('更新不屬於目前資料庫或工作區。')
        candidate=copy.deepcopy(state)
        updated,receipt=accept_effective_profile(profile,operation)
        candidate['generation']['profiles']=[updated if p['id']==workflow_id else p for p in candidate['generation']['profiles']]
        if 'multi_output' in candidate:
            from .multi_output import capture_current
            capture_current(candidate)
        validate_state(candidate)
        # The persisted receipt and graph are one document row. Lock before
        # checking the saved version so another connection cannot win the gap.
        self.db.execute('BEGIN IMMEDIATE')
        try:
            row=self.db.execute('SELECT body FROM document WHERE id=1').fetchone()
            saved=json.loads(row[0]) if row else {}
            prior=next((p for p in saved.get('generation',{}).get('profiles',[]) if p['id']==workflow_id),None)
            if (saved.get('workspace')!=state['workspace'] or prior is None
                    or prior.get('pcs_effective')!=profile.get('pcs_effective')
                    or prior.get('graph')!=profile.get('graph')):
                raise ValueError('保存版本已變更，請先對帳，未覆寫工作流。')
            self.db.execute('INSERT OR REPLACE INTO document VALUES (1,?)',(json.dumps(candidate,ensure_ascii=False),))
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        return candidate,receipt

    def cached(self, key, max_age=None):
        if not self.cache_enabled: return None
        row = self.db.execute("SELECT body,saved FROM cache WHERE key=?", (key,)).fetchone()
        if row:
            records=json.loads(row[0]); age=time.time()-row[1]
            if (not records and age>=3600) or (max_age is not None and age>=max_age): return None
            with self.db: self.db.execute('INSERT OR REPLACE INTO cache_access VALUES (?,?)',(key,time.time()))
            return records
        return None

    def cache_fresh(self,key):
        if not self.cache_enabled: return False
        row=self.db.execute('SELECT body,saved FROM cache WHERE key=?',(key,)).fetchone()
        return bool(row and time.time()-row[1]<(86400 if json.loads(row[0]) else 3600))

    def set_cache_enabled(self,enabled):
        if self.cache_enabled!=enabled: self.cache_epoch+=1; self.cache_enabled=enabled

    def clear_cache(self):
        self.cache_epoch+=1
        with self.db:
            self.db.execute('DELETE FROM cache'); self.db.execute('DELETE FROM cache_access')

    def cache(self, key, records):
        if not self.cache_enabled: return
        body=json.dumps(records[:12],ensure_ascii=False)
        if len(body.encode('utf-8'))>self.CACHE_BYTES: return
        with self.db:
            now=time.time()
            self.db.execute("INSERT OR REPLACE INTO cache (key,body,saved) VALUES (?,?,?)", (key,body,now))
            self.db.execute('INSERT OR REPLACE INTO cache_access VALUES (?,?)',(key,now))
            # Google keeps its former 500-query budget; the expanded budget is
            # for the existing Danbooru lookup. Never prefetch unqueried data.
            self.db.execute("DELETE FROM cache WHERE key IN (SELECT c.key FROM cache c LEFT JOIN cache_access a ON a.key=c.key WHERE c.key LIKE '[\"google\",%' ORDER BY COALESCE(a.accessed,c.saved) DESC LIMIT -1 OFFSET 500)")
            count,size=self.db.execute('SELECT COUNT(*),COALESCE(SUM(length(CAST(body AS BLOB))),0) FROM cache').fetchone()
            if count>self.CACHE_LIMIT or size>self.CACHE_BYTES:
                remove=[]
                for old,weight in self.db.execute('SELECT c.key,length(CAST(c.body AS BLOB)) FROM cache c LEFT JOIN cache_access a ON a.key=c.key ORDER BY COALESCE(a.accessed,c.saved),c.key'):
                    if count<=self.CACHE_LIMIT and size<=self.CACHE_BYTES: break
                    remove.append((old,)); count-=1; size-=weight
                self.db.executemany('DELETE FROM cache WHERE key=?',remove)
            self.db.execute('DELETE FROM cache_access WHERE key NOT IN (SELECT key FROM cache)')

    def use(self, workspace, tag, candidate=None):
        with self.db:
            self.db.execute("INSERT INTO usage VALUES (?,?,1,?) ON CONFLICT(workspace,tag) DO UPDATE SET count=count+1,last=excluded.last",(workspace,tag,time.time()))
            if candidate:
                self.db.execute("INSERT OR REPLACE INTO accepted VALUES (?,?,?,?)",(workspace,tag,json.dumps(candidate,ensure_ascii=False),time.time()))
                self.db.execute("DELETE FROM accepted WHERE rowid IN (SELECT rowid FROM accepted WHERE workspace=? ORDER BY last DESC LIMIT -1 OFFSET 2000)",(workspace,))

    def familiar(self, workspace, query, artist=False):
        query=query.casefold().replace(" ","_").replace("\\","\\\\").replace("%",r"\%").replace("_",r"\_")
        rows=self.db.execute("SELECT a.body FROM accepted a JOIN usage u ON a.workspace=u.workspace AND a.tag=u.tag WHERE a.workspace=? AND replace(lower(a.tag),' ','_') LIKE ? ESCAPE '\\' ORDER BY u.count DESC,a.last DESC LIMIT 12",(workspace,query+"%"))
        return [r for row in rows if (r:=json.loads(row[0])) and (not artist or r.get("category")==1)]

    def usage(self, workspace):
        return {row[0]: (row[1],row[2]) for row in self.db.execute("SELECT tag,count,last FROM usage WHERE workspace=?",(workspace,))}

    def backup(self):
        name = self.directory / ("before-import-"+str(time.time_ns())+".sqlite3")
        target=sqlite3.connect(name)
        try:
            self.db.backup(target)
        finally:
            target.close()
        return name

    def close(self):
        self.db.close()
