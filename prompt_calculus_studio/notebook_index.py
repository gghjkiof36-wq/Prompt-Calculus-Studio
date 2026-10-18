"""Immutable, root-scoped note inventory and bounded search expressions."""
from dataclasses import dataclass
from pathlib import Path
import re
from .markdown_links import note_paths, Notes, links, prose_mask
from .markdown_properties import frontmatter
from .markdown_commands import heading_sections


@dataclass(frozen=True)
class Note:
    path: Path
    relative: str
    text: str
    stamp: tuple
    properties: dict
    tags: tuple
    aliases: tuple
    headings: tuple
    links: tuple
    footnotes: tuple


def parse_note(root, path, text, stamp=()):
    _, entries, _ = frontmatter(text)
    properties = {p.name: p.value for p in entries if p.editable}
    masked = prose_mask(text)
    tag_value=properties.get('tags', [])
    tags = set(tag_value if isinstance(tag_value,list) else [tag_value] if isinstance(tag_value,str) else [])
    tags.update(m.group(1) for m in re.finditer(r'(?<![\w\\])#([^\s#.,，。!?！？:：;；\[\]{}()<>]+)', masked)
                if not m.group(1).isdigit())
    footnotes = tuple((m.group(1), m.start(), m.group(2)) for m in
                     re.finditer(r'(?m)^\[\^([^\]\n]+)\]:[ \t]*(.*)', masked))
    return Note(path, path.relative_to(root).as_posix(), text, stamp, properties,
                tuple(sorted(str(t).lstrip('#') for t in tags)),
                tuple(properties.get('aliases', [])) if isinstance(properties.get('aliases', []),list) else (str(properties['aliases']),),
                tuple(heading_sections(text)), tuple(links(text)), footnotes)


def inventory(root, previous=None, overrides=None, cancelled=None):
    root = Path(root); previous = previous or {}; overrides = overrides or {}
    records, errors = {}, []
    for path in note_paths(root):
        if cancelled and cancelled.is_set(): return {}, []
        try:
            info = path.stat(); stamp = (info.st_mtime_ns, info.st_size, info.st_ino)
            text = overrides.get(str(path))
            old = previous.get(str(path))
            if old and old.stamp == stamp and (text is None and str(path) not in overrides
                                             and old.stamp[-1:] != ('draft',)):
                records[str(path)] = old; continue
            if text is not None: stamp += ('draft',)
            else: text = Notes.disk(path)
            records[str(path)] = parse_note(root, path, text, stamp)
        except (OSError, ValueError, UnicodeError) as error:
            errors.append(path.name + '：' + str(error))
    return records, errors


def tokenize(query):
    if len(query) > 4096: raise ValueError('搜尋條件過長。')
    result = []; i = 0
    while i < len(query):
        if query[i].isspace(): i += 1; continue
        if query[i] in '()-': result.append(query[i]); i += 1; continue
        start = i; quote = ''; bracket = query[i] == '['; value = ''; literal=query[i]=='"'
        while i < len(query):
            c = query[i]
            if quote:
                if c == '\\' and i+1 < len(query): i += 1; value += query[i]
                elif c == quote: quote = ''
                else: value += c
            elif c == '"': quote = c
            elif bracket:
                value += c
                if c == ']': i += 1; break
            elif c.isspace() or c in '()': break
            else: value += c
            i += 1
        if quote or bracket and not value.endswith(']'): raise ValueError('搜尋條件的引號或括號尚未結束。')
        if i == start: raise ValueError('搜尋條件無法辨識。')
        result.append(('literal',value) if literal else value)
    return result


class Query:
    def __init__(self, query, case=False):
        self.tokens = tokenize(query); self.at = 0; self.case = case; self.words = []
        self.tree = self.expression() if self.tokens else ('all',)
        if self.at != len(self.tokens): raise ValueError('搜尋條件的括號不相符。')

    def peek(self): return self.tokens[self.at] if self.at < len(self.tokens) else None

    def expression(self, depth=0):
        if depth > 20: raise ValueError('搜尋條件的巢狀層級過多。')
        groups = [self.conjunction(depth)]
        while self.peek() == 'OR':
            self.at += 1; groups.append(self.conjunction(depth))
        return ('or', groups)

    def conjunction(self, depth):
        children = []
        while self.peek() is not None and self.peek() not in (')', 'OR'):
            if self.peek() == 'AND':
                if not children:raise ValueError('搜尋條件缺少文字。')
                self.at += 1
                if self.peek() in (None,'AND','OR',')'):raise ValueError('搜尋條件缺少文字。')
            children.append(self.term(depth))
        if not children: raise ValueError('搜尋條件缺少文字。')
        return ('and', children)

    def term(self, depth):
        if depth>20:raise ValueError('搜尋條件的巢狀層級過多。')
        token = self.peek(); self.at += 1
        if isinstance(token,tuple):
            self.words.append(token[1]);return ('term','content',token[1])
        if token == '-': return ('not', self.term(depth+1))
        if token == '(':
            node = self.expression(depth+1)
            if self.peek() != ')': raise ValueError('搜尋條件的括號不相符。')
            self.at += 1; return node
        if token is None or token in (')', 'OR'): raise ValueError('搜尋條件缺少文字。')
        if token.startswith('['):
            name, _, value = token[1:-1].partition(':')
            return ('property', name.strip(), value.strip(), ':' in token)
        field, sep, value = token.partition(':')
        if not sep: field, value = 'content', token
        if field not in ('content', 'path', 'file', 'tag', 'match-case', 'ignore-case'):
            raise ValueError('不支援的搜尋條件：' + field)
        if not value: raise ValueError('搜尋條件缺少值：' + field)
        if field in ('content', 'match-case', 'ignore-case'): self.words.append(value)
        return ('term', field, value)

    def matches(self, note):
        def contains(value, query, case=None):
            value = str(value)
            return query in value if (self.case if case is None else case) else query.casefold() in value.casefold()
        def visit(node):
            kind = node[0]
            if kind == 'all': return True
            if kind == 'and': return all(visit(n) for n in node[1])
            if kind == 'or': return any(visit(n) for n in node[1])
            if kind == 'not': return not visit(node[1])
            if kind == 'property':
                _, key, value, explicit = node
                if key not in note.properties: return False
                actual = note.properties[key]
                if not explicit: return True
                if value == 'null': return actual is None
                number = re.fullmatch(r'([<>]=?|=)(-?\d+(?:\.\d+)?)', value)
                if number and isinstance(actual, (int, float)) and not isinstance(actual, bool):
                    op, target = number.groups(); target = float(target)
                    return {'<': actual < target, '>': actual > target, '<=': actual <= target,
                            '>=': actual >= target, '=': actual == target}[op]
                if isinstance(actual, bool): actual = str(actual).lower()
                return any(contains(v, value) for v in actual) if isinstance(actual, list) else contains(actual, value)
            _, field, value = node
            if field == 'tag':
                value = value.lstrip('#').casefold()
                return any(t.casefold() == value or t.casefold().startswith(value + '/') for t in note.tags)
            text = note.relative if field == 'path' else note.path.name if field == 'file' else note.text
            return contains(text, value, True if field == 'match-case' else False if field == 'ignore-case' else None)
        return visit(self.tree)

    def position(self, note):
        hits = [m.start() for word in self.words for m in [re.search(re.escape(word), note.text, 0 if self.case else re.I)] if m]
        if hits:return min(hits)
        start=frontmatter(note.text)[0]
        for line in note.text[start:].splitlines(keepends=True):
            if line.strip() and not re.match(r'^\s*#',line):return start
            start+=len(line)
        return 0


def snippet(text, position, length=110):
    start = max(text.rfind('\n', 0, position)+1, position-35)
    stop=text.find('\n',position)
    fragment=text[start:min(start+length,stop if stop>=0 else len(text))]
    fragment=re.sub(r'!?\[\[([^\]|]+)(?:\|([^\]]+))?\]\]',lambda m:m.group(2) or m.group(1),fragment)
    fragment=re.sub(r'\[([^\]]+)\]\([^)]*\)',r'\1',fragment)
    fragment=re.sub(r'^\s{0,3}(?:#{1,6}\s+|>\s*)','',fragment)
    from .markdown_syntax import visible_text
    return visible_text(fragment).strip()


def relations(root, records, current):
    """Resolve against one immutable inventory; no UI-thread disk scans."""
    notes = Notes(root, lambda p: records[str(p)].text)
    notes._paths = [n.path for n in records.values()]; incoming = []; outgoing = []; mentions = []
    active = records.get(str(current))
    if not active: return incoming, outgoing, mentions
    names = [current.stem, *active.aliases]
    for note in records.values():
        covered = []
        for link in note.links:
            try: targets, anchor = notes.resolve(link.target, note.path, link.wiki)
            except (OSError, ValueError, KeyError): targets, anchor = [], ''
            if note.path == current: outgoing.append((note, link, targets, anchor))
            if current in targets and note.path != current: incoming.append((note, link.start, snippet(note.text, link.start)))
            covered.append((link.start, link.end))
        if note.path == current: continue
        masked = prose_mask(note.text)
        for name in names:
            if not name: continue
            for match in re.finditer(re.escape(name), masked, re.I):
                if not any(a <= match.start() < b for a, b in covered):
                    mentions.append((note, match.start(), snippet(note.text, match.start())))
    return incoming, outgoing, mentions
