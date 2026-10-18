# Markdown editing and source preservation

The Markdown file is the source of truth. Each open file owns one shared plain
`QTextDocument`, native Undo/Redo history, save receipt and composition state.
Views retain their own cursors and scroll positions. Rendering never serializes
HTML back over the source.

## Editing and preservation

`MarkdownBuffer.snapshot()` identifies the document, revision and Unicode source.
`SourceEdit` offsets are Python Unicode positions in that snapshot; the adapter
converts them to Qt UTF-16 positions. A transaction validates its target,
non-overlapping ranges and encoded size before entering one native edit block.
Stale snapshots, another document's snapshot, and commands during IME preedit
are rejected without editing anything.

`SourceLedger` stores shared slices of the original UTF-8 text and inserted text.
It follows native Qt undo states, including grouped typing and a new branch
after Undo. It does not infer source locations by matching repeated lines.
Existing CRLF, LF, CR, Unicode paragraph separators, NBSP and untouched markup
retain their original bytes. New paragraphs use the existing CRLF policy (or CR
when present, otherwise LF). A command spanning multiple disjoint ranges passes
its exact pieces to the ledger, so untouched text between ranges is preserved.
Saving does not discard provenance or history; closing/reopening starts a fresh
native undo history. BOM and the existing 8 MiB limit are retained.

The retained A text-style integration API demonstrates local font family, size and color
using readable inline HTML. It accepts plain selected prose and conservatively
rejects protected/unsupported syntax, entities and whitespace-sensitive selections
instead of escaping them into different rendered content. The B toolbar uses
syntax-aware `markdown_commands` transactions for compound, partial and
cross-paragraph selections. Unselected character formatting applies to subsequent
input. Clearing format splits only affected wrappers and preserves prose, URLs,
code and formula source. Paragraph indentation uses readable HTML; list indentation
uses Markdown whitespace. Typing Tab in the source remains a source indentation action.

## Presentation component

Each B view uses a local native `QTextEdit` for source text, selection, IME,
paragraph layout and folding. Content changes forward exact UTF-16 ranges to the
shared plain document. Local font/layout changes do not write source; native Undo
routes to the shared document. IME's paragraph-wide notifications are trimmed to
their changed slice while preserving the source ledger's byte provenance.

In **編寫**, syntax on the active line remains editable and inactive delimiters
collapse visually. **原文** shows original syntax. **閱讀** is a separate read-only
`QTextBrowser`; only recognized source syntax is rendered, never arbitrary HTML
attributes or scripts. Its resource loader returns empty bytes except for internally
drawn task-checkbox images. No file or remote attachment is fetched by B.

The three views share content/history and retain independent pane positions and
modes. Switching modes does not edit the document. Personal reading preferences
are stored separately from source. The compact toolbar uses slightly wider targets and a responsive floating icon grid for overflow. The view selector is an accessible icon button with named choices. It has a
keyboard entry (`Alt+F10`, `Esc` to return). Find/replace and command palette are
document-local; reading can search but cannot replace. The outline navigates and
reorders sections through one source transaction.

The retained A `MarkdownProjection` and B's bounded presentation are **not** full
Obsidian or CommonMark conformance. Advanced tables, formula/Mermaid rendering,
attachments, callouts and related extensions belong to C; properties/relations
belong to D, note management to E, recovery and advanced editing to F. B preserves
their original source and does not claim their rendered/interactive support.
No browser engine or downloaded dependency is introduced by B.

References: [QTextDocument](https://doc.qt.io/qtforpython-6/PySide6/QtGui/QTextDocument.html)
and [QTextCursor](https://doc.qt.io/qtforpython-6/PySide6/QtGui/QTextCursor.html).

## Save and external changes

One existing autosave timer per file runs after editing settles. Every view's
preedit state blocks autosave, external reload and file relocation until commit
or cancel. Closing a composing file is also refused without discarding it.
Synthetic `QInputMethodEvent` checks do not establish physical keyboard IME support.

A workspace timer checks only already-open Markdown files. Unchanged stat tuples
avoid repeated content reads. Changed clean files reload after UTF-8/size/path
validation, with view positions clamped and the prior version's Undo retired.
Dirty conflicts retain both the disk file and local editable draft, stop automatic
writes and use the existing notice/Save As route. An explicit save still checks
the full receipt before committing, including after the temporary write.
The stat-based poll is not a guarantee of detecting adversarial same-metadata
changes immediately; the full-byte save guard remains authoritative.

Periodic recovery versions, interactive comparison/merge and complete external
rename tracking remain later work. A failed write does not remove the live draft
or its Undo/Redo history. No source or runtime rollback implies newer documents
are automatically compatible with an older version.

## Verification

`test_markdown_foundation.py` checks mixed-format bytes, native histories,
Unicode range mapping, stale/cross-file transactions, rendering isolation,
preedit and failures. `test_markdown_editing.py` checks reachable B operations,
actual rendered styles/alignment, protected source, typing and task/fold behavior.
`test_markdown_workspace.py` checks actual workspace timers, split views, view-mode
and preference restart, blocked file operations, external reload and conflict rescue
through Save As. Use synthetic files and isolated data/temp with Qt offscreen.
These checks do not establish native clipboard, physical IME or user acceptance.
