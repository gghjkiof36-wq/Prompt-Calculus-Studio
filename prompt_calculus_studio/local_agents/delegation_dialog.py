"""Read-only child work disclosure in the original chat."""
import json
from pathlib import Path

from PySide6.QtWidgets import QPlainTextEdit

from ..widgets import ComboBox, StudioDialog, button
from ..work_resources.reader import line
from .dialogs import plain


def children(evidence):
    rows = {}
    for event in evidence.get('events', []):
        if event['operation'] not in ('subagent', 'delegate_task'):
            continue
        try:
            value = json.loads(event['content'])
            if not isinstance(value, dict) or value.get('task_id') != event['target']:
                continue
        except (ValueError, TypeError):
            continue
        rows[event['target']] = value
    return rows


def summary(evidence, receiving):
    rows = children(evidence)
    if not rows:
        return ''
    pending = [r for r in rows.values() if 'state' not in r]
    if pending and receiving:
        return str(pending[-1].get('agent_name') or '本地助理') + ' · 工作中'
    returned = sum('state' in r for r in rows.values())
    return f'子工作 · {returned}/{len(rows)} 已回傳'


def show(parent, evidence, request_id):
    rows = children(evidence)
    if not rows:
        return
    ancestor = parent
    while ancestor is not None and not hasattr(ancestor, 'chat_document'):
        ancestor = ancestor.parentWidget()
    document = ancestor.chat_document if ancestor is not None else None
    dialog = StudioDialog(parent)
    dialog.setWindowTitle('本輪子工作')
    dialog.resize(720, 560)
    choices = ComboBox()
    for task_id, result in rows.items():
        choices.addItem(str(result.get('agent_name') or '本地助理'), task_id)
    dialog.body.addWidget(choices)
    view = QPlainTextEdit()
    view.setReadOnly(True)
    view.setAccessibleName('子工作與回傳結果')
    dialog.body.addWidget(view)
    message = plain('回到原聊天查看主助手的核對結果。')
    dialog.body.addWidget(message)

    def describe():
        from .delegation_view import STATES
        result = rows[choices.currentData()]
        state = '已回傳' if result.get('state') == 'complete' else STATES.get(result.get('state'), '尚未取得回傳結果')
        text = f"工作：{result.get('task', '')}\n\n狀態：{state}"
        if 'draft' in result:
            text += '\n\n回傳內容：\n' + str(result['draft'] or '（沒有正文）')
        if result.get('reason'):
            text += '\n\n說明：' + str(result['reason'])
        if result.get('truncated'):
            text += '\n\n此處內容已縮短，可開啟子對話查看完整紀錄。'
        view.setPlainText(text)

    def open_child():
        from .delegation_store import TaskStore
        try:
            if document is None or document._disposed:
                raise ValueError('來源對話已關閉。')
            record = TaskStore(document.workspace.data).read(choices.currentData())
            if (record is None or record.get('parent_document_id') != document.key
                    or record.get('parent_request_id') != request_id):
                raise ValueError('本機沒有這一輪的子工作紀錄；匯入的紀錄只供檢視。')
            path = Path(record['path'])
            if not path.resolve().is_relative_to(Path(document.workspace.root).resolve()):
                raise ValueError('子對話已離開目前工作區。')
            document.workspace.open_path(path, retained=True)
            dialog.accept()
        except (ValueError, OSError, KeyError, TypeError) as exc:
            message.setText(str(exc))

    line(dialog.body, button('開啟子對話', open_child), None, button('關閉', dialog.accept, 'Quiet'))
    choices.currentIndexChanged.connect(describe)
    describe()
    dialog.open()
    parent.evidence_dialog = dialog
