"""Root-bound note creation, templates, and workspace commands."""
from datetime import datetime
from pathlib import Path
import os
import random
import re
import sqlite3
from .local_documents import paths
from .markdown_links import Notes, note_paths
from .markdown_source import SourceEdit, MAX_BYTES
from .markdown_properties import frontmatter


def moment_format(now,pattern):
    values={'YYYY':f'{now.year:04d}','YY':f'{now.year%100:02d}','MM':f'{now.month:02d}',
            'DD':f'{now.day:02d}','HH':f'{now.hour:02d}','mm':f'{now.minute:02d}','ss':f'{now.second:02d}'}
    return re.sub(r'\[([^\]]*)\]|YYYY|YY|MM|DD|HH|mm|ss',lambda m:m.group(1) if m.group(1) is not None else values[m.group()],pattern)


def expand_template(text,title,now=None):
    now=now or datetime.now()
    def variable(match):
        key,_,format=match.group(1).partition(':'); key=key.strip()
        if key=='title':return title
        if key in ('date','time'):return moment_format(now,format or ('YYYY-MM-DD' if key=='date' else 'HH:mm'))
        return match.group()
    return re.sub(r'\{\{([^{}]+)\}\}',variable,text)


def template_edits(source,template,start,end):
    """Merge only missing properties; keep existing and compound YAML verbatim."""
    stop,props,error=frontmatter(source); tstop,tprops,terror=frontmatter(template)
    if error or terror:raise ValueError(error or terror)
    edits=[]; body=template[tstop:].lstrip('\n') if tstop else template
    if tstop:
        if stop:
            names={p.name for p in props}; missing=''.join(p.raw for p in tprops if p.name not in names)
            closing=re.search(r'(?m)^(?:---|\.\.\.)[ \t]*(?:\n|$)',source[4:stop])
            if missing:edits.append(SourceEdit(4+closing.start(),4+closing.start(),missing))
        elif start==0:
            body=template[:tstop]+('\n' if not template[:tstop].endswith('\n\n') else '')+body
        else:edits.append(SourceEdit(0,0,template[:tstop]+'\n'))
    if stop and start<stop:raise ValueError('請將插入位置移到屬性下方。')
    edits.append(SourceEdit(start,end,body)); return edits


def create_note(root,relative,text=''):
    root=paths.root_path(root)
    if not relative.lower().endswith('.md'):relative+='.md'
    if any(part.casefold() in ('.pcs','.git','assets') for part in paths.relative_parts(relative)):
        raise ValueError('不能在內部資料夾建立筆記。')
    target=paths.checked(root,relative,new=True)
    raw=text if isinstance(text,bytes) else text.encode('utf-8')
    raw.decode('utf-8-sig')
    if len(raw)>MAX_BYTES:raise ValueError('文件超過 8 MiB。')
    if not target.parent.is_dir():
        # Recheck every created ancestor instead of following a replacement link.
        current=root
        for part in target.parent.relative_to(root).parts:
            current=paths.checked(root,(current/part).relative_to(root).as_posix())
            current.mkdir(exist_ok=True)
    target=paths.checked(root,relative,new=True)
    fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    try:
        with os.fdopen(fd,'wb') as stream:stream.write(raw); stream.flush(); os.fsync(stream.fileno())
    except Exception:
        # The exclusive file belongs to this attempt and has never been opened.
        target.unlink(missing_ok=True); raise
    return target


class NotebookActions:
    def __init__(self,manager):self.m=manager; self.w=manager.w; self.last_composition=None
    def guarded(self,action):
        try:return action()
        except (OSError,ValueError,UnicodeError,sqlite3.Error) as error:self.w.show_notice(str(error)); return None
    def root(self):
        root=self.m.current_root()
        if root is None:raise ValueError('尚未開啟內容資料夾。')
        return paths.root_path(root)
    def config(self):return self.m.store.get('notebook:'+str(self.root()),{})
    def available(self):
        root=self.root(); folder=self.config().get('templates','範本')
        directory=paths.checked(root,folder)
        return list(note_paths(directory)) if directory.is_dir() else []
    def created(self,path):
        self.w.refresh_tree(); self.m.open_note(path); self.m.schedule()
        self.m.store.capture(path,path.read_bytes(),'建立筆記',True)
        return path
    def create(self,name,text=''):return self.created(create_note(self.root(),name,text))
    def new_named(self):
        from .notebook_dialogs import NameDialog
        dialog=NameDialog(self.w,'新增筆記','檔案名稱',lambda name:self.create(name))
        self.m.keep(dialog)
    def unique(self):
        root=self.root(); base=datetime.now().strftime('%Y%m%d%H%M%S')
        for index in range(10000):
            name=base+('-'+str(index) if index else '')
            if not paths.checked(root,name+'.md').exists():return self.create(name)
        raise ValueError('無法產生不重複的筆記名稱。')
    def daily(self):
        config=self.config(); name=moment_format(datetime.now(),config.get('daily_format','YYYY-MM-DD'))
        relative=(config.get('daily_folder','日誌').rstrip('/')+'/'+name).lstrip('/')
        path=paths.checked(self.root(),relative+'.md')
        if path.exists():self.m.open_note(path); return path
        template=config.get('daily_template',''); text=''
        if template:text=expand_template(Notes.disk(paths.checked(self.root(),template)),name)
        return self.create(relative,text)
    def random(self):
        notes=list(note_paths(self.root()))
        if notes:self.m.open_note(random.choice(notes))
        else:self.w.show_notice('資料夾內沒有筆記。')
    def settings(self):
        from .notebook_dialogs import NotebookSettings
        self.m.keep(NotebookSettings(self))
    def insert_template(self,surface):
        from .notebook_dialogs import ChoiceDialog
        snapshot,start,end=surface.selection()
        def insert(path):
            if surface.page.buffer.snapshot()!=snapshot:raise ValueError('文件已變更，請重新選擇插入位置。')
            text=expand_template(Notes.disk(path),surface.page.path.stem)
            surface.apply(template_edits(snapshot.text,text,start,end))
        candidates=self.available()
        if not candidates:self.settings(); return
        self.m.keep(ChoiceDialog(self.w,'插入範本',[(p.stem,p) for p in candidates],insert))
    def preview_link(self,surface):
        from .markdown_relations import link_at,notes_for
        link=link_at(surface)
        if not link:return
        notes=notes_for(surface); targets,anchor=notes.resolve(link.target,surface.page.path,link.wiki)
        if targets:
            text=notes.text(targets[0]); start,_=notes.region(text,anchor)
            self.m.preview(targets[0],start)
    def compose(self,surface,extract=False):
        from .notebook_composer import ComposerDialog
        self.m.keep(ComposerDialog(self,surface,extract))
    def undo_composition(self):
        if self.last_composition:self.last_composition.undo(); self.last_composition=None
    def recover(self,surface):
        from .notebook_dialogs import RecoveryDialog
        self.m.keep(RecoveryDialog(self.m,surface))
    def workspaces(self):
        from .notebook_dialogs import WorkspacesDialog
        self.m.keep(WorkspacesDialog(self.m))
    def advanced(self,surface):
        from .markdown_advanced import AdvancedDialog
        self.m.keep(AdvancedDialog(surface))
    def shortcuts(self,surface):
        from .markdown_shortcuts import ShortcutDialog
        self.m.keep(ShortcutDialog(surface))
    def pdf(self,surface):
        from .markdown_pdf import export_pdf
        export_pdf(surface)
    def entries(self,surface):
        actions=[('新增筆記',self.new_named),('開啟今日筆記',self.daily),('新增不重複筆記',self.unique),
                 ('開啟隨機筆記',self.random),('插入範本',lambda:self.insert_template(surface)),
                 ('範本與日誌設定',self.settings),('合併目前筆記…',lambda:self.compose(surface)),
                 ('擷取選取內容…',lambda:self.compose(surface,True)),('復原上次筆記整理',self.undo_composition),
                 ('檔案復原…',lambda:self.recover(surface)),('工作區布局…',self.workspaces),
                 ('進階編輯…',lambda:self.advanced(surface)),('編輯快捷鍵…',lambda:self.shortcuts(surface)),
                 ('匯出 PDF…',lambda:self.pdf(surface))]
        return [(title,lambda fn=fn:self.guarded(fn)) for title,fn in actions]
