"""0.83 bounded offline/offscreen checks. Does not operate a live service."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from verify_queue_preview import PATTERNS

ROOT=Path(__file__).resolve().parents[1]

def main():
    output=ROOT/'qa/083';output.mkdir(parents=True,exist_ok=True)
    env=dict(os.environ,QT_QPA_PLATFORM='offscreen',PYTHONIOENCODING='utf-8',
             PROMPT_STUDIO_DATA=str(output/'data'))
    rows=[]
    suites=[(p,[sys.executable,'-m','unittest','discover','-s','tests','-p',p]) for p in (*PATTERNS,'test_node_images.py')]
    suites.append(('javascript',['node','--test',*[f'tests/{name}.test.mjs' for name in
        ('native_queue','native_open','native_switch','native_seed','native_bootstrap','workflow_sync','source_receipt','node_images')]]))
    for name,command in suites:
        started=time.monotonic()
        result=subprocess.run(command,cwd=ROOT,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=90)
        (output/(name+'.log')).write_text(result.stdout.decode('utf-8',errors='replace'),encoding='utf-8')
        rows.append(dict(suite=name,returncode=result.returncode,seconds=round(time.monotonic()-started,2)))
        print(name,'PASS' if result.returncode==0 else 'FAIL',flush=True)
    git=['git','-c','safe.directory='+str(ROOT),'-C',str(ROOT)]
    head=subprocess.check_output([*git,'rev-parse','HEAD']).decode().strip()
    dirty=subprocess.check_output([*git,'status','--porcelain']).decode().strip()
    (output/'RESULTS.json').write_text(json.dumps(dict(commit=head,working_changes=dirty,scope='offline/offscreen only',results=rows),indent=2),encoding='utf-8')
    return int(any(row['returncode'] for row in rows))

if __name__=='__main__':raise SystemExit(main())
