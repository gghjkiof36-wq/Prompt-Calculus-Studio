"""Source-owned chat file actions; publish new files without retargeting a chat."""
from pathlib import Path
from .local_documents import io
from .workspace_file_io import available_targets


def _live_owner(workspace, tab, identity=None):
    document = tab.document
    if (workspace.tabs_by_key.get(tab.key) is not tab or document is None
            or document.kind != 'chat' or document._disposed):
        return None
    if identity is not None and (document.key, document.owner_epoch, document.path) != identity:
        return None
    return document


def choose_pcs_attachments(workspace, source_tab):
    from PySide6.QtWidgets import QFileDialog
    document = _live_owner(workspace, source_tab)
    if document is None:
        workspace.show_notice('來源對話已關閉，未加入附件。'); return False
    identity = (document.key, document.owner_epoch, document.path)
    selected, _ = QFileDialog.getOpenFileNames(workspace, '加入 PCS 文件（已保存內容）',
        str(document.path.parent), 'PCS 文件 (*.md *.pcs-category *.pcs-workflow *.pcs-chat)')
    if not selected: return False
    if _live_owner(workspace, source_tab, identity) is None:
        workspace.show_notice('來源對話已變更，請重新加入附件。'); return False
    descriptors = []
    for value in selected:
        path = Path(value).absolute(); root = workspace.locations.root_for(path)
        if root is None:
            workspace.report_error('請從已加入的工作區資料夾選擇文件。'); return False
        descriptors.append(dict(source_type='pcs', path=str(path), workspace_root=str(root)))
    # Reading/decoding is owned by ChatDocument's worker. This view only carries
    # the original owner and explicit selected roots across the native picker.
    return document.add_attachments(descriptors)


def export_markdown(workspace, source_tab, turn_id, attempt_id):
    from PySide6.QtWidgets import QFileDialog
    from .local_ai.thinking import split_thinking
    from .local_documents import paths
    from .workspace_file_io import (create_markdown, named_path, TargetNameCollision,
                                    PublishedMarkdownError)
    document = _live_owner(workspace, source_tab)
    if document is None:
        workspace.show_notice('來源對話已關閉，無法另存回覆。'); return None
    identity = (document.key, document.owner_epoch, document.path)
    turn = next((t for t in document.payload['turns'] if t['id'] == turn_id), None)
    attempt = next((a for a in turn['attempts'] if a['id'] == attempt_id), None) if turn else None
    if attempt is None:
        workspace.show_notice('這則回覆已變更，請重新選擇。'); return None
    answer = split_thinking(attempt['text'], receiving=attempt['status'] == 'receiving').answer
    if not answer.strip():
        workspace.show_notice('這則回覆尚無可保存的正文。'); return None
    raw = answer.encode('utf-8')
    if len(raw) > 8 * 1024 * 1024:
        workspace.report_error('回覆超過 Markdown 文件容量限制。'); return None
    tail = ' - 回覆.md'; room = 510 - len(tail.encode('utf-16-le'))
    name = document.path.stem.encode('utf-16-le')[:room].decode('utf-16-le', errors='ignore') + tail
    chosen, _ = QFileDialog.getSaveFileName(workspace, '另存回覆正文為 Markdown',
        str(document.path.parent / name), 'Markdown (*.md)', options=QFileDialog.Option.DontConfirmOverwrite)
    if not chosen: return None
    if _live_owner(workspace, source_tab, identity) is None:
        workspace.show_notice('來源對話已變更，請重新另存回覆。'); return None
    target = None
    try:
        chosen = Path(chosen)
        directory = workspace.locations.directory(chosen.parent)
        preferred = named_path(directory, chosen.name, '.md')
        for target in available_targets(preferred):
            try:
                create_markdown(target, raw)
                break
            except TargetNameCollision:
                continue
        if paths.read_bytes(target, 8 * 1024 * 1024)[0] != raw:
            raise PublishedMarkdownError('保存後內容已變更，請檢查新檔。')
    except (ValueError, OSError) as error:
        message = '無法完成另存：' + str(error)
        if target is not None and target.exists():
            message += '\n檔案位置：\n' + str(target)
        workspace.report_error(message); return None
    try:
        workspace.refresh_tree(); workspace.last_open_error = ''
        fresh = workspace.open_path(target, True, new_tab=True, pane=workspace.pane_for(source_tab), quiet=True)
        if fresh is None:
            raise ValueError(workspace.last_open_error or '可從側欄或檔案位置重新開啟。')
    except (ValueError, OSError, RuntimeError) as error:
        workspace.report_error('Markdown 已保存，但無法開啟：\n' + str(target) + '\n' + str(error))
        return None
    return fresh


def _branch_name(stem):
    tail = ' - 分支.pcs-chat'
    room = 510 - len(tail.encode('utf-16-le'))
    return stem.encode('utf-16-le')[:room].decode('utf-16-le', errors='ignore') + tail


def create_branch(workspace, source_tab, turn_id, attempt_id=''):
    """Only a saved/read-back branch is opened; failed publication keeps evidence."""
    document = source_tab.document
    if (workspace.tabs_by_key.get(source_tab.key) is not source_tab
            or document is None or document.kind != 'chat' or document._disposed):
        workspace.show_notice('來源對話已關閉，無法建立分支。')
        return None
    target = None
    try:
        directory = workspace.locations.directory(document.path.parent)
        candidate = document.branch_document(turn_id, attempt_id or None)
        # Each candidate is checked again by exclusive create. A collision from
        # another process advances the name; no existing sibling is replaced.
        for target in available_targets(directory / _branch_name(document.path.stem)):
            result = io.create(target.parent, target.name, candidate)
            if result.status == 'saved':
                break
            if result.name_collision and not result.published:
                continue
            message = result.error or '分支尚未保存。'
            if result.published:
                message += '\n已建立檔案，但保存後核對未完成：\n' + str(target)
            if result.recovery_paths:
                message += '\n保留的回復版本：\n' + '\n'.join(result.recovery_paths)
            workspace.report_error(message)
            return None
    except (ValueError, OSError) as error:
        workspace.report_error('無法建立對話分支：' + str(error))
        return None

    # Publication is complete. A navigation failure must not remove the file or
    # turn a second click into an overwrite of the successfully created branch.
    try:
        workspace.refresh_tree()
        workspace.last_open_error = ''
        fresh = workspace.open_path(target, True, new_tab=True,
                                    pane=workspace.pane_for(source_tab), quiet=True)
    except (ValueError, OSError, RuntimeError) as error:
        workspace.report_error('分支已保存，但無法開啟：\n' + str(target) + '\n' + str(error))
        return None
    if fresh is None:
        message = '分支已保存，但無法開啟。可從側欄或檔案位置重新開啟：\n' + str(target)
        if getattr(workspace, 'last_open_error', ''):
            message += '\n' + workspace.last_open_error
        workspace.report_error(message)
        return None
    if document.error:
        workspace.show_notice('分支已建立；原對話仍有保存錯誤：' + document.error)
    return fresh
