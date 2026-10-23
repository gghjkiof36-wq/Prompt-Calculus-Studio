"""Local rich Markdown projection. Output is display-only and never persisted."""
from dataclasses import dataclass,field
import html
import re
from pathlib import Path
from urllib.parse import unquote,urlsplit
from markdown_it import MarkdownIt
from mdit_py_plugins.footnote import footnote_plugin
from mdit_py_plugins.tasklists import tasklists_plugin
from pygments import highlight
from pygments.lexers import get_lexer_by_name
from pygments.lexers.special import TextLexer
from pygments.formatters import HtmlFormatter
from .markdown_syntax import TAG_STYLES,css_style,style_css,display_style
from .markdown_math import math_plugin


def advanced(text):
    # None of the rich constructs can occur without one of these markers.
    # Fast literal scans avoid running multiline expressions over an entire
    # large plain manuscript while switching its view on the UI thread.
    if '<' in text and re.search(r'(?im)^\s*<table(?:\s|>)',text):return True
    if r'\(' in text or r'\[' in text or '==' in text:return True
    if not text.startswith('---\n') and not any(marker in text for marker in ('[','](','`','~','|','$','%%')):
        return False
    return bool(text.startswith('---\n') or re.search(r'\[\[|\]\(|(?m:^\[[^]]+\]:)',text) or re.search(r'(?m)^\s*(`{3,}|~{3,}|\|.*\||\$\$)|!\[|\[\^[^]]+\]|%%|(?<!\\)\$[^$\n]+\$|^\s*>\s*\[!',text)
                or re.search(r'(?m)^\s*:?-{3,}:?\s*\|',text))


def separate_resource_actions(body):
    """Keep generated image-load actions outside a surrounding article link.

    Resources hydrate after Markdown parsing. A remote placeholder's action
    must not create nested anchors, which browsers split and drop later text.
    Only generated remote actions move; the source and article URL stay intact.
    """
    if 'pcs-action:remote/' not in body:return body
    stack=[];edits=[]
    for match in re.finditer(r'<a\b[^>]*>|</a>',body):
        tag=match.group()
        if tag!='</a>':
            stack.append((match.start(),tag,[]))
        elif stack:
            start,opening,pending=stack.pop()
            if stack and re.search(r'\bhref="pcs-action:remote/\d+"',opening):
                edits.append((start,match.end(),''))
                stack[0][2].append(body[start:match.end()])
            elif not stack and pending:
                edits.append((match.end(),match.end(),' '+ ' '.join(pending)))
    if not edits:return body
    parts=[];at=0
    for start,end,value in sorted(edits):
        parts.extend((body[at:start],value));at=end
    parts.append(body[at:])
    return ''.join(parts)


def safe_tag(raw):
    """Only the already supported B tags/styles, plus a literal line break."""
    if re.fullmatch(r'<br\s*/?>',raw,re.I):return '<br>'
    if raw.startswith('<!--'):return ''
    match=re.fullmatch(r'<(/?)([a-zA-Z]+)(\s[^<>]*?)?>',raw)
    if not match:return html.escape(raw)
    closing,tag,attrs=match.groups();tag=tag.lower()
    if tag not in TAG_STYLES:return html.escape(raw)
    if closing:return f'</{tag}>'
    css=re.fullmatch(r'\s+style\s*=\s*(["\'])(.*?)\1\s*',attrs or '',re.I)
    if attrs and attrs.strip() and not css:return html.escape(raw)
    style=dict(TAG_STYLES[tag])
    if css:style.update(css_style(html.unescape(css.group(2))))
    value=style_css(display_style(style))
    return '<'+tag+(f' style="{html.escape(value,quote=True)}"' if value else '')+'>'


@dataclass
class Projection:
    body:str=''
    codes:list=field(default_factory=list)
    math:list=field(default_factory=list)
    diagrams:list=field(default_factory=list)
    attachments:list=field(default_factory=list)
    links:list=field(default_factory=list)
    sources:list=field(default_factory=list)
    dependencies:set=field(default_factory=set)
    embedded_count:int=0
    embedded_bytes:int=0
    citation_count:int=0


def local_target(target,path,root):
    from .local_documents import paths
    parsed=urlsplit(target);decoded=unquote(parsed.path)
    if parsed.scheme or parsed.netloc or decoded.startswith(('\\','//')):return None
    candidate=(path.parent/decoded).absolute()
    try:
        for part in (candidate,*candidate.parents):
            if part.exists() and paths.is_link(part):return None
        resolved=candidate.resolve();base=root.resolve()
        if not resolved.is_relative_to(base):return None
        return paths.checked(root,resolved.relative_to(base).as_posix())
    except (OSError,ValueError):return None


def render(text,path,root,media_renderer=None,hard_breaks=False,notes=None,*,_result=None,_trail=(),_origin=None,_offset=0,before_embed=None):
    from .markdown_links import Notes,region
    from .markdown_properties import frontmatter
    result=_result or Projection();path=Path(path);root=Path(root);origin=_origin or path
    notes=notes or Notes(root)
    result.dependencies.add(path)
    prefix='' if path==origin and not _trail else 'embed-'+str(len(result.links))+'-'
    def source_action(line,kind='line'):
        if not _trail:return f'pcs-action:{kind}/{line}'
        index=len(result.sources);result.sources.append(dict(path=str(path),line=line+_offset))
        return f'pcs-action:source/{index}'
    def link_action(target,wiki=False):
        index=len(result.links);result.links.append(dict(target=target,path=str(path),wiki=wiki))
        return f'pcs-action:link/{index}'
    def wiki_link(state,silent):
        if not state.src.startswith('[[',state.pos):return False
        end=state.src.find(']]',state.pos+2)
        if end<0:return False
        if not silent:
            token=state.push('pcs_wiki','',0);token.content=state.src[state.pos+2:end]
        state.pos=end+2;return True
    md=MarkdownIt('commonmark',{'html':True,'breaks':hard_breaks}).enable(['table','strikethrough'])
    md.use(footnote_plugin).use(math_plugin).use(tasklists_plugin,enabled=False)
    # Only the checkbox token inserted by the task-list plugin is trusted.
    # User-authored <input> still passes through safe_tag and stays literal.
    def task_candidates(state):
        candidates={}
        for index,token in enumerate(state.tokens):
            if (index>=2 and token.type=='inline' and state.tokens[index-1].type=='paragraph_open'
                    and state.tokens[index-2].type=='list_item_open' and re.match(r'^\[[ xX]\]\s+',token.content)):
                candidates[id(token)]=({id(child) for child in token.children or []},token.content[1].lower()=='x')
        state.env['_pcs_task_candidates']=candidates
    def task_checkboxes(state):
        candidates=state.env.pop('_pcs_task_candidates',{})
        for token in state.tokens:
            candidate=candidates.get(id(token))
            if candidate is None or not token.children:continue
            previous,checked=candidate;checkbox=token.children[0]
            if id(checkbox) not in previous and checkbox.type=='html_inline':
                checkbox.type='pcs_task_checkbox';checkbox.meta['checked']=checked
    md.core.ruler.before('github-tasklists','pcs_task_candidates',task_candidates)
    md.core.ruler.after('github-tasklists','pcs_task_checkboxes',task_checkboxes)
    md.add_render_rule('pcs_task_checkbox',lambda self,tokens,i,options,env:
        '<input class="task-list-item-checkbox" type="checkbox" disabled="disabled"'+
        (' checked="checked"' if tokens[i].meta['checked'] else '')+'>')
    md.inline.ruler.before('link','pcs_wiki',wiki_link)
    def wiki_html(self,tokens,i,options,env):
        target,_,label=tokens[i].content.partition('|')
        return '<a href="'+link_action(target,True)+'">'+html.escape(label or target)+'</a>'
    md.add_render_rule('pcs_wiki',wiki_html)
    md.add_render_rule('link_open',lambda self,tokens,i,options,env:'<a href="'+link_action(tokens[i].attrGet('href') or '')+'">')
    # Comments are recognized by the tokenizer, not removed from fenced/code text.
    def comment(state,silent):
        if not state.src.startswith('%%',state.pos):return False
        end=state.src.find('%%',state.pos+2)
        if end<0:return False
        if not silent:state.push('hidden_comment','',0)
        state.pos=end+2;return True
    md.inline.ruler.before('emphasis','hidden_comment',comment)
    def block_comment(state,start,end,silent):
        pos=state.bMarks[start]+state.tShift[start]
        if not state.src.startswith('%%',pos):return False
        last=state.src.find('%%',pos+2)
        if last<0:return False
        closing_line=state.src.count('\n',0,last)
        if state.src[last+2:state.eMarks[closing_line]].strip():return False
        if not silent:state.push('hidden_comment','',0)
        state.line=closing_line+1;return True
    md.block.ruler.before('paragraph','hidden_comment',block_comment)
    md.add_render_rule('hidden_comment',lambda self,tokens,index,options,env:'')
    def mark(state,silent):
        if not state.src.startswith('==',state.pos):return False
        end=state.src.find('==',state.pos+2)
        if end<0:return False
        if not silent:
            token=state.push('pcs_mark','mark',0);token.content=state.src[state.pos+2:end]
        state.pos=end+2;return True
    md.inline.ruler.before('emphasis','pcs_mark',mark)
    md.add_render_rule('pcs_mark',lambda self,tokens,index,options,env:'<mark>'+md.renderInline(tokens[index].content)+'</mark>')
    def wiki_embed(state,silent):
        if not state.src.startswith('![[',state.pos):return False
        end=state.src.find(']]',state.pos+3)
        if end<0:return False
        if not silent:
            token=state.push('pcs_embed','',0);token.content=state.src[state.pos+3:end]
        state.pos=end+2;return True
    md.inline.ruler.before('image','pcs_embed',wiki_embed)
    # A projection often references one image many times. Resolve once in this
    # synchronous pass, never across refreshes or different embedded sources.
    resolved_targets={}
    def embed(target,alt='',wiki=False):
        # Obsidian dimensions use |width or |widthxheight; the source is untouched.
        width=height=None
        dimensions=re.search(r'\|(\d{1,4})(?:x(\d{1,4}))?$',target)
        if dimensions:
            width=int(dimensions.group(1));height=int(dimensions.group(2)) if dimensions.group(2) else None
            target=target[:dimensions.start()]
        elif re.search(r'\|(\d{1,4})(?:x(\d{1,4}))?$',alt):
            dimensions=re.search(r'\|(\d{1,4})(?:x(\d{1,4}))?$',alt)
            width=int(dimensions.group(1));height=int(dimensions.group(2)) if dimensions.group(2) else None;alt=alt[:dimensions.start()]
        elif wiki and '|' in target:target,alt=target.split('|',1)
        address=urlsplit(target)
        if before_embed is not None and (wiki or (not address.scheme and not address.netloc
                and Path(unquote(address.path)).suffix.lower() in ('','.md'))):
            # Covers both ![[Child]] and ![](Child.md), before any lookup/I/O.
            before_embed()
        key=(target,wiki)
        resolved=resolved_targets.get(key)
        if resolved is None:
            candidates,anchor=notes.resolve(target,path,wiki)
            local=candidates[0] if len(candidates)==1 else local_target(target,path,root)
            resolved=(candidates,anchor,local)
            if len(resolved_targets)<2048:resolved_targets[key]=resolved
        candidates,anchor,local=resolved
        is_note=(local and local.suffix.lower()=='.md') or not Path(urlsplit(target).path).suffix
        if is_note and not urlsplit(target).scheme:
            label=alt or target
            link='<a class="source" href="'+link_action(target,wiki)+'">'+html.escape(label)+'</a>'
            if len(candidates)!=1:return '<span class="resource missing">'+link+' · '+('同名筆記，請選擇位置' if candidates else '找不到筆記')+'</span>'
            identity=(str(local),anchor)
            if identity in _trail or (local==origin and not anchor) or len(_trail)>=4 or result.embedded_count>=32:
                return '<span class="resource missing">'+link+' · 循環或多層嵌入，請開啟來源閱讀</span>'
            result.embedded_count+=1
            result.dependencies.add(local)
            try:
                source=notes.text(local);start,stop=region(source,anchor)
                size=len(source[start:stop].encode('utf-8'))
                if size>512*1024 or result.embedded_bytes+size>1024*1024:return '<span class="resource missing">'+link+' · 嵌入內容較長，請開啟來源閱讀</span>'
                result.embedded_bytes+=size
                nested=render(source[start:stop],local,root,media_renderer,hard_breaks,notes,_result=result,
                    _trail=(*_trail,identity),_origin=origin,_offset=source[:start].count('\n')).body
                return '<div class="note-embed"><header>'+link+'</header>'+nested+'</div>'
            except (ValueError,OSError) as error:return '<span class="resource missing">'+link+' · '+html.escape(str(error))+'</span>'
        index=len(result.attachments)
        entry=dict(target=target,path=str(local) if local else None,width=width,height=height,alt=alt)
        result.attachments.append(entry)
        if media_renderer:return media_renderer(entry,index)
        return '<span class="resource">'+html.escape(alt or target)+'</span>'
    def render_embed(self,tokens,i,options,env):
        # Interactive callers may transfer the complete projection to a worker
        # before resolving or reading a Child. Literal code never reaches here.
        return embed(tokens[i].content,wiki=True)
    md.add_render_rule('pcs_embed',render_embed)
    md.add_render_rule('image',lambda self,tokens,i,options,env:embed(tokens[i].attrGet('src') or '',tokens[i].content))
    md.add_render_rule('html_inline',lambda self,tokens,i,options,env:safe_tag(tokens[i].content))
    def html_block(self,tokens,i,options,env):
        raw=tokens[i].content
        if raw.lstrip().startswith('<!--'):return ''
        from .markdown_table_html import render_table_html
        table=render_table_html(raw,link_action)
        if table is not None:return table
        # B paragraph wrappers keep Markdown inside editable and readable.
        match=re.fullmatch(r'(<(?:p|div)(?:\s[^<>]*?)?>)(.*?)(</(?:p|div)>)[\n ]*',raw,re.S|re.I)
        if match and not safe_tag(match.group(1)).startswith('&lt;'):
            return safe_tag(match.group(1))+md.renderInline(match.group(2))+safe_tag(match.group(3))
        return '<pre class="unknown">'+html.escape(raw)+'</pre>'
    md.add_render_rule('html_block',html_block)
    plain_lexer=None
    def fence(self,tokens,i,options,env):
        nonlocal plain_lexer
        token=tokens[i];language=token.info.strip().split(' ')[0];code=token.content
        if language.lower()=='mermaid':
            index=len(result.diagrams);result.diagrams.append(code)
            return f'<div class="diagram" data-diagram="{index}"></div><a class="source" href="{source_action(token.map[0])}">編輯圖表原文</a>'
        index=len(result.codes);result.codes.append(code)
        try:
            if language.lower()=='text':
                # TextLexer has no coloured tokens. Keep its public newline/BOM
                # normalization without building an unused stylesheet per block.
                if plain_lexer is None:plain_lexer=TextLexer()
                painted=''.join(html.escape(value).replace('&#x27;','&#39;')
                    for _,value in plain_lexer.get_tokens(code))
            else:
                painted=highlight(code,get_lexer_by_name(language),HtmlFormatter(nowrap=True)) if language else html.escape(code)
        except Exception:painted=html.escape(code)
        return f'<section class="code"><header>{html.escape(language or "純文字")}<a href="pcs-action:copy/{index}">複製程式碼</a><a href="{source_action(token.map[0])}">編輯</a></header><pre><code>{painted}</code></pre></section>'
    md.add_render_rule('fence',fence)
    def math_render(block):
        def inner(self,tokens,i,options,env):
            index=len(result.math);result.math.append(dict(source=tokens[i].content,block=block))
            # Display-style math may occur inside a paragraph/list item. A span
            # keeps valid HTML there; KaTeX supplies the display layout itself.
            tag='div' if tokens[i].block else 'span'
            return f'<{tag} class="formula" data-math="{index}"></{tag}>'
        return inner
    md.add_render_rule('math_inline',math_render(False));md.add_render_rule('math_block',math_render(True))
    md.add_render_rule('math_inline_double',math_render(True))
    def table_open(self,tokens,i,options,env):
        line=tokens[i].map[0]
        return f'<div class="pcs-table-scroll"><table id="{prefix}line-{line}">'
    md.add_render_rule('table_open',table_open)
    md.add_render_rule('table_close',lambda self,tokens,i,options,env:'</table></div>\n')
    footnote_anchor=md.renderer.rules['footnote_anchor']
    def note_back(self,tokens,i,options,env):
        if tokens[i].meta['id'] in env.get('pcs_citations',{}):return ''
        anchor=footnote_anchor(tokens,i,options,env)
        label=env.get('footnotes',{}).get('list',{}).get(tokens[i].meta['id'],{}).get('label')
        if label:
            match=re.search(r'(?m)^\[\^'+re.escape(label)+r'\]:',text)
            if match:anchor+=f' <a class="source" href="{source_action(text[:match.start()].count(chr(10)))}">編輯腳註</a>'
        return anchor
    md.add_render_rule('footnote_anchor',note_back)
    front_end,properties,_=frontmatter(text)
    rendered_text='\n'*text[:front_end].count('\n')+text[front_end:]
    # Block IDs are anchors, not visible prose. Keep source line numbering.
    if '^' in rendered_text:
        from .markdown_links import prose_mask
        pieces=[];at=0
        for marker in re.finditer(r'(?m)(?:^|[ \t])\^[A-Za-z0-9-]+[ \t]*$',prose_mask(rendered_text)):
            pieces.append(rendered_text[at:marker.start()]);at=marker.end()
        if pieces:
            pieces.append(rendered_text[at:]);rendered_text=''.join(pieces)
    env={};tokens=md.parse(rendered_text,env);stack=[]
    from .markdown_citations import install as citation_footnotes
    citation_footnotes(md,tokens,env,result)
    for i,token in enumerate(tokens):
        if token.type=='blockquote_open':
            callout=None
            if i+2<len(tokens) and tokens[i+2].type=='inline':
                first=tokens[i+2];match=re.match(r'^\[!([\w-]+)\]([+-]?)(?:[ \t]+([^\n]*))?(?:\n|$)',first.content)
                if match:
                    kind,fold,title=match.groups();callout=(kind,fold,title or kind.capitalize())
                    first.content=first.content[match.end():];first.children=[];md.inline.parse(first.content,md,{},first.children)
            token.meta['callout']=callout;stack.append(callout)
        elif token.type=='blockquote_close':token.meta['callout']=stack.pop() if stack else None
    def quote_open(self,tokens,i,options,env):
        value=tokens[i].meta.get('callout')
        if not value:return '<blockquote>'
        kind,fold,title=value
        css='callout '+('warning' if kind in ('warning','caution','danger','failure','bug') else 'success' if kind in ('success','tip','done') else 'note')
        title=md.renderInline(title)
        return (f'<details class="{css}"'+(' open' if fold=='+' else '')+f'><summary>{title}</summary>' if fold else f'<aside class="{css}"><strong class="callout-title">{title}</strong>')
    md.add_render_rule('blockquote_open',quote_open)
    md.add_render_rule('blockquote_close',lambda self,tokens,i,options,env:'</details>' if tokens[i].meta.get('callout') and tokens[i].meta['callout'][1] else '</aside>' if tokens[i].meta.get('callout') else '</blockquote>')
    # Give block starts stable source-line anchors without changing the document.
    body=[];seen=set()
    for token in tokens:
        if token.map and token.nesting!=-1 and token.map[0] not in seen:
            seen.add(token.map[0]);token.attrSet('id',prefix+'line-'+str(token.map[0]))
    props=''
    if properties and not _trail:
        rows=''.join('<dt>'+html.escape(p.name)+'</dt><dd>'+html.escape(', '.join(p.value) if isinstance(p.value,list) else str(p.value if p.editable else '複合值 · 原文保留'))+'</dd>' for p in properties)
        props='<details class="note-properties"><summary>屬性</summary><dl>'+rows+'</dl></details>'
    result.body=separate_resource_actions(props+md.renderer.render(tokens,md.options,env))
    return result
