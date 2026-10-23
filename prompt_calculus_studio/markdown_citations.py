"""Enhance standard source footnotes without changing their Markdown body."""
import html
import re
from urllib.parse import urlsplit


SOURCE_LABEL = re.compile(r'^pcs-source-[0-9a-f]{8,64}-[1-9][0-9]*$')


def install(md, tokens, env, projection):
    """Keep the original parsed body and route it through the existing renderer."""
    citations = {}
    for index, token in enumerate(tokens):
        if token.type != 'footnote_open' or not SOURCE_LABEL.fullmatch(token.meta.get('label', '')):
            continue
        if index + 2 >= len(tokens) or tokens[index + 1].type != 'paragraph_open' or tokens[index + 2].type != 'inline':
            continue
        children = tokens[index + 2].children or []
        label = ''.join(child.content if child.type in ('text', 'code_inline', 'image')
                        else ' ' if child.type in ('softbreak', 'hardbreak') else '' for child in children).strip()
        if not label:
            continue
        identity = f'pcs-citation-note-{projection.citation_count}'
        projection.citation_count += 1
        citations[token.meta['id']] = (identity, label)
    if not citations:
        return
    env['pcs_citations'] = citations
    original_ref = md.renderer.rules['footnote_ref']
    original_open = md.renderer.rules['footnote_open']
    original_close = md.renderer.rules['footnote_close']
    original_block = md.renderer.rules['footnote_block_open']
    original_link = md.renderer.rules['link_open']

    def reference(self, items, index, options, state):
        token = items[index]
        citation = citations.get(token.meta['id'])
        if citation is None:
            return original_ref(items, index, options, state)
        identity, label = citation
        return (f'<a class="pcs-citation" href="#{identity}" data-citation-target="{identity}" '
                f'role="button" aria-haspopup="dialog" aria-expanded="false">{html.escape(label)}</a>')

    def note_open(self, items, index, options, state):
        citation = citations.get(items[index].meta['id'])
        state['pcs_citation_active'] = citation is not None
        if citation is None:
            # Hidden source notes must not renumber an ordinary footnote's list
            # marker while its in-text numeric reference keeps the original ID.
            value = items[index].meta['id'] + 1
            return original_open(items, index, options, state).replace('<li ', f'<li value="{value}" ', 1)
        return f'<li id="{citation[0]}" class="footnote-item pcs-citation-note">\n'

    def note_close(self, items, index, options, state):
        state.pop('pcs_citation_active', None)
        return original_close(items, index, options, state)

    def link_open(self, items, index, options, state):
        anchor = original_link(items, index, options, state)
        target = items[index].attrGet('href') or ''
        if state.get('pcs_citation_active'):
            try:
                parsed = urlsplit(target)
                if parsed.scheme in ('https', 'http') and parsed.netloc and not any(ord(c) < 32 for c in target):
                    anchor = anchor[:-1] + ' data-citation-url="' + html.escape(target, quote=True) + '">'
            except ValueError:
                pass
        return anchor

    def block_open(self, items, index, options, state):
        original = original_block(items, index, options, state)
        if len(citations) == len(state.get('footnotes', {}).get('list', {})):
            return original.replace('footnotes-sep', 'footnotes-sep pcs-citations-only').replace(
                'class="footnotes"', 'class="footnotes pcs-citations-only"')
        return original

    md.add_render_rule('footnote_ref', reference)
    md.add_render_rule('footnote_open', note_open)
    md.add_render_rule('footnote_close', note_close)
    md.add_render_rule('footnote_block_open', block_open)
    md.add_render_rule('link_open', link_open)
