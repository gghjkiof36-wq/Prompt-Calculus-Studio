"""Exact tag exclusions; keep original syntax when nothing is removed."""
import re


def tag_key(text):
    return ' '.join(text.strip().replace('_',' ').casefold().split())


def positions(text,delimiters):
    stack=[]; escaped=False; found=[]
    for index,char in enumerate(text):
        if escaped: escaped=False; continue
        if char=='\\': escaped=True; continue
        if char in '([<': stack.append(char)
        elif char in ')]>' and stack and stack[-1]=={')':'(',']':'[','>':'<'}[char]: stack.pop()
        elif char in delimiters and not stack: found.append(index)
    return found


def filter_tags(text,blocked):
    """Remove comma-delimited tags, including tags inside weighted groups."""
    original=text; value=text.strip(); removed=[]
    def padded(result):
        return original[:len(original)-len(original.lstrip())]+result+original[len(original.rstrip()):] if result else ''
    if tag_key(value) in blocked: return '',[value]
    cuts=positions(value,',，\n')
    if cuts:
        starts=[0]+[i+1 for i in cuts]; ends=cuts+[len(value)]; kept=[]
        for start,end in zip(starts,ends):
            part,hits=filter_tags(value[start:end],blocked); removed.extend(hits)
            if part.strip(): kept.append((start,part))
        if not removed: return original,[]
        return padded(''.join(('' if i==0 else value[start-1])+part for i,(start,part) in enumerate(kept)).strip()),removed
    if len(value)>2 and value[0] in '([' and value[-1]=={'(':')','[':']'}[value[0]]:
        # Only unwrap a group that spans the entire fragment.
        depth=0; escaped=False; closes=[]
        for i,c in enumerate(value):
            if escaped: escaped=False; continue
            if c=='\\': escaped=True; continue
            if c==value[0]: depth+=1
            elif c==value[-1]:
                depth-=1
                if depth==0: closes.append(i)
        if closes==[len(value)-1]:
            inner=value[1:-1]; suffix=''; colons=positions(inner,':')
            if colons and re.fullmatch(r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)',inner[colons[-1]+1:].strip()):
                at=colons[-1]; suffix=inner[at:]; inner=inner[:at]
            filtered,hits=filter_tags(inner,blocked)
            if hits: return padded(value[0]+filtered+suffix+value[-1] if filtered.strip() else ''),hits
    return original,[]


def exclusion_rules(state):
    selected={i for ids in state['selections'].values() for i in ids}
    rules={}
    for item in state['items']:
        if item['id'] in selected:
            for tag in item.get('excludes',[]):
                rules.setdefault(tag_key(tag),[]).append(item['name'])
    return rules
