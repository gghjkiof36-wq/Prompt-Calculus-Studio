"""Narrow projections of verified archive bytes. Raw carrier data is kept separately."""
from dataclasses import dataclass
from io import BytesIO
import math
from pathlib import Path
from ..conversation_archive import Limits, canonical, checkpoint, parse_archive
from ..conversation_markdown import references
from ..conversation_review import message_body, role
from ..local_documents import schema
from ..retained_files import digest


def _selected_content(content):
    if isinstance(content,str):
        return content, False
    if not isinstance(content,dict) or content.get('content_type') not in (None,'text','multimodal_text','code','execution_output'):
        return {}, True
    result = {}
    unknown = False
    if isinstance(content.get('text'),str):
        result['text'] = content['text']
    if isinstance(content.get('parts'),list):
        parts = []
        for part in content['parts']:
            if isinstance(part,str):
                parts.append(part)
            elif isinstance(part,dict) and part.get('content_type') in (None,'text','image_asset_pointer','audio_asset_pointer','video_asset_pointer'):
                parts.append({k:part[k] for k in ('text','file_id','asset_pointer') if isinstance(part.get(k),str)})
                unknown |= bool(set(part)-{'content_type','text','file_id','asset_pointer','width','height','size_bytes'})
            else:
                unknown = True
                parts.append({})
        result['parts'] = parts
    return result, unknown


@dataclass
class Story:
    key: str
    title: str
    nodes: dict
    branches: tuple
    current: str
    route: object
    notes: tuple = ()
    origin: tuple = ()

    def selected(self, leaf):
        if leaf not in self.branches:
            raise ValueError('找不到所選版本的分支。')
        return [self.nodes[key] for key in self.route(leaf)]

    def branch_for(self, key):
        if key not in self.nodes:
            raise ValueError('此版本沒有這則訊息。')
        for leaf in dict.fromkeys((self.current, *self.branches)):
            if leaf and key in self.route(leaf):
                return leaf
        raise ValueError('找不到包含這則訊息的分支。')


def _chatgpt(archive, item, cancel):
    nodes = {}
    for key, node in item.raw['mapping'].items():
        checkpoint(cancel)
        message = node.get('message') or {}
        content = message.get('content')
        safe_content, unknown = _selected_content(content)
        text = message_body({'message':{'content':safe_content}}) if message else ''
        stamp = message.get('create_time')
        stamp = stamp if type(stamp) in (int, float) and math.isfinite(stamp) and abs(stamp) < 253402300800 else None
        # Only the documented text and file-pointer fields of a selected node
        # are eligible. Unknown metadata can itself contain other branches.
        meta = message.get('metadata') if isinstance(message.get('metadata'), dict) else {}
        files = meta.get('attachments') if isinstance(meta.get('attachments'), list) else []
        explicit = [{k:v for k,v in a.items() if k in ('id','name','path','file_id','asset_pointer') and isinstance(v,str)}
                    for a in files if isinstance(a,dict)]
        refs = []
        for ref in references({'content':safe_content,'metadata':{'attachments':explicit}}):
            member, note = archive.attachment(ref)
            refs.append(dict(reference=ref, member=member, state='另存於原封存' if member else note))
        extras = (set(node) - {'id', 'parent', 'children', 'message'}
                  or set(message) - {'id', 'author', 'content', 'create_time', 'update_time', 'metadata', 'status',
                                     'recipient', 'weight', 'end_turn', 'channel'})
        if isinstance(content, dict):
            extras = extras or set(content) - {'parts', 'text', 'content_type'}
        if isinstance(message.get('metadata'), dict):
            extras = extras or set(message['metadata']) - {'attachments', 'pcs_capture_resources'}
        notes = []
        if extras or unknown or '（非文字內容保留於原始紀錄）' in text or '（來源未提供文字）' in text:
            notes.append('未解讀的欄位保留在封存原件。')
        if meta.get('pcs_capture_resources'):
            notes.append('擷取的資源僅保留來源參照。')
        mid = message.get('id')
        nodes[key] = dict(node_id=key, message_id=mid if isinstance(mid, str) else key,
                          parent=node.get('parent'), role=role(node) if message else 'root',
                          text=text, timestamp=stamp, attachments=refs, notes=notes)
    notes = list(item.warnings)
    if 'pcs_capture' in item.raw:
        notes.append('僅含擷取時可見的訊息，順序依當時畫面。')
    if sum(node['parent'] is None for node in nodes.values()) > 1:
        notes.append('此封存有多個起點，缺少的歷史未補入。')
    if set(item.raw) - {'id','conversation_id','mapping','current_node','title','create_time','update_time','pcs_capture'}:
        notes.append('來源的其他欄位保留在封存原件。')
    leaves = tuple(dict.fromkeys((*item.leaves, *((item.current,) if item.current else ()))))
    return Story(item.digest, item.title, nodes, leaves, item.current or '', item.path, tuple(notes))


def _pcs(doc, version, turns, title, cancel):
    nodes, contexts, owner, chosen = {}, {}, {}, set()
    for turn in turns:
        checkpoint(cancel)
        user = turn['id']
        refs = [dict(reference=a['id'], name=a['name'], sha256=a['sha256'], state='另存於原封存')
                for a in turn.get('attachments', ())]
        nodes[user] = dict(node_id=user, message_id=user, role='user', text=turn['text'],
                           attachments=refs, timestamp=None, notes=[])
        if not turn['attempts']:
            contexts[user] = ()
        for attempt in turn['attempts']:
            key = attempt['id']
            owner[key] = user
            contexts[key] = attempt['context']
            for ref in attempt['context']:
                chosen.add(ref['attempt_id'] or ref['user_id'])
            nodes[key] = dict(node_id=key, message_id=key, role='assistant', text=attempt['text'],
                              timestamp=None, attachments=[], notes=[], status=attempt['status'])
    def route(leaf):
        result = []
        for ref in contexts[leaf]:
            result.append(ref['user_id'])
            if ref['attempt_id']:
                result.append(ref['attempt_id'])
        if leaf in owner:
            result.append(owner[leaf])
        result.append(leaf)
        return result
    branches = tuple(key for key in contexts if key not in chosen)
    current = next(reversed(contexts), '')
    notes = ['只含保存的訊息及所選回覆，附件保留參照。']
    if current and current not in owner:
        notes.append('此問題沒有保存回覆的上下文，僅顯示該則問題。')
    return Story(version, title, nodes, branches, current, route, tuple(notes))


def stories(name, raw, cancel=None):
    path = Path(name)
    if path.suffix.casefold() == '.pcs-chat':
        doc = schema.decode(raw)
        if doc['kind'] != 'chat':
            raise ValueError('不是 PCS 對話文件。')
        payload = doc['payload']
        result = [_pcs(doc, doc['revision']['id'], payload['turns'], path.stem, cancel)]
        for i, rev in enumerate(payload.get('revisions', []), 1):
            result.append(_pcs(doc, rev['id'], rev['turns'], f'{path.stem} · 編輯紀錄 {i}', cancel))
        return result
    if path.suffix.casefold() not in {'.json', '.zip'}:
        raise ValueError('原件已保留，此格式尚未支援分支解析。')
    archive = parse_archive(BytesIO(raw), path, (), digest(raw), cancel, limits=Limits())
    return [_chatgpt(archive, item, cancel) for item in archive.conversations]
