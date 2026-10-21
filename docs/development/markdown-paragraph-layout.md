# Markdown paragraph layout

`SourceView.update_paragraphs` visits the complete document to preserve paragraph
heights, heading margins, list indentation and search geometry. Dense-document
syntax highlighting may be deferred; paragraph geometry is not deferred by this
optimization.

In writing mode, whole-line HTML styles can override paragraph alignment and
indentation. The existing `spans` parser produces these attributes only from HTML
tags, and the paragraph consumer requires a span starting at zero and ending at
the original line length. Therefore this call to `spans` is needed only when the
original, unstripped line starts with `<`. Plain text retains the existing default
format without running the inline parser at this call site.

Do not strip leading whitespace, search for a particular CSS property, or replace
this check with `line_info.container`. Lines beginning with `<` still use the
existing parser, including its entity, malformed-tag and protected-range behavior.
The guard does not introduce new HTML semantics or change parsing at other call
sites. The writing-mode branch, source-mode defaults and lazy thresholds remain
unchanged.

The dense-rendering regression checks the final search match is selected and
inside the viewport, alongside source bytes and Undo/Redo. Paragraph alignment,
blank-line height and indented wrapping have separate existing controls. A
characterization comparison should cover real paragraph formats in writing and
source modes, including leading whitespace and HTML-looking code content.

Source readiness, the largest GUI timer interval, the reading-mode switch call
and complete reading readiness are separate observations. Reducing source
paragraph parsing does not establish an improvement in initial WebEngine creation,
word counting, projection, bridge transfer or renderer completion. Measurements
under a CPU cap include scheduling delays; retain all samples and report total
reading readiness even when source readiness improves.
