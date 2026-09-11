from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from stock_watcher.domain import SHANGHAI
from stock_watcher.runtime.universe_cache import (
    RUNTIME_UNIVERSE_CACHE_VERSION,
    RuntimeUniverseCache,
    UniverseCacheError,
    UniverseCacheFailure,
)

SEED_FILENAME = "runtime-universe-seed.json"
MANIFEST_FILENAME = "runtime-universe-seed.manifest.json"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_universe_seed(
    path: Path,
    *,
    now: datetime | None = None,
    allow_stale: bool = True,
    minimum_profile_count: int | None = None,
) -> dict[str, Any]:
    if not path.is_file():
        raise UniverseCacheError(UniverseCacheFailure.MISSING)
    current = now or datetime.now(SHANGHAI)
    cache = RuntimeUniverseCache(path)
    if minimum_profile_count is not None:
        cache = RuntimeUniverseCache(
            path,
            minimum_profile_count=minimum_profile_count,
        )
    universe = cache.load(now=current, allow_stale=allow_stale)
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {
        "schema_version": str(payload.get("schema_version") or RUNTIME_UNIVERSE_CACHE_VERSION),
        "generated_at": universe.generated_at.isoformat() if universe.generated_at else "",
        "trend_through_date": (
            universe.trend_through_date.isoformat() if universe.trend_through_date else ""
        ),
        "profile_count": len(universe.profiles),
        "membership_count": len(universe.memberships),
        "concept_loaded": bool(universe.concept_loaded),
        "content_sha256": sha256_file(path),
        "seed_filename": path.name,
    }


def write_seed_manifest(
    destination: Path,
    *,
    source_commit: str,
    seed_summary: dict[str, Any],
    first_run_pack: bool,
) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    document = {
        "schema_version": seed_summary.get("schema_version", RUNTIME_UNIVERSE_CACHE_VERSION),
        "source_commit": source_commit,
        "generated_at": seed_summary.get("generated_at", ""),
        "trend_through_date": seed_summary.get("trend_through_date", ""),
        "profile_count": seed_summary.get("profile_count", 0),
        "membership_count": seed_summary.get("membership_count", 0),
        "concept_loaded": seed_summary.get("concept_loaded", False),
        "content_sha256": seed_summary.get("content_sha256", ""),
        "seed_filename": seed_summary.get("seed_filename", SEED_FILENAME),
        "first_run_pack": first_run_pack,
    }
    destination.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return destination


def assert_seed_matches_manifest(seed_path: Path, manifest_path: Path) -> None:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    actual = sha256_file(seed_path)
    expected = str(manifest.get("content_sha256") or "")
    if actual.lower() != expected.lower():
        raise UniverseCacheError(UniverseCacheFailure.CORRUPT)
