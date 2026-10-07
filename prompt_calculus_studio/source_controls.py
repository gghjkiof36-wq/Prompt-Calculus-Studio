"""Explicit finite image-list selection; no implicit latest-image fallback."""
import copy
from pathlib import Path
from PySide6.QtCore import Qt,QSize
from PySide6.QtWidgets import QFileDialog,QListWidget,QListWidgetItem,QAbstractItemView,QDialog,QLineEdit,QPlainTextEdit
from .widgets import StudioDialog,label,button,row,dialog_buttons,record_icon
from .image_source import folder_files,natural_key,set_items,select,metadata_sources


def load_many(panel, paths, iterate=True):
    owner=panel.owner
    if owner.window.comfy.input_flow.source_active(panel.key):
        owner.window.notice('這份圖片清單仍有未完成工作；先完成或停止這批供應，再更換清單。');return False
    try:
        images=[owner.window.generation_panel.import_source(path) for path in paths]
        if not images:return False
        result=owner.canvas.commit(lambda state:set_items(state,panel.key,images,owner.window.store.directory,iterate))
        return result is not False
    except (ValueError,OSError) as exc:owner.window.notice(str(exc));return False


def choose_many(panel, folder=False):
    options=QFileDialog.Option.DontUseNativeDialog
    if folder:
        name=QFileDialog.getExistingDirectory(panel.owner.window,'選擇圖片資料夾',options=options)
        if not name:return
        try:paths=folder_files(name)
        except OSError as exc:panel.owner.window.notice(str(exc));return
    else:
        paths,_=QFileDialog.getOpenFileNames(panel.owner.window,'選擇多張圖片','','圖片 (*.png *.jpg *.jpeg *.webp *.bmp)',options=options)
        paths=sorted(paths,key=natural_key)
    if load_many(panel,paths):edit_list(panel)


def choose_recent(panel):
    window=panel.owner.window;dialog=StudioDialog(window);dialog.setWindowTitle('選擇最近生成的圖片');dialog.resize(650,580)
    search=QLineEdit();search.setPlaceholderText('搜尋檔名（顯示最多 300 張）');dialog.body.addWidget(search)
    listing=QListWidget();listing.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection);listing.setIconSize(QSize(72,72));dialog.body.addWidget(listing)
    def refresh():
        listing.clear()
        for record in window.catalog.rows('recent',limit=300,search=search.text()):
            item=QListWidgetItem(record['name']);item.setIcon(record_icon(window.store,record,72));item.setData(Qt.ItemDataRole.UserRole,record['path']);listing.addItem(item)
            if not Path(record['path']).is_file():item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
    search.textChanged.connect(refresh);refresh()
    dialog.body.addWidget(label('Ctrl／Shift 選取；只使用這次選定的圖片，不追隨之後的新結果。','Subtle',True))
    dialog.body.addWidget(dialog_buttons(dialog))
    if dialog.exec()==QDialog.DialogCode.Accepted:
        if load_many(panel,[item.data(Qt.ItemDataRole.UserRole) for item in listing.selectedItems()]):edit_list(panel)


def edit_list(panel):
    window=panel.owner.window;value=panel.owner.data()['images'][panel.key]
    if window.state.get('multi_output',{}).get('version',0)>=7:
        from .flow_data import incoming
        if incoming(window.state,panel.key,'image') or value.get('stage_reference'):
            window.notice('這份清單來自接入圖片；可在「選擇圖片」選指定一張或全部。要改成固定清單，請從歷史選圖或載入資料夾。');return
    items=copy.deepcopy(value.get('items') or ([value['source']] if value.get('source') else []))
    dialog=StudioDialog(window);dialog.setWindowTitle('圖片來源 · 清單與目前圖片');dialog.resize(650,540)
    dialog.body.addWidget(label('拖曳排列；移除只略過這張圖片。保存清單後，由第一張開始；已排程的項目仍保留。','Subtle',True))
    listing=QListWidget();listing.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
    for source in items:
        item=QListWidgetItem(source['name']);item.setData(Qt.ItemDataRole.UserRole,source);listing.addItem(item)
    listing.setCurrentRow(value.get('index',0));dialog.body.addWidget(listing)
    def remove():
        if listing.currentRow()>=0:listing.takeItem(listing.currentRow())
    def jump():
        source=listing.currentItem()
        if source:
            index=items.index(source.data(Qt.ItemDataRole.UserRole))
            changed=panel.owner.canvas.commit(lambda state:select(state,panel.key,index,window.store.directory))
            if changed is not False:window.comfy.input_flow.direct.reset_source(panel.key)
            dialog.reject()
    dialog.body.addLayout(row(button('略過選取圖片',remove,'Quiet'),button('只切到選取圖片',jump,'Quiet'),None))
    dialog.body.addWidget(dialog_buttons(dialog))
    if dialog.exec()==QDialog.DialogCode.Accepted:
        ordered=[listing.item(i).data(Qt.ItemDataRole.UserRole) for i in range(listing.count())]
        if window.comfy.input_flow.source_active(panel.key):window.notice('執行期間圖片清單已固定；可檢視及切圖，不能改變這批清單。');return
        panel.owner.canvas.commit(lambda state:set_items(state,panel.key,ordered,window.store.directory,True))


def step(panel,delta):
    value=panel.owner.data()['images'][panel.key]
    window=panel.owner.window
    if window.state.get('multi_output',{}).get('version',0)>=7:
        collection=window.comfy.images.collection(panel.key)
        if collection:
            window.comfy.images.choose(panel.key,collection['collection'],value.get('input_index',0)+delta);return
    index=value.get('index',0)+delta
    changed=panel.owner.canvas.commit(lambda state:select(state,panel.key,index,panel.owner.window.store.directory))
    if changed is not False:panel.owner.window.comfy.input_flow.direct.reset_source(panel.key)


def choose_prompt(panel):
    from .pnginfo import png_metadata
    from .widgets import ComboBox
    source=panel.owner.current_source(panel.key)
    if not source:return
    try:fields=metadata_sources(png_metadata(panel.owner.window.store.directory/source['relative']))
    except (ValueError,OSError) as exc:panel.owner.window.notice(str(exc));return
    if not fields:panel.owner.window.notice('這張圖片沒有可讀取的提示詞來源。');return
    dialog=StudioDialog(panel.owner.window);dialog.setWindowTitle('圖片提示詞來源');dialog.resize(650,480)
    picker=ComboBox();roles={'positive':'正面','negative':'負面','unknown':'未判定'}
    for entry in fields:
        name=('parameters' if entry['node']=='__parameters__' else '#'+entry['node'])+' / '+entry['field']
        picker.addItem(roles[entry['role']]+' · '+name,entry['choice'])
    selected=panel.owner.data()['images'][panel.key].get('prompt_choice')
    if isinstance(selected,list) and len(selected)==2:selected=['positive',*selected]
    picker.setCurrentIndex(next((i for i,e in enumerate(fields) if e['choice']==selected),0))
    preview=QPlainTextEdit();preview.setReadOnly(True)
    def show_source():preview.setPlainText(fields[picker.currentIndex()]['text'])
    picker.currentIndexChanged.connect(show_source);show_source()
    dialog.body.addWidget(picker);dialog.body.addWidget(preview,1)
    dialog.body.addWidget(label('依實際接線區分正負；未判定來源由你選擇，保留原始文字。','Subtle',True))
    dialog.body.addWidget(dialog_buttons(dialog))
    if dialog.exec()!=QDialog.DialogCode.Accepted:return
    if panel.owner.current_source(panel.key)!=source:
        panel.owner.window.notice('圖片來源已更新，請重新選擇提示詞。');return
    def change(state):
        value=state['canvas_functions']['images'][panel.key];value['prompt_choice']=picker.currentData()
        from .flow_data import incoming,materialize
        if state['multi_output']['version']>=7 and (incoming(state,panel.key,'image') or value.get('stage_reference')):
            from .image_source import read_content
            value['content']=read_content(panel.owner.window.store.directory/source['relative'],value['prompt_choice'])
            value.pop('content_error',None);materialize(state);return
        select(state,panel.key,value.get('index',0),panel.owner.window.store.directory)
    panel.owner.canvas.commit(change)
