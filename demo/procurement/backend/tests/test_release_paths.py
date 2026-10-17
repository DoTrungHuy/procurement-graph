import importlib.util
from pathlib import Path

import pytest


def release_paths():
    path = Path(__file__).resolve().parents[4]/'scripts/audit_publish.py'
    spec = importlib.util.spec_from_file_location('release_path_audit',path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.safe_release_path


def test_gold_evidence_names_are_valid_release_paths():
    valid = release_paths()
    assert valid('docs/verification/task1_gold/03_reports/评测解读.md')
    assert valid('docs/verification/task1_gold/08_audit/审核清单.csv')


@pytest.mark.parametrize('path', ['../.env','/absolute/file','C:/private/file','docs/../.env',
                                  'docs\\..\\.env','docs/file\n.md','docs/trailing.'])
def test_invalid_paths_remain_blocked(path):
    assert not release_paths()(path)
