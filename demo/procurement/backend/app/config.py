from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent if BACKEND.name == 'backend' else BACKEND
WORKSPACE = ROOT.parent.parent if ROOT.parent.name == 'demo' else ROOT
load_dotenv(WORKSPACE / ".env")
load_dotenv(ROOT / ".env")


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    database_path: Path = Path(os.getenv("DATABASE_PATH", ROOT / "data/entity_mining.db")).resolve()
    upload_dir: Path = Path(os.getenv("UPLOAD_DIR", ROOT / "data/uploads")).resolve()
    deepseek_api_key: str = os.getenv("DEEPSEEK_API_KEY", "")
    deepseek_base_url: str = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
    deepseek_model: str = os.getenv("DEEPSEEK_MODEL", "deepseek-flash")
    model_settings_path: str = os.getenv('MODEL_SETTINGS_PATH') or str(WORKSPACE / 'config/model-settings.local.json')
    model_settings_error: str = ''
    model_profile_path: str = os.getenv('MODEL_PROFILE_PATH') or str(WORKSPACE / 'config/model-profile.local.json')
    model_network_enabled: bool = _bool('MODEL_NETWORK_ENABLED', False)
    model_require_free: bool = _bool('MODEL_REQUIRE_FREE', False)
    model_max_tokens: int = max(1024, min(65536, int(os.getenv('MODEL_MAX_TOKENS', '16384'))))
    model_thinking: bool = _bool('MODEL_THINKING', False)
    extraction_strategy: str = os.getenv('EXTRACTION_STRATEGY', 'schema')
    parse_cache_dir: str = os.getenv('PARSE_CACHE_DIR', str(WORKSPACE / '.cache/parsed'))
    model_cache_dir: str = os.getenv('MODEL_RESPONSE_CACHE_DIR', str(WORKSPACE / '.cache/model-responses'))
    max_llm_chars: int = int(os.getenv("MAX_LLM_CHARS", "28000"))
    demo_seed: bool = _bool("DEMO_SEED", False)
    reference_seed: bool = _bool('REFERENCE_SEED', True)
    official_data_dir: Path = Path(os.getenv("OFFICIAL_DATA_DIR", WORKSPACE / "data/official-benchmark")).resolve()
    max_upload_bytes: int = int(os.getenv("MAX_UPLOAD_BYTES", str(4 * 1024**3)))
    max_expanded_bytes: int = int(os.getenv("MAX_EXPANDED_BYTES", str(8 * 1024**3)))
    max_archive_files: int = int(os.getenv("MAX_ARCHIVE_FILES", "15000"))
    max_pdf_pages: int = int(os.getenv("MAX_PDF_PAGES", "600"))
    max_document_bytes: int = int(os.getenv('MAX_DOCUMENT_BYTES', str(256*1024**2)))
    max_document_chars: int = int(os.getenv('MAX_DOCUMENT_CHARS', '2000000'))
    max_ocr_pixels: int = int(os.getenv('MAX_OCR_PIXELS', '20000000'))
    model_concurrency: int = max(1, min(8, int(os.getenv("MODEL_CONCURRENCY", "2"))))
    parse_concurrency: int = max(1, min(4, int(os.getenv('PARSE_CONCURRENCY', '2'))))
    api_token: str = os.getenv("APP_API_TOKEN", "")
    neo4j_uri: str = os.getenv("NEO4J_URI", "")
    neo4j_user: str = os.getenv("NEO4J_USER", "neo4j")
    neo4j_password: str = os.getenv("NEO4J_PASSWORD", "")
    neo4j_database: str = os.getenv('NEO4J_DATABASE', 'neo4j')
    relation_backend: str = os.getenv('RELATION_BACKEND', 'sqlite').lower()


settings = Settings()
# Filesystem initialization belongs to application startup, not module import.

