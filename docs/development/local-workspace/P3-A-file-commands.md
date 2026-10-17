# P3-A: creation and explicit locations

The sidebar is an inventory of explicitly linked folders, not the owner of a
document. A document's absolute file path and its parent directory define its
read, write and asset location. Opening a single external file does not enroll
or recursively scan its parent directory.

## Command context

Each invocation captures its origin, target path/kind, linked root, asset base,
stable session tab key and document identity before showing a menu or form.
Creation from a tree row uses the folder or a file's parent; creation from a
new-tab page uses the current linked root, irrespective of a previously selected
child. A right click selects the clicked row but does not open its file.

Toolbar, folder context menu and new-tab actions share one creation controller
and one name/location form. The form retains input on validation or I/O failure.
Only a successful exclusive creation publishes a tree entry or retained tab.
Folder creation never creates a document tab. Managed suffixes, Windows names,
case-insensitive collisions and reparse boundaries are checked at I/O time.

The content heading and its three tools occupy the space beside the tabs when
navigation is at the top. Its divider joins the sidebar below, and the heading
disappears with the sidebar when collapsed. With navigation at the side, the
heading stays inside the sidebar and the tabs share the top row. File labels
reserve an empty folder-icon column; inside a child folder they align under its
label, while root-level files align with sibling folder labels. Folder icons,
disclosure controls and actual parent relationships remain intact.

## Linked roots and recent documents

The launch root seeds a per-data-directory list of linked folders. Adding and
removing links changes navigation only; open editors, drafts and native Undo
remain alive. Roots can be nested; selecting a row identifies its actual tree
root. No automatic private-directory discovery is performed. The inventory skips
`.obsidian`, `.pcs`, `assets`, reparse points and the application's data directory.
It reads metadata only, never document bodies. Explicit external-folder creation
offers to link and locate the result; declining leaves it on disk at the stated
location without inventing a sidebar row.

Recent-file records are absolute paths. Existing relative-only records resolve
against the original launch root and migrate on the next successful write.
Full paths in recent rows and tab tooltips distinguish same-named files. Missing
or invalid records are handled independently, not by discarding later entries.
This is not whole-session, tab or Undo restoration.

## Continuity for later batches

PCS document IDs and session tab keys remain independent of path. Markdown uses
a session UUID, without sidecar files or nonstandard text. Later rename/move
commands will update the existing model's path, receipt and asset base instead
of closing and reconstructing it. Contexts must resolve their captured tab key,
never a subsequently active tab. Root-link removal does not rename or move files.

Save As belongs to P3-B: serialize the target's current legal draft with the
existing identity duplication and asset-copy boundary, then open a new retained
clean tab. The original disk file, draft, dirty flag and Undo remain unchanged.
Rename/F2 belongs to P3-C; same-root single-item moves and their rollback/asset
relocation belong to P3-D. No placeholder commands advertise these unfinished
operations in P3-A. Existing save, Undo, preview and service isolation continue.

## Validation scope

Focused checks exercise destination defaults, all creation kinds, cancel and
invalid names, collisions/races, failed writes, external open/save/recent reopen,
explicit linking, editor isolation and the existing sidebar alignment. Native
Windows checks cover 125%/150% and 220/264/480 sidebar widths. These do not imply
whole-product acceptance or service, GPU, installation or release verification.
