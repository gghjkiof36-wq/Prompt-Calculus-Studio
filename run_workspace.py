"""Explicit isolated entry for the local document workspace; no default data."""
import argparse
import os
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--check', action='store_true', help='Check this launcher without writing its review files.')
    args = parser.parse_args()
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
    from PySide6.QtGui import QFont
    app = QApplication(sys.argv)
    app.setApplicationName('Prompt Calculus Studio')
    app.setOrganizationName('Local Tools')
    app.setFont(QFont('Microsoft JhengHei UI', 10))
    args.data.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(args.data/'workspace.lock'))
    lock.setStaleLockTime(0)
    if not lock.tryLock(100):
        QMessageBox.information(None, 'Prompt Calculus Studio', '這個分頁檢視資料夾已在另一個視窗開啟。')
        return 0
    from prompt_calculus_studio.workspace_shell import WorkspaceShell
    window = WorkspaceShell(args.root, args.data)
    def error(kind, value, traceback_):
        import traceback
        with (args.data/'error.log').open('a', encoding='utf-8') as stream:
            traceback.print_exception(kind, value, traceback_, file=stream)
        QMessageBox.warning(window, '操作未完成', str(value))
    sys.excepthook = error
    window.show()
    try:
        return app.exec()
    finally:
        lock.unlock()


if __name__ == '__main__':
    raise SystemExit(main())
