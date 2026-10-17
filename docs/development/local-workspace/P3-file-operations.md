# Local document operations

Ordinary file opening, recent-file navigation and creation replace the current
tab's contents in place, including a New Tab page. Only plus or an explicit
Open in New Tab action adds a tab. An already visible file is focused rather
than creating a second writer. The superseded preview/retained distinction no
longer controls navigation or appears in the tab menu.

Navigation saves pending confirmed edits before replacing the page. Failure
keeps its editor and shows the existing expiring notification. Back/Forward
retain the original live model, text Undo, selection and Canvas view while that
tab's navigation history remains reachable. Closing the tab releases its cached
pages. This is in-session navigation, not restoration across application restarts.

## Captured operations and timers

Sidebar rows, tabs and the category page menu capture their own target. A menu
on another file does not navigate or retarget the active editor. F2 handles the
focused sidebar row or document/tab surface, and leaves text, IME, forms and node
editing alone. Ctrl+Shift+S uses the active document; tool and New Tab pages have
no hidden file target. Sidebar rename is inline, with Enter to confirm and
Escape/focus loss to cancel. File names keep their managed suffix.

Each operation suspends affected writers before opening its form. An in-flight
synchronous save completes before a deferred operation begins; a merely pending
timer is not flushed. Closing, explicit save and duplicate operations cannot
bypass this boundary. Other files continue autosaving. Cancel or a fully rolled
back error restarts the normal delay only for files without prior save errors.

## Save As

The new file contains current confirmed edits; the original retains its last
saved bytes at operation start. Exclusive publication rejects existing files,
case aliases and hard links. PCS attachments are copied through the existing
validated portable-asset boundary. Markdown retains UTF-8/BOM/newline conventions
and receives no identity marker or sidecar.

Pending shared Canvas text is applied to its existing model before the copy is
serialized, while the external writer remains paused. Unconfirmed material
dialogs retain their confirm/cancel boundary; Save As cannot accept them.

For an open file, the same tab, editor and Undo stack become the new file. PCS
owned IDs are projected through a stable copy map at serialization, leaving the
native editor's runtime IDs and historical snapshots intact. Newly introduced
and restored historical objects reuse their map; external graph targets and
copy provenance keep their established semantics. File identity and path belong
to the document adapter, never an Undo snapshot. Reopening the original creates
an independent model from its unchanged disk bytes.

For a file not currently open in a visible tab, Save As reads its disk contents
and opens the successfully created file explicitly in a new tab, preserving the
other current editor. Cancel/failure leaves no temporary source tab. A published
file followed by an uncertain UI transition is reported with its location and
suspends the affected writer; it is not treated as a failed copy followed by an
automatic write to the original.

## Rename and move

Rename supports files and ordinary folders, including case-only changes. Move
supports a single file or folder within one explicitly linked root. Roots,
cross-root moves, merges, links/reparse escapes and moving a folder into itself
or a descendant are rejected. Source identity/content is rechecked before commit;
destination publication never overwrites a competing file.

All visible and navigation-cached descendants update their paths and receipts
before timers resume. Rename/move preserve IDs, dirty text, native Undo and
Canvas state; they do not rename internal Canvas nodes or Markdown headings.
File dependencies are checked/copied before moving, preserving shared source
assets. Failure rolls back the location when possible. An uncertain partial
result retains its actual locations and stops writes until recovery.

Markdown relative links must remain resolvable after relocation. Same-folder
copies retain their references. Folder moves can carry their own attachments;
unsupported wiki references or unverifiable destinations are blocked with the
affected reference. This batch does not rewrite other notes' inbound links or
provide complete Markdown/Obsidian attachment management.

The sidebar header contains creation and whole-tree controls, including empty
folder visibility. File rows remain text-only. Canvas node menus, text editing,
connection behavior and the accepted tab geometry use their existing surfaces.

## Validation scope

Qt integration cases exercise current-tab navigation, the full create/edit/copy/
rename/move/save/reopen chain, encoding, assets, ownership and Undo. Fault cases
cover conflicts, collisions, suspended timers, rollback and retained partial
results. Native Windows captures verify both navigation positions and UI scale.
These are isolated local editing checks, not live service, GPU, installation or
publication acceptance.
