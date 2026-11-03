"""Bounded complete skill folders and explicit rule sources; no Qt or execution."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re

MAX_FILE = 2 * 1024 * 1024
MAX_FOLDER = 16 * 1024 * 1024
MAX_FILES = 512


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def inside(path, root):
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def checked(root, relative):
    """The registered root may itself be a junction; descendants may not escape."""
    if not isinstance(relative, str) or not relative or '\x00' in relative or ':' in relative:
        raise ValueError('檔案位置無效。')
    part = Path(relative)
    if part.is_absolute() or '..' in part.parts:
        raise ValueError('檔案不在允許的資料夾內。')
    target = (root / part).resolve(strict=True)
    if not inside(target, root):
        raise ValueError('檔案連結指向允許範圍以外。')
    return target


def read_bytes(path, limit=MAX_FILE):
    from ..work_resources.source_io import read_source
    # Only host-selected, resolved sources call this helper. Existing handle
    # guards prevent ancestor replacement, reparse changes and hardlink reads.
    return read_source(path.parent, path.name, limit=limit, allow_hidden=True)[0]


def text_bytes(raw):
    if b'\x00' in raw:
        raise ValueError('此工具只讀取文字檔案。')
    try:
        return raw.decode('utf-8-sig')
    except UnicodeError:
        raise ValueError('此檔案不是 UTF-8 文字。') from None


def metadata(raw):
    """Safe YAML, unique keys, no aliases/custom constructors; preserve raw input."""
    import yaml
    text = text_bytes(raw).replace('\r\n', '\n')
    match = re.match(r'\A---\n(.*?)\n---(?:\n|$)', text, re.S)
    if not match or len(match[1]) > 32768:
        raise ValueError('SKILL.md 需要有名稱與用途的 YAML 開頭。')
    class Loader(yaml.SafeLoader):
        def compose_node(self, parent, index):
            if self.check_event(yaml.AliasEvent):
                raise ValueError('技能中繼資料不接受 YAML 別名。')
            return super().compose_node(parent, index)

        def construct_mapping(self, node, deep=False):
            result = {}
            for key_node, value_node in node.value:
                key = self.construct_object(key_node, deep=deep)
                if not isinstance(key, str) or key in result:
                    raise ValueError('技能中繼資料含重複或無效的欄位。')
                result[key] = self.construct_object(value_node, deep=deep)
            return result
    try:
        data = yaml.load(match[1], Loader=Loader)
    except (yaml.YAMLError, RecursionError) as exc:
        raise ValueError('技能中繼資料無法解析。') from exc
    if not isinstance(data, dict):
        raise ValueError('技能中繼資料必須是欄位表。')
    for key, maximum in (('name', 128), ('description', 4096)):
        if not isinstance(data.get(key), str) or not data[key].strip() or len(data[key]) > maximum:
            raise ValueError('技能缺少有效的 ' + key + '。')
    if any(c.isspace() for c in data['name']) or '@' in data['name']:
        raise ValueError('技能名稱不能包含空白或 @。')
    for key in ('disable-model-invocation', 'user-invocable'):
        if key in data and type(data[key]) is not bool:
            raise ValueError(key + ' 必須是 true 或 false。')
    # JSON converts only for display; the complete original YAML stays on disk.
    preserved = json.loads(json.dumps(data, ensure_ascii=False, default=str))
    manual = data.get('disable-model-invocation', False)
    config = data.get('metadata', {})
    if isinstance(config, dict) and config.get('manual-only') is True:
        manual = True
    return dict(name=data['name'], description=data['description'], metadata=preserved,
                manual=manual, invocable=data.get('user-invocable', True))


def folder_snapshot(source):
    logical = Path(source).expanduser().absolute()
    root = logical.resolve(strict=True)
    if not root.is_dir():
        raise ValueError('請選擇包含 SKILL.md 的資料夾。')
    entries, total, seen, entry_raw = [], 0, set(), None
    def walk(folder):
        nonlocal total, entry_raw
        physical = folder.resolve(strict=True)
        if not inside(physical, root) or physical in seen:
            raise ValueError('技能包含越界或循環的資料夾連結。')
        seen.add(physical)
        with os.scandir(folder) as scan:
            children = sorted(scan, key=lambda e: e.name.casefold())
        for child in children:
            path = Path(child.path)
            target = path.resolve(strict=True)
            if not inside(target, root):
                raise ValueError('技能附檔連結超出來源資料夾：' + path.name)
            if target.is_dir():
                walk(path)
            else:
                raw = read_bytes(target)
                total += len(raw)
                if len(entries) >= MAX_FILES or total > MAX_FOLDER:
                    raise ValueError('技能資料夾超過 512 個檔案或 16 MiB。')
                entries.append(dict(path=path.relative_to(root).as_posix(), sha256=digest(raw), size=len(raw)))
                if path == root / 'SKILL.md':
                    entry_raw = raw
    # Root is resolved once at enrollment; the logical alias is retained and
    # verified at use, so changing a junction never silently changes permission.
    walk(root)
    entry = next((f for f in entries if f['path'] == 'SKILL.md'), None)
    if entry is None:
        raise ValueError('這個資料夾沒有 SKILL.md。')
    meta = metadata(entry_raw)
    policy_file = next((f for f in entries if f['path'] == 'agents/openai.yaml'), None)
    if policy_file:
        import yaml
        raw = read_bytes(root / 'agents/openai.yaml', 32768)
        if digest(raw) != policy_file['sha256']:
            raise ValueError('技能政策在讀取時更改。')
        try:
            # No alias expansion: the policy is a small, explicit mapping.
            if any(isinstance(t, (yaml.AliasToken, yaml.AnchorToken)) for t in yaml.scan(raw)):
                raise ValueError('技能政策不接受 YAML 別名。')
            policy = yaml.safe_load(raw)
            implicit = policy.get('policy', {}).get('allow_implicit_invocation') if isinstance(policy, dict) else None
            if implicit is False:
                meta['manual'] = True
        except (yaml.YAMLError, AttributeError, RecursionError) as exc:
            raise ValueError('agents/openai.yaml 政策無法解析。') from exc
    revision = digest(json.dumps(entries, sort_keys=True).encode())
    return dict(source=str(logical), root=str(root), files=entries, revision=revision, **meta)


def file_snapshot(path, kind='rule'):
    logical = Path(path).absolute()
    physical = logical.resolve(strict=True)
    if kind not in ('rule', 'reference'):
        raise ValueError('來源用途無效。')
    raw = read_bytes(physical, 128 * 1024)
    text_bytes(raw)
    return dict(path=str(logical), resolved=str(physical), sha256=digest(raw), kind=kind)


def verify_source(item):
    logical = Path(item['path'])
    actual = logical.resolve(strict=True)
    if str(actual) != item['resolved']:
        raise ValueError('來源連結已更改，請重新確認：' + logical.name)
    raw = read_bytes(actual, 128 * 1024)
    if digest(raw) != item['sha256']:
        raise ValueError('規則來源已更新，請在 Agent 設定重新讀取：' + logical.name)
    return text_bytes(raw)
