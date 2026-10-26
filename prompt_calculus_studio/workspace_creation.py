"""Create a saved unnamed entry, then offer an optional inline rename."""
from .workspace_file_dialogs import InlineRename
from .workspace_commands import NAMES
from .workspace_locations import path_key
from .workspace_file_io import available_targets, name_collision


def create_unnamed(commands, kind, directory):
    suffix = dict(category='.pcs-category', workflow='.pcs-workflow', markdown='.md', folder='')[kind]
    for target in available_targets(directory/('未命名'+suffix), folder=kind == 'folder'):
        try:
            return commands.create_at(kind, target.name, directory)
        except (ValueError, OSError) as error:
            if not name_collision(error):
                raise


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
        path = create_unnamed(commands, kind, directory)
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
        created = create_unnamed(commands, kind, directory)
    except (OSError, ValueError) as error:
        w.show_notice(str(error))
        if finished:
            finished()
        return None
    w.open_route('home')
    w.sidebar_preference = True
    w.sync_sidebar()
    w.tree_state.revealed.add(directory)
    w.tree_state.revealed.add(created)
    w.refresh_tree()
    parent = w.tree.invisibleRootItem() if directory == w.locations.initial else w.path_items.get(path_key(directory))
    if parent is None:
        w.show_notice('找不到新增位置，請重新加入這個資料夾。')
        if finished:
            finished()
        return None
    item = w.path_items.get(path_key(created))
    if kind != 'folder':
        w.open_context(created, context)
    w.tree_state.selected = str(created)
    parent.setExpanded(True)
    w.tree.setCurrentItem(item)
    w.tree.scrollToItem(item)
    accepted = False
    def commit(value):
        nonlocal created, accepted
        if not value.strip():
            accepted = True
            return  # Empty optional name keeps the already-created entry.
        from .workspace_locations import CommandContext
        operation = commands.files.begin(CommandContext('tree', created, kind, context.root, directory))
        try:
            created = commands.files.rename(operation, value)
            accepted = True
        finally:
            commands.files.end(operation, refresh=False)
    def finish():
        commands.files.inline = None
        w.tree_state.selected = str(created)
        w.tree_state.revealed.add(created)
        w.refresh_tree()
        try:
            w.select_path(created)
            if after_create and accepted:
                after_create(created)
        except (OSError, ValueError) as error:
            w.show_notice(str(error))
        finally:
            if finished:
                finished()
    editor = InlineRename(w.tree, item, created, commit, finish)
    editor.name.setAccessibleName('新增' + NAMES[kind] + '名稱')
    commands.files.inline = editor
    return editor
