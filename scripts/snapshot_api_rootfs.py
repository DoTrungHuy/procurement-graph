"""Save an API rollback image without reading its original image manifests.

Use only after the deployment preflight. Mounted databases/configuration are
excluded by Docker export; Compose supplies runtime settings again on rollback.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


CONTAINER = 'procurement-ecs-api-1'
SAFE_ENV = {'PATH', 'LANG', 'LANGUAGE', 'LC_ALL', 'TZ', 'DEBIAN_FRONTEND',
            'PYTHONUNBUFFERED', 'PYTHONDONTWRITEBYTECODE', 'PYTHONPATH', 'VIRTUAL_ENV'}
SOURCE_LABEL = 'org.procurement.rollback.source-container'
HASH_LABEL = 'org.procurement.rollback.exports-sha256'
HASH_CODE = ('from pathlib import Path; import hashlib; '
             'print(hashlib.sha256(Path("/app/app/exports.py").read_bytes()).hexdigest())')


def run(*args: str, allow_missing: bool = False):
    result = subprocess.run(['docker', *args], text=True, capture_output=True)
    if result.returncode and not allow_missing:
        raise RuntimeError(f'Docker {args[0]} failed: {result.stderr.strip()}')
    return result


def image_changes(config: dict, identifier: str, digest: str) -> list[str]:
    """Retain launch metadata, excluding runtime credentials and Compose labels."""
    if not config.get('Cmd') and not config.get('Entrypoint'):
        raise RuntimeError('The running API has no launch command; cannot prepare a rollback image')
    changes = []
    for key, instruction in [('Entrypoint', 'ENTRYPOINT'), ('Cmd', 'CMD')]:
        if config.get(key) is not None:
            changes.append(instruction + ' ' + json.dumps(config[key], ensure_ascii=False))
    for key, instruction in [('WorkingDir', 'WORKDIR'), ('User', 'USER'), ('StopSignal', 'STOPSIGNAL')]:
        if config.get(key):
            value = config[key]
            if '\n' in value or '\r' in value:
                raise RuntimeError('Unexpected newline in API launch metadata')
            changes.append(instruction + ' ' + value)
    for value in config.get('Env') or []:
        key, separator, content = value.partition('=')
        if separator and key in SAFE_ENV:
            changes.append('ENV ' + key + '=' + json.dumps(content, ensure_ascii=False))
    for port in sorted(config.get('ExposedPorts') or {}):
        changes.append('EXPOSE ' + port)
    health = config.get('Healthcheck') or {}
    test = health.get('Test') or []
    if test:
        options = []
        for key, option in [('Interval', 'interval'), ('Timeout', 'timeout'),
                            ('StartPeriod', 'start-period'), ('StartInterval', 'start-interval')]:
            if health.get(key, 0) > 0:
                options.append(f'--{option}={health[key]}ns')
        if health.get('Retries', 0) > 0:
            options.append(f'--retries={health["Retries"]}')
        if test[0] == 'NONE':
            changes.append('HEALTHCHECK NONE')
        else:
            if test[0] == 'CMD':
                command = json.dumps(test[1:], ensure_ascii=False)
            elif test[0] == 'CMD-SHELL' and len(test) == 2:
                command = test[1]
            else:
                raise RuntimeError('Unsupported API healthcheck; cannot preserve rollback behavior')
            changes.append('HEALTHCHECK ' + (' '.join(options) + ' ' if options else '') + 'CMD ' + command)
    changes.extend([f'LABEL {SOURCE_LABEL}={identifier}', f'LABEL {HASH_LABEL}={digest}'])
    return changes


def verify_image(image: str, digest: str):
    actual = run('run', '--rm', '--pull', 'never', '--network', 'none', '--read-only',
                 '--entrypoint', 'python', image, '-c', HASH_CODE).stdout.strip()
    if actual != digest:
        raise RuntimeError('API rollback image file hash differs from the running container')


def snapshot() -> str:
    info = json.loads(run('inspect', CONTAINER).stdout)[0]
    identifier = info['Id']
    if len(identifier) != 64 or any(value not in '0123456789abcdef' for value in identifier):
        raise RuntimeError('Unexpected API container identifier')
    if not info.get('State', {}).get('Running'):
        raise RuntimeError('The API container is not running; stop and inspect the deployment')
    digest = run('exec', CONTAINER, 'python', '-c', HASH_CODE).stdout.strip()
    if len(digest) != 64 or any(value not in '0123456789abcdef' for value in digest):
        raise RuntimeError('Unexpected API source hash')
    image = 'procurement-site-api-rollback:container-' + identifier[:12]
    existing = run('image', 'inspect', '--format', '{{json .Config.Labels}}', image, allow_missing=True)
    if not existing.returncode:
        labels = json.loads(existing.stdout) or {}
        if labels.get(SOURCE_LABEL) != identifier or labels.get(HASH_LABEL) != digest:
            raise RuntimeError('An unrelated rollback tag already exists; it was not overwritten')
        verify_image(image, digest)
        print('Reusing verified API rollback image.', file=sys.stderr, flush=True)
        return image
    if 'No such image' not in existing.stderr:
        raise RuntimeError('Cannot inspect the API rollback tag: ' + existing.stderr.strip())
    # The observed API image is about 1.7 GB. Import stores both a layer
    # blob and its extracted snapshot. Check the daemon/default containerd disks.
    docker_root = Path(json.loads(run('info', '--format', '{{json .DockerRootDir}}').stdout))
    for directory in {docker_root, Path('/var/lib/containerd')}:
        if directory.exists() and shutil.disk_usage(directory).free < 4 * 1024**3:
            raise RuntimeError(f'Less than 4 GiB free at {directory}; API backup was not started')
    # This stream creates a self-contained image without an intermediate tar file.
    # Never copy the container's credential-bearing Env or Compose identity labels.
    changes = image_changes(info['Config'], identifier, digest)
    arguments = ['docker', 'import']
    for change in changes:
        arguments.extend(['--change', change])
    arguments.extend(['-', image])
    print('Exporting API root filesystem and importing rollback image; this may take a few minutes.',
          file=sys.stderr, flush=True)
    try:
        with tempfile.TemporaryFile() as export_log:
            exporter = subprocess.Popen(['docker', 'export', CONTAINER], stdout=subprocess.PIPE,
                                        stderr=export_log)
            result = None
            try:
                result = subprocess.run(arguments, stdin=exporter.stdout, text=True, capture_output=True)
            finally:
                exporter.stdout.close()
                # A successful importer consumed EOF; let the exporter exit normally.
                if (result is None or result.returncode) and exporter.poll() is None:
                    exporter.terminate()
                export_status = exporter.wait()
            export_log.seek(0)
            export_error = export_log.read(4096).decode('utf-8', errors='replace').strip()
        if result.returncode:
            raise RuntimeError('API rootfs import failed: ' + result.stderr.strip())
        if export_status:
            raise RuntimeError('API rootfs export failed; rollback image was rejected: ' + export_error)
        verify_image(image, digest)
    except BaseException:
        # This tag was absent before this invocation; live/service tags are untouched.
        run('image', 'rm', image, allow_missing=True)
        raise
    print('API rollback image created and file hash verified.', file=sys.stderr, flush=True)
    return image


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    try:
        print(snapshot())
    except Exception as error:
        print('API snapshot stopped: ' + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
