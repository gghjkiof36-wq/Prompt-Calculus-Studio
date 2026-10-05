"""Apply an edit inverse while retaining unrelated asynchronous state changes."""
import copy

MISSING=object()


def merge(current,expected,target):
    if expected==target:return copy.deepcopy(current) if current is not MISSING else MISSING
    if current==target:return copy.deepcopy(current) if current is not MISSING else MISSING
    if current==expected:return copy.deepcopy(target) if target is not MISSING else MISSING
    if all(isinstance(v,dict) for v in (current,expected,target)):
        result={}
        for key in current.keys()|expected.keys()|target.keys():
            value=merge(current.get(key,MISSING),expected.get(key,MISSING),target.get(key,MISSING))
            if value is not MISSING:result[key]=value
        return result
    if all(isinstance(v,list) and all(isinstance(k,dict) and isinstance(k.get('id'),str) for k in v)
           and len(v)==len({k['id'] for k in v}) for v in (current,expected,target)):
        values=merge(*({k['id']:k for k in v} for v in (current,expected,target)))
        order=merge(*([k['id'] for k in v] for v in (current,expected,target)))
        return [values[key] for key in order if key in values]
    if all(isinstance(v,list) and all(isinstance(k,str) for k in v) for v in (current,expected,target)):
        removed=set(expected)-set(target);added=set(target)-set(expected)
        result=[k for k in current if k not in removed and k not in added]
        for i,key in enumerate(target):
            if key in added:
                preceding=next((p for p in reversed(target[:i]) if p in result),None)
                result.insert(result.index(preceding)+1 if preceding else 0,key)
        if removed or added:return result
    raise ValueError('這次編輯的內容已被其他操作修改，無法安全復原；紀錄仍保留。')
