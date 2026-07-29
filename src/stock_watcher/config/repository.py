from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field


class VersionedConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    version: str = Field(pattern=r"^v[0-9]+(?:\.[0-9]+){0,2}$")
    source: str
    settings: dict[str, str | int | float | bool] = Field(default_factory=dict)


@dataclass(slots=True)
class ConfigRepository:
    directory: Path

    def save(self, config: VersionedConfig) -> Path:
        self.directory.mkdir(parents=True, exist_ok=True)
        target = self.directory / f"{config.version}.yaml"
        if target.exists():
            raise FileExistsError(f"config version already exists: {config.version}")
        target.write_text(yaml.safe_dump(config.model_dump(), sort_keys=True), encoding="utf-8")
        return target

    def load(self, version: str) -> VersionedConfig:
        payload = yaml.safe_load((self.directory / f"{version}.yaml").read_text(encoding="utf-8"))
        return VersionedConfig.model_validate(payload)
