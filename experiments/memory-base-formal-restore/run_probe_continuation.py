"""Finish the read-only proof on the already restored home; no install or restore."""
from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import subprocess
import time

import run_restore as previous


def main():
    root,home,python=previous.ROOT,previous.HOME,previous.PYTHON
    output=root/"probe-continuation-result.json"
    assert not output.exists(),"Preserve any existing probe result"
    _,snapshot=previous.snapshot_inventory()
    baseline=previous.r5_immutable()
    (root/"probe-continuation-r5-baseline.json").write_text(json.dumps(baseline,indent=2)+"\n",encoding="utf-8")
    config=json.loads((home/"deployment.json").read_text())
    assert json.loads((home/"restore-state.json").read_text())["identity_reissue_pending"] is False
    report={"snapshot":snapshot,"commands":[],"first_start":False,"install_or_restore_rerun":False,
            "cache_before":previous.cache_counts(),"current_r5_baseline_files":len(baseline)}
    def save():
        output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    def run(command,stage):
        command=list(map(str,command))
        log=root/("probe-continuation-"+stage+".log")
        entry={"stage":stage,"command":command,"status":"running","log":str(log)}
        started=time.perf_counter()
        with log.open("wb") as out:
            process=subprocess.Popen(command,cwd=os.environ["TEMP"],env=previous.env(),stdout=out,stderr=subprocess.STDOUT)
            entry["pid"]=process.pid;report["commands"].append(entry);save()
            print(json.dumps({"stage":stage,"pid":process.pid,"log":str(log)}),flush=True)
            entry["exit_code"]=process.wait()
        entry.update(seconds=time.perf_counter()-started,status="completed");save()
        assert entry["exit_code"]==0,stage+" failed; keep original log"
    try:
        run([python.parent/"memory-base.exe","start","--home",home],"start")
        run([python,"-B",Path(previous.__file__).resolve(),"--live-probe","--probe-prefix","probe-continuation-"],"live")
        report["live"]=json.loads((root/"probe-continuation-live.json").read_text())
        assert report["live"]["overall"]=="PASS"
        report["overall"]="PASS"
    except Exception as error:
        report["overall"]="FAIL";report["failure"]={"type":type(error).__name__,"message":str(error)}
        raise
    finally:
        try:
            (root/"probe-continuation-processes-before-stop.json").write_bytes((home/"processes.json").read_bytes())
            run([python.parent/"memory-base.exe","stop","--home",home],"stop")
            busy={}
            for name,port in config["ports"].items():
                with socket.socket() as sock:
                    sock.settimeout(0.3);busy[name]=sock.connect_ex(("127.0.0.1",port))==0
            assert not any(busy.values()) and json.loads((home/"processes.json").read_text())=={}
            _,after=previous.snapshot_inventory();assert after==snapshot
            assert previous.r5_immutable()==baseline
            formal=json.loads((previous.FORMAL/"deployment.json").read_text())
            report["cleanup"]={"ports_busy":busy,"records_after":{}}
            report["snapshot_unchanged"]=report["current_r5_non_cache_baseline_unchanged"]=True
            report["formal_health_readonly"]={n:previous.request(f"http://127.0.0.1:{p}/health")[0] for n,p in formal["ports"].items()}
            report["cache_after"]=previous.cache_counts()
        except Exception as error:
            report["overall"]="FAIL";report["cleanup_failure"]={"type":type(error).__name__,"message":str(error)}
        save();print(json.dumps({"overall":report.get("overall"),"failure":report.get("failure"),
                               "cleanup_failure":report.get("cleanup_failure")},indent=2),flush=True)
    if report["overall"]!="PASS":raise SystemExit(1)


if __name__=="__main__":
    main()
