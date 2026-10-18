"""Apply a narrow website update to an existing Docker Compose deployment."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess


def command(*args: str, capture: bool = False, env=None):
    return subprocess.run(args, check=True, text=True, capture_output=capture, env=env).stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('/opt/procurement-project'))
    parser.add_argument('--validate-only', action='store_true')
    parser.add_argument('--apply', action='store_true', help='Without this flag, inspect only')
    args = parser.parse_args()
    package = Path(__file__).resolve().parents[1]
    manifest = json.loads((package / 'site-update-manifest.json').read_text('utf-8'))
    for name, digest in manifest['files'].items():
        relative = Path(name)
        allowed = name.startswith('demo/procurement/frontend/') or name == 'demo/procurement/backend/app/exports.py'
        if not allowed or relative.is_absolute() or '..' in relative.parts:
            raise RuntimeError('Unexpected payload path')
        source = package / 'payload' / relative
        if source.is_symlink() or hashlib.sha256(source.read_bytes()).hexdigest() != digest:
            raise RuntimeError('Payload hash mismatch: ' + name)
    if args.validate_only:
        print(json.dumps({'valid': True, 'files': len(manifest['files']), 'contains_database_or_credentials': False}))
        return
    root = args.root.resolve(strict=True)
    candidates = command('docker', 'ps', '-q', '--filter', 'label=com.docker.compose.service=api', capture=True).split()
    matches = []
    for identifier in candidates:
        info = json.loads(command('docker', 'inspect', identifier, capture=True))[0]
        labels = info['Config']['Labels']
        directory = Path(labels.get('com.docker.compose.project.working_dir', '/')).resolve()
        if directory in {root, root / 'demo/procurement'}:
            matches.append((identifier, labels))
    if len(matches) != 1:
        raise RuntimeError('Cannot uniquely identify this project API container; no files changed')
    identifier, labels = matches[0]
    files = [Path(value).resolve() for value in labels['com.docker.compose.project.config_files'].split(',')]
    if not files or any(not value.is_relative_to(root) or not value.is_file() for value in files):
        raise RuntimeError('Compose configuration is outside the selected project; no files changed')
    compose = ['docker', 'compose', '--project-name', labels['com.docker.compose.project']]
    environment = os.environ.copy()
    env_file = root / '.env'
    if env_file.is_file():
        compose.extend(['--env-file', str(env_file)])
        for line in env_file.read_text('utf-8-sig').splitlines():
            if line.strip() and not line.lstrip().startswith('#') and '=' in line:
                environment.pop(line.partition('=')[0].strip(), None)
    for value in files:
        compose.extend(['-f', str(value)])

    def idle():
        code = "import sqlite3; c=sqlite3.connect('file:/app/data/entity_mining.db?mode=ro',uri=True); print(c.execute(\"SELECT COUNT(*) FROM jobs WHERE status IN ('queued','processing')\").fetchone()[0]); c.close()"
        if int(command('docker', 'exec', identifier, 'python', '-c', code, capture=True).strip()):
            raise RuntimeError('An extraction task is active; no container update permitted')

    idle()
    print(json.dumps({'root': str(root), 'project': labels['com.docker.compose.project'],
                      'files': len(manifest['files']), 'apply': args.apply, 'data_and_configuration': 'preserved'}))
    if not args.apply:
        return
    backup = root / '.backups' / ('site-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    backup.mkdir(parents=True, exist_ok=False)
    # The payload excludes .env, model settings, databases, uploads and extraction caches.
    for name in manifest['files']:
        target = root / name
        if not target.resolve().is_relative_to(root) or target.is_symlink():
            raise RuntimeError('Target escapes the project')
        if target.is_file():
            saved = backup / name; saved.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(target, saved)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(package / 'payload' / name, target)
    print('Source backup:', backup)
    command(*compose, 'build', 'api', 'web', env=environment)
    idle()
    command(*compose, 'up', '-d', '--no-build', '--wait', '--wait-timeout', '240', 'api', 'web', env=environment)
    print('Containers updated. Verify public HTTPS and the selected full dataset before accepting the release.')


if __name__ == '__main__':
    main()
