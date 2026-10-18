"""Lossless, conservative editing of basic YAML frontmatter properties."""
from dataclasses import dataclass
from datetime import date,datetime
import json
import math
import re
from .markdown_source import SourceEdit


@dataclass
class Property:
    name:str
    start:int
    end:int
    kind:str
    value:object
    raw:str
    editable:bool=True


def scalar(raw):
    value=raw.strip()
    if not value or value in ('null','~'):return 'text',None
    if value.startswith('"'):return 'text',json.loads(value)
    if value.startswith("'") and value.endswith("'"):
        return 'text',value[1:-1].replace("''", "'")
    value=re.split(r'\s+#',value,1)[0].rstrip()
    if value in ('true','false'):return 'checkbox',value=='true'
    if re.fullmatch(r'[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?',value):
        number=float(value) if any(c in value for c in '.eE') else int(value)
        if not math.isfinite(number):raise ValueError('非有限數值')
        return 'number',number
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}',value):date.fromisoformat(value);return 'date',value
    if re.match(r'^\d{4}-\d{2}-\d{2}[Tt ]\d',value):datetime.fromisoformat(value);return 'datetime',value
    if re.search(r'(^[!&*|>{}\[\]#?%`@]|:\s)',value):raise ValueError('複合 YAML')
    return 'text',value


def inline_comment(raw):
    quote=None;escaped=False
    for i,char in enumerate(raw):
        if escaped:escaped=False;continue
        if quote=='"' and char=='\\':escaped=True;continue
        if char in ('"',"'"):
            if quote==char:quote=None
            elif quote is None:quote=char
        if char=='#' and quote is None and i>0 and raw[i-1].isspace():return ' '+raw[i:]
    return ''


def list_value(raw):
    # JSON strings are valid YAML. Existing plain/single quoted lists also work.
    values=[]
    pieces=re.findall(r'"(?:\\.|[^"\\])*"|\x27(?:\x27\x27|[^\x27])*\x27|[^,]+',raw)
    for piece in pieces:
        kind,value=scalar(piece)
        if kind!='text' or not isinstance(value,str):raise ValueError('非文字清單')
        values.append(value)
    return values


def frontmatter(text):
    if not re.match(r'^---[ \t]*(?:\r?\n)',text):return 0,[],None
    closing=re.search(r'(?m)^(?:---|\.\.\.)[ \t]*(?:\r?\n|$)',text[4:])
    if closing is None:return 0,[],'YAML 尚未結束，請先在原文補上 ---。'
    stop=4+closing.start();end=4+closing.end();header=text[:stop]
    matches=list(re.finditer(r'(?m)^([^\s:#][^:\r\n]*):[ \t]*([^\r\n]*)(?:\r?\n|$)',header))
    properties=[];seen=set()
    for i,match in enumerate(matches):
        name=match.group(1).strip();last=matches[i+1].start() if i+1<len(matches) else stop
        # Leave trailing comments/blank lines outside the property edit span.
        lines=text[match.end():last].splitlines(keepends=True)
        while lines and (not lines[-1].strip() or lines[-1].lstrip().startswith('#')):lines.pop()
        last=match.end()+sum(len(line) for line in lines);raw=text[match.start():last]
        kind='complex';value=raw;editable=True
        try:
            first=match.group(2).strip();tail=''.join(lines)
            if first.startswith('[') and first.endswith(']') and not tail.strip():
                kind='list';value=list_value(first[1:-1])
            elif not first and tail.strip():
                items=[re.fullmatch(r'\s*-\s+(.*?)\s*',line) for line in tail.splitlines() if line.strip()]
                if not items or not all(items):raise ValueError('巢狀 YAML')
                value=[]
                for item in items:
                    entry_kind,entry=scalar(item.group(1))
                    if entry_kind!='text' or not isinstance(entry,str):raise ValueError('複合清單')
                    value.append(entry)
                kind='list'
            elif tail.strip():raise ValueError('多行 YAML')
            else:kind,value=scalar(first)
            if name in ('aliases','tags','cssclasses'):
                if isinstance(value,str):value=[value]
                if value is None:value=[]
                if not isinstance(value,list):raise ValueError('需要文字清單')
                kind='tags' if name=='tags' else 'list'
            if not re.fullmatch(r'[^:#\s][^:\r\n#]*',name) or name.startswith(('"',"'",'!','&','*')):raise ValueError('進階鍵名')
        except (ValueError,TypeError,json.JSONDecodeError):kind='complex';value=raw;editable=False
        if name in seen:
            editable=False;kind='complex'
            for prior in properties:
                if prior.name==name:prior.editable=False;prior.kind='complex'
        seen.add(name);properties.append(Property(name,match.start(),last,kind,value,raw,editable))
    # Unsupported root sequences/anchors stay readable and are never rebuilt.
    return end,properties,None


def serialized(name,kind,value):
    if not re.fullmatch(r'[^:#\s!&*\[\]{}"\x27][^:\r\n#]*',name):raise ValueError('屬性名稱不能包含冒號、換行或 YAML 標記。')
    if kind=='checkbox':result='true' if value is True or str(value).lower()=='true' else 'false'
    elif kind=='number':
        _,number=scalar(str(value))
        if not isinstance(number,(int,float)) or isinstance(number,bool):raise ValueError('請填入數字。')
        result=str(number)
    elif kind in ('date','datetime'):
        (date if kind=='date' else datetime).fromisoformat(str(value));result=str(value)
    elif kind in ('list','tags'):
        values=value if isinstance(value,list) else [part.strip() for part in str(value).split('\n') if part.strip()]
        if kind=='tags':values=[v.lstrip('#') for v in values]
        result=json.dumps(values,ensure_ascii=False)
    else:result=json.dumps(str(value or ''),ensure_ascii=False)
    return name+': '+result+'\n'


def property_edits(text,updates):
    """Updates are (original name or None, new name, kind, value), value None deletes."""
    end,properties,error=frontmatter(text)
    if error:raise ValueError(error)
    known={p.name:p for p in properties};edits=[];added=[];names=set(known)
    for old,name,kind,value in updates:
        prop=known.get(old) if old else None
        if old and (prop is None or not prop.editable):raise ValueError('這個複合屬性請在原文編輯。')
        if name!=old and name in names:raise ValueError('屬性名稱已存在：'+name)
        if prop and value is not None and (name,kind,value)==(prop.name,prop.kind,prop.value):continue
        replacement=serialized(name,kind,value) if value is not None else ''
        if prop:
            # Preserve an existing inline comment when changing a basic value.
            comment=inline_comment(prop.raw.splitlines()[0])
            if replacement and comment:replacement=replacement.rstrip('\n')+comment+'\n'
            edits.append(SourceEdit(prop.start,prop.end,replacement))
        elif replacement:added.append(replacement)
        names.discard(old);names.add(name)
    if added:
        value=''.join(added)
        if end:
            closing=re.search(r'(?m)^(?:---|\.\.\.)[ \t]*(?:\r?\n|$)',text[4:end]);at=4+closing.start()
            edits.append(SourceEdit(at,at,value))
        else:edits.append(SourceEdit(0,0,'---\n'+value+'---\n\n'))
    return edits
