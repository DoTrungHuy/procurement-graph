"""Check publishable files for credentials, oversized artifacts and broken links.

Uses Git's file list when a repository exists, otherwise the project's simple
ignore rules. Credential contents are compared in memory and never printed.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]


def ignore_rules():
    return [line.strip() for line in (ROOT / '.gitignore').read_text('utf-8-sig').splitlines()
            if line.strip() and not line.lstrip().startswith('#')]


def matches(name, pattern):
    pattern = pattern.rstrip('/')
    if pattern.startswith('**/'):
        pattern = pattern[3:]
        return any(fnmatch.fnmatch(part, pattern) for part in name.split('/'))
    if '/' not in pattern:
        return any(fnmatch.fnmatch(part, pattern) for part in name.split('/'))
    return name == pattern or name.startswith(pattern + '/') or fnmatch.fnmatch(name, pattern)


def ignored(name, rules):
    result = False
    for rule in rules:
        negate = rule.startswith('!')
        if matches(name, rule[1:] if negate else rule):
            result = not negate
    return result


def safe_release_path(name: str) -> bool:
    """UTF-8 names are valid ZIP paths; reject traversal and Windows hazards."""
    return (bool(name) and not re.search(r'[\x00-\x1f\x7f<>:"\\|?*]',name)
            and all(part not in {'','.', '..'} and not part.endswith((' ','.')) for part in name.split('/')))


def candidates():
    if (ROOT / '.git').exists() and shutil.which('git'):
        command = ['git', 'ls-files', '--cached', '--others', '--exclude-standard', '-z']
        result = subprocess.run(command, cwd=ROOT, check=True, capture_output=True)
        return sorted(set(n for n in result.stdout.decode('utf-8').split('\0') if n)), 'git-ls-files'
    rules = ignore_rules()
    names = []
    for parent, directories, files in os.walk(ROOT, followlinks=False):
        directories[:] = [name for name in directories if name != '.git'
                           and not (path := Path(parent, name)).is_symlink()
                           and not ignored(path.relative_to(ROOT).as_posix(), rules)]
        for name in files:
            path = Path(parent, name)
            relative = path.relative_to(ROOT).as_posix()
            if not ignored(relative, rules):
                names.append(relative)
    return sorted(names), 'project-ignore-rules (Git not initialized)'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / '.cache/maintenance/publish-audit.json')
    args = parser.parse_args()
    names, selection = candidates()
    secrets = []
    for env in [ROOT / '.env', ROOT / 'demo/procurement/.env']:
        if env.exists():
            secrets.extend(str(value).encode() for key, value in dotenv_values(env).items()
                           if any(word in key for word in ['KEY', 'TOKEN', 'PASSWORD']) and value and len(value) >= 10)
    findings, files = [], {}
    for name in names:
        path = ROOT / name
        if not path.is_file():
            continue
        content = path.read_bytes()
        files[name] = {'bytes': len(content), 'sha256': hashlib.sha256(content).hexdigest()}
        if not safe_release_path(name):
            findings.append({'file': name, 'issue': 'unsafe release path'})
        if Path(name).name == '.env' or any(value in content for value in secrets):
            findings.append({'file': name, 'issue': 'private credential/configuration must be excluded'})
        if re.search(rb'\bsk-[A-Za-z0-9_-]{20,}', content):
            findings.append({'file': name, 'issue': 'possible API credential'})
        if len(content) > 10 * 1024**2:
            findings.append({'file': name, 'issue': 'artifact over 10 MiB; check before publishing'})
        if path.suffix == '.md':
            for target in re.findall(r'!?\[[^\]]*\]\(([^)]+)\)', content.decode('utf-8-sig')):
                target = target.split('#', 1)[0].strip('<>')
                if not target or '://' in target or target.startswith('mailto:'):
                    continue
                linked = (path.parent / target).resolve()
                if not linked.exists():
                    findings.append({'file': name, 'issue': 'broken local Markdown link', 'target': target})
    report = {'checked_at': datetime.now().astimezone().isoformat(), 'passed': not findings,
              'selection': selection, 'file_count': len(files), 'bytes': sum(f['bytes'] for f in files.values()),
              'credential_values_logged': False, 'findings': findings, 'files': files}
    output = args.output.resolve()
    if not output.is_relative_to(ROOT):
        raise ValueError('Audit output must remain inside the project')
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({key: report[key] for key in ['passed', 'selection', 'file_count', 'bytes', 'findings']}, ensure_ascii=False))
    if findings:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
