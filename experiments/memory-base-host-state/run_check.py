"""Capture only existing, local candidate validation commands."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time

ROOT=Path(r'D:\python_work\work2026-4\memory-base-bubble-bridge-assets\20261012\candidate93')


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser=argparse.ArgumentParser();parser.add_argument('label');parser.add_argument('command',nargs=argparse.REMAINDER);args=parser.parse_args()
    directory=Path(__file__).parent/'commands';directory.mkdir(exist_ok=True)
    output=directory/(args.label+'.txt')
    if output.exists() or not args.command:raise SystemExit('New label and command required')
    command=[shutil.which(args.command[0]) or args.command[0],*args.command[1:]]
    started=time.perf_counter()
    with output.open('xb') as file:
        process=subprocess.Popen(command,cwd=ROOT,stdout=file,stderr=subprocess.STDOUT)
        print(json.dumps({'label':args.label,'pid':process.pid,'output':str(output)}),flush=True)
        code=process.wait()
    data=output.read_bytes()
    report={'command':args.command,'cwd':str(ROOT),'pid':process.pid,'exit_code':code,'wall_seconds':time.perf_counter()-started,'raw_output_sha256':hashlib.sha256(data).hexdigest()}
    output.with_suffix('.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report));print(data.decode('utf-8',errors='replace')[-2600:])
    raise SystemExit(code)


if __name__=='__main__':main()
