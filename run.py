"""Double-click Start.cmd; data lives beside this launcher unless overridden."""
import os
import sys
from pathlib import Path

ROOT=Path(sys.executable).resolve().parent if getattr(sys,"frozen",False) else Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/"vendor"))

def main():
    from PySide6.QtCore import QLockFile
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication, QMessageBox
    from prompt_studio.window import Window
    app=QApplication(sys.argv)
    app.setApplicationName("Prompt Studio"); app.setOrganizationName("Local Tools")
    from prompt_studio import __file__ as package_file
    app.setWindowIcon(QIcon(str(Path(package_file).parent/"assets"/"studio.ico")))
    data=Path(os.environ.get("PROMPT_STUDIO_DATA",str(ROOT/"data"))).resolve(); data.mkdir(parents=True,exist_ok=True)
    if any(flag in sys.argv for flag in ("--menu-smoke-test","--frame-smoke-test")) and (data/"studio.sqlite3").exists():
        raise RuntimeError("Diagnostics require a new, empty PROMPT_STUDIO_DATA directory.")
    lock=QLockFile(str(data/"studio.lock")); lock.setStaleLockTime(0)
    if not lock.tryLock(100):
        QMessageBox.information(None,"Prompt Studio","這個資料庫已在另一個 Prompt Studio 視窗開啟。")
        return 0
    try:
        window=Window(data)
        def report_error(kind,exception,traceback_object):
            import traceback
            (data/"error.log").write_text("".join(traceback.format_exception(kind,exception,traceback_object)),encoding="utf-8")
            window.error("操作發生問題，已將詳細資訊保存到 data/error.log。\n"+str(exception))
        sys.excepthook=report_error
        available=app.primaryScreen().availableGeometry()
        window.resize(min(1440,max(960,available.width()-60)),min(900,max(620,available.height()-60)))
        window.show()
        if "--frame-smoke-test" in sys.argv:
            from prompt_studio.frame_check import start
            start(window)
        if "--menu-smoke-test" in sys.argv:
            from prompt_studio.menu_check import start
            start(window)
        if "--smoke-test" in sys.argv:
            from PySide6.QtCore import QTimer
            def finish_test():
                (data/"smoke-result.json").write_text(__import__("json").dumps(dict(ok=True,qt=__import__("PySide6").__version__,native_material=window.native_material,items=len(window.state["items"])),indent=2),encoding="utf-8")
                window.close()
            QTimer.singleShot(1200,finish_test)
        return app.exec()
    except Exception as exc:
        import traceback
        (data/"error.log").write_text(traceback.format_exc(),encoding="utf-8")
        QMessageBox.critical(None,"Prompt Studio 無法啟動",str(exc)+"\n詳細資訊已寫入 data/error.log")
        return 1
    finally:
        lock.unlock()

if __name__=="__main__":
    raise SystemExit(main())
