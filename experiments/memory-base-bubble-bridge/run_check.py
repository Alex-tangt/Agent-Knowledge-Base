"""Capture an existing Bubble validation command without loading its .env."""
import argparse
import json
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(r'D:\python_work\work2026-4\memory-base-bubble-bridge-assets\20261012\candidate')


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser()
    parser.add_argument('label')
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    directory = Path(__file__).parent / 'commands'
    directory.mkdir(exist_ok=True)
    output = directory / (args.label + '.txt')
    if output.exists() or not args.command:
        raise SystemExit('A new label and command are required')
    command = [shutil.which(args.command[0]) or args.command[0], *args.command[1:]]
    started = datetime.now(timezone.utc).isoformat()
    timer = time.monotonic()
    result = subprocess.run(command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding='utf-8', errors='replace')
    output.write_text(result.stdout, encoding='utf-8')
    report = {'command': args.command, 'cwd': str(ROOT), 'started_at_utc': started, 'seconds': round(time.monotonic()-timer, 3), 'exit_code': result.returncode, 'output': output.name}
    output.with_suffix('.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(report))
    print(result.stdout[-1800:])
    raise SystemExit(result.returncode)


if __name__ == '__main__':
    main()
