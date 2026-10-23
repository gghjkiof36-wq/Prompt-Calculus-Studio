"""Start the local document workspace with portable data and content folders."""
import argparse
import os
import sys
from pathlib import Path


def open_initial_documents(window, *, left_path=None, right_path=None):
    """Keep restored sessions; show the bundled Markdown on first launch."""
    if window.layout_restored:
        return None
    left = None
    if left_path:
        left = window.open_path(left_path)
    elif not right_path:
        from prompt_calculus_studio.local_documents.paths import is_link
        welcome = window.root/'歡迎使用.md'
        if welcome.is_file() and not is_link(welcome):
            left = window.open_path(welcome)
            surface = getattr(left.widget, 'md_surface', None) if left else None
            if surface and not window._markdown_mode_chosen:
                surface.set_mode('read')
    if right_path:
        window.open_path(right_path, pane=window.ensure_split())
    return left


def start_smoke(window, left, args, app):
    from PySide6.QtCore import QTimer
    import json
    import time
    deadline = time.monotonic()+20
    surface = getattr(left.widget, 'md_surface', None) if left else None
    if surface:
        surface.set_mode('read')
    client = None
    if args.smoke_comfy_url:
        from prompt_calculus_studio.core import local_address
        client = window.ensure_utility().comfy
        client.connect_to(local_address(args.smoke_comfy_url))
    def finish_test():
        rich = surface.reader.rich if surface else None
        ready = not surface or (not surface.page.buffer.opening and
            (not surface.reader.advanced or rich is not None and rich.render_state == 'ready'))
        visible = not surface or surface.reader.isVisible()
        if rich:
            target = window.childAt(rich.mapTo(window, rich.rect().center()))
            visible = visible and rich.isVisible() and (target is rich or rich.isAncestorOf(target))
        ready = ready and visible and (client is None or client.connected)
        if not ready and time.monotonic() < deadline:
            QTimer.singleShot(100, window, finish_test)
            return
        window.grab().save(str(args.data/'smoke-reading.png'))
        (args.data/'smoke-result.json').write_text(json.dumps(dict(
            ok=ready, entry='WorkspaceShell', title=window.windowTitle(),
            opened_files=[str(tab.path) for tab in window.tabs_by_key.values() if tab.path],
            initial_document=str(left.path) if left else None,
            reading=rich.render_state if rich else 'native',
            reading_visible=visible,
            comfy_connected=client.connected if client else None,
            connection_label=window.connection.brand.text(),
            blocked_urls=rich.gate.blocked if rich else [],
            root=str(window.root), qt=__import__('PySide6').__version__)), encoding='utf-8')
        window.close()
        if not ready:
            app.exit(1)
    QTimer.singleShot(1200, window, finish_test)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, help='Content folder; defaults to files beside the program.')
    parser.add_argument('--data', type=Path, help='Application data; defaults to PROMPT_STUDIO_DATA or data beside the program.')
    parser.add_argument('--v0.8.9-alpha', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--v0.8.9-beta', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--v0.8.9-beta.2', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--v0.8.9-beta.3', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--open-left', type=Path, help='Open this file in the initial view.')
    parser.add_argument('--open-right', type=Path, help='Open this file beside the initial view.')
    parser.add_argument('--check', action='store_true', help='Check this launcher without writing its review files.')
    parser.add_argument('--smoke-test', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--smoke-comfy-url', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.smoke_comfy_url and not args.smoke_test:
        parser.error('--smoke-comfy-url requires --smoke-test')
    base = Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent
    args.data = (args.data or Path(os.environ.get('PROMPT_STUDIO_DATA', base/'data'))).absolute()
    if args.root is None:
        args.root = base/'files'
        if not args.check:
            args.root.mkdir(exist_ok=True)
    if args.smoke_test and args.data.exists() and any(args.data.iterdir()):
        raise RuntimeError('Diagnostics require a new, empty data directory.')
    os.environ['PROMPT_STUDIO_DATA'] = str(args.data.absolute())
    os.environ['PROMPT_STUDIO_V08'] = '1'
    if args.check:
        import json
        from prompt_calculus_studio.local_documents import index, io
        from PySide6 import QtCore
        entries = list(index.scan(args.root))
        for entry in entries:
            if entry.kind != 'markdown':
                io.load(args.root, entry.relative)
        print(json.dumps(dict(root=str(args.root), data=str(args.data), files=len(entries),
                              qt=QtCore.__version__, check='passed'), ensure_ascii=False))
        return 0
    from PySide6.QtWidgets import QApplication, QMessageBox
    from PySide6.QtCore import QLockFile
    from PySide6.QtGui import QFont, QIcon
    app = QApplication(sys.argv)
    app.setApplicationName('Prompt Calculus Studio')
    app.setOrganizationName('Local Tools')
    app.setFont(QFont('Microsoft JhengHei UI', 12))
    from prompt_calculus_studio import __file__ as package_file
    app.setWindowIcon(QIcon(str(Path(package_file).parent/'assets/studio.ico')))
    args.data.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(args.data/'workspace.lock'))
    lock.setStaleLockTime(0)
    if not lock.tryLock(100):
        QMessageBox.information(None, 'Prompt Calculus Studio', '這個分頁檢視資料夾已在另一個視窗開啟。')
        return 0
    from prompt_calculus_studio.workspace_startup import StartupProgress

    def error(kind, value, traceback_):
        import traceback
        with (args.data/'error.log').open('a', encoding='utf-8') as stream:
            traceback.print_exception(kind, value, traceback_, file=stream)
        QMessageBox.warning(window, '操作未完成', str(value))

    def ready(window, left):
        if args.smoke_test:
            start_smoke(window, left, args, app)

    from prompt_calculus_studio.workspace_shell import WorkspaceShell
    window = WorkspaceShell(args.root, args.data, defer_restore=True)
    startup = StartupProgress(window,
        lambda window: open_initial_documents(window, left_path=args.open_left, right_path=args.open_right),
        ready, lambda value: error(type(value), value, value.__traceback__))
    previous_hook = sys.excepthook
    sys.excepthook = error
    startup.start()
    try:
        return app.exec()
    finally:
        sys.excepthook = previous_hook
        lock.unlock()


if __name__ == '__main__':
    raise SystemExit(main())
