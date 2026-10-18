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

Drag a tab title into the right third of a single content area to create a right
pane. The translucent highlight previews the resulting pane, and a page label
follows the pointer. New-tab pages support the same gesture. When two panes are
open, dropping into the other pane's content area moves the view there; dropping
on a tab strip uses the visible insertion marker. Escape, an outside drop or
application deactivation cancels the move. Hovering a tab scroll arrow during a
drag reveals offscreen tabs. Feedback follows the current accent and honors the
reduced-motion setting.

Pane slots own visibility and geometry, while editor widgets remain under a
common workspace parent. Moving a view therefore avoids rebuilding its controls
or cascading a stylesheet change through the Canvas. Pane creation styles only
the new strip, and source placeholders do not briefly steal the active pane.

Each split pane has a permanent tab-menu dropdown. `合回單區` retains all views,
in left-then-right order, including duplicate views of the same document.
`關閉此分區` saves and closes that pane's tabs. A failed save keeps the affected
views open and focuses the first failure; other panes are unaffected.

Closing the last tab in a split pane removes that pane. This also applies to a
new-tab placeholder. Moving the last right-hand tab back to the left also removes
the empty right pane. Creating a split by moving a left tab to the right retains
a left placeholder so the requested split has two usable destinations.
The final tab of a single-pane workspace is replaced by a new-tab page.

Home creation fills the clicked home slot immediately for workflows, Prompt
management pages and Markdown files. A unique default filename is saved without
opening the sidebar; the tab menu provides Rename. Sidebar creation commands
retain their explicit inline naming flow. Queued clicks retain their originating
home identity, even when the other pane has focus.

`開啟檔案` and Ctrl+O open a filename/path search sheet using Canvas dialog colors.
The cached list appears immediately while enrolled folders are scanned in the
background. Recent files rank first with an empty query; icons distinguish the
three document types and relative paths disambiguate folders. Arrow keys select,
Enter opens in the captured pane, and Escape cancels. `從電腦選擇…` provides the
explicit native picker for files elsewhere. Searching never reads file contents.

Management documents reserve enough pane width for the list and right preview,
including when the preview is closed. The splitter and window enforce this
content minimum; the document has no horizontal scrollbar. Inactive management
tabs do not constrain another page. Stable document widgets share the outer
workspace mask so the bottom-left contour remains continuous after tab changes,
sidebar changes, and moving a view between panes.

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

The file tree displays `CANVAS` for workflow documents and `MANAGER` for category
documents at the trailing edge of each row. These are presentation labels; file
names, extensions, document kinds and Markdown names are unchanged.

## Navigation rendering

Returning to a Canvas restores its camera only when needed. An unchanged camera
does not reset the transform, disable decoration reuse, or request a second full
paint. Pane visibility is synchronized once, and editors remain below the shared
surface edge so tab changes do not repeatedly raise and repaint that overlay.
The connection control restyles only when its appearance actually changes.

Canvas forms render only their exposed area, using native widget/text painting
at the current scale. Embedded text is not replaced with a scaled screenshot.
Header measurements and native icon pixmaps have bounded caches whose keys
include the actual font/width and resolved foreground/state. Shadows omit only
an interior that the following opaque card completely covers; translucent cards
keep the original drawing. Full viewport updates remain enabled to preserve
the established Windows repaint behavior.

## Verification

Focused coverage lives in `test_workspace_tab_drag.py`, `test_workspace_panes.py`, `test_workspace_layout.py`,
`test_workspace_multiview_files.py`, and the existing workspace interaction
tests. Catalog appearance is covered across all eleven kinds and three palettes
in `test_workspace_file_commands.py`. Synthetic execution checks establish
ownership and routing, not live ComfyUI or GPU operation. Native interaction
and offscreen rendering remain separate evidence categories.
