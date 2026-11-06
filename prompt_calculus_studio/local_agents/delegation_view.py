"""Project-owned task view over existing delegation receipts and chat owners."""
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QHeaderView, QPlainTextEdit, QTreeWidgetItem, QVBoxLayout, QWidget
from shiboken6 import isValid

from ..widgets import button
from ..work_resources.reader import icon_button, line
from .delegation import delegation_for
from .delegation_store import TERMINAL
from .dialogs import plain
from .editors import listing


STATES = dict(preparing='準備中', working='執行中', waiting_approval='等待核准',
              stopping='停止中', complete='結果待覆核', stopped='已停止',
              failed='未完成', limited='達到限制', interrupted='已中斷', save_failed='保存失敗')


class DelegationTasks(QWidget):
    def __init__(self, library):
        super().__init__()
        self.library, self.project_id = library, library.project_id
        self.service = delegation_for(library.workspace)
        self.rows, self.revision, self.pending, self.loading = {}, 0, True, False
        body = QVBoxLayout(self)
        body.setContentsMargins(0, 0, 0, 0); body.setSpacing(12)
        self.toggle = button('', self.toggle_enabled)
        self.refresh_button = icon_button('refresh-cw', '重新整理委派工作', self.mark)
        line(body, plain('委派工作', 'SectionTitle'), self.toggle, self.refresh_button)
        self.results = listing(['工作', '進度'], '委派工作清單')
        self.results.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.results.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.results.header().setStretchLastSection(False)
        self.results.currentItemChanged.connect(self.selected)
        self.results.itemActivated.connect(lambda *_: self.open_chat())
        body.addWidget(self.results, 1)
        self.empty = plain('尚無委派工作。', 'Subtle'); body.addWidget(self.empty)
        self.detail = plain('', 'Subtle'); body.addWidget(self.detail)
        self.task = QPlainTextEdit()
        self.task.setReadOnly(True); self.task.setAccessibleName('完整委派工作')
        self.task.setMaximumHeight(120); body.addWidget(self.task)
        self.open_button = button('開啟對話', self.open_chat)
        self.stop_button = button('停止工作', self.stop_task, 'Quiet')
        line(body, self.open_button, self.stop_button)
        self.message = plain(''); self.message.hide(); body.addWidget(self.message)
        self.timer = QTimer(self); self.timer.setInterval(500)
        self.timer.timeout.connect(self.tick)
        self.service.changed.connect(self.mark)
        self.update_toggle(); self.selected()

    def can_leave(self):
        return True

    def update_toggle(self):
        enabled = self.project_id in self.service.enabled
        self.toggle.setText('停止本機委派' if enabled else '啟用本機委派')
        self.toggle.setToolTip('停止此專案的委派並撤回外部存取' if enabled else '允許本機主代理交辦工作')

    def toggle_enabled(self):
        try:
            self.service.set_enabled(self.project_id, self.project_id not in self.service.enabled)
            self.error('')
        except (ValueError, OSError) as exc:
            self.error(exc)
        self.update_toggle()

    def mark(self, *_):
        self.revision += 1
        self.pending = True

    def showEvent(self, event):
        super().showEvent(event)
        self.timer.start(); self.tick()

    def hideEvent(self, event):
        self.timer.stop()
        super().hideEvent(event)

    def tick(self):
        self.update_toggle()
        if self.pending and not self.loading and not self.library.page.jobs.active:
            self.pending, self.loading = False, True
            revision = self.revision
            store, project = self.service.store, self.project_id
            def done(rows):
                if not isValid(self): return
                self.loading = False
                if revision != self.revision:
                    self.pending = True
                    return
                self.rows = {r['task_id']: r for r in rows}
                # A crash may have left a receipt pending after its chat saved.
                # Use the existing recovery path once; never resubmit a task.
                for identity, row in list(self.rows.items()):
                    if row['state'] not in TERMINAL and identity not in self.service.active:
                        self.rows[identity] = self.service.current_summary(self.service.get(identity, local=True))
                self.error(''); self.render()
            def failed(error):
                if isValid(self):
                    self.loading = False; self.error(error)
            self.library.page.jobs.start('正在讀取委派工作…', lambda cancel: store.recent(project),
                                         done, failed, announce=False)
        # Active state comes from memory, not a repeated scan of saved chats.
        changed = False
        for context in self.service.active.values():
            if context['record']['project_id'] != self.project_id: continue
            row = self.service.current_summary(context['record'])
            if self.rows.get(row['task_id']) != row:
                self.rows[row['task_id']] = row; changed = True
        if changed: self.render()

    def render(self):
        current = self.identity()
        self.results.blockSignals(True); self.results.clear()
        for record in sorted(self.rows.values(), key=lambda r: r.get('created_at') or '', reverse=True):
            title = ' '.join((record.get('task') or '委派工作').split())
            item = QTreeWidgetItem([title, STATES.get(record['state'], record['state'])])
            item.setData(0, Qt.ItemDataRole.UserRole, record['task_id'])
            item.setToolTip(0, record.get('agent_name') or record['agent_id'])
            self.results.addTopLevelItem(item)
            if record['task_id'] == current: self.results.setCurrentItem(item)
        if self.results.currentItem() is None and self.results.topLevelItemCount():
            self.results.setCurrentItem(self.results.topLevelItem(0))
        self.results.blockSignals(False)
        self.empty.setVisible(not self.rows)
        self.selected()

    def identity(self):
        item = self.results.currentItem()
        return item.data(0, Qt.ItemDataRole.UserRole) if item else None

    def selected(self, *_):
        record = self.rows.get(self.identity())
        self.task.setVisible(record is not None)
        self.detail.setVisible(record is not None)
        self.open_button.setEnabled(record is not None)
        self.stop_button.setVisible(bool(record and record['state'] not in TERMINAL))
        self.stop_button.setEnabled(bool(record and record['state'] != 'stopping'))
        if record:
            role = record.get('agent_name') or record['agent_id']
            self.detail.setText(f"{role} · {STATES.get(record['state'], record['state'])}")
            task = record.get('task') or '這是較早的工作，完整內容請開啟對話查看。'
            if self.task.toPlainText() != task: self.task.setPlainText(task)

    def open_chat(self):
        record = self.rows.get(self.identity())
        if record is None: return
        try:
            root = Path(self.library.workspace.root).resolve()
            path = Path(record['path']).resolve(strict=True)
            if not path.is_relative_to(root):
                raise ValueError('委派對話已離開工作區，請從原位置開啟。')
            self.library.workspace.open_path(path, retained=True)
        except (ValueError, OSError) as exc:
            self.error(exc)

    def stop_task(self):
        context = self.service.active.get(self.identity())
        if context is not None:
            self.service._stop(context)
            self.tick()

    def error(self, text):
        self.message.setText(str(text)); self.message.setVisible(bool(text))
