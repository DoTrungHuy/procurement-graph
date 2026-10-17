"""Disposable checkpoints for successfully grounded extraction pieces."""
from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path

from pydantic import ValidationError

from .models import ProjectExtract

CHECKPOINT_VERSION = 'grounded-piece-v1'


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':')).encode()).hexdigest()


class PieceCheckpoints:
    def __init__(self, directory: str) -> None:
        self.directory = Path(directory).resolve() if directory else None

    def load(self, key: str):
        if self.directory is None:
            return None
        try:
            path = self.directory / (key + '.json')
            if path.stat().st_size > 32 * 1024**2:
                return None
            record = json.loads(path.read_text('utf-8'))
            metadata = record['metadata']
            if (not isinstance(metadata, dict)
                    or not isinstance(metadata.get('response_models'), list)
                    or not all(isinstance(item, str) for item in metadata['response_models'])
                    or not isinstance(metadata.get('retrieval'), list)
                    or not all(isinstance(item, dict) for item in metadata['retrieval'])):
                return None
            if (record['version'] != CHECKPOINT_VERSION or record['key'] != key
                    or digest([record['pieces'], metadata]) != record['sha256']):
                return None
            pieces = [ProjectExtract.model_validate(item) for item in record['pieces']]
            if not pieces or any(item.processing_status != 'complete' for item in pieces):
                return None
            return pieces, metadata
        except (OSError, ValueError, TypeError, KeyError, ValidationError):
            return None

    def save(self, key: str, pieces: list[ProjectExtract], metadata: dict) -> bool:
        if self.directory is None or not pieces or any(item.processing_status != 'complete' for item in pieces):
            return False
        data = [item.model_dump(mode='json') for item in pieces]
        record = {'version': CHECKPOINT_VERSION, 'key': key, 'pieces': data,
                  'sha256': digest([data, metadata]), 'metadata': metadata}
        path = self.directory / (key + '.json')
        temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.part')
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            temporary.write_text(json.dumps(record, ensure_ascii=False), encoding='utf-8')
            temporary.replace(path)
            return True
        except OSError:
            return False
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
