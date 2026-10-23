from pathlib import Path
from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QFileDialog, QLineEdit, QWidget, QVBoxLayout
from . import markdown_demos as demos
from .local_documents import paths
from .markdown_source import SourceEdit
from .retained_dialog import RetainedSheet, plain_editor
from .widgets import button, label, row, scrolling


class DemoSheet(RetainedSheet):
    def __init__(self, surface, record_path=None):
        super().__init__(surface, '演示與原檔' if record_path else '加入演示／原檔')
        self.record_path = Path(record_path) if record_path else None
        self.previous = None
        self.prepared = None
        self.controls = []
        fields = QWidget()
        layout = QVBoxLayout(fields)
        layout.setContentsMargins(0, 0, 0, 0)
        self.title = QLineEdit(); self.title.setPlaceholderText('例如：操作流程示意')
        self.source = QLineEdit(); self.source.setPlaceholderText('來源網址（選填）')
        layout.addWidget(label('標題')); layout.addWidget(self.title)
        self.original = self.file_field(layout, '原檔', False)
        self.preview = self.file_field(layout, '靜態預覽', False, True)
        self.dependencies = self.file_field(layout, '依賴資料夾（含入口原檔）', True)
        layout.addWidget(label('來源網址')); layout.addWidget(self.source)
        if record_path:
            layout.addWidget(button('開啟附件資料夾', lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.record_path.parent)))))
        layout.addWidget(label('僅保存你選取的檔案。閱讀時不執行 HTML／SVG，也不下載外部引用。', 'Muted', wrap=True))
        self.body.addWidget(scrolling(fields), 1)
        self.details = plain_editor(True)
        self.details.setPlaceholderText('預覽會列出檔案、大小及已知缺項。')
        self.body.addWidget(self.details, 1)
        self.prepare_button = button('預覽保存內容', self.prepare)
        self.save_button = button('保存並插入' if not record_path else '建立修訂並替換引用', self.save, 'Primary')
        self.save_button.setEnabled(False)
        self.finish_layout(self.prepare_button, self.save_button)
        self.controls.extend([self.title, self.source, self.prepare_button])
        for field in (self.title, self.source, self.original, self.preview, self.dependencies):
            field.textChanged.connect(self.invalidate)
        if record_path:
            self.run(lambda cancel, progress: demos.inspect(self.record_path), self.loaded)

    def file_field(self, layout, title, directory, preview=False):
        edit = QLineEdit(); edit.setPlaceholderText('未指定，可稍後補入')
        def choose():
            if directory:
                chosen = QFileDialog.getExistingDirectory(self, title, str(self.document.parent))
            else:
                chosen, _ = QFileDialog.getOpenFileName(self, title, str(self.document.parent),
                    '靜態圖片 (*.png *.jpg *.jpeg *.gif *.webp *.bmp)' if preview else '所有檔案 (*)')
            if chosen:
                edit.setText(chosen)
        control = button('選擇…', choose)
        layout.addWidget(label(title)); layout.addLayout(row(edit, control))
        self.controls.extend([edit, control])
        return edit

    def invalidate(self):
        self.prepared = None
        self.save_button.setEnabled(False)

    def loaded(self, result):
        record, statuses = result
        self.previous = record
        self.title.setText(record['title']); self.source.setText(record.get('source', ''))
        for field, key in ((self.original, 'original'), (self.preview, 'preview')):
            if record.get(key):
                path = paths.checked(self.record_path.parent, record[key])
                if path.is_file():
                    field.setText(str(path))
        if (self.record_path.parent / 'original').is_dir():
            self.dependencies.setText(str(self.record_path.parent / 'original'))
        self.details.setPlainText('\n'.join(row['path'] + ' · ' + state for row, state in statuses)
                                  + '\n\n' + '\n'.join(record.get('warnings', [])))
        self.status.setText('修訂會保留上版檔案；請確認要替換目前正文引用。')

    def set_busy(self, busy):
        for control in self.controls:
            control.setEnabled(not busy)
        self.save_button.setEnabled(not busy and self.prepared is not None and self.mode != 'read')

    def prepare(self):
        args = dict(title=self.title.text(), original=self.original.text() or None,
                    preview=self.preview.text() or None, dependencies=self.dependencies.text() or None,
                    source=self.source.text(), previous=self.previous)
        self.run(lambda cancel, progress: demos.prepare(**args, cancel=cancel, progress=progress), self.prepared_done)

    def prepared_done(self, result):
        self.prepared = result
        bundle, record = result
        self.details.setPlainText('\n'.join(f'{name} · {len(data):,} bytes' for name, data in bundle.files)
            + '\n\n' + ('\n'.join(record['warnings']) or '未發現缺少的靜態引用。')
            + '\n' + record['dependency_check'])
        self.status.setText('以上是本次固定內容。確認後保存到筆記的附件資料夾。')
        self.set_busy(False)

    def save(self):
        try:
            self.require_current(True)
            if not self.prepared:
                return
            self.edit_range = demos.block_range(self.snapshot.text, self.previous) if self.previous else (self.start, self.end)
            bundle, record = self.prepared
            self.run(lambda cancel, progress: demos.save(self.root, bundle, cancel, progress, root_identity=self.root_identity),
                     lambda folder: self.saved(folder, record))
        except (ValueError, OSError) as error:
            self.status.setText(str(error))

    def saved(self, folder, record):
        self.require_current(True)
        start, end = self.edit_range
        text = demos.markdown(record, folder, self.document)
        self.surface.page.buffer.apply(self.snapshot, [SourceEdit(start, end, text)])
        self.surface.navigate(start + len(text)); self.surface.refresh()
        self.accept()


def open_demo(surface, path=None):
    if not path and (surface.mode == 'read' or surface.page.buffer.composing):
        return
    sheet = DemoSheet(surface, path)
    surface.demo_sheet = sheet
    sheet.show()
    return sheet


def open_demo_target(surface, path):
    """Only an explicit link click can open a verified retained original."""
    from .markdown_attachments import root_for
    if path.name == demos.RECORD:
        open_demo(surface, path)
        return True
    root = Path(root_for(surface)).absolute()
    for parent in path.parents:
        if not parent.is_relative_to(root):
            break
        manifest = parent / demos.RECORD
        if manifest.is_file():
            record, statuses = demos.inspect(manifest)
            if record.get('original') and paths.checked(parent, record['original']) == path:
                status = next((state for row, state in statuses if row['path'] == record['original']), '')
                if status != '已保存':
                    open_demo(surface, manifest)
                    return True
                if path.suffix.lower() not in demos.OPENABLE:
                    raise ValueError('此原檔類型請由檔案總管開啟；PCS 不執行程式或指令檔。')
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
                return True
            break
    return False
