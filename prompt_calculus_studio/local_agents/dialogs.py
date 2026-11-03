"""Small chat-binding, evidence and approval dialogs."""
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QPlainTextEdit
from ..widgets import ComboBox, StudioDialog, button, label
from ..work_resources.reader import line
from .store import AgentStore


def plain(text, role='Muted'):
    result = label(text, role, True)
    result.setTextFormat(Qt.TextFormat.PlainText)
    return result


class BindingDialog(StudioDialog):
    def __init__(self, page):
        super().__init__(page)
        self.setWindowTitle('協作設定')
        self.resize(580, 260)
        self.document = page.chat_document
        self.owner = self.document.key, self.document.owner_epoch
        self.agents = ComboBox()
        self.agents.addItem('一般對話', '')
        store = AgentStore(self.document.workspace.data)
        for agent in store.value['agents']:
            self.agents.addItem(agent['name'], agent['id'])
        current = self.document.payload.get('agent_id', '')
        self.agents.setCurrentIndex(max(0, self.agents.findData(current)))
        self.body.addWidget(self.agents)
        self.details = plain('')
        self.body.addWidget(self.details)
        self.agents.currentIndexChanged.connect(self.describe)
        self.describe()
        self.message = plain('')
        self.body.addWidget(self.message)
        line(self.body, button('管理 Agent', self.manage, 'Quiet'), None, button('套用', self.apply, 'Primary'))

    def describe(self):
        key = self.agents.currentData()
        if not key:
            self.details.setText('使用原有的聊天模型與設定。')
            return
        store = AgentStore(self.document.workspace.data)
        agent = store.get('agents', key)
        project = store.get('projects', agent['project'])
        skills = [store.get('skills', identity) for identity in agent['skills']]
        names = ['@' + s['name'] for s in skills if s['enabled'] and s['invocable']]
        self.details.setText(project['name'] + '\n' + ('、'.join(names) if names else '未設定可手動指定的 Skill'))

    def manage(self):
        workspace = self.document.workspace
        workspace.open_route('explore', 'local_agents')
        self.accept()

    def apply(self):
        doc = self.document
        if doc._disposed or (doc.key, doc.owner_epoch) != self.owner:
            self.message.setText('來源對話已關閉或另存。'); return
        try:
            if not doc.set_agent(self.agents.currentData()):
                raise ValueError(doc.error or '請先結束本輪回覆，再切換 Agent。')
        except (ValueError, OSError) as exc:
            self.message.setText(str(exc)); return
        self.accept()


def show_evidence(parent, evidence):
    dialog = StudioDialog(parent)
    dialog.setWindowTitle('本輪讀取紀錄')
    dialog.resize(860, 670)
    view = QPlainTextEdit()
    view.setReadOnly(True)
    view.setPlainText('\n\n'.join(e['operation'] + ' · ' + e['target'] + '\n' + e['content']
                                 for e in evidence.get('events', []) if e['operation'] != 'accepted'))
    dialog.body.addWidget(view)
    dialog.open()
    parent.evidence_dialog = dialog


def request_read_approval(document, request, ticket):
    from PySide6.QtWidgets import QWidget
    parent = document.workspace if isinstance(document.workspace, QWidget) else None
    dialog = StudioDialog(parent)
    dialog.setWindowTitle('核准讀取 · ' + document.title)
    dialog.resize(560, 260)
    action = ticket['action']
    titles = {'read_file': '讀取專案檔案', 'search_files': '搜尋專案', 'read_skill': '讀取 Skill',
              'read_source': '讀取規則或參考', 'list_skills': '搜尋 Skill'}
    dialog.body.addWidget(plain(titles.get(action.operation, action.operation), 'SectionTitle'))
    dialog.body.addWidget(plain(action.scope['arguments']))
    line(dialog.body, None, button('拒絕', dialog.reject, 'Quiet'), button('允許本次', dialog.accept, 'Primary'))
    def done(result):
        ticket['allow'] = result == dialog.DialogCode.Accepted and document._owns(request)
        ticket['done'].set()
        timer.stop()
    timer = QTimer(dialog)
    timer.setInterval(200)
    timer.timeout.connect(lambda: dialog.reject() if not document._owns(request) or ticket['done'].is_set() else None)
    dialog.finished.connect(done)
    timer.start()
    dialog.open()
    document._approval_dialog = dialog
