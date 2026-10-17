# Local workspace: document views and split panes

The workspace supports one or two panes. A document owns its content, save path,
undo history and execution state; each view owns its cursor, selection, scroll
position or Canvas camera. Opening another view does not create another writer
or execution owner. A view key is independent of a document ID, including after
Save As followed by reopening the original file.

## Opening, moving and closing

Ordinary navigation replaces the current tab in the active pane. The explicit
new-tab and other-pane commands create additional views. Moving a tab preserves
the existing widget and its local state.

Each split pane has a permanent tab-menu dropdown. `合回單區` retains all views,
in left-then-right order, including duplicate views of the same document.
`關閉此分區` saves and closes that pane's tabs. A failed save keeps the affected
views open and focuses the first failure; other panes are unaffected.

Closing the last tab in a split pane removes that pane. This also applies to a
new-tab placeholder. Moving the last tab can leave a placeholder so a new split
still has two usable destinations; the placeholder can then be closed with ×.
The final tab of a single-pane workspace is replaced by a new-tab page.

## Persistence and recovery

`workspace-layout.json` contains view metadata only: paths, per-pane order and
current tab, active pane, split ratio, and view positions. Document bytes remain
in their original files. Undo is shared during a session, not serialized into
the layout record.

Layout writes use `QSaveFile` without direct-write fallback. Normal window close
first resolves dirty files and saves the layout. A failed save keeps the window
and its views available. Startup restores shared document ownership; explicit
launcher example files are used only when no layout was restored.

A missing file or disconnected root retains its tab with a retry action; no
empty replacement file or root folder is created. Invalid layout records fall
back to a new tab with a transient notice. File operations refresh saved paths
for every live view, including descendants of a moved folder.

## Appearance boundary

The workspace chrome does not redefine Canvas module styles. Embedded proxy
widgets and the function catalog use the same Canvas stylesheet and effective
appearance. The catalog's synthetic state copies the display settings without
changing the document's saved settings. The module color roles and control
styles remain those of 0.8.8.

## Verification

Focused coverage lives in `test_workspace_panes.py`, `test_workspace_layout.py`,
`test_workspace_multiview_files.py`, and the existing workspace interaction
tests. Catalog appearance is covered across all eleven kinds and three palettes
in `test_workspace_file_commands.py`. Synthetic execution checks establish
ownership and routing, not live ComfyUI or GPU operation. Native interaction
and offscreen rendering remain separate evidence categories.
