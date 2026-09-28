"""Small queue dialog; the existing workspace layout stays in place."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QListWidget, QListWidgetItem, QPlainTextEdit
from .widgets import StudioDialog, label, button, row

NAMES = dict(waiting='等待', submitting='提交中', queued='後端等待', running='執行中',
             complete='完成', failed='失敗', unconfirmed='待確認', results_pending='結果待齊')


class QueueDialog(StudioDialog):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.runner = window.comfy.queue
        self.setWindowTitle('工作佇列')
        self.resize(880, 660)
        self.body.addWidget(label('加入時保存工作版本；開始後可關閉 ComfyUI 網頁。PCS 與 ComfyUI 服務仍須運行。', 'Subtle', True))
        self.add_current=button('加入目前工作', lambda:self.act(self.runner.enqueue_current))
        self.body.addLayout(row(self.add_current,
                                button('圖片逐張…',self.image_batch),
                                button('開始／恢復', lambda:self.act(self.runner.start)),
                                button('暫停／停止後續', self.runner.pause), None))
        self.status = label('', 'Subtle', True)
        self.body.addWidget(self.status)
        self.items = QListWidget()
        self.items.currentItemChanged.connect(self.details)
        self.body.addWidget(self.items, 1)
        self.body.addLayout(row(*[button(title, lambda checked=False, a=action:self.edit(a), 'Quiet')
                                 for title, action in (('移至下一項','next'),('上移','up'),('下移','down'),('移除等待項','remove'))],
                                button('重試已確認失敗', self.retry, 'Quiet'), None))
        self.body.addLayout(row(button('移除選定的已結案紀錄',self.remove_finished,'Quiet'),
                                button('查看這項結果',self.show_results,'Quiet'),
                                label('原始圖片、生成歷史與資產副本保留。','Subtle'),None))
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setMaximumHeight(190)
        self.body.addWidget(self.detail)
        self.refresh()

    def image_batch(self):
        from .image_iteration_panel import ImageIterationDialog
        self.image_dialog=ImageIterationDialog(self.window)
        self.image_dialog.show()

    def act(self, function):
        try:
            function()
        except (ValueError, OSError) as exc:
            self.runner.notify(str(exc))
        self.refresh()

    def selected(self):
        item = self.items.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def edit(self, action):
        ident = self.selected()
        if ident:
            self.act(lambda:self.runner.store.edit_pending(ident, action))

    def retry(self):
        ident = self.selected()
        if ident:
            self.act(lambda:self.runner.store.retry(ident))

    def remove_finished(self):
        ident=self.selected()
        if ident:self.act(lambda:self.runner.store.remove_finished(ident))

    def show_results(self):
        ident=self.selected()
        if not ident:return
        record=self.runner.store.read(ident)
        if record['work']['scope']['server']!=self.window.comfy.url:
            self.runner.notify('請先連線至這項工作的原 ComfyUI 服務。');return
        snapshot=record['work']['snapshot'];state=snapshot['state']
        workspace=dict(id=state['workspace'],name=next(w['name'] for w in state['workspaces'] if w['id']==state['workspace']))
        rows=[dict(prompt_id=record.get('prompt_id',''),node_id=node,image=image,workspaces=[workspace])
              for node,output in record.get('outputs',{}).items() for image in output.get('images',[])]
        if not rows:self.runner.notify('這項工作尚未有可確認的圖片結果。');return
        self.window.recent.receive_results(rows);self.window.show_recent_sheet()

    def refresh(self):
        selected = self.selected()
        self.items.blockSignals(True)
        self.items.clear()
        for index, record in enumerate(self.runner.store.rows()):
            item = QListWidgetItem(f"{index+1}. {record['label']} · {NAMES.get(record['state'], record['state'])}")
            item.setData(Qt.ItemDataRole.UserRole, record['id'])
            self.items.addItem(item)
            if record['id'] == selected:
                self.items.setCurrentItem(item)
        self.items.blockSignals(False)
        self.status.setText(self.runner.message)
        self.details()

    def details(self, *_):
        ident = self.selected()
        if not ident:
            self.detail.clear()
            return
        record = self.runner.store.read(ident)
        from .job_details import describe
        if record.get('payload'):
            value = dict(record, server=record['work']['scope']['server'])
            self.detail.setPlainText(describe(value))
        else:
            work=record['work'];lines=['工作流：'+work['generation']['workflow'],
                '狀態：'+NAMES.get(record['state'],record['state']),'輸入圖片：'+str(len(work['assets']))+' 張']
            for key,node in work['graph'].items():
                values=node.get('inputs',{})
                for field,title in (('seed','Seed'),('noise_seed','Seed'),('batch_size','每批張數'),('width','寬'),('height','高')):
                    if field in values and not isinstance(values[field],list):lines.append(f"{title}（#{key}）：{values[field]}")
            for text in work['texts']:
                origin='ComfyUI 手動' if text.get('text_source')=='web' else 'PCS'
                lines.extend(['',f"文字（#{text['node']}，{origin}）：",text['text'] or '（空白）'])
            if record.get('error'):lines.extend(['',record['error']])
            lines.extend(['','工作版本：'+work['sha256']])
            self.detail.setPlainText('\n'.join(lines))


def show_queue(window):
    dialog = getattr(window, 'queue_dialog', None)
    if dialog is None:
        window.queue_dialog = QueueDialog(window)
        dialog = window.queue_dialog
    dialog.refresh()
    dialog.show()
    dialog.raise_()
