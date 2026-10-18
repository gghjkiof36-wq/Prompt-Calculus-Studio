"""Coordinated reference edits with checked writes and retained recovery bytes."""
from dataclasses import dataclass
from pathlib import Path
import json
import uuid
from PySide6.QtCore import QSaveFile,QIODevice
from .local_documents import paths
from .markdown_source import SourceLedger,edited_text,MAX_BYTES
from .markdown_links import Notes,relocation_edits
from .workspace_file_io import relocate,stamp


def rewritten_bytes(raw,edits):
    ledger=SourceLedger(raw)
    for edit in reversed(edits):ledger.pieces=ledger.replacement(edit.start,edit.end,edit.replacement)
    return ledger.encoded()


def checked_write(path,before,after):
    path=paths.checked(path.parent,path.name)
    if paths.read_bytes(path,MAX_BYTES)[0]!=before:raise ValueError('引用檔案已被外部修改：'+str(path))
    output=QSaveFile(str(path));output.setDirectWriteFallback(False)
    if not output.open(QIODevice.OpenModeFlag.WriteOnly):raise OSError(output.errorString())
    if output.write(after)!=len(after) or paths.read_bytes(path,MAX_BYTES)[0]!=before:
        output.cancelWriting();raise OSError('引用保存前內容已變更，原稿已保留：'+str(path))
    if not output.commit():raise OSError(output.errorString())


@dataclass
class Change:
    path:Path
    target:Path
    before:bytes
    after:bytes
    page:object=None
    snapshot:object=None
    edits:object=None


class RelocationPlan:
    def __init__(self,workspace,source,target):
        self.w=workspace;self.source=source;self.target=target;self.changes=[];self.written=[];self.applied=[];self.paused=[];self.journal=None;self.restored={}
        root=workspace.locations.root_for(source) or source.parent
        def read(path):
            tab=workspace.tab_for_path(path)
            return tab.widget.buffer.snapshot().text if tab and hasattr(tab.widget,'buffer') else Notes.disk(path)
        notes=Notes(root,read)
        for path in notes.files():
            raw=paths.read_bytes(path,MAX_BYTES)[0];ledger=SourceLedger(raw);text=ledger.text_of(ledger.pieces)
            destination=relocate(path,source,target)
            edits=relocation_edits(text,path,destination,source,target,notes)
            tab=workspace.tab_for_path(path);page=tab.widget if tab and hasattr(tab.widget,'buffer') else None
            snapshot=page.buffer.snapshot() if page else None
            buffer_edits=relocation_edits(snapshot.text,path,destination,source,target,notes) if page else []
            if not edits and not buffer_edits:continue
            if page:
                if page.buffer.composing or page.buffer.mapping_error or page.operation_error:raise ValueError('請先完成組字或處理文件狀態：'+path.name)
                if page.baseline!=raw:raise ValueError('引用文件有外部修改，請先處理衝突：'+path.name)
                edited_text(snapshot,buffer_edits)
                rewritten_bytes(page.encoded(),buffer_edits)
            self.changes.append(Change(path,destination,raw,rewritten_bytes(raw,edits),page,snapshot,buffer_edits))
    def validate(self):
        for change in self.changes:
            if paths.read_bytes(change.path,MAX_BYTES)[0]!=change.before:raise ValueError('引用在操作期間已變更：'+change.path.name)
            if change.page and change.page.buffer.snapshot()!=change.snapshot:raise ValueError('引用稿件已變更，請重新操作。')
    def pause(self):
        buffers={id(c.page.buffer) for c in self.changes if c.page}
        for tab in self.w.all_pages():
            if hasattr(tab.widget,'buffer') and id(tab.widget.buffer) in buffers and id(tab) not in self.w.autosave.paused:
                self.w.autosave.paused.add(id(tab));self.w.autosave.stop(tab);self.paused.append(tab)
    def resume(self):
        for tab in self.paused:self.w.autosave.paused.discard(id(tab));self.w.autosave.schedule(tab)
    def preserve(self):
        if not self.changes:return
        self.journal=self.w.data/'relation-recovery'/uuid.uuid4().hex;self.journal.mkdir(parents=True)
        for index,change in enumerate(self.changes):
            (self.journal/f'{index}.before.md').write_bytes(change.before)
            (self.journal/f'{index}.after.md').write_bytes(change.after)
        self.status('prepared')
    def status(self,state):
        if self.journal:
            (self.journal/'operation.json').write_text(json.dumps(dict(state=state,source=str(self.source),target=str(self.target),
                files=[dict(before=str(c.path),after=str(c.target),index=i) for i,c in enumerate(self.changes)]),ensure_ascii=False,indent=2),encoding='utf-8')
    def publish(self):
        for change in self.changes:
            if change.before!=change.after:
                checked_write(change.target,change.before,change.after);self.written.append(change)
    def apply_buffers(self):
        for change in self.changes:
            if change.page:
                if change.edits:
                    change.page.buffer.apply(change.snapshot,change.edits);self.applied.append(change)
                change.page.baseline=change.after
                change.page.document().setModified(change.page.encoded()!=change.after)
    def complete(self):
        # Updating recovery metadata is post-commit bookkeeping. A failure here
        # must not undo successful native edits and leave a false Redo command.
        try:self.status('complete')
        except OSError:self.w.show_notice('引用與位置已更新；回復紀錄尚未標記完成：'+str(self.journal))
    def rollback(self):
        errors=[]
        for change in reversed(self.written):
            try:
                checked_write(change.target,change.after,change.before)
                info=change.target.stat()
                self.restored[change.target]=(stamp(change.target),(info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns))
            except (OSError,ValueError) as error:errors.append(str(error))
        for change in reversed(self.applied):
            change.page.document().undo();change.page.baseline=change.before
            change.page.document().setModified(change.page.encoded()!=change.before)
        self.status('recovery-needed' if errors else 'rolled-back')
        if errors:
            for change in self.changes:
                if change.page:
                    change.page.operation_error=True;change.page.error='引用尚未完整回復，原稿保留於 '+str(self.journal)
            raise ValueError('\n'.join(errors)+'\n原稿副本：'+str(self.journal))

    def restored_source_stamp(self,expected):
        if self.target in self.restored:return self.restored[self.target][0]
        if len(expected)==3 and isinstance(expected[2],dict):
            entries=dict(expected[2])
            for path,(_,info) in self.restored.items():
                if self.target in path.parents:entries[path.relative_to(self.target).as_posix()]=info
            return expected[:2]+(entries,)
        return expected


def copy_edits(raw,source,target,root,read=None):
    ledger=SourceLedger(raw);text=ledger.text_of(ledger.pieces);notes=Notes(root,read)
    # Copying a note changes its outgoing relative locations; incoming links
    # continue pointing to the existing original.
    edits=relocation_edits(text,source,target,source,source,notes)
    return rewritten_bytes(raw,edits),edits
