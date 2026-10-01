"""Double-click Start.cmd; data lives beside this launcher unless overridden."""
import os
import sys
from pathlib import Path

ROOT=Path(sys.executable).resolve().parent if getattr(sys,"frozen",False) else Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/"vendor"))

def main():
    os.environ.setdefault('PROMPT_STUDIO_V08','1')
    if any(flag in sys.argv for flag in ('--v08-alpha','--v08-smoke-test','--v081-alpha','--v081-smoke-test','--v082-alpha','--v083-alpha','--v084-alpha','--v0.8.4-alpha','--v0.8.5-alpha')):os.environ['PROMPT_STUDIO_V08']='1'
    from PySide6.QtCore import QLockFile
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication, QMessageBox
    from prompt_studio.window import Window
    app=QApplication(sys.argv)
    app.setApplicationName("Prompt Calculus Studio"); app.setOrganizationName("Local Tools")
    from prompt_studio import __file__ as package_file
    app.setWindowIcon(QIcon(str(Path(package_file).parent/"assets"/"studio.ico")))
    data=Path(os.environ.get("PROMPT_STUDIO_DATA",str(ROOT/"data"))).resolve(); data.mkdir(parents=True,exist_ok=True)
    if any(flag in sys.argv for flag in ("--menu-smoke-test","--frame-smoke-test","--export-smoke-test","--canvas-smoke-test","--img2img-smoke-test","--interface-smoke-test","--repair-smoke-test","--v08-smoke-test","--v081-smoke-test")) and (data/"studio.sqlite3").exists():
        raise RuntimeError("Diagnostics require a new, empty PROMPT_STUDIO_DATA directory.")
    lock=QLockFile(str(data/"studio.lock")); lock.setStaleLockTime(0)
    if not lock.tryLock(100):
        QMessageBox.information(None,"Prompt Calculus Studio","這個資料庫已在另一個 Prompt Calculus Studio 視窗開啟。")
        return 0
    # Native crashes bypass sys.excepthook. Keep stack traces with this data
    # directory, without dumping prompts or changing the user's running process.
    import faulthandler
    fault_log=(data/'fault.log').open('a',encoding='utf-8')
    faulthandler.enable(fault_log,all_threads=True)
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
        if not any(arg.endswith('smoke-test') for arg in sys.argv): window.start_interface()
        if '--v08-smoke-test' in sys.argv:
            from prompt_studio.v08_check import start
            start(window)
        if '--v081-smoke-test' in sys.argv:
            from prompt_studio.v081_check import start
            start(window)
        if '--interface-smoke-test' in sys.argv:
            from prompt_studio.interface_check import start
            start(window)
        if '--repair-smoke-test' in sys.argv:
            from prompt_studio.repair_check import start
            start(window)
        if '--img2img-smoke-test' in sys.argv:
            from prompt_studio.generation_check import start
            start(window)
        if '--canvas-smoke-test' in sys.argv:
            from prompt_studio.canvas_check import start
            start(window)
        if '--export-smoke-test' in sys.argv:
            from prompt_studio.export_check import start
            start(window)
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
        QMessageBox.critical(None,"Prompt Calculus Studio 無法啟動",str(exc)+"\n詳細資訊已寫入 data/error.log")
        return 1
    finally:
        faulthandler.disable(); fault_log.close()
        lock.unlock()

if __name__=="__main__":
    raise SystemExit(main())
