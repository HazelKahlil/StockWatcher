from __future__ import annotations

from enum import StrEnum
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator


class DataSourceMode(StrEnum):
    SUPER = "super"
    FAST = "fast"
    SMART = "smart"
    REPLAY = "replay"
    TDX_DIAGNOSTIC = "tdx_diagnostic"


class HttpProfile(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    base_url: HttpUrl
    credential_ref: str
    enabled: bool = True
    use_system_proxy: bool = False
    connect_timeout_seconds: float = Field(default=5.0, gt=0, le=30)
    read_timeout_seconds: float = Field(default=30.0, gt=0, le=120)

    @field_validator("base_url")
    @classmethod
    def require_https(cls, value: HttpUrl) -> HttpUrl:
        if value.scheme != "https":
            raise ValueError("external provider URLs must use HTTPS")
        return value


class DataSourceSettings(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: str = "v1"
    mode: DataSourceMode = DataSourceMode.SUPER
    super_profile: HttpProfile = HttpProfile(
        name="super",
        base_url=HttpUrl("https://ai-tool.indevs.in"),
        credential_ref="StockWatcher/Tushare/Super",
    )
    fast_profile: HttpProfile = HttpProfile(
        name="fast",
        base_url=HttpUrl("https://fastapic.stockai888.top"),
        credential_ref="StockWatcher/Tushare/Fast",
        enabled=False,
    )
    super_pro_prefix: str = "/tushare/pro"
    realtime_warmup_cycles: int = Field(default=3, ge=3, le=30)

    @field_validator("super_pro_prefix")
    @classmethod
    def validate_prefix(cls, value: str) -> str:
        if not value.startswith("/") or value.endswith("/") or ".." in value:
            raise ValueError("super pro prefix must be one safe absolute path prefix")
        return value


class DataSourceConfigRepository:
    """Stores only non-secret routing configuration with an atomic replace."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> DataSourceSettings:
        if not self.path.is_file():
            return DataSourceSettings()
        payload = yaml.safe_load(self.path.read_text(encoding="utf-8"))
        return DataSourceSettings.model_validate(payload)

    def save(self, settings: DataSourceSettings) -> None:
        payload = settings.model_dump(mode="json")
        rendered = yaml.safe_dump(payload, allow_unicode=True, sort_keys=True)
        lowered = rendered.casefold()
        if any(marker in lowered for marker in ("api_key:", "token:", "secret:", "password:")):
            raise ValueError("data-source configuration must not contain secrets")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(rendered, encoding="utf-8")
        temporary.replace(self.path)
