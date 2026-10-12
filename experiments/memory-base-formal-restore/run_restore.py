"""Restore the fixed formal snapshot in a new home without touching formal data."""
from __future__ import annotations

import argparse
import asyncio
import base64
import csv
from datetime import datetime, timezone
import hashlib
import importlib.metadata as metadata
import importlib.util
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

ROOT = Path(r"C:\Users\Tan\.mb94")
HOME = ROOT / "a"
SNAPSHOT = Path(r"D:\python_work\work2026-4\memory-base-backups\formal-20261012T014904")
MANIFEST_HASH = "27dc14f772e61a351be62bf3c5dfb24397c27cec8894612065139a8677541c65"
R5 = Path(r"C:\Users\Tan\.mb86\r5")
PYTHON = R5 / "f/runtime/Scripts/python.exe"
WHEEL = R5 / "wheels/agent_memory_base-0.1.0-py3-none-any.whl"
WHEEL_HASH = "baa5877bc55a834f7abf16c833d9e869c0768624efdd63de9ad1ef8b2768bfde"
MODEL = Path(r"C:\Users\Tan\.cache\openviking\models\bge-small-zh-v1.5-f16.gguf")
FORMAL = Path(r"C:\Users\Tan\.agent-memory-base")
HERE = Path(__file__).resolve().parent
RECEIPTS = {
    "article": ("4b24c835877cfaa5666c2ba322431a663aab3fdd17c3196ac99274e94fcf591d", "a773d1d44eb19f2b3e061417b37aabfc728a1878bf50f0918eb994e1746b8095"),
    "video": ("0ada0a7776fcf4e646f0b3b54d69972334f01b04a7da6488c4044fa6f46dbd7b", "5f6ba7e2e05b205484c0034111404a363151d35d95516871c61fd6d727eeadc7"),
    "lesson": ("5a3c5791d6c61e7a5771ca7006d5d0498134c7076dcad2e3312177af4f47ce9c", "167b784b8f81f90927b6b3d656c0d3df8cec10f92d1601fb4c1901271f0ca6e0"),
}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def env():
    return {k:v for k,v in os.environ.items() if not k.upper().startswith("MEMORY_") and
            k.upper() not in {"PYTHONPATH", "PYTHONHOME", "PYTHONDONTWRITEBYTECODE", "PYTHONOPTIMIZE", "PYTHONPYCACHEPREFIX"}}


def inventory(root, *, exclude_manifest=False):
    files, dirs = [], []
    for p in sorted(root.rglob("*")):
        assert not p.is_symlink() and not (getattr(p.lstat(),"st_file_attributes",0)&0x400)
        name = p.relative_to(root).as_posix()
        if p.is_dir():
            dirs.append(name)
        elif not (exclude_manifest and name == "manifest.json"):
            raw = p.read_bytes()
            files.append({"path":name,"size":len(raw),"sha256":sha(raw)})
    # WindowsPath comparison case-folds. Manifest relative strings preserve case.
    return sorted(files,key=lambda item:item["path"]),sorted(dirs)


def snapshot_inventory():
    manifest_raw=(SNAPSHOT/"manifest.json").read_bytes()
    assert sha(manifest_raw)==MANIFEST_HASH
    manifest=json.loads(manifest_raw)
    files,dirs=inventory(SNAPSHOT,exclude_manifest=True)
    assert files==sorted(manifest["files"],key=lambda x:x["path"])
    assert dirs==sorted(manifest["directories"])
    assert len(files)==67
    return manifest, {"manifest_sha256":sha(manifest_raw),"files":len(files),"directories":len(dirs),
                      "inventory_sha256":sha(json.dumps([files,dirs],sort_keys=True).encode()),
                      "bytes":sum(f["size"] for f in files)}


def r5_immutable():
    items={}
    for label in ("d","f"):
        base=R5/label
        for name in ("deployment.json","openviking.conf"):
            items[str(base/name)]=sha((base/name).read_bytes())
        for entry in inventory(base/"data")[0]:
            items[str(base/"data"/entry["path"])]=entry["sha256"]
    for p in [*R5.glob("*.json"),*R5.glob("*.log")]:
        items[str(p)]=sha(p.read_bytes())
    site=R5/"f/runtime/Lib/site-packages"
    for directory in (site/"memory_base",next(site.glob("agent_memory_base-*.dist-info"))):
        for p in directory.rglob("*"):
            if p.is_file() and p.suffix != ".pyc":
                items[str(p)]=sha(p.read_bytes())
    items[str(WHEEL)]=sha(WHEEL.read_bytes())
    items[str(PYTHON.parent/"memory-base.exe")]=sha((PYTHON.parent/"memory-base.exe").read_bytes())
    return items


def cache_counts():
    site=R5/"f/runtime/Lib/site-packages"
    roots=[p for p in site.glob("volcenginesdk*") if p.is_dir() and not p.name.endswith(".dist-info")]
    return {"lark_pyc":len(list((site/"lark_oapi").rglob("*.pyc"))),
            "volc_sdk_pyc":sum(len(list(p.rglob("*.pyc"))) for p in roots)}


def request(url, method="GET", body=None, *, token=None, key=None):
    headers={"Content-Type":"application/json"}
    if token:headers["Authorization"]="Bearer "+token
    if key:headers["X-API-Key"]=key
    req=urllib.request.Request(url,data=json.dumps(body,ensure_ascii=False).encode() if body is not None else None,
                               method=method,headers=headers)
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req,timeout=120) as response:
            return response.status,json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code,json.loads(error.read())


async def mcp_probe(gateway, receipts):
    from mcp import ClientSession,StdioServerParameters
    from mcp.client.stdio import stdio_client
    params=StdioServerParameters(command=str(PYTHON),args=["-B","-m","memory_base.mcp_stdio","--home",str(HOME),
        "--profile","reader","--url",gateway],env=env(),cwd=os.environ["TEMP"])
    with (ROOT/"mcp.stderr.log").open("w",encoding="utf-8") as err:
        async with stdio_client(params,errlog=err) as (read,write):
            async with ClientSession(read,write) as client:
                await client.initialize()
                listing=await client.list_tools()
                names=sorted(t.name for t in listing.tools)
                assert names==["memory_add","memory_get","memory_list","memory_search"]
                assert all(t.inputSchema.get("additionalProperties") is False for t in listing.tools)
                for item in receipts:
                    response=await client.call_tool("memory_get",{"uri":item["uri"],"expected_revision":item["sha256"]})
                    assert not response.isError
                    payload=response.structuredContent or json.loads(next(c.text for c in response.content if c.type=="text"))
                    assert sha(payload["content"].encode())==item["sha256"]
                response=await client.call_tool("memory_list",{"scope":"captures","limit":20})
                assert not response.isError
                payload=response.structuredContent or json.loads(next(c.text for c in response.content if c.type=="text"))
                assert {i["uri"] for i in receipts}.issubset(i["uri"] for i in payload["items"])
                response=await client.call_tool("memory_search",{"query":"README","scope":"projects/agent-kb","limit":5})
                assert not response.isError
    return {"sdk_version":metadata.version("mcp"),"closed_tools":names,"original_get_checks":len(receipts),
            "reader_list":True,"project_search":True}


def live_probe():
    from memory_base import deploy
    from memory_base.credentials import get_secret
    config=deploy.load_config(HOME)
    assert Path(sys.executable).resolve()==PYTHON.resolve()
    gateway=f"http://127.0.0.1:{config['ports']['gateway']}"
    upstream=f"http://127.0.0.1:{config['ports']['upstream']}"
    reader=get_secret("host-reader",HOME)
    native_key=get_secret("upstream-key",HOME)
    keys=("root-key","upstream-key",*("host-"+p for p in deploy.PROFILES))
    distinct={name:bool(get_secret(name,HOME)) and get_secret(name,HOME)!=get_secret(name,FORMAL) for name in keys}
    assert all(distinct.values())
    pending=json.loads((HOME/"restore-state.json").read_text())["identity_reissue_pending"]
    assert pending is False
    records=deploy._records(HOME)
    assert set(records)==set(deploy.COMPONENTS)
    assert all(deploy.owned_component(n,HOME,config,r) for n,r in records.items())
    (ROOT/"processes-started.json").write_text(json.dumps(records,indent=2)+"\n",encoding="utf-8")
    receipts=[]
    for label,(capture_id,digest) in RECEIPTS.items():
        uri=f"viking://resources/captures/{capture_id}/content.md"
        code,payload=request(gateway+"/api/memory/get","POST",{"uri":uri,"expected_revision":digest},token=reader)
        assert code==200 and sha(payload["content"].encode())==digest==payload["revision"]
        source=SNAPSHOT/f"data/viking/personal/resources/captures/{capture_id}/capture.json"
        restored=HOME/f"data/viking/personal/resources/captures/{capture_id}/capture.json"
        assert restored.read_bytes()==source.read_bytes()
        # Metadata contains real private source URL/task provenance. Only record its hash.
        meta=json.loads(source.read_bytes())
        query=meta.get("title") or "记忆"
        code,searched=request(gateway+"/api/memory/search","POST",{"query":query,"scope":"captures","limit":20},token=reader)
        assert code==200 and any(i["uri"]==uri for i in searched["items"])
        receipts.append({"result":"PASS","uri":uri,"sha256":digest,"source_metadata_sha256":sha(source.read_bytes()),
                         "source_and_task_provenance_retained":True,"native_search_found":True})
    code,listed=request(gateway+"/api/memory/list","POST",{"scope":"captures","limit":20},token=reader)
    assert code==200 and {r["uri"] for r in receipts}.issubset(i["uri"] for i in listed["items"])
    count_before=listed["completeness"]["total"]
    code,_=request(gateway+"/api/memory/add","POST",{"request_id":"mb94-reader-denied","content":"synthetic denied probe","kind":"note"},token=reader)
    assert code==403
    code,after=request(gateway+"/api/memory/list","POST",{"scope":"captures","limit":20},token=reader)
    assert code==200 and after["completeness"]["total"]==count_before
    code,_=request(gateway+"/api/memory/get","POST",{"uri":receipts[0]["uri"]})
    assert code==401
    code,_=request(gateway+"/api/memory/get","POST",{"uri":receipts[0]["uri"],"scope":"projects"},token=reader)
    assert code==403
    code,_=request(gateway+"/api/memory/get","POST",{"uri":receipts[0]["uri"],"expected_revision":"0"*64},token=reader)
    assert code==409
    registry=json.loads((HOME/"sources.json").read_bytes())
    project=next(p for p in registry["projects"] if p["name"]=="agent-kb")
    real_root=Path(project["root"])
    project_receipts=[]
    for relative in ("README.md","docs/memory-base-deployment.md"):
        raw=(real_root/relative).read_bytes()
        uri="projects/agent-kb/"+relative
        code,payload=request(gateway+"/api/memory/get","POST",{"uri":uri},token=reader)
        assert code==200 and sha(payload["content"].encode())==sha(raw)==payload["revision"]
        project_receipts.append({"uri":uri,"sha256":sha(raw),"result":"PASS","current_registered_source":True})
    code,native_denied=request(upstream+"/api/v1/admin/accounts/mb94-unowned/users",key=native_key)
    assert code==403
    native_denied_status=code
    code,roster=request(upstream+"/api/v1/admin/accounts/personal/users",key=get_secret("root-key",HOME))
    assert code==200 and isinstance(roster["result"],list)
    expected=json.loads((SNAPSHOT/"data/viking/personal/_system/users.json").read_bytes())["users"]
    actual=sorted(({"user_id":v["user_id"],"role":v["role"]} for v in roster["result"]),key=lambda x:x["user_id"])
    previous=sorted(({"user_id":name,"role":v["role"]} for name,v in expected.items()),key=lambda x:x["user_id"])
    assert actual==previous
    current_users=json.loads((HOME/"data/viking/personal/_system/users.json").read_bytes())["users"]
    assert current_users["gateway"]["key"]!=expected["gateway"]["key"]
    result={"overall":"PASS","three_owned_services":True,"identity_reissue_pending":pending,
        "target_credentials_distinct_count":sum(distinct.values()),"target_credentials_distinct":True,
        "native_identity_roster_retained_sha256":sha(json.dumps(actual,sort_keys=True).encode()),
        "native_gateway_key_hash_rotated":True,"native_foreign_account_read_status":native_denied_status,
        "native_foreign_account_read_403":True,"receipts":receipts,"projects":project_receipts,
        "reader_write_403_no_new_capture":True,"anonymous_401":True,"scope_403":True,"stale_revision_409":True,
        "capture_count":count_before,"mcp":asyncio.run(mcp_probe(gateway,receipts))}
    (ROOT/"live-probe.json").write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"overall":"PASS","originals":len(receipts),"projects":len(project_receipts),
                      "native_foreign_account_read_403":True},ensure_ascii=False))


def runtime_audit():
    from memory_base import deploy
    assert Path(sys.executable).resolve()==PYTHON.resolve()
    site=R5/"f/runtime/Lib/site-packages"
    dist=metadata.distribution("agent-memory-base")
    assert Path(dist._path).resolve().is_relative_to(site.resolve())
    assert Path(deploy.__file__).resolve()==site/"memory_base/deploy.py"
    for name,version in deploy.PINS.items():
        installed=metadata.distribution(name)
        assert installed.version==version and Path(installed._path).resolve().is_relative_to(site.resolve())
    for name in ("memory_base","mcp","fastapi","openviking"):
        assert Path(importlib.util.find_spec(name).origin).resolve().is_relative_to(site.resolve())
    record_rows=0
    for name,digest,size in csv.reader(io.StringIO(dist.read_text("RECORD"))):
        record_rows+=1
        path=(site/name).resolve()
        assert path.is_relative_to((R5/"f/runtime").resolve()) and path.is_file()
        if not digest:
            assert not size and (name.endswith("/RECORD") or name.endswith(".pyc"))
        else:
            raw=path.read_bytes()
            encoded=base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).rstrip(b"=").decode()
            assert digest=="sha256="+encoded and size==str(len(raw))
    with zipfile.ZipFile(WHEEL) as z:
        files=[n for n in z.namelist() if n.startswith("memory_base/")]
        assert all((site/n).read_bytes()==z.read(n) for n in files)
    origin=json.loads(dist.read_text("direct_url.json"))
    assert origin["url"]==WHEEL.as_uri() and origin["archive_info"]["hashes"]["sha256"]==WHEEL_HASH
    result={"overall":"PASS","own_site":True,"pins":deploy.PINS,"product_payload_files":len(files),
        "product_payload_equals_frozen_wheel":True,"installed_record_rows_checked":record_rows,
        "installed_record_sha256":sha((Path(dist._path)/"RECORD").read_bytes()),
        "console_launcher_sha256":sha((PYTHON.parent/"memory-base.exe").read_bytes()),
        "direct_url_sha256":sha((Path(dist._path)/"direct_url.json").read_bytes()),
        "local_wheel_provenance":True,"wheel_sha256":WHEEL_HASH}
    (ROOT/"continuation-runtime-audit.json").write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(result,ensure_ascii=False))


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--live-probe",action="store_true")
    parser.add_argument("--runtime-audit",action="store_true")
    parser.add_argument("--continue-installed",action="store_true")
    args=parser.parse_args()
    if args.live_probe:
        live_probe()
        return
    if args.runtime_audit:
        runtime_audit()
        return
    if args.continue_installed:
        assert ROOT.is_dir() and (HOME/"deployment.json").is_file()
        assert not (ROOT/"result-continuation.json").exists(),"Never overwrite a continuation result"
        assert not any((HOME/name).exists() for name in ("data","sources.json","access.json","restore-state.json"))
    else:
        assert not ROOT.exists(),"Preserve all existing target assets; root must be new"
    assert sha(WHEEL.read_bytes())==WHEEL_HASH
    immutable_before=r5_immutable()
    manifest,original=snapshot_inventory()
    if not args.continue_installed:ROOT.mkdir()
    (ROOT/("r5-continuation-baseline.json" if args.continue_installed else "r5-initial-baseline.json")).write_text(
        json.dumps(immutable_before,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    report={"started_at":datetime.now(timezone.utc).isoformat(),"snapshot":original,"commands":[],"checks":{},
        "wheel_sha256":WHEEL_HASH,"product_frozen_sha":"45296a58fe2559e0b92f4af851c018b56148865a",
        "runtime_reuse":{"python":str(PYTHON),"new_dependency_stack":False,"own_site":True},
        "cache_before":cache_counts(),"original_r5_immutable_file_count":len(immutable_before),
        "continued_successful_install":args.continue_installed,"no_install_rerun":args.continue_installed,
        "boundary":"Actual 67-file formal snapshot recovery only. Shared R5 runtime; not a third new dependency stack or a new cold-install gate."}
    def save():
        (ROOT/("result-continuation.json" if args.continue_installed else "result.json")).write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    def run(command,stage):
        command=list(map(str,command))
        log=ROOT/(("continuation-" if args.continue_installed else "")+stage+".log")
        entry={"stage":stage,"command":command,"status":"running","log":str(log)}
        started=time.perf_counter()
        with log.open("wb") as out:
            process=subprocess.Popen(command,cwd=os.environ["TEMP"],env=env(),stdout=out,stderr=subprocess.STDOUT)
            entry["pid"]=process.pid
            report["commands"].append(entry)
            save()
            print(json.dumps({"stage":stage,"pid":process.pid,"log":str(log)}),flush=True)
            entry["exit_code"]=process.wait()
        entry["seconds"]=time.perf_counter()-started
        entry["status"]="completed"
        save()
        assert entry["exit_code"]==0,stage+" failed; preserve original log"
        return log.read_text(encoding="utf-8",errors="replace")
    cli=[PYTHON,"-B","-m","memory_base"]
    config=None
    try:
        report["checks"]["independent_snapshot_67_inventory"]=True
        if args.continue_installed:
            run([PYTHON,"-B",Path(__file__).resolve(),"--runtime-audit"],"runtime-audit")
            report["runtime_current_audit"]=json.loads((ROOT/"continuation-runtime-audit.json").read_text())
        else:
            run([*cli,"install","--home",HOME,"--runtime-python",PYTHON,"--wheel",WHEEL,"--model",MODEL],"install")
        assert r5_immutable()==immutable_before,"R5 non-cache bytes changed after install"
        config=json.loads((HOME/"deployment.json").read_text())
        assert Path(config["python"]).resolve()==PYTHON.resolve()
        sockets=[socket.socket() for _ in range(3)]
        try:
            for sock in sockets:sock.bind(("127.0.0.1",0))
            config["ports"]=dict(zip(("upstream","gateway","embed"),(s.getsockname()[1] for s in sockets)))
        finally:
            for sock in sockets:sock.close()
        (HOME/"deployment.json").write_text(json.dumps(config,indent=2)+"\n",encoding="utf-8")
        assert not (HOME/"data").exists()
        (HOME/"data").mkdir()
        assert not any((HOME/"data").iterdir())
        assert not any((HOME/name).exists() for name in ("sources.json","access.json","restore-state.json"))
        report["pre_restore"]={"empty_data":True,"sources_access_restore_absent":True,"ports":config["ports"],
            "ports_fixture":"Only unstarted target deployment config adjusted; not a CLI ports feature"}
        save()
        run([*cli,"restore","--home",HOME,"--backup",SNAPSHOT],"restore")
        for item in manifest["files"]:
            raw=(HOME/item["path"]).read_bytes()
            assert len(raw)==item["size"] and sha(raw)==item["sha256"]
        files,dirs=inventory(HOME/"data")
        assert [{**i,"path":"data/"+i["path"]} for i in files]==[i for i in manifest["files"] if i["path"].startswith("data/")]
        assert ["data",*("data/"+d for d in dirs)]==[d for d in manifest["directories"] if d=="data" or d.startswith("data/")]
        assert (HOME/"sources.json").read_bytes()==(SNAPSHOT/"sources.json").read_bytes()
        assert (HOME/"access.json").exists()==(SNAPSHOT/"access.json").exists()
        assert json.loads((HOME/"restore-state.json").read_text())["identity_reissue_pending"] is True
        report["checks"].update({"restore_cli_exact_67_pre_start":True,"directories_exact_pre_start":True,
            "sources_bytes_retained":True,"access_absence_retained":True,"identity_reissue_pending_before_start":True})
        save()
        run([PYTHON.parent/"memory-base.exe","start","--home",HOME],"start")
        report["checks"]["default_180_three_service_start"]=True
        run([PYTHON,"-B",Path(__file__).resolve(),"--live-probe"],"live-probe")
        report["live"]=json.loads((ROOT/"live-probe.json").read_text())
        report["checks"]["actual_original_source_permission_native_and_mcp"]=True
        report["overall"]="PASS"
    except Exception as error:
        report["overall"]="FAIL"
        report["failure"]={"type":type(error).__name__,"message":str(error)}
        raise
    finally:
        try:
            if config:
                records=HOME/"processes.json"
                if records.exists():
                    (ROOT/"processes-before-stop.json").write_bytes(records.read_bytes())
                run([PYTHON.parent/"memory-base.exe","stop","--home",HOME],"stop")
                busy={}
                for name,port in config["ports"].items():
                    with socket.socket() as sock:
                        sock.settimeout(0.3)
                        busy[name]=sock.connect_ex(("127.0.0.1",port))==0
                assert not any(busy.values())
                assert json.loads((HOME/"processes.json").read_text())=={}
                report["cleanup"]={"ports_busy":busy,"records_after":{}}
                report["checks"]["owned_manager_stop_and_ports_clear"]=True
            _,after=snapshot_inventory()
            assert after==original
            assert r5_immutable()==immutable_before
            report["checks"]["snapshot_original_byte_hash_unchanged"]=True
            report["checks"]["r5_current_install_baseline_non_cache_bytes_unchanged" if args.continue_installed
                             else "r5_original_non_cache_bytes_unchanged"]=True
            report["cache_after"]=cache_counts()
            formal_config=json.loads((FORMAL/"deployment.json").read_text())
            report["formal_health_readonly"]={name:request(f"http://127.0.0.1:{port}/health")[0] for name,port in formal_config["ports"].items()}
        except Exception as error:
            report["overall"]="FAIL"
            report["cleanup_failure"]={"type":type(error).__name__,"message":str(error)}
        save()
        print(json.dumps({"overall":report.get("overall"),"checks":report["checks"],"failure":report.get("failure"),
                          "cleanup_failure":report.get("cleanup_failure")},ensure_ascii=False,indent=2),flush=True)
    if report["overall"]!="PASS":raise SystemExit(1)


if __name__=="__main__":
    main()
