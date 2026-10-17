"""One captured file target, one suspension boundary, across every UI entrance."""
import copy
from dataclasses import dataclass, replace
from pathlib import Path
import uuid

from PySide6.QtCore import Qt, QTimer, QObject, QEvent
from PySide6.QtWidgets import (QApplication, QDialog, QWidget, QLineEdit, QPlainTextEdit,
    QTextEdit, QAbstractSpinBox, QComboBox)

from .local_documents import io, assets, identity, paths, schema
from .workspace_locations import KINDS, path_key
from .workspace_files import MarkdownPage
from .workspace_file_io import (named_path, stamp, relocate, prepare_move,
    rename_exclusive, create_markdown, validate_markdown_location, PublishedMarkdownError)


@dataclass
class FileOperation:
    context: object
    source: Path
    pages: list
    expected: object
    result: object = None


class WorkspaceFileOperations(QObject):
    def __init__(self, workspace):
        super().__init__(workspace)
        self.w = workspace
        self.pending = []
        self.inline = None
        QApplication.instance().installEventFilter(self)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_F2 and not event.modifiers():
            if self.rename_focused():
                event.accept()
                return True
        return super().eventFilter(watched, event)

    def busy(self, path):
        return path is not None and any(path == op.source or path in op.source.parents
            or op.source in path.parents for op in self.pending)

    def begin(self, context, rescue=False):
        source = context.path
        if source is None or context.kind == 'root':
            raise ValueError('請選擇文件或一般子資料夾。')
        if self.busy(source):
            raise ValueError('這個項目正在操作中，請先完成或取消。')
        pages = [tab for tab in self.w.all_pages() if tab.path and (tab.path == source or source in tab.path.parents)]
        if any(self.w.autosave.key(tab) in self.w.autosave.saving for tab in pages):
            raise ValueError('文件正在保存，請稍後再試。')
        if any(self.w.autosave.editing_dialog(tab) for tab in pages):
            raise ValueError('請先完成素材表單的確認或取消。')
        for tab in pages:
            self.w.autosave.paused.add(id(tab))
            self.w.autosave.stop(tab)
        try:
            expected = stamp(source) if source.exists() else None
            if expected is None and not (rescue and pages):
                raise ValueError('來源已不存在：' + str(source))
            op = FileOperation(context, source, pages, expected)
            self.pending.append(op)
            return op
        except BaseException:
            for tab in pages:
                self.w.autosave.paused.discard(id(tab))
                self.w.autosave.schedule(tab)
            raise

    def end(self, op):
        if op not in self.pending:
            return
        self.pending.remove(op)
        for tab in op.pages:
            self.w.autosave.paused.discard(id(tab))
            self.w.autosave.schedule(tab)
        self.refresh(op.result if isinstance(op.result, Path) else None)

    def refresh(self, selected=None):
        try:
            self.w.refresh_tree()
            for tab in self.w.all_pages():
                self.w.update_tab(tab)
            if selected:
                self.w.select_path(selected)
            self.w.save_recent()
        except OSError as error:
            self.w.show_notice('文件已完成操作，但導覽紀錄尚未保存：' + str(error))

    def block_pages(self, op, message, locations):
        for page in op.pages:
            owner = page.document or page.widget
            owner.error, owner.operation_error = message, True
            owner.recovery_paths = [str(p) for p in locations]
            self.w.autosave.stop(page)
            self.w.update_tab(page)

    def source_tab(self, op):
        return self.w.tabs_by_key.get(op.context.tab_key) or self.w.tab_for_path(op.source)

    def run(self, action, context, *, name=None, directory=None):
        if action == 'move' and directory is None:
            from .widgets import RoundMenu
            from .workspace_move_menu import MoveMenu
            from PySide6.QtGui import QCursor
            menu = RoundMenu(self.w)
            menu.move_session = MoveMenu(self, menu, context)
            menu.open_at(QCursor.pos())
            return None
        if self.inline is not None and not self.busy(context.path):
            self.inline.cancel()
        # Writes are synchronous on the UI thread. A reentrant request waits
        # until the current write has returned, without flushing a pending timer.
        affected = [t for t in self.w.all_pages() if t.path and (t.path == context.path or context.path in t.path.parents)] if context.path else []
        if any(self.w.autosave.key(t) in self.w.autosave.saving for t in affected):
            QTimer.singleShot(0, lambda: self.run(action, context, name=name, directory=directory))
            return None
        try:
            op = self.begin(context, rescue=action == 'save_as')
        except (ValueError, OSError) as error:
            self.w.show_notice(str(error))
            return None
        deferred = False
        try:
            from .workspace_file_dialogs import SaveAsDialog, RenameDialog, InlineRename
            if action == 'save_as':
                commit = lambda n, p: self.save_as(op, n, p)
                if name is not None:
                    return commit(name, directory or op.source.parent)
                dialog = SaveAsDialog(self.w, KINDS[op.source.suffix.casefold()], op.source, commit)
            elif action == 'rename':
                commit = lambda n: self.rename(op, n)
                if name is not None:
                    return commit(name)
                item = self.w.path_items.get(path_key(op.source))
                if context.origin == 'tree' and item is not None:
                    def finish():
                        self.inline = None
                        self.end(op)
                    self.inline = InlineRename(self.w.tree, item, op.source, commit, finish)
                    deferred = True
                    return None
                dialog = RenameDialog(self.w, op.source, commit)
            elif action == 'move':
                root = self.w.locations.root_for(op.source)
                if root is None:
                    raise ValueError('請先將所在資料夾加入側欄，再使用「移動到」。')
                commit = lambda p: self.move(op, p)
                if directory is not None:
                    return commit(directory)
            else:
                raise ValueError('不支援的文件操作。')
            dialog.exec()
            return op.result
        except (ValueError, OSError) as error:
            self.w.show_notice(str(error))
            return None
        finally:
            if not deferred:
                self.end(op)

    def save_as(self, op, name, directory):
        w = self.w
        parent = w.locations.directory(directory)
        target = named_path(parent, name, op.source.suffix)
        paths.checked(parent, target.name, new=True)
        tab = self.source_tab(op)
        if tab is None:
            self.check_source(op)
        if op.source.suffix.casefold() == '.md':
            if tab:
                raw = tab.widget.encoded()
            else:
                raw = paths.read_bytes(op.source, 8*1024*1024)[0]
                raw.decode('utf-8-sig')
            validate_markdown_location(raw.decode('utf-8-sig'), op.source, target)
            if tab is None:
                self.check_source(op)
            try:
                create_markdown(target, raw)
            except PublishedMarkdownError as error:
                self.block_pages(op, str(error) + '\n新檔位置：' + str(target), [op.source, target])
                raise
            result = None
        else:
            document = tab.document if tab else None
            if document:
                if not document.flush_views():
                    raise ValueError('目前有尚未套用的畫布編輯，請先處理後再另存。')
                document.host.remember_canvas_view()
                source = document.project(remap=False)
                source_root = document.data
            else:
                source = io.load(op.source.parent, op.source.name).document
                source_root = op.source.parent
            duplicate = identity.duplicate(source)
            candidate = assets.prepare(duplicate.document, source_root, target.parent)
            if document:
                # Stage the new manifest in the same isolated runtime asset root
                # before publishing. Native widgets/Undo keep their existing IDs.
                assets.prepare(duplicate.document, source_root, document.data)
            if tab is None:
                self.check_source(op)
            result = io.create(target.parent, target.name, candidate)
            if result.status != 'saved':
                details = '\n'.join(result.recovery_paths)
                if target.exists():
                    try:
                        published = io.load(target.parent, target.name).document['document_id'] == candidate['document_id']
                    except (ValueError, OSError):
                        published = True
                    if published:
                        self.block_pages(op, '另存結果需要確認，已停止自動儲存：\n' + str(target), [target, *result.recovery_paths])
                raise ValueError((result.error or '另存尚未完成。') + ('\n保留的內容：\n'+details if details else ''))
        try:
            if tab:
                w.autosave.forget(tab)
                old_key = tab.key
                new_key = result.document['document_id'] if result else 'md:' + uuid.uuid4().hex
                if result:
                    document.document, document.receipt = result.document, result.receipt
                    document.path, document.key = target, new_key
                    document.identity_maps = duplicate.id_maps
                    document.baseline = copy.deepcopy(document.project()['payload'])
                    document.dirty = document.draft_dirty = False
                    document.error = ''
                    document.last_result = result
                    document.retarget_execution()
                else:
                    tab.widget.path, tab.widget.baseline, tab.widget.error = target, raw, ''
                    tab.widget.setAccessibleName(target.name + '，Markdown 編輯')
                    tab.widget.document().setModified(False)
                owner = tab.document or tab.widget
                owner.operation_error = False
                for view in op.pages:
                    w.autosave.forget(view)
                    view.path, view.title, view.retained = target, target.stem, True
                    if isinstance(view.widget, MarkdownPage):
                        view.widget.path = target
                        view.widget.setAccessibleName(target.name + '，Markdown 編輯')
                    w.update_tab(view)
                if isinstance(tab.widget, MarkdownPage):tab.widget.buffer.save_key = new_key
                w.layout_changed()
            else:
                # Explicit file command on an unopened item leaves the current
                # editor intact; ordinary navigation still reuses its tab.
                tab = w.open_path(target, True, new_tab=True)
                if tab is None:
                    raise ValueError('新檔已保存，但尚未能開啟。')
            w.remember_recent(target)
        except Exception as error:
            message = '新檔已保存於：\n' + str(target) + '\n頁面切換尚未完成，已停止自動儲存：' + str(error)
            self.block_pages(op, message, [target])
            raise ValueError(message) from error
        op.result = target
        return target

    def check_source(self, op):
        if stamp(op.source) != op.expected:
            raise ValueError('來源在操作期間已變更；原稿與位置已保留，請重新確認。')
        for tab in op.pages:
            if tab.document:
                actual = io.load(tab.path.parent, tab.path.name).receipt
                if actual != tab.document.receipt:
                    raise ValueError('文件已被外部修改，請先處理衝突或另存本機稿：' + str(tab.path))
            elif isinstance(tab.widget, MarkdownPage):
                if paths.read_bytes(tab.path, 8*1024*1024)[0] != tab.widget.baseline:
                    raise ValueError('Markdown 已被外部修改，請先處理衝突或另存本機稿：' + str(tab.path))

    def rename(self, op, name):
        suffix = op.source.suffix if op.source.is_file() else ''
        target = named_path(op.source.parent, name, suffix)
        if str(target) == str(op.source):
            op.result = op.source
            return op.source
        return self.relocate(op, target)

    def move(self, op, directory):
        parent = self.w.locations.directory(directory)
        root = self.w.locations.root_for(op.source)
        if root is None or self.w.locations.root_for(parent) != root:
            raise ValueError('這一版請在同一個已加入的根資料夾內移動。')
        if parent == op.source or op.source in parent.parents:
            raise ValueError('不能移入自己或自己的子資料夾。')
        if parent == op.source.parent:
            op.result = op.source
            return op.source
        return self.relocate(op, parent/op.source.name)

    def relocate(self, op, target):
        w, source = self.w, op.source
        if any(source == root or source in root.parents for root in w.locations.roots):
            raise ValueError('這個項目包含已連結的根資料夾，請先處理側欄連結。')
        case_only = source.parent == target.parent and source.name.casefold() == target.name.casefold()
        if not case_only:
            paths.checked(target.parent, target.name, new=True)
        self.check_source(op)
        prepare_move(source, target, op.pages)
        self.check_source(op)
        if not case_only:
            paths.checked(target.parent, target.name, new=True)
        previous = [(tab, tab.path, tab.title, tab.document.receipt if tab.document else None) for tab in op.pages]
        try:
            rename_exclusive(source, target)
            if stamp(target) != op.expected:
                raise ValueError('移動完成時來源內容已變更。')
            self.apply_location(op.pages, source, target)
        except Exception as error:
            try:
                if target.exists() and (not source.exists() or case_only):
                    if stamp(target) != op.expected:
                        raise ValueError('移動後檔案已由外部修改，未再搬動。')
                    rename_exclusive(target, source)
                if stamp(source) != op.expected:
                    raise ValueError('無法確認原位置完整回復。')
                for tab, old_path, title, receipt in previous:
                    tab.path, tab.title = old_path, title
                    if tab.document:
                        tab.document.path, tab.document.receipt = old_path, receipt
                    else:
                        tab.widget.path = old_path
            except Exception as recovery:
                message = '移動尚未完整回復，已停止自動儲存。\n原位置：' + str(source) + '\n目的位置：' + str(target) + '\n' + str(recovery)
                self.block_pages(op, message, [source, target])
                raise ValueError(message) from error
            raise ValueError('操作未完成，原位置與編輯內容已保留：' + str(error)) from error
        w.recent_files = [str(relocate(Path(p), source, target)) for p in w.recent_files]
        w.tree_state.relocate(source, target)
        op.result = target
        return target

    def apply_location(self, pages, source, target):
        # No view/model reconstruction. Rebind every writer before timers resume.
        for tab in pages:
            new = relocate(tab.path, source, target)
            tab.path, tab.title = new, new.stem
            if tab.document:
                tab.document.path = new
                tab.document.receipt = replace(tab.document.receipt, path=str(new))
            else:
                tab.widget.path = new
                tab.widget.setAccessibleName(new.name + '，Markdown 編輯')

    def active_context(self):
        if self.w.active_route != 'home':
            return None
        tab = self.w.tabs_by_key.get(self.w.active_key)
        return self.w.commands.context('tab', tab=tab) if tab and tab.path else None

    def rename_focused(self):
        w = self.w
        if QApplication.activeModalWidget() or self.inline is not None:
            return
        focus = QApplication.focusWidget()
        if focus is None or QWidget.window(focus) is not w:
            return
        if isinstance(focus, (QLineEdit, QPlainTextEdit, QTextEdit, QAbstractSpinBox, QComboBox)):
            return
        if w.tree.isVisible() and (focus is w.tree or w.tree.isAncestorOf(focus)):
            context = w.commands.context('tree', w.tree.currentItem())
        else:
            context = self.active_context()
            if context is None:
                return
            tab = w.tabs_by_key[w.active_key]
            if not (focus is w.tab_strip.bar or w.tab_strip.isAncestorOf(focus)
                    or focus is tab.widget or tab.widget.isAncestorOf(focus)):
                return
            canvas = tab.host.canvas if tab.document else None
            view = canvas.view if canvas else None
            scene = view.scene() if view and callable(getattr(view, 'scene', None)) else None
            item = scene.focusItem() if scene is not None and (focus is view or view.isAncestorOf(focus)) else None
            if item is not None:
                return
        if context and context.path and context.kind != 'root':
            self.run('rename', context)
            return True
        return False

    def save_as_current(self):
        if QApplication.activeModalWidget():
            return
        context = self.active_context()
        if context:
            self.run('save_as', context)

    def add_actions(self, menu, context):
        for title, action in (('重新命名', 'rename'), ('移動到', 'move'), ('另存新檔…', 'save_as')):
            if action == 'save_as' and context.kind == 'folder':
                continue
            if action == 'move':
                from .workspace_move_menu import MoveMenu
                submenu = menu.addMenu(title)
                submenu.move_session = MoveMenu(self, submenu, context)
                item = submenu.menuAction()
            else:
                item = menu.addAction(title, lambda checked=False, a=action: self.run(a, context))
            reason = ''
            if self.busy(context.path):
                reason = '請先完成或取消目前的文件操作。'
            elif action == 'move' and context.root is None:
                reason = '請先將所在資料夾加入側欄。'
            elif context.path is None or not context.path.exists():
                if action != 'save_as' or not context.tab_key:
                    reason = '來源已不存在。'
            item.setEnabled(not reason)
            item.setToolTip(reason)
        return menu
