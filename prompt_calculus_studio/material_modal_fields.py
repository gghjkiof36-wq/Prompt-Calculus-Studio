"""Fields borrowed by the modal editor, without a second hidden library UI."""
from PySide6.QtWidgets import QComboBox,QLineEdit,QPlainTextEdit,QCheckBox
from .widgets import ComboBox,label
from .material_editor_documents import ModalPromptText
from .material_editor_panels import MaterialPreviewImage


def build_fields(form):
    # These selectors store the established form/session values. They never
    # appear in the modal and need no custom popup, delegate or library rows.
    form.category=QComboBox(form);form.destination=QComboBox(form);form.edit_destination=QComboBox(form)
    form.more=QCheckBox(form)
    form.name=QLineEdit(form);form.aliases=QLineEdit(form)
    form.kind=ComboBox(form)
    for title,key in (('標籤','tags'),('純文字','text'),('Markdown','markdown')):form.kind.addItem(title,key)
    form.edit_category=ComboBox(form)
    form.notes=QPlainTextEdit(form);form.notes.setMaximumHeight(70)
    form.excludes=QPlainTextEdit(form);form.excludes.setMaximumHeight(70)
    form.lookup=QCheckBox('標籤查詢',form);form.lookup_hint=label('');form.lookup_hint.setParent(form);form.lookup_hint.hide()
    form.prompt=ModalPromptText(form);form.prompt.setParent(form);form.prompt.setObjectName('Prompt')
    form.prompt.setPlaceholderText('輸入文字內容')
    form.preview=MaterialPreviewImage(form.window.store);form.preview.setParent(form)
    form.preview.pathReady.connect(form.receive_image);form.preview.failed.connect(form.window.notice)
    form.status=label('');form.status.setParent(form)
    form.lookup.toggled.connect(form.lookup_changed)
    form.destination.currentIndexChanged.connect(lambda:form.destination_changed(form.destination))
    form.edit_destination.currentIndexChanged.connect(lambda:form.destination_changed(form.edit_destination))
    for field in (form.name,form.aliases):field.textChanged.connect(form.edited)
    for field in (form.prompt,form.notes,form.excludes):field.textChanged.connect(form.edited)
    form.kind.currentIndexChanged.connect(form.edited);form.edit_category.currentIndexChanged.connect(form.edited)
    form.refresh()
