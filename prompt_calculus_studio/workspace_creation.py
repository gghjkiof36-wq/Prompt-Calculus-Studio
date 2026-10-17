"""A temporary sidebar row becomes a file only after the name is confirmed."""
from PySide6.QtWidgets import QTreeWidgetItem
from .workspace_file_dialogs import InlineRename
from .workspace_commands import NAMES
from .workspace_locations import path_key
from .ui_icons import icon


def start_creation(commands, kind, context, after_create=None, finished=None):
    w = commands.workspace
    if commands.files.inline is not None:
        commands.files.inline.cancel()
    directory = context.destination
    if directory is None or w.locations.root_for(directory) is None:
        w.choose_root()
        directory = w.locations.current
        if directory is None:
            if finished:
                finished()
            return None
    try:
        directory = w.locations.directory(directory)
        existing = {p.name.casefold() for p in directory.iterdir()}
    except (OSError, ValueError) as error:
        w.show_notice(str(error))
        if finished:
            finished()
        return None
    w.open_route('home')
    w.sidebar_preference = True
    w.sync_sidebar()
    w.tree_state.revealed.add(directory)
    w.refresh_tree()
    parent = w.path_items.get(path_key(directory))
    if parent is None:
        w.show_notice('找不到新增位置，請重新加入這個資料夾。')
        if finished:
            finished()
        return None
    previous = w.tree_state.selected
    w.select_path(directory)
    suffix = dict(category='.pcs-category', workflow='.pcs-workflow', markdown='.md', folder='')[kind]
    name, number = '未命名', 1
    while (name+suffix).casefold() in existing:
        number += 1
        name = '未命名 ' + str(number)
    item = QTreeWidgetItem([name])
    if kind == 'folder':
        item.setIcon(0, icon('folder', 'navigation'))
    parent.insertChild(0, item)
    parent.setExpanded(True)
    w.tree.setCurrentItem(item)
    w.tree.scrollToItem(item)
    created = None
    def commit(value):
        nonlocal created
        created = commands.create_at(kind, value, directory)
    def finish():
        commands.files.inline = None
        parent.removeChild(item)
        w.tree_state.selected = str(created) if created else previous
        if created:
            w.tree_state.revealed.add(created)
        w.refresh_tree()
        try:
            if created:
                w.select_path(created)
                if after_create:
                    after_create(created)
                elif kind != 'folder':
                    w.open_context(created, context)
        except (OSError, ValueError) as error:
            w.show_notice(str(error))
        finally:
            if finished:
                finished()
    editor = InlineRename(w.tree, item, directory/name, commit, finish)
    editor.name.setAccessibleName('新增' + NAMES[kind] + '名稱')
    commands.files.inline = editor
    return editor
