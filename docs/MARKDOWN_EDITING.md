# Markdown source editing foundation

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

The minimal A text-style operation demonstrates local font family, size and color
using readable inline HTML. It accepts plain selected prose and conservatively
rejects protected/unsupported syntax, entities and whitespace-sensitive selections
instead of escaping them into different rendered content. It is an integration API, not the complete
format toolbar: compound formatting, zero-width typing attributes, cross-paragraph
selection and syntax-aware live editing are subsequent work.

## Presentation component

Native `QPlainTextEdit` remains the editable source/IME host. A separate disposable
`MarkdownProjection` (`QTextDocument`) proves basic Markdown and inline style
presentation from a versioned snapshot. Its resource loader returns handled empty
bytes; it cannot retrieve local/remote attachments. It is never a write target.

Qt's built-in Markdown presentation is **not** full Obsidian support. Live preview,
source-to-rendered selection mapping, formula/Mermaid rendering, attachment
resolution, callouts and other extensions remain later presentation work. A
projection preserves formula/code source; it does not claim rendered mathematics.
No browser engine or downloaded dependency is introduced by this foundation.

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
preedit and failures. `test_markdown_workspace.py` checks actual workspace timers,
split views, blocked file operations, external reload and conflict rescue through
Save As. Use synthetic files and an isolated data/temp directory with Qt offscreen.
