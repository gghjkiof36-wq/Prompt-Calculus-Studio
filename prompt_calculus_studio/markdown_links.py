"""Root-scoped Markdown/Wiki relations and source ranges, independent of Qt."""
from dataclasses import dataclass
from pathlib import Path
import os
import re
from urllib.parse import unquote,urlsplit,quote
from .local_documents import paths
from .markdown_source import SourceEdit,MAX_BYTES
from .markdown_properties import frontmatter
from .markdown_render import local_target


def address_of(target,wiki=False):
    if not wiki:
        from markdown_it.common.utils import unescapeAll
        target=unescapeAll(target)
    return urlsplit(target)


@dataclass(frozen=True)
class Link:
    start:int
    end:int
    target_start:int
    target_end:int
    target:str
    label:str=''
    wiki:bool=False
    embed:bool=False


def prose_mask(text):
    from .markdown_syntax import line_info
    masked=list(text)
    def hide(a,b):masked[a:b]=['\n' if c=='\n' else ' ' for c in text[a:b]]
    for line in line_info(text):
        if line['protected']:hide(line['start'],line['end'])
    visible=''.join(masked)
    for match in re.finditer(r'(`+)([^`]|(?!\1)`)*?\1|%%.*?%%|<!--.*?-->',visible,re.S):hide(match.start(),match.end())
    return ''.join(masked)


def links(text):
    masked=prose_mask(text);result=[];occupied=[]
    pattern=r'(?<!\\)(!?)\[\[([^\]\n|]+)(?:\|([^\]\n]*))?\]\]'
    for match in re.finditer(pattern,masked):
        result.append(Link(match.start(),match.end(),match.start(2),match.end(2),text[match.start(2):match.end(2)],match.group(3) or '',True,bool(match.group(1))))
        occupied.append((match.start(),match.end()))
    # Scan balanced labels/destinations. A flat regex misses valid escaped ]
    # and nested labels, silently leaving broken references after a rename.
    cursor=0
    while cursor<len(masked):
        start=masked.find('[',cursor)
        if start<0:break
        cursor=start+1
        occupied_end=next((b for a,b in occupied if a<=start<b),None)
        if occupied_end:cursor=occupied_end;continue
        if (start-len(masked[:start].rstrip('\\')))%2:continue
        at=start+1;depth=1
        while at<len(masked) and depth:
            if masked[at]=='\\':at+=2;continue
            if masked[at]=='[':depth+=1
            elif masked[at]==']':depth-=1
            at+=1
        if depth or masked[at:at+1]!='(':continue
        label_end=at-1;at+=1
        while at<len(masked) and masked[at] in ' \t\n':at+=1
        angle=masked[at:at+1]=='<'
        if angle:at+=1
        first=at;depth=0
        while at<len(masked):
            char=masked[at]
            if char=='\\':at+=2;continue
            if angle and char=='>':break
            if not angle:
                if char=='(':
                    depth+=1
                    if depth>32:break
                elif char==')':
                    if not depth:break
                    depth-=1
                elif char.isspace():break
            at+=1
        last=at
        if angle:
            if masked[at:at+1]!='>':continue
            at+=1
        suffix=re.match(r'(?:\s+["\x27][^\n]*?["\x27])?\s*\)',masked[at:])
        if not suffix or depth:continue
        end=at+suffix.end();embed=start>0 and masked[start-1]=='!'
        result.append(Link(start-1 if embed else start,end,first,last,text[first:last],text[start+1:label_end],False,embed))
        cursor=end
    # Reference-style links share one destination definition.
    for match in re.finditer(r'(?m)^ {0,3}\[(?!\^)([^\]\n]+)\]:\s*(?:<([^<>\n]+)>|(\S+))',masked):
        group=2 if match.group(2) is not None else 3
        result.append(Link(match.start(),match.end(),match.start(group),match.end(group),text[match.start(group):match.end(group)],match.group(1)))
    return sorted(result,key=lambda item:item.start)


def note_paths(root,*,visited=None):
    root=paths.root_path(root)
    for directory,folders,files in os.walk(root,followlinks=False):
        if visited is not None:visited(Path(directory))
        folders[:]=[name for name in folders if name.casefold() not in ('.obsidian','.pcs','assets','.git') and not paths.is_link(Path(directory)/name)]
        for name in files:
            if name.lower().endswith('.md'):
                try:
                    path=paths.checked(root,(Path(directory)/name).relative_to(root).as_posix())
                    if path.is_file():yield path
                except (OSError,ValueError):continue


class Notes:
    def __init__(self,root,read=None):
        self.root=paths.root_path(root);self.read=read or self.disk;self._paths=None;self.cache={}
    @staticmethod
    def disk(path):
        path=paths.checked(path.parent,path.name)
        return paths.read_bytes(path,MAX_BYTES)[0].decode('utf-8-sig').replace('\r\n','\n').replace('\r','\n')
    def files(self):
        if self._paths is None:self._paths=list(note_paths(self.root))
        return self._paths
    def text(self,path):
        # Revalidate on use, including cached projections after a directory was
        # replaced by a junction while the reader was already open.
        path=paths.checked(self.root,Path(path).relative_to(self.root).as_posix())
        if path not in self.cache:self.cache[path]=self.read(path)
        return self.cache[path]
    def candidate(self,value,base):
        candidate=local_target(value,base/'_.md',self.root)
        return candidate if candidate and candidate.is_file() else None
    def resolve(self,target,source,wiki=False):
        target=target.split('|',1)[0] if wiki else target;address=address_of(target,wiki)
        if address.scheme or address.netloc:return [],address.fragment
        name=unquote(address.path);anchor=unquote(address.fragment)
        if not name:return [source],anchor
        if '\\' in name or name.startswith('/') or any(part.casefold()=='.obsidian' for part in Path(name).parts):return [],anchor
        names=[name] if Path(name).suffix else [name+'.md',name]
        # Wiki folder paths are rooted; Markdown paths resolve relative first.
        bases=[self.root,source.parent] if wiki and '/' in name and not name.startswith('.') else [source.parent,self.root]
        for base in dict.fromkeys(bases):
            for value in names:
                candidate=self.candidate(value,base)
                if candidate is not None:return [candidate],anchor
        if '/' not in name:
            matches=[p for p in self.files() if p.name.casefold() in {n.casefold() for n in names}]
            if matches:return matches,anchor
            if wiki:
                for path in self.files():
                    _,properties,_=frontmatter(self.text(path))
                    for prop in properties:
                        if prop.name=='aliases' and prop.editable and isinstance(prop.value,list) and name.casefold() in [v.casefold() for v in prop.value]:matches.append(path)
                return matches,anchor
        return [],anchor


def region(text,anchor):
    """Return source offsets for an entire note, heading subtree or block."""
    end,_,_=frontmatter(text)
    if not anchor:return end,len(text)
    from .markdown_commands import heading_sections
    if anchor.startswith('^'):
        match=re.search(r'(?m)(?:^|\s)\^'+re.escape(anchor[1:])+r'[ \t]*$',prose_mask(text))
        if not match:raise ValueError('找不到區塊：'+anchor)
        stop=match.end();line_start=text.rfind('\n',0,match.start())+1
        start=line_start
        # An ID on its own line attaches to the preceding paragraph/block.
        if not text[line_start:match.start()].strip():
            start=len(text[:line_start].rstrip())
            start=text.rfind('\n\n',0,start)+2 if '\n\n' in text[:start] else end
        elif not re.match(r'\s*(?:[-*+] |\d+[.)] )',text[line_start:match.start()]):
            start=text.rfind('\n\n',0,line_start)+2 if '\n\n' in text[:line_start] else end
        return start,stop
    parts=anchor.split('#');lower,upper=end,len(text)
    for name in parts:
        def normalized(value):return re.sub(r'[^\w\s-]','',value).strip().casefold().replace(' ','-')
        found=next((entry for entry in heading_sections(text) if lower<=entry[0]<upper and normalized(entry[3])==normalized(name)),None)
        if found is None:raise ValueError('找不到標題：'+name)
        lower,upper=found[:2]
    return lower,upper


def relocation_edits(text,old,new,source,target,notes):
    """Rewrite destination slices only, retaining labels, fragments and markup."""
    from .workspace_file_io import relocate
    edits=[]
    for link in links(text):
        address=address_of(link.target,link.wiki)
        if address.scheme or address.netloc:continue
        candidates,_=notes.resolve(link.target,old,link.wiki)
        if len(candidates)>1:
            if any(relocate(path,source,target)!=path for path in candidates):raise ValueError('同名引用無法確定目標：'+link.target+'（'+old.name+'）')
            continue
        resolved=candidates[0] if candidates else local_target(address.path,old,notes.root)
        if resolved is None:continue
        destination=relocate(resolved,source,target)
        if old==new and resolved==destination:continue
        if not address.path and old==resolved and new==destination:continue
        if link.wiki:
            # Explicit root-relative destinations avoid basename/alias collisions.
            value=destination.relative_to(notes.root).as_posix()
            if destination.suffix.lower()=='.md' and not address.path.lower().endswith('.md'):value=value[:-3]
        else:value=quote(os.path.relpath(destination,new.parent).replace('\\','/'),safe='/._-~')
        if address.query:value+='?'+address.query
        if address.fragment:value+='#'+address.fragment
        if value!=link.target:edits.append(SourceEdit(link.target_start,link.target_end,value))
    return edits
