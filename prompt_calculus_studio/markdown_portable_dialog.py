from pathlib import Path
from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QFileDialog, QCheckBox
from shiboken6 import isValid
from . import markdown_portable as portable
from .conversation_review import Source
from .conversation_import import Destination
from .retained_dialog import RetainedSheet, plain_editor
from .widgets import ComboBox, button, label, row


class PortableSheet(RetainedSheet):
    def __init__(self, surface):
        super().__init__(surface, '匯出文件與來源')
        self.source_data = None; self.package = None; self.result_path = None
        self.source_button = button('選擇來源對話…', self.choose_source)
        self.branch = ComboBox(); self.branch.addItem('僅此文件與引用附件', None)
        self.include_summary = QCheckBox('包含整理稿的來源與修訂資料'); self.include_summary.setChecked(True)
        self.format = ComboBox(); self.format.addItems(['資料夾', 'ZIP'])
        self.details = plain_editor(True)
        self.details.setPlaceholderText('匯出檔案預覽')
        self.body.addLayout(row(self.source_button, self.format))
        self.body.addWidget(self.branch); self.body.addWidget(self.include_summary)
        self.body.addWidget(self.details, 1)
        self.preview_button = button('預覽匯出內容', self.prepare)
        self.export_button = button('選位置並匯出', self.export, 'Primary'); self.export_button.setEnabled(False)
        self.open_button = button('開啟完成位置', self.open_result); self.open_button.hide()
        self.finish_layout(self.open_button, self.preview_button, self.export_button)
        self.branch.currentIndexChanged.connect(self.invalidate)
        self.include_summary.toggled.connect(self.invalidate)
        for parent in self.document.parents:
            if not parent.is_relative_to(self.root):break
            if (parent/'原始對話.json').is_file():
                self.load_source(parent/'原始對話.json'); break

    def invalidate(self, *_):
        self.package = None; self.export_button.setEnabled(False)

    def set_busy(self, busy):
        for control in (self.source_button, self.branch, self.include_summary, self.format, self.preview_button):
            control.setEnabled(not busy)
        self.export_button.setEnabled(not busy and self.package is not None)

    def choose_source(self):
        path, _ = QFileDialog.getOpenFileName(self, '選擇原始對話', str(self.document.parent), 'JSON (*.json)')
        if path:self.load_source(Path(path))

    def load_source(self, path):
        if not path.is_relative_to(self.root):
            self.status.setText('來源對話必須在目前接入根內，請先使用對話匯入。'); return
        self.run(lambda cancel, progress: Source.load(path, cancel), self.loaded)

    def loaded(self, source):
        self.source_data = source; self.source_button.setText(source.item.title[:16].replace('\n',' ') + ('…' if len(source.item.title)>16 else ''))
        self.source_button.setToolTip(source.item.title)
        self.branch.clear(); self.branch.addItem('僅此文件與引用附件', None)
        for index, leaf in enumerate(source.item.leaves, 1):self.branch.addItem(f'分支 {index}（含共同歷史）', (leaf,))
        self.branch.addItem('這份對話的全部分支', source.item.leaves)
        selected = self.branch.findData((source.item.current,))
        import re
        match = re.match(r'分支-(\d+)-', self.document.name)
        if match and 1 <= int(match[1]) <= len(source.item.leaves):selected = int(match[1])
        self.branch.setCurrentIndex(max(1, selected))
        self.status.clear()

    def prepare(self):
        branches = self.branch.currentData() or ()
        source = self.source_data if branches else None
        include = self.include_summary.isChecked()
        self.run(lambda cancel, progress: portable.prepare(self.root, self.document, self.snapshot.text,
                 source, branches, include, cancel, progress), self.prepared)

    def prepared(self, package):
        self.package = package
        total = sum(len(data) for _, data in package.bundle.files)
        self.details.setPlainText('\n'.join(f'{name} · {len(data):,} bytes' for name, data in package.bundle.files)
            + ('\n\n' + '\n'.join(package.warnings) if package.warnings else ''))
        self.status.setText(f'{len(package.bundle.files)} 個檔案 · {total:,} bytes')
        self.export_button.setEnabled(True)

    def export(self):
        if not self.package:return
        path = QFileDialog.getExistingDirectory(self, '選擇匯出位置（建立全新資料夾或 ZIP）', str(self.document.parent))
        if not path:return
        try:destination = Destination.choose(path)
        except (ValueError, OSError) as error:self.status.setText(str(error)); return
        package = self.package
        function = portable.export_folder if self.format.currentIndex()==0 else portable.export_zip
        def write(cancel, progress):
            destination.verify()
            return function(destination.path, package, cancel, progress,root_identity=destination.identity)
        self.run(write, self.exported)

    def exported(self, path):
        self.result_path = path; self.open_button.show()
        self.status.setText('已完成並核對：' + str(path))

    def open_result(self):
        if self.result_path:QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.result_path if self.result_path.is_dir() else self.result_path.parent)))


class PortableImportSheet(RetainedSheet):
    def __init__(self, surface, path, destination):
        super().__init__(surface, '開啟可攜 ZIP 副本')
        self.details = plain_editor(True); self.body.addWidget(self.details)
        self.finish_layout()
        self.destination = destination
        def read(cancel, progress):
            destination.verify()
            return portable.import_zip(path, destination.path, cancel, progress,root_identity=destination.identity)
        self.run(read, self.imported)

    def imported(self, result):
        folder, entry = result
        self.details.setPlainText('已驗證並建立獨立副本：\n' + str(entry))
        self.status.setText('已開啟副本')
        if self.workspace is not None and isValid(self.workspace) and not self.workspace.layout_store.stopped:
            self.workspace.add_root(folder); self.workspace.refresh_tree()
            self.workspace.open_path(entry, retained=True, new_tab=True)


def open_export(surface):
    sheet = PortableSheet(surface); surface.portable_sheet = sheet; sheet.show(); return sheet


def open_import(surface):
    path, _ = QFileDialog.getOpenFileName(surface, '選擇 PCS 可攜 ZIP', str(surface.page.path.parent), 'ZIP (*.zip)')
    if not path:return
    folder = QFileDialog.getExistingDirectory(surface, '選擇副本位置', str(surface.page.path.parent))
    if not folder:return
    try:destination = Destination.choose(folder)
    except (ValueError, OSError) as error:surface.report(str(error)); return
    sheet = PortableImportSheet(surface, Path(path), destination)
    surface.portable_import_sheet = sheet; sheet.show(); return sheet
