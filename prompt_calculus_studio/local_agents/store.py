"""Versioned local registrations. A skill selection grants no execution."""
import copy
import json
from pathlib import Path
import shutil
import re
from uuid import uuid4

from ..local_ai.registry import atomic_json
from .sources import checked, digest, file_snapshot, folder_snapshot, read_bytes


class AgentStore:
    def __init__(self, data):
        self.directory = Path(data) / 'local_agents'
        self.path = self.directory / 'registry.json'
        self.reload()

    def reload(self):
        raw = read_bytes(self.path, 4 * 1024 * 1024) if self.path.exists() else b''
        value = json.loads(raw) if raw else dict(version=1, skills=[], projects=[], agents=[])
        if not isinstance(value, dict) or set(value) != {'version', 'skills', 'projects', 'agents'} or value['version'] not in (1, 2):
            raise ValueError('本地 Agent 設定版本無法讀取，原檔保留。')
        for key in ('skills', 'projects', 'agents'):
            rows = value[key]
            if not isinstance(rows, list) or len(rows) > 500 or any(not isinstance(r, dict) or not isinstance(r.get('id'), str) for r in rows):
                raise ValueError('本地 Agent 設定格式無效。')
            if len({r['id'] for r in rows}) != len(rows):
                raise ValueError('本地 Agent 設定身分重複。')
        # Fail at the data boundary rather than crashing a dialog on a missing
        # field. Loading a damaged registry never overwrites its original.
        try:
            for item in value['skills']:
                for key in ('name', 'description', 'source', 'root', 'revision', 'original'):
                    if not isinstance(item[key], str) or not item[key]:
                        raise ValueError()
                if any(type(item[key]) is not bool for key in ('enabled', 'managed', 'manual', 'invocable')):
                    raise ValueError()
                if not isinstance(item['metadata'], dict) or not isinstance(item['files'], list) or not 1 <= len(item['files']) <= 512:
                    raise ValueError()
                for entry in item['files']:
                    if (not isinstance(entry['path'], str) or not entry['path'] or
                            not isinstance(entry['sha256'], str) or len(entry['sha256']) != 64 or
                            type(entry['size']) is not int or not 0 <= entry['size'] <= 2 * 1024 * 1024):
                        raise ValueError()
            for item in value['projects']:
                if (not isinstance(item['name'], str) or not isinstance(item['cwd'], str) or
                        not Path(item['cwd']).is_absolute() or not isinstance(item['sources'], list) or len(item['sources']) > 24):
                    raise ValueError()
                self._validate_sources(item['sources'])
            for item in value['agents']:
                if (any(not isinstance(item[k], str) or not item[k] for k in ('name', 'project', 'model')) or
                        not isinstance(item['skills'], list) or len(item['skills']) > 32 or
                        any(not isinstance(k, str) for k in item['skills'])):
                    raise ValueError()
                if 'sources' in item:
                    if value['version'] < 2:
                        raise ValueError()
                    self._validate_sources(item['sources'])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError('本地 Agent 設定內容不完整，原檔保留。') from exc
        self.value, self.revision = value, digest(raw)
        return self

    @staticmethod
    def _validate_sources(sources):
        if not isinstance(sources, list) or len(sources) > 24:
            raise ValueError('規則與參考來源最多 24 份。')
        for source in sources:
            if (not isinstance(source, dict) or source['kind'] not in ('rule', 'reference') or
                    any(not isinstance(source[k], str) or not source[k] for k in ('path', 'resolved', 'sha256'))):
                raise ValueError('規則與參考來源格式無效。')

    def save(self):
        raw = read_bytes(self.path, 4 * 1024 * 1024) if self.path.exists() else b''
        if digest(raw) != self.revision:
            raise ValueError('設定已在其他頁面變更，請重新讀取。')
        if len(json.dumps(self.value, ensure_ascii=False).encode()) > 4 * 1024 * 1024:
            raise ValueError('Agent 設定超過大小上限。')
        atomic_json(self.path, self.value)
        self.revision = digest(self.path.read_bytes())

    def get(self, kind, key):
        result = next((r for r in self.value[kind] if r['id'] == key), None)
        if result is None:
            raise ValueError('此項目已不在清單中，請重新整理。')
        return copy.deepcopy(result)

    def add_skill(self, source, *, managed=False):
        item = folder_snapshot(source)
        if not managed and any(s['root'] == item['root'] and not s['managed'] for s in self.value['skills']):
            raise ValueError('此技能來源已加入。')
        identity = str(uuid4())
        original = item['source']
        if managed:
            target = self.directory / 'skills' / identity
            if target.exists() or target.parent.resolve() != target.parent.absolute():
                raise ValueError('管理副本位置無法使用。')
            target.mkdir(parents=True)
            try:
                root = Path(item['root'])
                for entry in item['files']:
                    raw = read_bytes(checked(root, entry['path']))
                    if digest(raw) != entry['sha256']:
                        raise ValueError('技能來源在複製時更改，請重試。')
                    destination = target / entry['path']
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(raw)
                item = folder_snapshot(target)
            except Exception:
                shutil.rmtree(target)  # Only this newly-created UUID folder.
                raise
        item.update(id=identity, managed=managed, original=original, enabled=True)
        self.value['skills'].append(item)
        self.save()
        return copy.deepcopy(item)

    def refresh_skill(self, identity):
        old = self.get('skills', identity)
        fresh = folder_snapshot(old['source'])
        if fresh['root'] != old['root']:
            raise ValueError('技能來源連結已更改，請重新加入新來源。')
        old.update(fresh)
        self._put('skills', old)
        return old

    def enable_skill(self, identity, enabled):
        item = self.get('skills', identity)
        item['enabled'] = bool(enabled)
        self._put('skills', item)

    def _put(self, kind, item):
        before = self.value
        candidate = copy.deepcopy(before)
        candidate[kind] = [item if r['id'] == item['id'] else r for r in candidate[kind]]
        if not any(r['id'] == item['id'] for r in candidate[kind]):
            candidate[kind].append(item)
        if kind == 'agents' and 'sources' in item:
            # Old readers reject v2 instead of silently ignoring role rules.
            candidate['version'] = 2
        self.value = candidate
        try:
            self.save()
        except (ValueError, OSError):
            self.value = before
            raise

    def save_project(self, name, cwd, sources, identity=None):
        item = self._project_value(name, cwd, sources, identity)
        self._put('projects', item)
        return item

    def _project_value(self, name, cwd, sources, identity=None):
        root = Path(cwd).expanduser().resolve(strict=True)
        if not root.is_dir() or root == Path(root.anchor) or not isinstance(name, str) or not 1 <= len(name.strip()) <= 120:
            raise ValueError('請填寫專案名稱並選擇工作目錄。')
        rules = self._capture_sources(sources)
        return dict(id=identity or str(uuid4()), name=name.strip(), cwd=str(root), sources=rules)

    @staticmethod
    def _capture_sources(sources):
        if len(sources) > 24:
            raise ValueError('規則與參考來源最多 24 份。')
        rules, seen = [], set()
        for path, kind in sources:
            item = file_snapshot(path, kind)
            if item['resolved'] not in seen:
                rules.append(item); seen.add(item['resolved'])
        return rules

    def save_agent(self, name, project, model, skills, identity=None, *, sources=None):
        project_value = self.get('projects', project)
        item = self._agent_value(name, project, model, skills, identity, sources=sources)
        self._merged_sources(project_value['sources'], item.get('sources', []))
        self._put('agents', item)
        return item

    def _agent_value(self, name, project, model, skills, identity=None, *, sources=None):
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 120 or not model:
            raise ValueError('請填寫 Agent 名稱並選擇模型。')
        if len(set(skills)) > 32:
            raise ValueError('一個 Agent 最多選擇 32 個技能。')
        for key in skills:
            self.get('skills', key)
        item = dict(id=identity or str(uuid4()), name=name.strip(), project=project,
                    model=model, skills=list(dict.fromkeys(skills)))
        previous = self.get('agents', identity) if identity else {}
        if sources is not None:
            captured = self._capture_sources(sources)
            if captured or 'sources' in previous:
                item['sources'] = captured
        elif 'sources' in previous:
            item['sources'] = previous['sources']
        return item

    @staticmethod
    def _merged_sources(common, own):
        """A request contains only common rules and its own role, never siblings."""
        result, seen = [], {}
        for source in common + own:
            key = Path(source['resolved'])
            if key in seen:
                previous = seen[key]
                if previous['sha256'] != source['sha256']:
                    raise ValueError('專案與 Agent 的來源版本不同，請重新讀取來源。')
                if source['kind'] == 'rule':
                    previous['kind'] = 'rule'
                continue
            value = copy.deepcopy(source)
            seen[key] = value; result.append(value)
        if len(result) > 24:
            raise ValueError('專案與 Agent 合計最多 24 份規則與參考。')
        return result

    def save_configuration(self, name, model, skills, project_name, cwd, sources, *, identity=None, project_identity=None):
        """Validate both drafts, then replace their shared registry once."""
        project = self._project_value(project_name, cwd, sources, project_identity)
        agent = self._agent_value(name, project['id'], model, skills, identity)
        self._merged_sources(project['sources'], agent.get('sources', []))
        before = self.value
        candidate = copy.deepcopy(before)
        for kind, item in (('projects', project), ('agents', agent)):
            candidate[kind] = [item if r['id'] == item['id'] else r for r in candidate[kind]]
            if not any(r['id'] == item['id'] for r in candidate[kind]):
                candidate[kind].append(item)
        self.value = candidate
        try:
            self.save()
        except (ValueError, OSError):
            self.value = before
            raise
        return copy.deepcopy(agent)

    def request(self, identity, text, approval):
        self.reload()
        agent = self.get('agents', identity)
        project = self.get('projects', agent['project'])
        # Preserve the accepted v1 execution contract: all effective sources
        # are explicit in this immutable request, including for old readers.
        project['sources'] = self._merged_sources(project['sources'], agent.get('sources', []))
        skills = [self.get('skills', key) for key in agent['skills']]
        explicit, selected = [], []
        for token in re.findall(r'(?<!\w)@([^\s,，。!?！？;；]+)', text):
            matches = [s for s in skills if token in (s['name'], s['id'])]
            if not matches:
                raise ValueError('這個 Agent 沒有可用的 @' + token + '。')
            if len(matches) != 1:
                raise ValueError('技能名稱重複，請用技能清單中的識別碼指定。')
            if not matches[0]['enabled'] or not matches[0]['invocable']:
                raise ValueError('指定的技能已停用或不允許手動指定。')
            explicit.append(matches[0]['id'])
        for skill in skills:
            if skill['enabled'] and (not skill['manual'] or skill['id'] in explicit):
                selected.append(skill)
        return dict(version=1, agent=agent, project=project, skills=selected,
                    explicit=explicit, approval=approval)


def pcs_rules(root, group='03-1'):
    """Explicit PCS shortcut, not traversal of every Markdown link in a vault."""
    return pcs_common_rules(root) + pcs_role_rules(root, group)


def pcs_common_rules(root):
    root = Path(root).resolve(strict=True)
    vault = root / 'obsidian工作區' / '交接管理庫' / '交接管理庫'
    rules = [root / 'AGENTS.md', vault / 'AGENTS.md', root / 'repository' / 'AGENTS.md']
    return [(str(p), 'rule') for p in rules if p.is_file()]


def pcs_role_rules(root, group):
    root = Path(root).resolve(strict=True)
    vault = root / 'obsidian工作區' / '交接管理庫' / '交接管理庫'
    parent = group.split('-')[0]
    local = vault / '組別' / group / 'AGENTS.md'
    main = vault / '組別' / parent / 'AGENTS.md'
    rules = [main]
    if local != main and local.is_file():
        rules.append(local)
    result = [(str(p), 'rule') for p in rules if p.is_file()]
    handoff = vault / '交接' / group / '入口.md'
    if handoff.is_file():
        result.append((str(handoff), 'reference'))
    return result
