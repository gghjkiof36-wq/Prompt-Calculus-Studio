"""Conversation paths and attachment references rendered as ordinary local notes."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import PurePosixPath
import re
from urllib.parse import quote

from .conversation_archive import checkpoint
from .markdown_html import escape, fence

ROLES = {'user': '使用者', 'assistant': '助理', 'system': '系統', 'tool': '工具', 'developer': '開發者'}
MEDIA = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.wav', '.mp3', '.ogg', '.flac', '.m4a', '.mp4', '.webm', '.ogv', '.pdf'}


def single_line(value):
    return escape(re.sub(r'[\r\n]+', ' ', str(value)))


def references(message):
    """Use only explicit pointers/attachment metadata; arbitrary IDs are not files."""
    found, seen = [], set()
    def add(value):
        if isinstance(value, str) and value and value not in seen:
            seen.add(value)
            found.append(value)
    pending = [(message.get('content'), False), (message.get('metadata', {}).get('attachments')
               if isinstance(message.get('metadata'), dict) else None, True)]
    while pending:
        value, attachment = pending.pop()
        if isinstance(value, dict):
            explicit = next((value[k] for k in ('asset_pointer', 'file_id') if isinstance(value.get(k), str)), None)
            if explicit:add(explicit)
            elif attachment:
                add(value.get('id') or value.get('path') or value.get('name'))
            for key, child in value.items():
                if key not in ('asset_pointer', 'file_id'):pending.append((child, attachment or key == 'attachments'))
        elif isinstance(value, list):
            pending.extend((child, attachment) for child in reversed(value))
        elif isinstance(value, str):
            for match in re.finditer(r'(?:file-service://|sandbox:/mnt/data/|attachment://)[^\s)<>"\]]+', value):add(match[0])
    return found


def attachment_plan(archive, item, cancel=None):
    mapping, assets, warnings = {}, {}, list(item.warnings)
    for key, node in item.raw['mapping'].items():
        checkpoint(cancel)
        message = node.get('message')
        if not message:continue
        for reference in references(message):
            member, reason = archive.attachment(reference)
            if member is None:
                warnings.append('訊息 '+single_line(key)+'：'+single_line(reference)+' — '+reason)
                mapping[reference] = None
            else:
                suffix = PurePosixPath(member).suffix.lower()
                suffix = suffix if suffix in MEDIA | {'.txt', '.csv', '.json', '.md'} else '.bin'
                target = 'assets/'+hashlib.sha256(member.encode()).hexdigest()[:24]+suffix
                assets[member] = target
                mapping[reference] = target
    return mapping, assets, warnings


def message_text(key, node, links=None):
    message = node.get('message')
    if message is None:return ''
    preview = links is None
    links = links or {}
    author = message.get('author')
    role = author.get('role', '未知角色') if isinstance(author, dict) else '未知角色'
    if not isinstance(role, str):role = '未知角色'
    label = ROLES.get(role, single_line(role))
    stamp = message.get('create_time')
    date = ''
    if isinstance(stamp, (int, float)):
        try:date = '*'+datetime.fromtimestamp(stamp, timezone.utc).strftime('%Y-%m-%d · %H:%M UTC')+'*\n\n'
        except (ValueError, OSError, OverflowError):pass
    content = message.get('content')
    sections = []
    if isinstance(content, str):sections.append(content)
    elif isinstance(content, dict):
        parts = content.get('parts')
        if isinstance(parts, list):
            for part in parts:
                if isinstance(part, str):sections.append(part)
                elif isinstance(part, dict) and isinstance(part.get('text'), str):
                    sections.append(part['text'])
                elif isinstance(part, dict) and any(k in part for k in ('asset_pointer', 'file_id')):
                    sections.append('> 附件內容見下方連結。')
                else:
                    kind = part.get('content_type', '未知內容') if isinstance(part, dict) else type(part).__name__
                    sections.append('> '+single_line(kind)+'：原始內容保留於原始對話.json。')
        elif isinstance(content.get('text'), str):sections.append(content['text'])
        else:sections.append('> '+single_line(content.get('content_type', '未知內容'))+'：原始內容保留於原始對話.json。')
    elif content is not None:sections.append('> 無法轉成文字的訊息；原始內容保留於原始對話.json。')
    body = '\n\n'.join(sections) or '（來源未提供文字）'
    for reference in references(message):
        target = links.get(reference)
        if target and PurePosixPath(target).suffix.lower() in MEDIA | {'.md'}:
            body = body.replace(reference, quote(target))
            prefix = '!' if PurePosixPath(target).suffix.lower() in MEDIA else ''
            body += '\n\n'+prefix+'['+single_line(reference)+']('+quote(target)+')'
        elif target:
            body += '\n\n> 附件已保存在 `'+target+'`；請在對話資料夾中查看。'
        else:body += '\n\n> '+('附件：' if preview else '附件未取得：')+single_line(reference)
    # IDs remain in editable source for traceability, without crowding reading mode.
    trace = json.dumps({'node': key, 'message': message.get('id'), 'parent': node.get('parent')},
                       ensure_ascii=False).replace('--', '\\u002d\\u002d').replace('>', '\\u003e')
    return '## '+label+'\n\n'+date+'<!-- '+trace+' -->\n\n'+body+'\n\n'


def split_large(text, capacity):
    """Split an exceptional oversized message without dropping Unicode or source."""
    raw = text.encode('utf-8')
    cursor = 0
    while cursor < len(raw):
        end = min(len(raw), cursor+capacity)
        while end < len(raw) and raw[end] & 0xc0 == 0x80:end -= 1
        if end == cursor:raise ValueError('分卷容量不足以保存訊息。')
        yield raw[cursor:end].decode('utf-8')
        cursor = end


def branch_files(item, links, limits, cancel=None):
    for number, leaf in enumerate(item.leaves, 1):
        part, size, pieces = 1, 0, []
        def output():
            name = f'分支-{number:03d}-{part:03d}.md'
            heading = '# '+single_line(item.title)+f' · 分支 {number} · 第 {part} 卷\n\n[返回對話索引](對話.md)\n\n'
            return name, (heading+''.join(pieces)).encode('utf-8')
        for key in item.path(leaf):
            checkpoint(cancel)
            text = message_text(key, item.raw['mapping'][key], links)
            if not text:continue
            budget = limits.volume-4096-len(item.title.encode('utf-8'))*2
            if budget < 128:raise ValueError('對話標題過長或分卷容量不足。')
            if len(text.encode('utf-8')) > budget:
                blocks = ('> 單則訊息過長，以下分段保留其 Markdown 原文。\n\n'+fence(chunk, 'markdown')+'\n\n'
                          for chunk in split_large(text, max(64, budget//3)))
            else:blocks = (text,)
            for block in blocks:
                length = len(block.encode('utf-8'))
                if length > budget:raise ValueError('訊息無法在分卷容量內保存。')
                if pieces and size+length > budget:
                    yield number, part, *output()
                    part += 1
                    size, pieces = 0, []
                pieces.append(block)
                size += length
        if not pieces:pieces.append('來源只有空節點；關係保留於原始對話.json。\n')
        yield number, part, *output()


def index_markdown(item, volumes, warnings, assets):
    lines = ['# '+single_line(item.title), '',
             '這是本機匯入的對話副本，可直接編輯下方各分支。',
             '', '原始完整紀錄保存在同一資料夾的 `原始對話.json`。',
             '可從匯入結果按「開啟資料夾」查看。', '',
             '## 對話分支', '']
    for number, part, name in volumes:
        current = ' · 來源目前路徑' if item.leaves[number-1] == item.current else ''
        lines.append(f'- [分支 {number} · 第 {part} 卷{current}]('+quote(name)+')')
    if item.current is not None and item.current not in item.leaves:
        lines.extend(('', '來源目前訊息：'+single_line(item.current)+'（位於分支中途）。'))
    if assets:
        lines.extend(('', '## 附件', ''))
        for member, target in assets.items():
            if PurePosixPath(target).suffix.lower() in MEDIA | {'.md'}:
                lines.append('- ['+single_line(member)+']('+quote(target)+')')
            else:
                lines.append('- '+single_line(member)+'：`'+target+'`（在資料夾中查看）')
    if warnings:
        lines.extend(('', '## 匯入說明', ''))
        lines.extend('- '+warning for warning in warnings)
    lines.extend(('', '原始封存未被修改；這裡只包含所選對話與可辨識的附件。', ''))
    return '\n'.join(lines).encode('utf-8')


def preview_text(item, leaf, limit=24_000):
    chunks, size = [], 0
    for key in item.path(leaf):
        text = message_text(key, item.raw['mapping'][key])
        if size+len(text) > limit:
            chunks.append(text[:max(0, limit-size)]+'\n\n（預覽至此；匯入會保存所有分支的完整內容。）')
            break
        chunks.append(text)
        size += len(text)
    return ''.join(chunks)
