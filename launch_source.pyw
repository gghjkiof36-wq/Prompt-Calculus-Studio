"""Supervise source startup without a console, retaining a unique launch log."""
from datetime import datetime
from pathlib import Path
import subprocess
import sys
import traceback
from uuid import uuid4


def show_error(message):
    import ctypes
    ctypes.windll.user32.MessageBoxW(None, message, 'Prompt Calculus Studio', 0x10)


def launch(home=None, arguments=(), *, error_dialog=show_error):
    home = Path(home or Path(__file__).resolve().parent).resolve()
    log_path = None
    try:
        logs = home/'logs'
        logs.mkdir(parents=True, exist_ok=True)
        log_path = logs/('startup-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+uuid4().hex[:12]+'.log')
        with log_path.open('x', encoding='utf-8', buffering=1) as log:
            print('PCS source startup', file=log)
            print('Application:', home, file=log)
            try:
                python = home/'.venv/Scripts/python.exe'
                for path in (python, home/'run.py'):
                    if not path.is_file():
                        raise FileNotFoundError('找不到啟動所需檔案：'+str(path))
                # Resolve identity from this source package, never the preview install.
                from prompt_calculus_studio.releases import CURRENT
                command = [str(python), '-B', '-u', '-X', 'utf8', str(home/'run.py'),
                           CURRENT.argument, *map(str, arguments)]
                child = subprocess.Popen(command, cwd=home, stdin=subprocess.DEVNULL,
                                         stdout=log, stderr=subprocess.STDOUT,
                                         creationflags=subprocess.CREATE_NO_WINDOW)
                print('PCS process:', child.pid, file=log)
                status = child.wait()
                print('PCS exited with status:', status, file=log)
            except Exception:
                traceback.print_exc(file=log)
                status = 1
    except Exception as error:
        error_dialog('無法建立啟動記錄，PCS 尚未啟動。\n\n'+str(error)+'\n\n程式位置：'+str(home))
        return 1
    if status:
        error_dialog('PCS 未能正常啟動或已異常結束。\n\n啟動記錄：'+str(log_path))
    return status


if __name__ == '__main__':
    raise SystemExit(launch(arguments=sys.argv[1:]))
