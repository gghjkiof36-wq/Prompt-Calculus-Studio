"""Image display size edits; attachment bytes and source ownership stay intact."""
import re
from pathlib import PurePosixPath
from urllib.parse import unquote, urlsplit
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QSpinBox, QFormLayout, QDialogButtonBox
from .widgets import StudioDialog
from .markdown_links import links
from .markdown_source import SourceEdit

DIMENSIONS=re.compile(r'\|(\d{1,4})(?:x(\d{1,4}))?$')
IMAGES={'.png','.jpg','.jpeg','.gif','.webp','.bmp'}


def image_at(text, position):
    candidates=[]
    for link in links(text):
        target=DIMENSIONS.sub('',link.target)
        if not link.embed or PurePosixPath(unquote(urlsplit(target).path)).suffix.lower() not in IMAGES:continue
        if link.start<=position<=link.end:return link
        if not any(c in text[min(position,link.start):max(position,link.end)] for c in '\n\u2028'):
            candidates.append(link)
    return candidates[0] if len(candidates)==1 else None


def size_edit(text, link, width):
    suffix='|'+str(width) if width else ''
    label=DIMENSIONS.sub('',link.label)+suffix
    if link.wiki:
        return SourceEdit(link.start,link.end,'![['+link.target+('|' if label else '')+label+']]')
    # Replace only the label and a legacy size suffix on the destination;
    # preserve angle brackets, title, escaping and all surrounding whitespace.
    raw=text[link.start:link.end]
    # Link ranges already account for escaped/nested brackets; find the close
    # immediately preceding the parsed destination, rather than the first one.
    label_end=raw.rfind('](',0,link.target_start-link.start)
    raw='!['+label+raw[label_end:]
    match=DIMENSIONS.search(link.target)
    if match:
        offset=(link.target_start-link.start)+len(label)-len(link.label)
        raw=raw[:offset+match.start()]+raw[offset+match.end():]
    return SourceEdit(link.start,link.end,raw)


class ImageSizeDialog(StudioDialog):
    def __init__(self, surface, snapshot, link):
        super().__init__(surface);self.surface=surface;self.snapshot=snapshot;self.link=link
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.setWindowTitle('圖片尺寸');self.resize(420,260);self.body.setSpacing(16)
        name=QLabel(unquote(link.target));name.setWordWrap(True);self.body.addWidget(name)
        self.width_field=QSpinBox();self.width_field.setRange(0,1600)
        self.width_field.setSpecialValueText('原始尺寸');self.width_field.setSuffix(' px')
        self.width_field.setAccessibleName('圖片顯示寬度')
        match=DIMENSIONS.search(link.target) or DIMENSIONS.search(link.label)
        self.width_field.setValue(int(match.group(1)) if match else 0)
        form=QFormLayout();form.addRow('寬度',self.width_field);self.body.addLayout(form)
        note=QLabel('高度依圖片比例調整；圖片超過頁寬時會自動縮小。原始圖片不會變更。')
        note.setWordWrap(True);self.body.addWidget(note)
        self.message=QLabel();self.message.setWordWrap(True);self.message.hide();self.body.addWidget(self.message)
        self.buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Apply|QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Apply).setText('套用')
        self.buttons.button(QDialogButtonBox.StandardButton.Apply).setObjectName('Primary')
        self.buttons.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(self.submit)
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        self.buttons.rejected.connect(self.reject);self.body.addWidget(self.buttons)

    def submit(self):
        s=self.surface
        if 0<self.width_field.value()<16:
            self.message.setText('寬度請輸入 16–1600 px，或設為 0 使用原始尺寸。');self.message.show();return
        if s.mode=='read' or s.page.buffer.composing:
            self.message.setText('請回到編寫模式並完成目前的組字後再套用。');self.message.show();return
        try:
            edit=size_edit(self.snapshot.text,self.link,self.width_field.value())
            s.page.buffer.apply(self.snapshot,[edit])
        except (ValueError,OSError) as error:
            self.message.setText('文件已變更，請關閉後重新選取圖片。\n'+str(error));self.message.show();return
        s.navigate(edit.start);s.refresh();self.accept()


def open_image_size(surface, position=None, expected=None):
    if surface.mode=='read' or surface.page.buffer.composing:return
    snapshot,start,_=surface.selection()
    if expected is not None and snapshot!=expected:
        surface.report('文件已變更，請重新選取圖片。');return
    link=image_at(snapshot.text,start if position is None else position)
    if link is None:
        surface.report('請將游標放在圖片原文上，再選擇「調整圖片尺寸」。');return
    previous=getattr(surface,'image_size_dialog',None)
    if previous:previous.close();previous.deleteLater()
    surface.image_size_dialog=ImageSizeDialog(surface,snapshot,link)
    surface.image_size_dialog.show()
