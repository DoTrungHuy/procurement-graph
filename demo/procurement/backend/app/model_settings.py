"""Local model configuration. Secrets stay server-side; saving never calls a model."""
from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
from threading import RLock
from urllib.parse import urlsplit, urlunsplit
import uuid

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from .config import Settings

_lock = RLock()
_ERROR = '保存的模型配置无法读取，请重新填写Key并保存配置'


class ModelConfigurationUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    base_url: str = Field(min_length=1, max_length=2048)
    model: str = Field(min_length=1, max_length=200)
    api_key: SecretStr | None = None
    enabled: bool = True

    @field_validator('base_url')
    @classmethod
    def normalize_url(cls, value):
        value = value.strip().rstrip('/')
        if any(char.isspace() or ord(char) < 32 for char in value):
            raise ValueError('服务地址不能包含空白或控制字符')
        try:
            parsed = urlsplit(value)
            port = parsed.port
        except ValueError:
            raise ValueError('请输入有效的HTTP或HTTPS基础地址') from None
        if parsed.scheme not in {'http','https'} or not parsed.hostname or parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment:
            raise ValueError('服务地址须为HTTP或HTTPS，不能含账号、查询参数或片段')
        if parsed.path.rstrip('/').endswith('/chat/completions'):
            parsed = parsed._replace(path=parsed.path.rstrip('/')[:-len('/chat/completions')])
        return urlunsplit(parsed).rstrip('/')

    @field_validator('model')
    @classmethod
    def normalize_model(cls, value):
        value = value.strip()
        if not value or any(char.isspace() or ord(char) < 32 for char in value):
            raise ValueError('模型名称不能为空，且不能包含空白或控制字符')
        return value

    @field_validator('api_key')
    @classmethod
    def normalize_key(cls, value):
        if value is None:
            return None
        raw = value.get_secret_value().strip()
        if len(raw) > 8192 or any(char.isspace() or ord(char) < 32 for char in raw):
            raise ValueError('Key格式无效，不能包含空白或控制字符')
        return SecretStr(raw)


def _read(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open('rb') as stream:
        content = stream.read(65537)
    if len(content) > 65536:
        raise ValueError('Configuration is oversized')
    data = json.loads(content.decode('utf-8-sig'))
    if not isinstance(data,dict) or set(data) != {'base_url','model','api_key','enabled'}:
        raise ValueError('Invalid configuration structure')
    validated = ModelConfigurationUpdate.model_validate(data)
    if validated.api_key is None or (validated.enabled and not validated.api_key.get_secret_value()):
        raise ValueError('Missing saved credential')
    return {'deepseek_base_url':validated.base_url,'deepseek_model':validated.model,
            'deepseek_api_key':validated.api_key.get_secret_value(),'model_network_enabled':validated.enabled}


def current_settings(base: Settings) -> Settings:
    # Read-only deployments ignore any saved file so server environment variables stay authoritative.
    if base.model_settings_readonly:
        return base
    try:
        with _lock:
            values = _read(Path(base.model_settings_path))
        return replace(base, **values) if values else base
    except (OSError,ValueError,TypeError):
        # Keep browsing/parsing usable without silently sending with a stale key.
        return replace(base,deepseek_api_key='',model_network_enabled=False,model_settings_error=_ERROR)


def public_settings(base: Settings) -> dict:
    effective = current_settings(base)
    try:
        address = ModelConfigurationUpdate.normalize_url(effective.deepseek_base_url)
    except ValueError:
        address = ''
    return {'base_url':address,'model':effective.deepseek_model,
            'api_key_configured':bool(effective.deepseek_api_key),'enabled':effective.model_network_enabled,
            'configuration_error':effective.model_settings_error,
            'read_only':base.model_settings_readonly}


def save_settings(base: Settings, update: ModelConfigurationUpdate) -> dict:
    if base.model_settings_readonly:
        raise ValueError('模型配置由管理员在服务端设定，网站内不可修改')
    with _lock:
        current = current_settings(base)
        supplied = update.api_key.get_secret_value() if update.api_key is not None else ''
        if current.model_settings_error and not supplied:
            raise ValueError(_ERROR)
        key = supplied or current.deepseek_api_key
        if update.enabled and not key:
            raise ValueError('启用模型抽取前请填写Key')
        payload = {'base_url':update.base_url,'model':update.model,'api_key':key,'enabled':update.enabled}
        path = Path(base.model_settings_path)
        path.parent.mkdir(parents=True,exist_ok=True)
        temporary = path.with_name(f'.model-settings-{uuid.uuid4().hex}.local.json')
        try:
            descriptor = os.open(temporary,os.O_WRONLY | os.O_CREAT | os.O_EXCL,0o600)
            with os.fdopen(descriptor,'w',encoding='utf-8') as stream:
                json.dump(payload,stream,ensure_ascii=False,indent=2)
                stream.write('\n')
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    return public_settings(base)
