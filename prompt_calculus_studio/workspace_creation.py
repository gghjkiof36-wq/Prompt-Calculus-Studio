"""Direct home creation and explicit naming for sidebar file commands."""
from PySide6.QtWidgets import QTreeWidgetItem
from .workspace_file_dialogs import InlineRename
from .workspace_commands import NAMES
from .workspace_locations import path_key
from .ui_icons import icon


def create_home_document(commands, kind, context):
    """Fill this home slot immediately; the saved document can be renamed later."""
    from .workspace_home import WorkspaceHome
    w = commands.workspace
    home = w.tabs_by_key.get(context.view_key)
    # Ignore a queued second click once this particular home has been replaced.
    if home is None or not isinstance(home.widget, WorkspaceHome):
        return None
    directory = context.destination
    if directory is None or w.locations.root_for(directory) is None:
        previous = w.sidebar_preference
        try:
            w.choose_root()
        finally:
            w.sidebar_preference = previous
            w.sync_sidebar()
        directory = w.locations.current
        if directory is None:
            return None
    try:
        directory = w.locations.directory(directory)
        existing = {p.name.casefold() for p in directory.iterdir()}
        suffix = dict(category='.pcs-category', workflow='.pcs-workflow', markdown='.md')[kind]
        name, number = '未命名', 1
        while (name+suffix).casefold() in existing:
            number += 1
            name = '未命名 ' + str(number)
        path = commands.create_at(kind, name, directory)
    except (ValueError, OSError) as error:
        w.show_notice(str(error))
        return None
    w.refresh_tree()
    created = w.open_context(path, context)
    if created:
        if kind == 'workflow':
            created.host.canvas.view.setFocus()
        elif kind == 'category':
            created.host.prompt_editor.search.setFocus()
        else:
            created.widget.setFocus()
    return created


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
