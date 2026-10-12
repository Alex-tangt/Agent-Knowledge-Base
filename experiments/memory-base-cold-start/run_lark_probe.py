"""One authorized Lark-only bytecode preparation, then the original native gate."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import zipfile

from memory_base import deploy

ROOT = Path(r'C:\Users\Tan\.mb86\r4')
HOME = ROOT / 'd'
ASSET = ROOT / 'cold-start-91'
HERE = Path(__file__).resolve().parent

IDENTITY = '''import hashlib,importlib.metadata as m,json,os,sys,sysconfig
from pathlib import Path
import memory_base
site=Path(sysconfig.get_path('purelib')).resolve()
d=m.distribution('lark-oapi');root=Path(d.locate_file('lark_oapi')).resolve()
counts={'py':0,'pyc':0}
for _,_,names in os.walk(root):
 for suffix in counts: counts[suffix]+=sum(n.endswith('.'+suffix) for n in names)
print(json.dumps({'python':sys.executable,'site':str(site),'memory_origin':memory_base.__file__,
 'lark_origin':str(root),'lark_version':d.version,'lark_record_sha256':hashlib.sha256(d.read_text('RECORD').encode()).hexdigest(),
 'counts':counts,'versions':{name:m.version(name) for name in %s}}))
''' % repr(list(deploy.PINS))
COMPILE = '''import compileall,faulthandler,json,os,sys,sysconfig,time
from pathlib import Path
root=Path(sys.argv[1]).resolve();site=Path(sysconfig.get_path('purelib')).resolve()
assert root==site/'lark_oapi' and root.is_dir()
assert sys.pycache_prefix is None
faulthandler.dump_traceback_later(280)
started=time.perf_counter()
success=compileall.compile_dir(str(root),quiet=2,force=False,optimize=0,workers=1)
faulthandler.cancel_dump_traceback_later()
print(json.dumps({'compiled_only':str(root),'force':False,'optimize':0,'workers':1,'success':success,'seconds':time.perf_counter()-started}))
raise SystemExit(0 if success else 1)
'''


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    if ASSET.exists():
        raise SystemExit('This bounded probe has already created its evidence directory')
    config = deploy.load_config(HOME)
    records = deploy._records(HOME)
    data_entries = list((HOME / 'data').rglob('*')) if (HOME / 'data').exists() else []
    busy = {name: deploy._port_busy(port) for name, port in config['ports'].items()}
    command = "$p = Get-CimInstance Win32_Process -Filter \"Name='python.exe' OR Name='memory-base.exe'\" | Where-Object { $_.CommandLine -and $_.CommandLine.Contains('" + str(HOME) + "') }; @($p | ForEach-Object { $_.ProcessId }) | ConvertTo-Json -Compress"
    nodes = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', command], capture_output=True, text=True, check=True)
    process_ids = json.loads(nodes.stdout or '[]')
    precondition = {'home': str(HOME), 'owned_records_empty': not records, 'data_entries': [str(path.relative_to(HOME)) for path in data_entries], 'ports_busy': busy, 'home_process_ids': process_ids}
    assert not records and not data_entries and not any(busy.values()) and not process_ids, precondition
    python = Path(config['python']).resolve()
    assert python == HOME / 'runtime/Scripts/python.exe'
    ASSET.mkdir()
    report = {'precondition': precondition, 'script_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), 'commands': [], 'overall': 'RUNNING'}
    protected = [ROOT/'results.json', ROOT/'native.json', ROOT/'normal-native/native.json', ROOT/'normal-native/result.json']
    report['protected_report_hashes_before'] = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in protected}

    def save():
        (ASSET/'probe.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')

    env = {key: value for key, value in deploy.clean_env().items() if not key.upper().startswith('MEMORY_') and key.upper() not in {'PYTHONPATH','PYTHONHOME','PYTHONDONTWRITEBYTECODE','PYTHONPYCACHEPREFIX'}}
    env['PIP_DISABLE_PIP_VERSION_CHECK'] = '1'

    def run(args, stage, timeout):
        log = ASSET/(stage+'.log')
        command = list(map(str,args))
        entry = {'stage':stage,'command':command,'cwd':os.environ['TEMP'],'timeout_seconds':timeout,'log':str(log)}
        report['commands'].append(entry)
        print(json.dumps({'stage':stage,'log':str(log)}),flush=True)
        started = time.perf_counter()
        with log.open('xb') as output:
            process = subprocess.Popen(command,cwd=os.environ['TEMP'],env=env,stdout=output,stderr=subprocess.STDOUT)
            entry['pid'] = process.pid
            save()
            try:
                entry['exit_code'] = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                process.kill();process.wait();entry['exit_code'] = process.returncode;entry['timeout'] = True
        entry['wall_seconds'] = time.perf_counter()-started
        save()
        return entry,log.read_text(encoding='utf-8',errors='replace')

    try:
        entry,text = run([python,'-B','-c',IDENTITY],'identity-before',90)
        assert entry['exit_code']==0
        before = json.loads(text)
        assert Path(before['site'])==HOME/'runtime/Lib/site-packages'
        assert Path(before['memory_origin'])==Path(before['site'])/'memory_base/__init__.py'
        assert Path(before['lark_origin'])==Path(before['site'])/'lark_oapi'
        assert before['versions']==deploy.PINS
        wheel = ROOT/'wheels/agent_memory_base-0.1.0-py3-none-any.whl'
        report['wheel_sha256'] = hashlib.sha256(wheel.read_bytes()).hexdigest()
        with zipfile.ZipFile(wheel) as archive:
            assert all((Path(before['site'])/name).read_bytes()==archive.read(name) for name in archive.namelist() if name.startswith('memory_base/') and not name.endswith('/'))
        report['identity_before'] = before
        entry,text = run([python,'-c',COMPILE,before['lark_origin']],'lark-only-compile',300)
        assert entry['exit_code']==0,'Bounded Lark-only compile failed; do not extend this probe'
        report['compile'] = json.loads(text)
        entry,text = run([python,'-B','-c',IDENTITY],'identity-after',90)
        assert entry['exit_code']==0
        after = json.loads(text);report['identity_after'] = after
        assert before['lark_record_sha256']==after['lark_record_sha256']
        assert after['counts']['pyc']==after['counts']['py']
        assert not deploy._records(HOME) and not any(deploy._port_busy(port) for port in config['ports'].values())
        entry,text = run([python,HERE/'native_probe_91.py','--home',HOME,'--store',ASSET],'standard-native',1100)
        report['native'] = json.loads((ASSET/'native.json').read_text(encoding='utf-8'))
        report['overall'] = 'PASS' if entry['exit_code']==0 and report['native']['overall']=='PASS' else 'FAIL'
    except Exception as error:
        report['overall']='FAIL'
        report['failure']={'type':type(error).__name__,'message':str(error)}
        raise
    finally:
        report['protected_report_hashes_after']={str(path):hashlib.sha256(path.read_bytes()).hexdigest() for path in protected}
        report['protected_reports_unchanged']=report['protected_report_hashes_before']==report['protected_report_hashes_after']
        save()
        print(json.dumps({'overall':report['overall'],'asset':str(ASSET),'protected_reports_unchanged':report['protected_reports_unchanged']}),flush=True)
    if report['overall']!='PASS':raise SystemExit(1)


if __name__=='__main__':
    main()
