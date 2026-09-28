"""Bounded offline/offscreen validation; no desktop automation or live services."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
PATTERNS=('test_work_queue.py','test_queue_ui.py','test_queue_metadata.py','test_snapshot_ui.py',
          'test_binding_submission.py','test_native_queue.py','test_comfy_integration.py',
          'test_maintenance_loading.py','test_maintenance_state.py','test_image_collections.py',
          'test_generation.py','test_maintenance_package.py','test_source_privacy.py')


def main():
    output=ROOT/'qa'/'queue-snapshots';output.mkdir(parents=True,exist_ok=True)
    env=dict(os.environ,QT_QPA_PLATFORM='offscreen',PROMPT_STUDIO_DATA=str(output/'isolated-data'))
    rows=[]
    for pattern in PATTERNS:
        started=time.monotonic()
        run=subprocess.run([sys.executable,'-m','unittest','discover','-s','tests','-p',pattern],cwd=ROOT,env=env,
                           stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=90)
        log=run.stdout.decode('utf-8',errors='replace');(output/(pattern+'.log')).write_text(log,encoding='utf-8')
        rows.append(dict(suite=pattern,returncode=run.returncode,seconds=round(time.monotonic()-started,2)))
        print(pattern, 'PASS' if run.returncode==0 else 'FAIL',flush=True)
    js=subprocess.run(['node','--test','tests/native_queue.test.mjs','tests/native_seed.test.mjs',
                       'tests/workflow_sync.test.mjs','tests/source_receipt.test.mjs'],cwd=ROOT,env=env,
                       stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=90)
    (output/'javascript.log').write_text(js.stdout.decode('utf-8',errors='replace'),encoding='utf-8')
    rows.append(dict(suite='javascript',returncode=js.returncode))
    head=subprocess.check_output(['git','-c','safe.directory='+str(ROOT),'-C',str(ROOT),'rev-parse','HEAD']).decode().strip()
    (output/'RESULTS.json').write_text(json.dumps(dict(commit=head,scope='offline/offscreen only',results=rows),indent=2),encoding='utf-8')
    return int(any(r['returncode'] for r in rows))


if __name__=='__main__':raise SystemExit(main())
