"""Read-only four-file provenance. CRLF normalization is explicitly separate."""
import argparse
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

FILES = ['server/memory-base.ts', 'server/memory-tools.ts', 'server/agent.ts', 'server/tests/memory-base.test.ts']
ASSETS = Path(r'D:\python_work\work2026-4\memory-base-bubble-bridge-assets\20261012')
ROOTS = {'actual': Path(r'D:\python_work\work2026-10\Agent-bubble'), 'source': ASSETS / 'source', 'candidate': ASSETS / 'candidate'}


def hashes(data):
    return {'bytes': len(data), 'raw_sha256': hashlib.sha256(data).hexdigest(),
            'crlf_to_lf_sha256': hashlib.sha256(data.replace(b'\r\n', b'\n')).hexdigest()}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--phase', choices=['baseline', 'final'], required=True)
    args = parser.parse_args()
    output = Path(__file__).parent / (args.phase + '-hashes.json')
    if output.exists():
        raise SystemExit('Refusing to overwrite an existing hash manifest')
    data = {'captured_at_utc': datetime.now(timezone.utc).isoformat(), 'normalization': 'replace CRLF bytes with LF only', 'roots': {}}
    for name, root in ROOTS.items():
        entry = {'path': str(root), 'files': {path: hashes((root / path).read_bytes()) for path in FILES}}
        if name != 'actual':
            entry['commit'] = subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip()
            entry['git_blobs'] = {path: hashes(subprocess.check_output(['git', '-C', str(root), 'show', f'HEAD:{path}'])) for path in FILES}
        data['roots'][name] = entry
    if args.phase == 'final':
        baseline = json.loads((output.parent / 'baseline-hashes.json').read_text(encoding='utf-8'))
        data['actual_raw_unchanged'] = all(data['roots']['actual']['files'][path]['raw_sha256'] == baseline['roots']['actual']['files'][path]['raw_sha256'] for path in FILES)
        if not data['actual_raw_unchanged']:
            raise SystemExit('Actual Bubble source changed; inspect before delivery')
    output.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(str(output))


if __name__ == '__main__':
    main()
