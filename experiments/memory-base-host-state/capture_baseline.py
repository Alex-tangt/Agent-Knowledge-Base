"""Preserve the real failure and source hashes, without reading actual secrets."""
from pathlib import Path
import hashlib
import json
import subprocess

HERE=Path(__file__).parent
ASSETS=Path(r'D:\python_work\work2026-4\memory-base-bubble-bridge-assets\20261012')
ACTUAL=Path(r'D:\python_work\work2026-10\Agent-bubble')
KB92=Path(r'C:\Users\Tan\.codex\worktrees\memory-bootstrap-fix\Agent-Knowledge-Base\experiments\memory-base-note-intent')
FILE='scripts/local-backend.mjs'


def fingerprint(data):
    return {'bytes':len(data),'raw_sha256':hashlib.sha256(data).hexdigest(),'crlf_to_lf_sha256':hashlib.sha256(data.replace(b'\r\n',b'\n')).hexdigest()}


def main():
    output=HERE/'baseline.json'
    if output.exists():raise SystemExit('Baseline already captured; refusing overwrite')
    report={'baseline_commit':'0a86cb1013a481aa91b4b97b826d62a4a8456fb8','files':{},'original_failure':{}}
    for name,root in [('actual',ACTUAL),('candidate',ASSETS/'candidate93')]:
        report['files'][name]={'root':str(root),'path':FILE,**fingerprint((root/FILE).read_bytes())}
    report['files']['git_blob']=fingerprint(subprocess.check_output(['git','-C',str(ASSETS/'candidate93'),'show','HEAD:'+FILE]))
    failure_files={'first_host_failure.json':KB92/'first_host_failure.json',
      'launcher-original.log':ASSETS/'candidate92/.tools/host-test-w6CFiU/host/logs/launcher.log'}
    original=HERE/'original-fail';original.mkdir()
    for name,path in failure_files.items():
        data=path.read_bytes();(original/name).write_bytes(data)
        report['original_failure'][name]={'source':str(path),**fingerprint(data)}
    output.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'baseline':report['baseline_commit'],'actual_raw_sha256':report['files']['actual']['raw_sha256'],'failure_log_sha256':report['original_failure']['launcher-original.log']['raw_sha256']}))


if __name__=='__main__':main()
