"""Text rules and persistence, independent of the Qt user interface."""
import copy
import json
import re
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from .exclusions import filter_tags, exclusion_rules, tag_key


DEFAULT_SETTINGS = dict(ui_size=11, prompt_size=12, density="comfortable",
                        material="mica", accent="neutral", online=True,
                        acrylic_transparency=61, confirm_clear_draft=True,
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


def validate_state(state):
    if not isinstance(state, dict) or state.get("version") != 1:
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
    for item in items.values():
        if item.get("module") not in modules or not isinstance(item.get("prompt"), str) or not item["prompt"].strip():
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
    order=state.get("output_order")
    if "output_order" in state and (not isinstance(order,list) or
            any(not isinstance(mid,str) or mid not in set(modules)|{TEMPORARY_GROUP} for mid in order) or
            len(order)!=len(set(order))):
        raise ValueError("輸出群組順序無效。")
    for key, allowed in {"density":("comfortable","compact"), "material":("solid","mica","acrylic"),
                         "accent":("neutral","blue","green"), "formatter":("spaces","original"),
                         "translator":("dictionary","google")}.items():
        if settings.get(key, DEFAULT_SETTINGS[key]) not in allowed:
            raise ValueError(f"設定 {key} 無效。")
    for key in ("ui_size", "prompt_size"):
        value = settings.get(key, DEFAULT_SETTINGS[key])
        if type(value) is not int or not 9 <= value <= 22:
            raise ValueError("字體大小必須介於 9 和 22 pt。")
    count=settings.get('comfy_count',1)
    if type(count) is not int or not 1<=count<=100: raise ValueError('運行次數須介於 1–100。')
    transparency=settings.get("acrylic_transparency",DEFAULT_SETTINGS["acrylic_transparency"])
    if type(transparency) is not int or not 0 <= transparency <= 100:
        raise ValueError("Acrylic 透明度必須介於 0 和 100%。")
    for key in ("online", "artist_prefix", "confirm_clear_draft"):
        if type(settings.get(key, DEFAULT_SETTINGS[key])) is not bool:
            raise ValueError(f"設定 {key} 必須是布林值。")
    for key in ("font_family", "model_root"):
        if not isinstance(settings.get(key, DEFAULT_SETTINGS[key]), str):
            raise ValueError(f"設定 {key} 必須是文字。")
    categories = settings.get("model_categories", DEFAULT_SETTINGS["model_categories"])
    if not isinstance(categories,list) or any(not isinstance(v,str) or not v.strip() for v in categories):
        raise ValueError("模型分類格式無效。")
    for item in state["items"]:
        if not isinstance(item.get("preview",""),str): raise ValueError("預覽圖片路徑格式無效。")
    for workspace in state["workspaces"]:
        if not isinstance(workspace.get("parameters",{}),dict) or any(not isinstance(v,str) for v in workspace.get("parameters",{}).values()):
            raise ValueError("工作區建議參數格式無效。")
        history=workspace.get("history",[])
        if not isinstance(history,list) or any(not isinstance(v,dict) or not isinstance(v.get("parameters"),dict) or not isinstance(v.get("time"),(int,float)) or not isinstance(v.get("note",""),str) for v in history):
            raise ValueError("工作區參數歷史格式無效。")
    return state


TEMPORARY_GROUP = "__temporary_group__"


def output_groups(state):
    """Keep output independent; new groups enter at their sidebar position."""
    modules=[m["id"] for m in state["modules"]]
    active={mid for mid in modules if state["selections"].get(mid)}
    if state.get("temporary"): active.add(TEMPORARY_GROUP)
    if "output_order" not in state:
        order=list(modules); before=state.get("temporary_before")
        order.insert(order.index(before) if before in order else len(order),TEMPORARY_GROUP)
        return [mid for mid in order if mid in active]
    order=[mid for mid in state["output_order"] if mid in active]
    for index,mid in enumerate(modules):
        if mid in active and mid not in order: order.insert(min(index,len(order)),mid)
    if TEMPORARY_GROUP in active and TEMPORARY_GROUP not in order: order.append(TEMPORARY_GROUP)
    return order


def reorder_output(state, source, target, after=False):
    """Move groups, or entries within the same group. Never reclassify items."""
    kind,ident=source; other,other_id=target
    if source==target: return source
    if kind==other=="group":
        order=output_groups(state)
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


def item_prompt(state, item, text=None):
    """Wrap the intact source once; integer tenths avoid cumulative rounding."""
    text = item['prompt'].strip() if text is None else text.strip()
    if not text: return ''
    weight = state.get('weights', {}).get(item['id'], 10)
    return text if weight == 10 else f'({text}:{weight / 10:.1f})'


def compose_details(state):
    items = {i["id"]: i for i in state["items"]}
    groups = []; affected={}; rules=exclusion_rules(state)
    def fragment(ident,text):
        filtered,removed=filter_tags(text,rules)
        if removed:
            affected[ident]=dict(tags=list(dict.fromkeys(removed)),by=list(dict.fromkeys(name for tag in removed for name in rules.get(tag_key(tag),[]))))
        return filtered
    for mid in output_groups(state):
        if mid==TEMPORARY_GROUP:
            groups.extend(clean for i,v in enumerate(state.get('temporary',[])) if (clean:=fragment('temporary:'+str(i),v).strip()))
            continue
        fragments = [clean for i in state['selections'].get(mid,[]) if i in items and (clean:=item_prompt(state,items[i],fragment(i,items[i]['prompt'])))]
        if fragments:
            groups.append(", ".join(fragments))
    return ",\n".join(groups),affected


def build_prompt(state):
    return compose_details(state)[0]


def apply_workspace(state, workspace_id):
    w = next(w for w in state["workspaces"] if w["id"] == workspace_id)
    for mid in w["fixed"]:
        state["selections"][mid] = list(w["picks"].get(mid, []))
        for ident in state['selections'][mid]: state.setdefault('weights',{})[ident]=w.get('weights',{}).get(ident,10)
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
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.directory / "studio.sqlite3")
        self.db.execute("CREATE TABLE IF NOT EXISTS document (id INTEGER PRIMARY KEY CHECK(id=1), body TEXT NOT NULL)")
        self.db.execute("CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, body TEXT NOT NULL, saved REAL NOT NULL)")
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
        validate_state(state)
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO document VALUES (1,?)", (json.dumps(state, ensure_ascii=False),))

    def cached(self, key, max_age=None):
        row = self.db.execute("SELECT body,saved FROM cache WHERE key=?", (key,)).fetchone()
        if row and (max_age is None or time.time()-row[1] < max_age):
            return json.loads(row[0])
        return None

    def cache(self, key, records):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO cache VALUES (?,?,?)", (key,json.dumps(records),time.time()))
            self.db.execute("DELETE FROM cache WHERE key IN (SELECT key FROM cache ORDER BY saved DESC LIMIT -1 OFFSET 500)")

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
