"""Keep text assistance beside the source selection, inside its document view."""
from PySide6.QtCore import QObject, QEvent, QPoint, QRect, QTimer, Qt
from PySide6.QtGui import QTextCursor
from shiboken6 import isValid

from .markdown_source import utf16_position
from .markdown_assist_projection import AssistProjection


class MarkdownInlineAssist(QObject):
    def __init__(self, surface):
        super().__init__(surface)
        self.surface = surface
        self.panel = None
        self.projection = None
        self._candidate = None
        self._dragging = False
        self._suppressed = None
        self._opening = False
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(80)
        self.timer.timeout.connect(self.refresh)
        self.project_timer = QTimer(self)
        self.project_timer.setSingleShot(True)
        self.project_timer.setInterval(40)
        self.project_timer.timeout.connect(self.project_state)
        editor = surface.editor
        editor.selectionChanged.connect(self.schedule)
        editor.verticalScrollBar().valueChanged.connect(self.reposition)
        editor.horizontalScrollBar().valueChanged.connect(self.reposition)
        editor.document().documentLayout().update.connect(self.schedule)
        surface.page.buffer.compositionChanged.connect(self.schedule)
        for widget in (surface, editor, editor.viewport()):
            widget.installEventFilter(self)

    def schedule(self, *args):
        if not self._opening:
            self.timer.start()

    def eventFilter(self, watched, event):
        kind = event.type()
        if not isValid(self.surface) or not isValid(self.surface.editor):
            return False
        editor = self.surface.editor
        if watched in (editor, editor.viewport()):
            if kind == QEvent.Type.MouseButtonPress:
                self._dragging = event.button() == Qt.MouseButton.LeftButton
                self.dismiss()
                self._suppressed = None
            elif kind == QEvent.Type.MouseButtonRelease:
                self._dragging = False
                self.schedule()
            elif kind == QEvent.Type.KeyPress:
                if event.key() == Qt.Key.Key_Escape and self.panel is not None:
                    self.dismiss()
                    event.accept()
                    return True
                self._suppressed = None
                self.schedule()
            elif kind == QEvent.Type.KeyRelease:
                self.schedule()
            elif kind == QEvent.Type.ContextMenu:
                self.dismiss()
            elif kind == QEvent.Type.Hide:
                # Mode switches dismiss; an inactive tab keeps its draft and owner.
                if self.surface.mode == 'read':
                    self.dismiss()
                elif self.panel is not None and self.projection is None:
                    self.panel.hide()
            elif kind in (QEvent.Type.Resize, QEvent.Type.Show, QEvent.Type.FontChange):
                self.schedule()
        elif watched is self.surface:
            if kind == QEvent.Type.Hide:
                self.timer.stop()
                if self.panel is not None:
                    self.panel.hide()
            elif kind in (QEvent.Type.Show, QEvent.Type.Resize, QEvent.Type.LayoutRequest):
                self.schedule()
        return False

    def _allowed(self):
        s = self.surface
        advanced = getattr(s, 'advanced', None)
        return (isValid(s) and isValid(s.editor) and s.isVisible()
                and (s.editor.isVisible() or self.projection is not None) and s.mode in ('write', 'source')
                and not s.editor.isReadOnly() and not s.page.buffer.opening
                and not s.page.buffer.composing and not self._dragging
                and not (advanced and (advanced.rectangle is not None or advanced.cursors)))

    def refresh(self):
        self.timer.stop()
        if self.panel is not None and not isValid(self.panel):
            self.panel = None
        if not self._allowed():
            if self.panel is not None:
                self.panel.hide()
            return
        selection = self.surface.selection()
        snapshot, start, end = selection
        panel = self.panel
        if panel is not None:
            target = panel.target
            if target.used:
                self.dismiss()
                return
            # An external edit restores the authoritative page immediately.
            if panel.controller.invalidated:
                self.restore_projection()
                self.reposition()
                return
            if self.projection is not None:
                self.reposition()
                return
            if selection == (target.snapshot, target.start, target.end):
                self.reposition()
                return
            self.dismiss()
            self._suppressed = None
        if start == end or not snapshot.text[start:end].strip() or selection == self._suppressed:
            return
        if not self.surface.editor.hasFocus():
            return
        self._opening = True
        try:
            from .markdown_text_assist import open_text_assist
            open_text_assist(self.surface, snapshot, start, end)
        finally:
            self._opening = False

    def attach(self, panel):
        self.panel = panel
        self._suppressed = None
        panel.closed.connect(lambda: self._closed(panel))
        panel.geometry_needed.connect(self.reposition)
        panel.controller.changed.connect(self.state_changed)
        if hasattr(self.surface, 'draft_composer'):
            self.surface.draft_composer.refresh()
        self.reposition()

    def start_draft(self, instruction):
        """Capture the current complete document only when the user sends."""
        if not instruction.strip() or not self._allowed() or self.panel is not None:
            return None
        self._opening = True
        try:
            from .markdown_text_assist import open_text_assist
            panel = open_text_assist(self.surface, kind='draft')
            if panel is not None:
                panel.instruction.setPlainText(instruction)
                if not panel.start_button.isEnabled():
                    self.surface.report(panel.start_button.toolTip())
                    self.dismiss()
                    return None
                panel.start_button.click()
            return panel
        finally:
            self._opening = False

    def state_changed(self):
        panel = self.panel
        if panel is not None and isValid(panel) and panel.controller.busy and self.projection is not None:
            if not self.project_timer.isActive():
                self.project_timer.start()
            return
        self.project_timer.stop()
        self.project_state()

    def project_state(self):
        panel = self.panel
        if panel is None or not isValid(panel):
            return
        controller = panel.controller
        if controller.disposed or controller.invalidated:
            self.restore_projection()
            return
        if controller.state == 'idle':
            return
        s = self.surface
        if self.projection is None:
            self.projection = AssistProjection(s)
            self._needs_anchor = True
            self.projection.verticalScrollBar().valueChanged.connect(self.reposition)
            self.projection.viewport().installEventFilter(self)
            s.stack.addWidget(self.projection)
            s.stack.setCurrentWidget(self.projection)
            s.find_bar.hide()
            s.update_controls()
            workspace = s.page.file_title.workspace
            tab = workspace.tabs_by_key.get(panel.target.tab_key)
            if tab is not None:
                workspace.retain(tab)
        candidate = controller.candidate
        if candidate != self._candidate:
            # Until a visible answer arrives, keep the original passage in place.
            available = max(220, self.projection.viewport().width()-16)
            width = panel.preferred_width(available)
            self.projection.project(panel.target, candidate, panel.preferred_height(width))
            self._candidate = candidate
            if self._needs_anchor:
                self._needs_anchor = False
                cursor = self.projection.textCursor()
                cursor.setPosition(self.projection.start_position)
                self.projection.setTextCursor(cursor)
                self.projection.ensureCursorVisible()
                bar = self.projection.verticalScrollBar()
                clearance = panel.preferred_height(width)+16
                bar.setValue(bar.value()-max(0, clearance-self.projection.cursorRect(cursor).top()))
        self.reposition()

    def restore_projection(self):
        self.project_timer.stop()
        view, self.projection = self.projection, None
        self._candidate = None
        s = self.surface
        if view is not None and isValid(view) and isValid(s):
            s.stack.removeWidget(view)
            view.deleteLater()
            s.stack.setCurrentWidget(s.reader if s.mode == 'read' else s.editor)
            s.toolbar_host.setVisible(s.mode != 'read')
            s.update_controls()

    def apply_candidate(self, mode, text):
        panel = self.panel
        if panel is None:
            raise ValueError('這次文字協作已關閉。')
        self.restore_projection()
        applied = panel.target.apply(mode, text)
        if applied and panel.target.kind == 'draft':
            self.surface.draft_composer.instruction.clear()
        return applied

    def _closed(self, panel):
        if self.panel is panel:
            self.restore_projection()
            self.panel = None
            self._suppressed = self.surface.selection()
            self.surface.text_assist_panel = None
            if hasattr(self.surface, 'draft_composer'):
                self.surface.draft_composer.refresh()

    def dismiss(self):
        self.timer.stop()
        panel = self.panel
        if panel is not None and isValid(panel):
            panel.close()
        self.restore_projection()
        self.panel = None
        self._suppressed = self.surface.selection()
        if hasattr(self.surface, 'draft_composer'):
            self.surface.draft_composer.refresh()

    def reposition(self, *args):
        panel = self.panel
        if panel is None or not isValid(panel):
            return
        s = self.surface
        editor = self.projection or s.editor
        if not self._allowed():
            panel.hide()
            return
        viewport = editor.viewport()
        origin = viewport.mapTo(s, QPoint())
        bounds = QRect(origin, viewport.size()).adjusted(8, 8, -8, -8)
        if bounds.width() < 220 or bounds.height() < 48:
            panel.hide()
            return
        width = panel.preferred_width(bounds.width())
        height = min(panel.preferred_height(width), bounds.height())
        if self.projection is not None:
            self.projection.reserve_controls(height)
            frame = self.projection.review_rect().translated(origin)
            if not frame.intersects(bounds):
                panel.hide()
                return
            x = max(bounds.left(), min(frame.center().x()-width//2, bounds.right()-width+1))
            y = max(bounds.top(), min(frame.top()-height-8, bounds.bottom()-height+1))
            panel.setGeometry(x, y, width, height)
            panel.show()
            panel.raise_()
            return
        target = panel.target
        text = s.page.buffer.snapshot().text
        first = QTextCursor(editor.document())
        last = QTextCursor(editor.document())
        first.setPosition(utf16_position(text, min(target.start, len(text))))
        last.setPosition(utf16_position(text, min(max(target.start, target.end-1), len(text))))
        first_rect = editor.cursorRect(first).translated(origin)
        last_rect = editor.cursorRect(last).translated(origin)
        selection_band = first_rect.united(last_rect)
        if not selection_band.intersects(bounds):
            panel.hide()
            return
        x = max(bounds.left(), min(first_rect.left(), bounds.right()-width+1))
        below = last_rect.bottom()+10
        above = first_rect.top()-height-10
        y = below if below+height <= bounds.bottom()+1 else above
        y = max(bounds.top(), min(y, bounds.bottom()-height+1))
        panel.setGeometry(x, y, width, height)
        if not panel.isVisible():
            panel.show()
        panel.raise_()

    def model_changed(self):
        panel = self.panel
        if panel is None or not isValid(panel):
            return
        selection = (panel.target.snapshot, panel.target.start, panel.target.end)
        kind = panel.target.kind
        instruction = panel.instruction.toPlainText()
        self.dismiss()
        if kind == 'draft':
            self.surface.draft_composer.instruction.setPlainText(instruction)
            self.surface.draft_composer.instruction.setFocus()
            return
        from .markdown_text_assist import open_text_assist
        replacement = open_text_assist(self.surface, *selection, kind=kind)
        if replacement is not None:
            replacement.instruction.setPlainText(instruction)


def add_model_menu(menu, surface):
    from .local_ai.service import for_workspace
    from .markdown_models import markdown_model_id, set_markdown_model
    service = for_workspace(surface.page.file_title.workspace)
    choices = menu.addMenu('文字協作模型')
    try:
        selected = markdown_model_id(surface, service)
    except ValueError as error:
        selected = ''
        choices.addAction(str(error)).setEnabled(False)
    if not service.models:
        choices.addAction('尚無本機模型').setEnabled(False)
        return
    def choose(identity):
        try:
            set_markdown_model(surface, service, identity)
        except (ValueError, OSError) as error:
            surface.report(str(error))
    for model in service.models:
        action = choices.addAction(model['name'], lambda checked=False, identity=model['id']: choose(identity))
        action.setCheckable(True)
        action.setChecked(model['id'] == selected)
