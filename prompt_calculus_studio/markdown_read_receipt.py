"""Per-reading content evidence. Pure values only; no UI, cache or global revision."""
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path

from .local_documents import paths
from .markdown_links import Notes, note_paths
from .markdown_source import MAX_BYTES


class EmbedHandoff(Exception):
    """The real embed renderer reached child work on the GUI thread."""


def handoff_embed():
    raise EmbedHandoff()


def disk_content(path):
    """The digest covers the same bounded bytes the parser will consume."""
    path=paths.checked(path.parent,path.name)
    raw,_=paths.read_bytes(path,MAX_BYTES)
    evidence=('bytes',len(raw),hashlib.sha256(raw).hexdigest())
    return raw,evidence


def disk_evidence(path):
    try:return disk_content(path)[1]
    except (ValueError,OSError) as error:return ('error',type(error).__name__,str(error))


@dataclass(frozen=True)
class ReadReceipt:
    root:Path
    reads:tuple
    probes:tuple
    names:tuple|None
    directories:tuple

    @property
    def read_paths(self):return frozenset(path for path,_ in self.reads)

    def watch_paths(self):
        # The root or one of its ancestors may have become a reparse point
        # since the worker read it. Never let RelationWatch bind that new root.
        paths.root_path(self.root)
        # Include the ancestors of failed probes: creating a previously missing
        # folder/target can change resolution without touching any selected child.
        targets={self.root,*self.directories,*self.read_paths}
        for value,base,_ in self.probes:
            target=Path(os.path.abspath(base/value))
            if target.is_relative_to(self.root):targets.add(target)
        wanted=set()
        for target in targets:
            if not target.is_relative_to(self.root):continue
            # Rejected probes may name a junction. Watch its containing folder,
            # never the link or descendants that QFileSystemWatcher would follow.
            current=self.root
            for component in target.relative_to(self.root).parts:
                candidate=current/component
                try:linked=paths.is_link(candidate)
                except FileNotFoundError:linked=False
                except OSError:break
                if linked:break
                current=candidate
            wanted.add(current)
        for path in tuple(wanted):
            while path!=self.root and path.is_relative_to(self.root):
                path=path.parent;wanted.add(path)
        # RelationWatch always adds root itself. Do not accidentally ask it to
        # watch root.parent through its ordinary dependency-parent rule.
        wanted.discard(self.root)
        return wanted


class ReceiptNotes(Notes):
    def __init__(self,root,overrides):
        super().__init__(root,self._read)
        self.overrides=overrides
        self.reads={};self.probes={};self.names=None;self.directories=set()

    def _read(self,path):
        if path in self.overrides:
            self.reads[path]=('buffer',)
            return self.overrides[path]
        try:raw,evidence=disk_content(path)
        except (ValueError,OSError) as error:
            self.reads[path]=('error',type(error).__name__,str(error));raise
        self.reads[path]=evidence
        return raw.decode('utf-8-sig').replace('\r\n','\n').replace('\r','\n')

    def files(self):
        if self._paths is None:
            self._paths=list(note_paths(self.root,visited=self.directories.add))
            self.names=tuple(sorted(self._paths))
        return self._paths

    def candidate(self,value,base):
        result=super().candidate(value,base)
        self.probes[(value,base)]=result
        return result

    def receipt(self):
        return ReadReceipt(self.root,tuple(self.reads.items()),
            tuple((value,base,result) for (value,base),result in self.probes.items()),
            self.names,tuple(sorted(self.directories)))


def verify_receipt(receipt):
    """Run after installing watches, in a worker; never use stat as content proof.

    Open-buffer identity/revision is checked by the GUI request owner. This is
    not an atomic filesystem snapshot: subsequent watch events invalidate it.
    """
    try:
        notes=Notes(receipt.root)
        for value,base,result in receipt.probes:
            if notes.candidate(value,base)!=result:return False
        if receipt.names is not None:
            visited=set()
            if tuple(sorted(note_paths(receipt.root,visited=visited.add)))!=receipt.names:return False
            if tuple(sorted(visited))!=receipt.directories:return False
        for path,evidence in receipt.reads:
            checked=paths.checked(receipt.root,path.relative_to(receipt.root).as_posix())
            if checked!=path:return False
            if evidence[0]!='buffer' and disk_evidence(path)!=evidence:return False
        return True
    except (ValueError,OSError):return False
