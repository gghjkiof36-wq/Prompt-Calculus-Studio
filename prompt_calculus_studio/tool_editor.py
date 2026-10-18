"""Editing adapters used by tools without constructing the obsolete list UI.

Media metadata restore and model actions retain their Canvas, draft document
and generation controller. The workspace's files own the visible editors;
the old library tree, list builder, splitters and their controls are not used
by this host and are never constructed here.
"""
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QWidget, QPlainTextEdit, QVBoxLayout
from .widgets import button
from .generation_panel import GenerationPanel
from .run_controls import RunControls
from .multi_canvas import MultiCanvas


def make_tool_editor(window):
    page = QWidget()
    window.builder_panel = QWidget(page)
    body = QVBoxLayout(window.builder_panel)
    window.final = QPlainTextEdit()
    window.final.textChanged.connect(window.final_edited)
    body.addWidget(window.final)
    window.generation_panel = GenerationPanel(window)
    window.generation_panel.setParent(page)
    window.run_controls = RunControls(window, copy_fallback=True)
    window.run_controls.setParent(page)
    window.copy_button = window.run_controls.run_button
    window.copy_timer = QTimer(window)
    window.copy_timer.setSingleShot(True)
    window.copy_timer.timeout.connect(window.reset_copy_feedback)
    window.latest_preview = button('查看最近生成', lambda: window.show_page(window.recent), 'Quiet')
    window.latest_preview.setParent(page)
    window.latest_preview.hide()
    window.canvas = MultiCanvas(window)
    return page
