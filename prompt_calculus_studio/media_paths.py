"""Resolve indexed media across portable data moves without guessing filenames."""
from pathlib import Path
import shutil


def within(root,relative,folder=None):
    if not isinstance(relative,str) or not relative: return None
    relative=Path(relative)
    if relative.is_absolute() or '..' in relative.parts: return None
    if folder and relative.parts[0]!=folder: return None
    root=Path(root).resolve(); path=(root/relative).resolve()
    return path if path.is_relative_to(root) else None


def legacy_root(record):
    """Only the exact stored original path may establish a former data root."""
    relative=record.get('original_relative'); stored=record.get('path')
    if not record.get('owned') or not relative or not stored: return None
    relative=Path(relative); path=Path(stored)
    if relative.is_absolute() or '..' in relative.parts or relative.parts[0]!='originals' or not path.is_absolute(): return None
    if tuple(p.casefold() for p in path.parts[-len(relative.parts):])!=tuple(p.casefold() for p in relative.parts): return None
    root=path.parents[len(relative.parts)-1]
    return root if within(root,str(relative),'originals')==path.resolve() else None


def original_path(directory,record):
    if record.get('owned'):
        local=within(directory,record.get('original_relative'),'originals')
        if local:
            if local.is_file(): return local
            old=legacy_root(record)
            previous=within(old,record['original_relative'],'originals') if old else None
            return previous if previous else local
    return Path(record.get('path') or '')


def preview_file(directory,record):
    thumb=within(directory,record.get('thumb'),'thumbnails')
    if thumb and thumb.is_file(): return thumb
    old=legacy_root(record)
    thumb=within(old,record.get('thumb'),'thumbnails') if old else None
    if thumb and thumb.is_file(): return thumb
    original=original_path(directory,record)
    return original if original.is_file() else None


def recover_owned_files(directory,records):
    """Explicit repair: copy exact indexed assets only; never replace/delete any file."""
    report=[]
    for record in records:
        old=legacy_root(record)
        if not old: continue
        for field,folder in (('original_relative','originals'),('thumb','thumbnails')):
            source=within(old,record.get(field),folder); target=within(directory,record.get(field),folder)
            if not source or not target or target.exists() or not source.is_file(): continue
            target.parent.mkdir(parents=True,exist_ok=True)
            created=False
            try:
                with source.open('rb') as incoming,target.open('xb') as outgoing:
                    created=True; shutil.copyfileobj(incoming,outgoing)
            except FileExistsError: continue
            except OSError:
                if created: target.unlink(missing_ok=True)
                raise
            report.append(dict(id=record['id'],kind=folder,source=str(source),destination=str(target)))
    return report
