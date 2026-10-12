"""Verify owned cleanup and scan deliverable metadata without exporting memory text."""
from __future__ import annotations

from datetime import datetime,timezone
import json
from pathlib import Path
import socket

import run_restore as probe
from memory_base import deploy
from memory_base.credentials import get_secret


def main():
    root=probe.ROOT
    final=json.loads((root/"probe-continuation-result.json").read_text())
    assert final["overall"]=="PASS" and final["live"]["overall"]=="PASS"
    for name in ("result.json","result-continuation.json"):
        assert json.loads((root/name).read_text())["overall"]=="FAIL"
    records=json.loads((root/"probe-continuation-processes-before-stop.json").read_text())
    owned={name:deploy.owned_process(record) for name,record in records.items()}
    assert not any(owned.values())
    config=deploy.load_config(probe.HOME)
    busy={}
    for name,port in config["ports"].items():
        with socket.socket() as sock:
            sock.settimeout(0.3);busy[name]=sock.connect_ex(("127.0.0.1",port))==0
    assert not any(busy.values()) and deploy._records(probe.HOME)=={}
    _,snapshot=probe.snapshot_inventory()
    assert snapshot==final["snapshot"]
    baseline=json.loads((root/"probe-continuation-r5-baseline.json").read_text())
    assert probe.r5_immutable()==baseline
    values=[]
    for home in (probe.HOME,probe.FORMAL,probe.R5/"d",probe.R5/"f"):
        for name in ("root-key","upstream-key",*("host-"+p for p in deploy.PROFILES)):
            value=get_secret(name,home)
            if value:values.append(value)
    private_values=[]
    for capture_id,_ in probe.RECEIPTS.values():
        directory=probe.SNAPSHOT/f"data/viking/personal/resources/captures/{capture_id}"
        private_values.append((directory/"content.md").read_text(encoding="utf-8"))
        meta=json.loads((directory/"capture.json").read_bytes())
        for key in ("source_url","title"):
            if isinstance(meta.get(key),str) and len(meta[key])>=8:private_values.append(meta[key])
    candidates=sorted([*root.glob("*.json"),*root.glob("*.log"),*(probe.HOME/"logs").glob("*.log")])
    for path in candidates:
        raw=path.read_bytes()
        assert not any(v.encode(enc) in raw for v in values for enc in ("utf-8","utf-16-le","utf-16-be")),path.name
        assert not any(v.encode("utf-8") in raw or json.dumps(v,ensure_ascii=False)[1:-1].encode() in raw or
                       json.dumps(v,ensure_ascii=True)[1:-1].encode() in raw for v in private_values),path.name
    for item in final["live"]["receipts"]:
        capture_id=item["uri"].split("/")[-2]
        path=probe.HOME/f"data/viking/personal/resources/captures/{capture_id}/content.md"
        assert probe.sha(path.read_bytes())==item["sha256"]
    for item in final["live"]["projects"]:
        registry=json.loads((probe.HOME/"sources.json").read_bytes())
        project=next(p for p in registry["projects"] if p["name"]=="agent-kb")
        path=Path(project["root"])/item["uri"].removeprefix("projects/agent-kb/")
        assert probe.sha(path.read_bytes())==item["sha256"]
    result={"overall":"PASS","audited_at":datetime.now(timezone.utc).isoformat(),"snapshot":snapshot,
        "owned_pid_birth_command_survivors":owned,"ports_busy":busy,"process_records":{},
        "current_r5_baseline_files_unchanged":len(baseline),"original_failures_retained":2,
        "original_memory_physical_hash_checks":3,"current_project_hash_checks":2,
        "scanned_credentials":len(values),"private_body_url_title_patterns":len(private_values),
        "scanned_asset_report_and_log_files":len(candidates),"credential_or_private_text_matches":0,
        "boundary":"Only metadata/log records are exportable; no snapshot data, memory bodies, source URL, credentials. Earlier R5 launcher/RECORD reinstallation and missing old baseline are explicitly retained as limitations."}
    (root/"delivery-audit.json").write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=="__main__":
    main()
