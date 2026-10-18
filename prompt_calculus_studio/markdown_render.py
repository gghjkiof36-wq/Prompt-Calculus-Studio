"""Local rich Markdown projection. Output is display-only and never persisted."""
from dataclasses import dataclass,field
import html
import re
from pathlib import Path
from urllib.parse import unquote,urlsplit
from markdown_it import MarkdownIt
from mdit_py_plugins.footnote import footnote_plugin
from mdit_py_plugins.dollarmath import dollarmath_plugin
from mdit_py_plugins.tasklists import tasklists_plugin
from pygments import highlight
from pygments.lexers import get_lexer_by_name
from pygments.formatters import HtmlFormatter
from .markdown_syntax import TAG_STYLES,css_style,style_css,display_style


def advanced(text):
    return bool(re.search(r'(?m)^\s*(`{3,}|~{3,}|\|.*\||\$\$)|!\[|\[\^[^]]+\]|%%|(?<!\\)\$[^$\n]+\$|^\s*>\s*\[!',text)
                or re.search(r'(?m)^\s*:?-{3,}:?\s*\|',text))


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


def render(text,path,root,media_renderer=None,hard_breaks=False):
    result=Projection();path=Path(path);root=Path(root)
    md=MarkdownIt('commonmark',{'html':True,'breaks':hard_breaks}).enable(['table','strikethrough'])
    md.use(footnote_plugin).use(dollarmath_plugin,allow_labels=False).use(tasklists_plugin,enabled=False)
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
    def embed(target,alt=''):
        # Obsidian dimensions use |width or |widthxheight; the source is untouched.
        width=height=None
        dimensions=re.search(r'\|(\d{1,4})(?:x(\d{1,4}))?$',target)
        if dimensions:
            width=int(dimensions.group(1));height=int(dimensions.group(2)) if dimensions.group(2) else None
            target=target[:dimensions.start()]
        elif re.search(r'\|(\d{1,4})(?:x(\d{1,4}))?$',alt):
            dimensions=re.search(r'\|(\d{1,4})(?:x(\d{1,4}))?$',alt)
            width=int(dimensions.group(1));height=int(dimensions.group(2)) if dimensions.group(2) else None;alt=alt[:dimensions.start()]
        index=len(result.attachments);local=local_target(target,path,root)
        entry=dict(target=target,path=str(local) if local else None,width=width,height=height,alt=alt)
        result.attachments.append(entry)
        if media_renderer:return media_renderer(entry,index)
        return '<span class="resource">'+html.escape(alt or target)+'</span>'
    md.add_render_rule('pcs_embed',lambda self,tokens,i,options,env:embed(tokens[i].content))
    md.add_render_rule('image',lambda self,tokens,i,options,env:embed(tokens[i].attrGet('src') or '',tokens[i].content))
    md.add_render_rule('html_inline',lambda self,tokens,i,options,env:safe_tag(tokens[i].content))
    def html_block(self,tokens,i,options,env):
        raw=tokens[i].content
        if raw.lstrip().startswith('<!--'):return ''
        # B paragraph wrappers keep Markdown inside editable and readable.
        match=re.fullmatch(r'(<(?:p|div)(?:\s[^<>]*?)?>)(.*?)(</(?:p|div)>)[\n ]*',raw,re.S|re.I)
        if match and not safe_tag(match.group(1)).startswith('&lt;'):
            return safe_tag(match.group(1))+md.renderInline(match.group(2))+safe_tag(match.group(3))
        return '<pre class="unknown">'+html.escape(raw)+'</pre>'
    md.add_render_rule('html_block',html_block)
    def fence(self,tokens,i,options,env):
        token=tokens[i];language=token.info.strip().split(' ')[0];code=token.content
        if language.lower()=='mermaid':
            index=len(result.diagrams);result.diagrams.append(code)
            return f'<div class="diagram" data-diagram="{index}"></div><a class="source" href="pcs-action:line/{token.map[0]}">編輯圖表原文</a>'
        index=len(result.codes);result.codes.append(code)
        try:painted=highlight(code,get_lexer_by_name(language),HtmlFormatter(nowrap=True)) if language else html.escape(code)
        except Exception:painted=html.escape(code)
        return f'<section class="code"><header>{html.escape(language or "純文字")}<a href="pcs-action:copy/{index}">複製程式碼</a><a href="pcs-action:line/{token.map[0]}">編輯</a></header><pre><code>{painted}</code></pre></section>'
    md.add_render_rule('fence',fence)
    def math_render(block):
        def inner(self,tokens,i,options,env):
            index=len(result.math);result.math.append(dict(source=tokens[i].content,block=block))
            tag='div' if block else 'span'
            return f'<{tag} class="formula" data-math="{index}"></{tag}>'
        return inner
    md.add_render_rule('math_inline',math_render(False));md.add_render_rule('math_block',math_render(True))
    def table_open(self,tokens,i,options,env):
        line=tokens[i].map[0]
        return f'<a class="source" href="pcs-action:table/{line}">編輯表格</a><table id="line-{line}">'
    md.add_render_rule('table_open',table_open)
    footnote_anchor=md.renderer.rules['footnote_anchor']
    def note_back(self,tokens,i,options,env):
        anchor=footnote_anchor(tokens,i,options,env)
        label=env.get('footnotes',{}).get('list',{}).get(tokens[i].meta['id'],{}).get('label')
        if label:
            match=re.search(r'(?m)^\[\^'+re.escape(label)+r'\]:',text)
            if match:anchor+=f' <a class="source" href="pcs-action:line/{text[:match.start()].count(chr(10))}">編輯腳註</a>'
        return anchor
    md.add_render_rule('footnote_anchor',note_back)
    env={};tokens=md.parse(text,env);stack=[]
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
            seen.add(token.map[0]);token.attrSet('id','line-'+str(token.map[0]))
    result.body=md.renderer.render(tokens,md.options,env)
    return result
