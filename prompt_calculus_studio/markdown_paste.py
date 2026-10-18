"""Conservative HTML-to-Markdown paste, without resource fetch or execution."""
import html
from html.parser import HTMLParser
import re
from urllib.parse import urlsplit
from .markdown_syntax import css_style,style_css
from .markdown_tables import encode_cell


class PasteParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True);self.out=[];self.stack=[];self.skip=0;self.list_depth=[];self.table=None;self.row=None;self.cell=None;self.pre=False;self.pre_content=[];self.inline_code=None
    def put(self,text):
        (self.cell if self.cell is not None else self.out).append(text)
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if tag in ('script','style','iframe','object','noscript'):self.skip+=1;return
        if self.skip:return
        if tag=='table':self.table=[];return
        if tag=='tr' and self.table is not None:self.row=[];return
        if tag in ('td','th') and self.row is not None:self.cell=[];return
        if tag=='br':self.put('\n');return
        if tag in ('ul','ol'):self.list_depth.append(tag);self.put('\n');return
        if tag=='li':self.put('\n'+'  '*max(0,len(self.list_depth)-1)+('1. ' if self.list_depth and self.list_depth[-1]=='ol' else '- '));return
        if tag=='pre':self.pre=True;self.pre_content=[];return
        if tag=='code' and self.pre:return
        if tag=='code':self.inline_code=[];return
        if tag=='img':
            src=attrs.get('src','');alt=attrs.get('alt','圖片')
            if urlsplit(src).scheme in ('http','https'):
                self.put('!['+alt.replace(']','\\]')+']('+src.replace(' ','%20').replace(')','%29')+')')
            else:self.put(alt)
            return
        prefixes={'b':'**','strong':'**','i':'*','em':'*','s':'~~','del':'~~','u':'<u>','code':'`','blockquote':'\n\n> '}
        prefix=prefixes.get(tag,'');suffix={'u':'</u>','blockquote':'\n\n'}.get(tag,prefix)
        if re.fullmatch('h[1-6]',tag):prefix='\n\n'+'#'*int(tag[1])+' ';suffix='\n\n'
        elif tag in ('p','div'):prefix='\n\n';suffix='\n\n'
        elif tag=='a':
            target=attrs.get('href','');scheme=urlsplit(target).scheme
            if scheme in ('','http','https','mailto') and not target.startswith(('//','\\')):
                prefix='[';suffix=']('+target.replace(' ','%20').replace(')','%29')+')'
        style=css_style(attrs.get('style',''))
        if style:
            prefix+='<span style="'+html.escape(style_css(style),quote=True)+'">';suffix='</span>'+suffix
        self.put(prefix);self.stack.append((tag,suffix))
    def handle_endtag(self,tag):
        if tag in ('script','style','iframe','object','noscript'):
            self.skip=max(0,self.skip-1);return
        if self.skip:return
        if tag in ('td','th') and self.cell is not None:
            value=''.join(self.cell).strip()
            self.row.append(encode_cell(value));self.cell=None;return
        if tag=='tr' and self.row is not None:self.table.append(self.row);self.row=None;return
        if tag=='table' and self.table is not None:
            rows=self.table;self.table=None
            if rows:
                width=max(map(len,rows));rows=[r+['']*(width-len(r)) for r in rows]
                self.out.append('\n\n| '+' | '.join(rows[0])+' |\n| '+' | '.join(['---']*width)+' |\n')
                self.out.extend('| '+' | '.join(r)+' |\n' for r in rows[1:]);self.out.append('\n')
            return
        if tag in ('ul','ol'):
            if self.list_depth:self.list_depth.pop()
            self.put('\n');return
        if tag=='li':return
        if tag=='code' and self.pre:return
        if tag=='code' and self.inline_code is not None:
            content=''.join(self.inline_code);self.inline_code=None
            fence='`'*(max([len(m.group()) for m in re.finditer('`+',content)]+[0])+1)
            padded=' '+content+' ' if content.startswith(('`',' ')) or content.endswith(('`',' ')) else content
            self.put(fence+padded+fence);return
        if tag=='pre':
            self.pre=False;content=''.join(self.pre_content)
            fence='`'*(max([len(m.group()) for m in re.finditer('`+',content)]+[2])+1)
            self.put('\n\n'+fence+'\n'+content+('' if content.endswith('\n') else '\n')+fence+'\n\n');return
        if self.stack and self.stack[-1][0]==tag:self.put(self.stack.pop()[1])
    def handle_data(self,data):
        if not self.skip:
            if self.pre:self.pre_content.append(data)
            elif self.inline_code is not None:self.inline_code.append(data)
            else:
                escaped=re.sub(r'([\\`*_\[\]<>])',r'\\\1',data)
                if self.cell is not None:
                    # Only ordinary text was escaped above. Code spans arrive
                    # separately and must keep their literal backslashes.
                    escaped=re.sub(r'(\\+)\|',lambda m:'\\'*(len(m.group(1))//2)+'|',escaped)
                self.put(escaped)


def from_html(source):
    parser=PasteParser();parser.feed(source);parser.close()
    return ''.join(parser.out).strip()
