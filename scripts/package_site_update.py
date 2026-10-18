"""Build a narrowly scoped, credential-free website update for Workbench upload."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import tarfile
import io

ROOT = Path(__file__).resolve().parents[1]


def main():
    selected = [ROOT / 'demo/procurement/backend/app/exports.py']
    frontend = ROOT / 'demo/procurement/frontend'
    selected.extend(sorted((frontend / 'src').rglob('*')))
    selected.extend(sorted((frontend / 'dist').rglob('*')))
    selected.extend(frontend / name for name in ('package.json', 'package-lock.json', 'vite.config.js', 'index.html', 'Dockerfile', 'nginx.conf'))
    selected = [file for file in selected if file.is_file()]
    files = {file.relative_to(ROOT).as_posix(): file.read_bytes() for file in selected}
    manifest = {'scope': 'Website code and production assets only; no database, upload, cache or credential',
                'base_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                'files': {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}}
    output = ROOT / 'deliverables/deployment/competition-site-readiness-20261018.tar.gz'
    output.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(output, 'w:gz') as archive:
        def add(name, data):
            info = tarfile.TarInfo('competition-site-update/' + name)
            info.size = len(data); info.mode = 0o644
            archive.addfile(info, io.BytesIO(data))
        for name, data in files.items():
            add('payload/' + name, data)
        add('site-update-manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2).encode())
        add('scripts/apply_site_update.py', (ROOT / 'scripts/apply_site_update.py').read_bytes())
        add('scripts/finish_site_update.sh', (ROOT / 'scripts/finish_site_update.sh').read_bytes())
        add('scripts/snapshot_api_rootfs.py', (ROOT / 'scripts/snapshot_api_rootfs.py').read_bytes())
        add('README.md', (ROOT / 'docs/site-update-workbench.md').read_bytes())
    recovery = output.with_name('competition-site-recovery-20261018.tar.gz')
    with tarfile.open(recovery, 'w:gz') as archive:
        for name in ('apply_site_update.py', 'finish_site_update.sh', 'snapshot_api_rootfs.py'):
            data = (ROOT / 'scripts' / name).read_bytes()
            info = tarfile.TarInfo('competition-site-update/scripts/' + name)
            info.size = len(data); info.mode = 0o644
            archive.addfile(info, io.BytesIO(data))
        data = (ROOT / 'docs/site-update-workbench.md').read_bytes()
        info = tarfile.TarInfo('competition-site-update/README.md')
        info.size = len(data); info.mode = 0o644
        archive.addfile(info, io.BytesIO(data))
    print(json.dumps({'file': str(output), 'files': len(files), 'bytes': output.stat().st_size,
                      'sha256': hashlib.sha256(output.read_bytes()).hexdigest(),
                      'recovery': {'file': str(recovery), 'bytes': recovery.stat().st_size,
                                   'sha256': hashlib.sha256(recovery.read_bytes()).hexdigest()}}))


if __name__ == '__main__':
    main()
