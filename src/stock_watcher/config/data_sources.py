from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator


class DataSourceMode(StrEnum):
    TUSHARE_15000 = "tushare_15000"
    ADVANCED_DIAGNOSTIC = "advanced_diagnostic"
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


class NativeRealtimeProfile(BaseModel):
    """Tushare SDK realtime route explicitly approved by the Human Owner."""

    model_config = ConfigDict(frozen=True)

    name: str = "native_realtime"
    verify_url: HttpUrl = HttpUrl("https://realtime.stockai888.top")
    credential_ref: str = "StockWatcher/Tushare/Primary"
    source: Literal["sina"] = "sina"
    batch_size: int = Field(default=800, ge=1, le=800)
    min_interval_seconds: float = Field(default=1.0, ge=0.6, le=30)
    stale_after_seconds: float = Field(default=60.0, gt=0, le=120)

    @field_validator("verify_url")
    @classmethod
    def require_https(cls, value: HttpUrl) -> HttpUrl:
        if value.scheme != "https":
            raise ValueError("native realtime verification URL must use HTTPS")
        return value


class DataSourceSettings(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: str = "v2"
    mode: DataSourceMode = DataSourceMode.TUSHARE_15000
    primary_profile: HttpProfile = HttpProfile(
        name="tushare_15000",
        base_url=HttpUrl("https://fastapic.stockai888.top"),
        credential_ref="StockWatcher/Tushare/Primary",
    )
    super_profile: HttpProfile = HttpProfile(
        name="super",
        base_url=HttpUrl("https://ai-tool.indevs.in"),
        credential_ref="StockWatcher/Tushare/Super",
        enabled=False,
    )
    fast_profile: HttpProfile = HttpProfile(
        name="fast",
        base_url=HttpUrl("https://fastapic.stockai888.top"),
        credential_ref="StockWatcher/Tushare/Fast",
        enabled=False,
    )
    native_realtime_profile: NativeRealtimeProfile = NativeRealtimeProfile()
    request_budget_interval_seconds: float = Field(default=1.0, ge=0.6, le=30)
    super_pro_prefix: str = "/tushare/pro"
    realtime_warmup_cycles: int = Field(default=3, ge=3, le=30)
    scan_target_seconds: float = Field(default=10.0, ge=5.0, le=60.0)
    full_scan_max_seconds: float = Field(default=60.0, ge=10.0, le=120.0)
    source_fresh_seconds: float = Field(default=60.0, gt=0, le=120.0)
    source_stop_seconds: float = Field(default=120.0, ge=60.0, le=300.0)

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
        settings = DataSourceSettings.model_validate(payload)
        if settings.mode in {
            DataSourceMode.SUPER,
            DataSourceMode.FAST,
            DataSourceMode.SMART,
        }:
            return settings.model_copy(update={"mode": DataSourceMode.TUSHARE_15000})
        return settings

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
