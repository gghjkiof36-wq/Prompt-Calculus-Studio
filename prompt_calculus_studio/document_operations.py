"""Content transactions shared by document editors, independent of Qt.

The caller supplies access to its live state and existing history dictionary.
Presentation, persistence notifications and runtime flow work stay in adapter
callbacks; this coordinator creates neither a widget nor another Undo stack.
File save, Save As and rename remain the workspace file commands' concern.
"""
import copy
import json
import time

from . import composition
from .core import prune_output_order, validate_state
from . import content_history
from .material_navigation import navigation_only
from .local_documents.library import change_only
from .prompt_groups import mark_version


HISTORY_FIELDS = ('version', 'modules', 'items', 'library_categories', 'selections', 'temporary', 'temporary_before', 'weights', 'instances', 'output_order', 'text_positions', 'text_sizes', 'uses', 'workspaces', 'workspace', 'workspace_scenes', 'prompt_layout', 'view_drafts', 'canvas_functions', 'generation', 'material_navigation')
MULTI_HISTORY_FIELDS = HISTORY_FIELDS + ('multi_output', 'draft', 'draft_base')


class DocumentOperations:
    def __init__(self, *, state, replace_state, history, project, checkpoint,
                 sync, notice, is_layout_only, begin=None, prepare=None,
                 complete=None, clock=time.monotonic):
        """Bind capabilities, not an editor or window.

        ``prepare(candidate, context)`` derives flow content before validation.
        ``complete(context)`` runs after state/history publication and sync;
        its final projection is then recorded in that same Undo entry. These
        hooks preserve the MultiCanvas runtime boundary, including exceptions.
        ``checkpoint(old_state)`` retains the pre-upgrade persistence boundary.
        """
        self.state = state
        self.replace_state = replace_state
        self.history = history
        self.project = project
        self.checkpoint = checkpoint
        self.sync = sync
        self.notice = notice
        self.is_layout_only = is_layout_only
        self.begin = begin
        self.prepare = prepare
        self.complete = complete
        self.clock = clock
        if history.get('undo_stack') is None:
            history.update(undo_stack=[], redo_stack=[], last_state=None)
        self._undo_sizes = {}

    def commit(self, operation, label='編輯', merge_key=None):
        context = self.begin() if self.begin else None
        result = self._commit(operation, label, merge_key, context)
        # Runtime route notification was outside TextCanvas's validation error
        # handler. Do not turn its failure into a rejected content transaction.
        if result and self.complete:
            self.complete(context)
            if self.history['undo_stack']:
                before, _ = self.history['undo_stack'][-1]
                after = self.project(self.state())
                self.history['undo_stack'][-1] = (before, after)
                self.history['last_state'] = after
        return result

    def _commit(self, operation, label, merge_key, context):
        before = self.project(self.state())
        value = copy.deepcopy(self.state())
        try:
            operation(value)
            if self.prepare:
                self.prepare(value, context)
            mark_version(value)
            library_only = navigation_only(self.state(), value) or change_only(self.state(), value)
            if not library_only:
                composition.prune_instances(value)
                if value.get('uses'):
                    value.setdefault('prompt_layout', 'paragraphs')
                prune_output_order(value)
            validate_state(value)
            if value == self.state():
                return
            if value['version'] > self.state()['version']:
                self.checkpoint(self.state())
            after = self.project(value)
            previous = self.history.get('_last_edit')
            now = self.clock()
            undo = self.history['undo_stack']
            coalesce = bool(merge_key and previous and previous[0] == merge_key
                            and now-previous[1] < .65 and undo
                            and isinstance(undo[-1], tuple) and undo[-1][1] == before)
            content_history.record(value, before, after, label=label, merge_key=merge_key, coalesce=coalesce)
            validate_state(value)
            self.replace_state(value)
            if coalesce:
                undo[-1] = (undo[-1][0], after)
            else:
                undo.append((before, after))
            self.trim_history()
            self.history['_last_edit'] = (merge_key, now)
            self.history['redo_stack'].clear()
            self.history['last_state'] = after
            self.sync('library' if library_only else 'layout' if self.is_layout_only(before, after) else 'prompt')
            return True
        except (ValueError, KeyError, StopIteration) as exc:
            self.notice(str(exc))
            return False

    def trim_history(self):
        # Cache retained immutable entry identities, not a second history.
        undo = self.history['undo_stack'][-100:]
        self.history['undo_stack'] = undo
        sizes = []
        retained = {}
        for entry in undo:
            prior = self._undo_sizes.get(id(entry))
            size = prior[1] if prior and prior[0] is entry else len(json.dumps(entry, ensure_ascii=False, default=str).encode('utf-8'))
            sizes.append(size)
            retained[id(entry)] = (entry, size)
        total = sum(sizes)
        while len(undo) > 1 and total > 32_000_000:
            removed = undo.pop(0)
            total -= sizes.pop(0)
            retained.pop(id(removed), None)
        self._undo_sizes = retained
