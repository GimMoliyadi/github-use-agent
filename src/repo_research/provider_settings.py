"""Server-only UI model configuration; credentials never enter task history."""

from __future__ import annotations

import json
import os
import tempfile
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from .config import Settings


class ModelConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["mock", "live"] = "live"
    base_url: str = Field(default="", max_length=1000)
    api_key: SecretStr = SecretStr("")
    model: str = Field(default="", max_length=200)
    output_mode: Literal["auto", "tools", "json_schema", "json_text"] = "auto"

    @field_validator("base_url", "model", mode="before")
    @classmethod
    def strip_text(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator("base_url")
    @classmethod
    def validate_url(cls, value):
        if not value:
            return value
        try:
            parsed = urlsplit(value)
            valid = (
                parsed.scheme in {"https", "http"} and parsed.hostname
                and not parsed.username and not parsed.password
                and not parsed.query and not parsed.fragment and parsed.port != 0
            )
        except ValueError:
            valid = False
        if not valid:
            raise ValueError("Base URL must be an HTTP(S) URL without credentials or query.")
        if parsed.path.rstrip("/").endswith(("/chat/completions", "/responses")):
            raise ValueError("Base URL 请填接口前缀（通常以 /v1 结尾），不要填 /chat/completions。")
        return value.rstrip("/")


def candidate_settings(current: Settings, configuration: ModelConfiguration) -> Settings:
    key = configuration.api_key.get_secret_value().strip()
    if not key:
        if configuration.base_url != current.llm_base_url.rstrip("/"):
            if configuration.mode == "live" and current.llm_api_key.get_secret_value():
                raise ValueError("更换 Base URL 时请填写新密钥；已有密钥不会转发到新地址。")
            key = ""
        else:
            key = current.llm_api_key.get_secret_value()
    if configuration.mode == "live" and not (
        configuration.base_url and key and configuration.model
    ):
        raise ValueError("请填写 Base URL、API 密钥和模型名称。")
    return Settings.model_validate({
        **current.model_dump(), "model_mode": configuration.mode,
        "llm_base_url": configuration.base_url, "llm_api_key": key,
        "llm_model": configuration.model, "llm_output_mode": configuration.output_mode,
    })


def load_model_settings(settings: Settings) -> Settings:
    path = settings.data_dir / "model-settings.json"
    if not settings.load_saved_model or not path.exists():
        return settings
    try:
        configuration = ModelConfiguration.model_validate_json(path.read_text())
        return candidate_settings(settings, configuration)
    except Exception:
        raise ValueError(
            "保存的模型配置无法读取；请恢复或移除 data/model-settings.json。"
        ) from None


def save_model_settings(settings: Settings) -> None:
    path = settings.data_dir / "model-settings.json"
    payload = {
        "mode": settings.model_mode, "base_url": settings.llm_base_url,
        "api_key": settings.llm_api_key.get_secret_value(), "model": settings.llm_model,
        "output_mode": settings.llm_output_mode,
    }
    descriptor, temporary = tempfile.mkstemp(prefix=".model-settings-", dir=settings.data_dir)
    try:
        with os.fdopen(descriptor, "w") as handle:
            if hasattr(os, "fchmod"):
                os.fchmod(handle.fileno(), 0o600)
            json.dump(payload, handle, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def public_model_settings(settings: Settings) -> dict:
    # Legacy environment URLs may contain credentials; do not send them to browsers.
    url = settings.llm_base_url
    try:
        ModelConfiguration(base_url=url)
    except ValueError:
        url = ""
    return {
        "mode": settings.model_mode, "base_url": url, "model": settings.llm_model,
        "output_mode": settings.llm_output_mode,
        "key_configured": bool(settings.llm_api_key.get_secret_value()),
    }
