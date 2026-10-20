"""Inspect durable tasks, including owners no longer attached to a file view."""
import sqlite3
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QPlainTextEdit

from .widgets import ComboBox, StudioDialog, button, label, row


class WorkspaceHistory(StudioDialog):
    def __init__(self, workspace):
        super().__init__(workspace)
        self.workspace = workspace
        self.execution = None
        self.setWindowTitle('工作區任務紀錄')
        self.resize(850, 650)
        self.body.addWidget(label('同一時間只執行一份 PCS 工作流；其他工作流依序等待。', 'Subtle', True))
        self.picker = ComboBox()
        self.picker.setAccessibleName('任務所屬文件')
        self.values = [v for v in reversed(workspace.runtimes.rows())
                       if v['document']['kind'] == 'workflow']
        names = {}
        for value in self.values:
            suffix = ' · 另存前任務' if value.get('recovery_only') else ''
            name = Path(value['path']).stem + suffix
            names[name] = names.get(name, 0) + 1
            self.picker.addItem(name + (f"（{names[name]}）" if names[name] > 1 else ''))
        self.body.addWidget(self.picker)
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        self.body.addWidget(self.detail, 1)
        self.query = button('查詢原任務', self.recheck, 'Quiet')
        self.flows = button('流程紀錄', self.show_flows, 'Quiet')
        self.stop = button('停止後續流程', self.cancel, 'DangerQuiet')
        self.body.addLayout(row(self.query, self.flows, self.stop, None, button('關閉', self.accept, 'Quiet')))
        self.picker.currentIndexChanged.connect(self.select)
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.refresh)
        self.finished.connect(self.timer.stop)
        self.select()
        self.timer.start()

    def select(self, *_):
        self.execution = None
        index = self.picker.currentIndex()
        if 0 <= index < len(self.values):
            value = self.values[index]
            try:
                self.execution = self.workspace.runtimes.history_owner(value)
            except (ValueError, OSError, sqlite3.Error, KeyError, TypeError) as exc:
                self.workspace.runtimes.errors[value['token']] = str(exc)
                self.detail.setPlainText('任務紀錄無法讀取，原資料已保留：' + str(exc))
        else:
            self.detail.setPlainText('目前沒有 PCS 工作流任務紀錄。')
        for control in (self.query, self.flows, self.stop):
            control.setEnabled(self.execution is not None)
        self.refresh()

    def refresh(self):
        if self.execution is None:
            return
        from .job_details import describe
        from .stage_widgets import NAMES
        owner = self.execution
        client = owner.client
        value = self.values[self.picker.currentIndex()]
        parts = [value['path'], client.scheduling_message or '目前沒有等待中的執行']
        if owner.document is None:
            parts.append('這份紀錄未連到編輯中的文件。可以查詢或停止舊任務；新的執行請從文件開始。')
        for run in client.input_flow.chain.runs():
            status = ('排隊中' if run['status'] == 'running' and owner.runtime_token in self.workspace.scheduler.waiting
                      else NAMES.get(run['status'], run['status']))
            parts.append('流程：' + status + '\n' + run.get('message', ''))
        for record in client.generation.records():
            parts.append(describe(record))
        statuses = dict(preparing='準備中', submitted='已提交', unconfirmed='等待確認',
                        failed='失敗', applied='已套用', opened='已開啟',
                        inspected='已讀取', captured='已擷取', settled='已結束')
        for item in client.input_flow.chain.store.rows('input'):
            parts.append('輸入同步 · ' + statuses.get(item['status'], item['status']) + '\n' + item['id'])
        for ident, status in owner.store.db.execute('SELECT id,status FROM workspace_operations ORDER BY rowid DESC'):
            parts.append('工作流操作 · ' + statuses.get(status, status or '等待確認') + '\n' + ident)
        text = '\n\n'.join(parts)
        if self.detail.toPlainText() != text:
            position = self.detail.verticalScrollBar().value()
            self.detail.setPlainText(text)
            self.detail.verticalScrollBar().setValue(position)
        self.query.setEnabled(client.connected)
        self.stop.setEnabled(client.input_flow.has_work() or self.workspace.scheduler.pending(client))

    def recheck(self):
        if self.execution is not None:
            self.execution.client.poll()
            self.refresh()

    def cancel(self):
        if self.execution is not None:
            self.execution.client.interrupt(clear_pending=True)
            self.refresh()

    def show_flows(self):
        if self.execution is not None:
            from .stage_widgets import show_history
            show_history(self.workspace, execution=self.execution)


def show(workspace):
    dialog = WorkspaceHistory(workspace)
    dialog.exec()
    dialog.deleteLater()
