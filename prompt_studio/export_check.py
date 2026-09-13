"""Opt-in packaged-codec check, only allowed with an empty diagnostic database."""
import json
from pathlib import Path
from PySide6.QtCore import QTimer,Qt
from PySide6.QtGui import QImage,QImageWriter
from . import clean_export,clean_metadata

def start(window):
    def check():
        root=window.store.directory; result=dict(ok=False,formats=[bytes(v).decode() for v in QImageWriter.supportedImageFormats()])
        try:
            source=root/'test-master.png'; output=root/'test-export'; output.mkdir()
            image=QImage(64,32,QImage.Format.Format_ARGB32); image.fill(Qt.GlobalColor.blue)
            image.setText('prompt','test only'); image.setText('workflow','{"nodes":[]}')
            if not image.save(str(source),'PNG'): raise ValueError('Unable to write fixture')
            before=clean_export.sha256(source); rows=clean_export.scan([source]); runs=[]
            for fmt in ('png','jpeg','webp'):
                plan=clean_export.prepare(rows,dict(format=fmt,resize='percent',percent=50),str(output))
                run=clean_export.execute(plan); window.clean_export.records.save_run(run)
                for record in run['results']:
                    if record['status']!='Passed': raise ValueError(str(record))
                    clean_metadata.verify(record['target'],'all')
                runs.extend(run['results'])
            if before!=clean_export.sha256(source) or window.catalog.count('image')!=0: raise ValueError('Master/catalog changed')
            result.update(ok=True,master_unchanged=True,gallery_images=0,exports=runs)
        except Exception as exc: result['error']=str(exc)
        (root/'export-smoke-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        window.close()
    QTimer.singleShot(200,check)
