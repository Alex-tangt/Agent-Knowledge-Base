"""Freeze scripts-only artifacts and verify untouched actual/original evidence."""
from pathlib import Path
import hashlib
import json
import subprocess

from capture_baseline import ACTUAL, ASSETS, HERE, fingerprint

BASE='0a86cb1013a481aa91b4b97b826d62a4a8456fb8'
FROZEN='ba8b5b5c557fceee93c9e5738b43ecee7ec9b156'
FILES=['scripts/local-backend.mjs','scripts/host-state.mjs','scripts/tests/local-backend.test.mjs']


def main():
    candidate=ASSETS/'candidate93'
    if (HERE/'delivery.json').exists():raise SystemExit('Delivery already exported; refusing overwrite')
    assert subprocess.check_output(['git','-C',str(candidate),'rev-parse','HEAD'],text=True).strip()==FROZEN
    assert not subprocess.check_output(['git','-C',str(candidate),'status','--porcelain'],text=True).strip()
    assert set(subprocess.check_output(['git','-C',str(candidate),'diff','--name-only',BASE,FROZEN],text=True).splitlines())==set(FILES)
    baseline=json.loads((HERE/'baseline.json').read_text())
    actual=fingerprint((ACTUAL/'scripts/local-backend.mjs').read_bytes())
    assert actual['raw_sha256']==baseline['files']['actual']['raw_sha256']
    protected={name:hashlib.sha256(Path(entry['source']).read_bytes()).hexdigest()==entry['raw_sha256'] for name,entry in baseline['original_failure'].items()}
    assert all(protected.values())
    manifest={'baseline':BASE,'candidate_commit':FROZEN,'candidate_path':str(candidate),'files':{},'actual_launcher_raw_unchanged':True,
              'protected_original_fail_unchanged':protected,'actual_helper_exists_before_lead_application':(ACTUAL/'scripts/host-state.mjs').exists(),
              'original_fail_boundary':'Original native Windows EPERM from #92; never overwritten',
              'fault_boundary':'Actual candidate launcher/backend with explicit fs.renameSync fault injection at state publication; not a native lock reproduction',
              'readiness_timeout_ms':120000,'rename_policy':{'codes':['EPERM','EACCES','EBUSY'],'attempts':6,'delay_ms':40,'maximum_retry_pause_ms':200},
              'dependencies':'Read-only junction to existing actual node_modules; no install or external LLM','commands':{}}
    artifacts=HERE/'artifacts'
    for name in FILES:
        data=subprocess.check_output(['git','-C',str(candidate),'show',FROZEN+':'+name])
        path=artifacts/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
        manifest['files'][name]={'artifact':str(path.relative_to(HERE)),'git_blob':fingerprint(data),'candidate_working':fingerprint((candidate/name).read_bytes())}
        assert manifest['files'][name]['git_blob']['crlf_to_lf_sha256']==manifest['files'][name]['candidate_working']['crlf_to_lf_sha256']
    patch=subprocess.check_output(['git','-C',str(candidate),'diff','--binary','--full-index',BASE,FROZEN,'--',*FILES])
    (HERE/'host-state-three-files.patch').write_bytes(patch);manifest['patch']=fingerprint(patch)
    for path in (HERE/'commands').glob('*.json'):
        result=json.loads(path.read_text());output=path.with_suffix('.txt')
        assert hashlib.sha256(output.read_bytes()).hexdigest()==result['raw_output_sha256']
        manifest['commands'][path.stem]=result
    latest={}
    all_assets=[]
    for path in (candidate/'.tools').glob('state-test-*/fixture-result.json'):
        result=json.loads(path.read_text());all_assets.append(str(path.parent))
        mode=result['mode']
        if mode not in latest or path.stat().st_mtime_ns>latest[mode].stat().st_mtime_ns:latest[mode]=path
    output=HERE/'fault-green';output.mkdir()
    manifest['green_fixtures']={};manifest['all_state_fixture_assets']=all_assets
    for mode,path in latest.items():
        result=json.loads(path.read_text());assert not result['childStillAlive'] and not result['backendHealthy'] and not result['fallbackCleanup']
        assert result['state'] is None and result['lock'] is None
        folder=output/mode;folder.mkdir()
        hashes={}
        for name in ['fixture-result.json','rename-injection.jsonl','child-identity.json','launcher-direct.log']:
            source=path.parent/name
            if source.exists():
                data=source.read_bytes();(folder/name).write_bytes(data);hashes[name]=fingerprint(data)
        manifest['green_fixtures'][mode]={'asset':str(path.parent),'result':result,'files':hashes}
    assert set(latest)=={'transient','persistent','initial','restarting'}
    (HERE/'delivery.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'candidate_commit':FROZEN,'patch_sha256':manifest['patch']['raw_sha256'],'artifact_sha256':{name:entry['git_blob']['raw_sha256'] for name,entry in manifest['files'].items()}}))


if __name__=='__main__':main()
