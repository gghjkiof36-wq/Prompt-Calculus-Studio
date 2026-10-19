"""Checked, journalled multi-note composition with explicit one-operation undo."""
from pathlib import Path
import difflib
import json
import os
import re
import uuid
from PySide6.QtWidgets import QFormLayout, QPlainTextEdit
from .widgets import ComboBox as QComboBox,style_completion
from .markdown_source import SourceEdit,SourceSnapshot,edited_text,MAX_BYTES
from .markdown_links import Notes,relocation_edits,links,note_paths
from .markdown_relocation import checked_write,rewritten_bytes
from .notebook_actions import template_edits,create_note
from .notebook_dialogs import Sheet,decoded,NotePreview
from .local_documents import paths


def delta(before,after):
    return [SourceEdit(a,b,after[c:d]) for op,a,b,c,d in difflib.SequenceMatcher(None,before,after).get_opcodes() if op!='equal']


def apply_text(text,edits):
    for edit in reversed(edits):text=text[:edit.start]+edit.replacement+text[edit.end:]
    return text


class Composition:
    def __init__(self,actions,surface,target,extract=False,replacement='link',prepend=False):
        self.actions=actions; self.w=actions.w; self.root=actions.root(); self.surface=surface
        self.source=surface.page.path; self.target=target; self.extract=extract; self.changes=[]; self.paused=[]
        self.journal=None; self.committed=False; self.written=[]; self.applied=[]; self.archived=False
        if self.source==target:raise ValueError('來源與目的筆記不能相同。')
        if self.root not in target.parents:raise ValueError('目的筆記不在目前資料夾。')
        paths.checked(self.root,target.relative_to(self.root).as_posix())
        if not target.parent.is_dir():raise ValueError('目的資料夾不存在。')
        snapshot,start,end=surface.selection()
        if extract and start==end:raise ValueError('請先選取要擷取的內容。')
        def current(path):
            tab=self.w.tab_for_path(path)
            return tab.widget.buffer.snapshot().text if tab and hasattr(tab.widget,'buffer') else Notes.disk(path)
        notes=Notes(self.root,current)
        source=snapshot.text; piece=source[start:end] if extract else source
        moved_anchors={s[3] for s in __import__(__package__+'.markdown_commands',fromlist=['heading_sections']).heading_sections(piece)}
        moved_anchors.update('^'+m.group(1) for m in re.finditer(r'(?m)\^([\w-]+)\s*$',piece))
        piece=apply_text(piece,relocation_edits(piece,self.source,target,self.source,target if not extract else self.source,notes))
        target_text=current(target) if target.exists() else ''
        from .markdown_properties import frontmatter
        stop,_,error=frontmatter(target_text)
        if error:raise ValueError(error)
        position=stop if prepend else len(target_text)
        prefix='\n\n' if position and not target_text[:position].endswith('\n\n') else ''
        insertion=template_edits(target_text,piece,position,position)
        # Preserve YAML at the beginning; separate concatenated prose.
        insertion=[SourceEdit(e.start,e.end,(prefix if e.start==position and e.replacement else '')+e.replacement+('\n\n' if prepend and e.start==position else '')) for e in insertion]
        result=apply_text(target_text,insertion)
        if extract:
            name=target.relative_to(self.root).with_suffix('').as_posix()
            replacement_text=('!'+('[['+name+']]') if replacement=='embed' else '[['+name+']]' if replacement=='link' else '')
            source_result=source[:start]+replacement_text+source[end:]
        else:source_result=None
        for path in list(notes.files())+([] if target.exists() else [target]):
            before=paths.read_bytes(path,MAX_BYTES)[0] if path.exists() else None
            tab=self.w.tab_for_path(path); page=tab.widget if tab and hasattr(tab.widget,'buffer') else None
            raw=page.encoded() if page else before
            text=decoded(raw) if raw is not None else ''
            if page and (page.buffer.composing or page.operation_error or page.baseline!=before):
                raise ValueError('文件有未完成的組字或外部變更：'+path.name)
            if path==target:after_text=result
            elif path==self.source:after_text=source_result
            else:
                edits=relocation_edits(text,path,path,self.source,target,notes)
                if extract:
                    moved=[]
                    for link in links(text):
                        targets,anchor=notes.resolve(link.target,path,link.wiki)
                        if self.source in targets and anchor in moved_anchors:moved.append((link.target_start,link.target_end))
                    edits=[e for e in edits if any(a<=e.start and e.end<=b for a,b in moved)]
                after_text=apply_text(text,edits)
            if after_text==text and path!=self.source:continue
            edits=delta(text,after_text) if after_text is not None else []
            after=rewritten_bytes(raw or b'',edits) if after_text is not None else None
            if after is not None and len(after)>MAX_BYTES:raise ValueError('整理後文件超過 8 MiB。')
            self.changes.append(dict(path=path,before=before,after=after,buffer_before=raw,page=page,
                snapshot=page.buffer.snapshot() if page else None,edits=edits))
        self.source_snapshot=snapshot

    def validate(self,undo=False):
        for c in self.changes:
            path=paths.checked(self.root,c['path'].relative_to(self.root).as_posix())
            expected=c['after'] if undo else c['before']
            actual=paths.read_bytes(path,MAX_BYTES)[0] if path.exists() else None
            if actual!=expected:raise ValueError('文件已變更，整理未執行：'+path.name)
            page=c['page']
            if not undo and page and page.buffer.snapshot()!=c['snapshot']:raise ValueError('編輯內容已變更，請重新整理。')
            if undo:
                tab=self.w.tab_for_path(path)
                if tab and hasattr(tab.widget,'dirty') and tab.widget.dirty:raise ValueError('請先保存或復原後續編輯：'+path.name)

    def pause(self):
        for tab in self.w.all_pages():
            if tab.path in {c['path'] for c in self.changes}:
                self.w.autosave.paused.add(id(tab));self.w.autosave.stop(tab);self.paused.append(tab)
    def resume(self):
        for tab in self.paused:self.w.autosave.paused.discard(id(tab));self.w.autosave.schedule(tab)
        self.paused=[]
    def status(self,state):
        (self.journal/'operation.json').write_text(json.dumps({'state':state,'source':str(self.source),'target':str(self.target),
            'files':[{'path':str(c['path']),'index':i,'created':c['before'] is None,'removed':c['after'] is None} for i,c in enumerate(self.changes)]},ensure_ascii=False,indent=2),encoding='utf-8')
    def commit(self):
        self.validate();self.pause()
        self.journal=self.w.data/'note-composition'/uuid.uuid4().hex;self.journal.mkdir(parents=True)
        try:
            for i,c in enumerate(self.changes):
                for key in ('before','after','buffer_before'):
                    if c[key] is not None:(self.journal/f'{i}.{key}.md').write_bytes(c[key])
                if c['before'] is not None:self.actions.m.store.capture(c['path'],c['buffer_before'],'整理前',True)
            self.status('prepared');self.validate()
            for c in sorted(self.changes,key=lambda c:c['after'] is None):
                if c['after'] is None:
                    if c['path'].read_bytes()!=c['before']:raise ValueError('來源已變更。')
                    os.replace(c['path'],self.journal/'merged-source.md');self.archived=True
                elif c['before'] is None:
                    create_note(self.root,c['path'].relative_to(self.root).as_posix(),c['after'])
                else:checked_write(c['path'],c['before'],c['after'])
                self.written.append(c)
            for c in self.changes:
                if c['page'] and c['after'] is not None:
                    self.applied.append(c)
                    if c['edits']:c['page'].buffer.apply(c['snapshot'],c['edits'])
                    c['page'].baseline=c['after'];c['page'].document().setModified(False)
            self.status('complete');self.committed=True
        except Exception:
            failures=[]
            for c in reversed(self.written):
                try:self.restore_file(c)
                except Exception as error:failures.append(str(error))
            for c in reversed(self.applied):
                try:self.restore_buffer(c,c['page'])
                except Exception as error:failures.append(str(error))
            self.status('recovery-needed' if failures else 'rolled-back')
            if failures:raise ValueError('整理尚未完整復原。原稿保存於 '+str(self.journal)+'\n'+'\n'.join(failures))
            raise
        finally:self.resume()
        if not self.extract:
            for pane in list(self.w.panes):
                for i in reversed(range(pane.strip.bar.count())):
                    tab=self.w.tabs_by_key.get(pane.strip.bar.tabData(i))
                    if tab and tab.path==self.source:self.w.close_tab(i,decision='discard',pane=pane)
        self.w.refresh_tree();self.actions.m.open_note(self.target);self.actions.m.schedule();self.actions.last_composition=self
    def restore_file(self,c):
        path=c['path']
        if c['after'] is None:
            if path.exists():raise ValueError('來源位置已有新檔案。')
            create_note(self.root,path.relative_to(self.root).as_posix(),c['before'])
        elif c['before'] is None:
            if path.read_bytes()!=c['after']:raise ValueError('新筆記已有後續變更。')
            path.unlink()
        else:checked_write(path,c['after'],c['before'])
    def restore_buffer(self,c,page):
        snapshot=page.buffer.snapshot();before=decoded(c['buffer_before'])
        if snapshot.text!=before:page.buffer.apply(snapshot,delta(snapshot.text,before))
        page.baseline=c['before'];page.document().setModified(page.encoded()!=c['before'])
    def close_path(self,path):
        for pane in list(self.w.panes):
            for i in reversed(range(pane.strip.bar.count())):
                tab=self.w.tabs_by_key.get(pane.strip.bar.tabData(i))
                if tab and tab.path==path:self.w.close_tab(i,decision='discard',pane=pane)
    def undo(self):
        if not self.committed:return
        self.validate(True);self.pause()
        restored=[]
        try:
            for c in reversed(self.changes):self.restore_file(c);restored.append(c)
            self.status('undone')
        except Exception:
            failures=[]
            for c in reversed(restored):
                try:self.restore_file(dict(c,before=c['after'],after=c['before']))
                except Exception as error:failures.append(str(error))
            self.status('recovery-needed' if failures else 'complete')
            if failures:raise ValueError('復原尚未完成。原稿保存於 '+str(self.journal)+'\n'+'\n'.join(failures))
            raise
        finally:self.resume()
        try:
            for c in self.changes:
                if c['before'] is None:self.close_path(c['path'])
            self.actions.m.open_note(self.source)
            for c in self.changes:
                tab=self.w.tab_for_path(c['path'])
                if tab and hasattr(tab.widget,'buffer') and c['before'] is not None:
                    self.restore_buffer(c,tab.widget)
            self.committed=False
        finally:self.w.refresh_tree();self.actions.m.schedule()


class ComposerDialog(Sheet):
    def __init__(self,actions,surface,extract):
        super().__init__(actions.w,'擷取選取內容' if extract else '合併筆記',620,470)
        self.actions=actions;self.surface=surface;self.extract=extract;self.plan=None
        form=QFormLayout();self.destination=QComboBox();self.destination.setEditable(True)
        style_completion(self.destination.completer(),self.destination)
        self.destination.setAccessibleName('目的筆記')
        if extract:self.destination.addItem('')
        for p in note_paths(actions.root()):
            if p!=surface.page.path:self.destination.addItem(p.relative_to(actions.root()).with_suffix('').as_posix())
        form.addRow('目的筆記',self.destination)
        self.position=QComboBox();self.position.addItems(['附加到末尾','插入到開頭']);form.addRow('位置',self.position)
        self.replace=QComboBox();self.replace.addItems(['連結','嵌入','不保留文字'])
        if extract:form.addRow('原位置',self.replace)
        self.body.addLayout(form);self.preview=NotePreview(surface);self.body.addWidget(self.preview,1)
        self.footer(self.commit,'擷取' if extract else '合併')
        self.destination.currentTextChanged.connect(self.prepare);self.position.currentIndexChanged.connect(self.prepare)
        self.replace.currentIndexChanged.connect(self.prepare);self.prepare()
    def prepare(self,*_):
        self.plan=None;self.error.hide()
        try:
            relative=self.destination.currentText().strip()
            if not relative:self.apply_button.setEnabled(False);self.preview.clear();return
            if not relative.lower().endswith('.md'):relative+='.md'
            target=paths.checked(self.actions.root(),relative)
            self.plan=Composition(self.actions,self.surface,target,self.extract,['link','embed','none'][self.replace.currentIndex()],self.position.currentIndex()==1)
            change=next(c for c in self.plan.changes if c['path']==target)
            self.preview.set_note(decoded(change['after']),target);self.apply_button.setEnabled(True)
        except (OSError,ValueError) as error:self.error.setText(str(error));self.error.show();self.apply_button.setEnabled(False)
    def commit(self):
        if not self.plan:return False
        self.plan.commit()
