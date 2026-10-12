"""Export a frozen four-file patch and locally installed dependency evidence."""
import hashlib
import json
import subprocess
from pathlib import Path

from capture_hashes import ASSETS, FILES

BASELINE = 'a1f5426adbf7de8efb7877181717ee4385388f10'


def main():
    root, directory = ASSETS / 'candidate', Path(__file__).parent
    changed = subprocess.check_output(['git', '-C', str(root), 'diff', '--name-only', BASELINE, 'HEAD'], text=True).splitlines()
    if set(changed) != set(FILES) or subprocess.check_output(['git', '-C', str(root), 'status', '--porcelain'], text=True).strip():
        raise SystemExit('Expected only the four allowed files and a clean candidate')
    patch = subprocess.check_output(['git', '-C', str(root), 'diff', '--binary', '--full-index', BASELINE, 'HEAD', '--', *FILES])
    output = directory / 'bubble-four-tools.patch'
    if output.exists():
        raise SystemExit('Refusing to overwrite an exported patch')
    output.write_bytes(patch)
    dependencies = {}
    for name in ['@earendil-works/pi-ai', '@earendil-works/pi-coding-agent', 'typebox', 'zod', 'typescript', 'tsx']:
        dependencies[name] = json.loads((root / 'node_modules' / name / 'package.json').read_text(encoding='utf-8'))['version']
    commands = {path.stem: json.loads(path.read_text(encoding='utf-8')) for path in sorted((directory / 'commands').glob('*.json'))}
    data = {'baseline_commit': BASELINE,
            'candidate_commit': subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip(),
            'candidate_path': str(root), 'allowed_source_files': FILES,
            'patch': {'file': output.name, 'bytes': len(patch), 'raw_sha256': hashlib.sha256(patch).hexdigest(), 'source_text': 'Git LF-normalized blobs; compare raw working files separately with final-hashes.json'},
            'node': subprocess.check_output(['node', '--version'], text=True).strip(),
            'installed_dependencies': dependencies, 'dependencies_boundary': 'Existing actual Bubble node_modules junction; no installation or modification',
            'model_boundary': 'Real pi SDK; model is local synthetic OpenAI-compatible SSE, no external LLM',
            'gateway_boundary': 'Local HTTP protocol fixtures, not formal gateway or real OpenViking/project sources',
            'host_boundary': 'Existing host tests launch candidate dist only with separate test DATA_DIR, free loopback port, fake provider key and memory integration disabled; formal host untouched',
            'host_test_assets': [str(path) for path in sorted((root / '.tools').glob('host-test-*'))],
            'commands': commands}
    (directory / 'delivery.json').write_text(json.dumps(data, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print(json.dumps({'candidate_commit': data['candidate_commit'], 'patch_sha256': data['patch']['raw_sha256'], 'dependencies': dependencies}))


if __name__ == '__main__':
    main()
