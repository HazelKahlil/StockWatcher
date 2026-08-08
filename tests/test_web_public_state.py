"""Public dashboard state keeps the latest realtime observation visible."""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from stock_watcher.domain import SHANGHAI, HealthState
from stock_watcher.engine import Candidate
from stock_watcher.engine.candidates import CandidateBatch
from stock_watcher.services.public_state import PublicStateBuilder
from stock_watcher.storage import SQLiteStore


def make_batch(source_ts: datetime, prefix: str) -> CandidateBatch:
    candidates = tuple(
        Candidate(
            code=f"6000{index:02d}.SH",
            name=f"{prefix}{index}",
            sector="测试板块",
            sector_code="TEST",
            level="强" if index == 1 else "中",
            score=50.0 - index,
            price_score=20.0,
            sector_score=20.0,
            trend_score=10.0,
            penalty=0.0,
            reasons=("测试",),
            source_ts=source_ts,
            provider_version="test-provider",
            config_version="test-config",
            app_version="test-app",
            price=10.0 + index,
            change_pct=float(index),
            total_score=50.0 - index,
            core_score=40.0,
            is_formal=True,
            is_supplement=False,
        )
        for index in range(1, 4)
    )
    return CandidateBatch(
        source_ts=source_ts,
        generated_at=source_ts,
        health=HealthState.HEALTHY,
        overall_weak=False,
        candidates=candidates,
    )


def test_public_state_falls_back_to_latest_snapshot_when_projection_is_empty(
    tmp_path: Path,
) -> None:
    store = SQLiteStore(tmp_path / "state.sqlite3")
    store.initialize()
    builder = PublicStateBuilder(store)
    first_time = datetime(2026, 8, 8, 9, 45, tzinfo=SHANGHAI)

    initial = builder.build(now=first_time)
    assert initial["snapshot_id"] is None
    assert initial["candidates"] == []

    first_id = store.record_batch(make_batch(first_time, "第一批"))
    with store.transaction() as connection:
        store.upsert_public_state(
            connection,
            state_version=1,
            snapshot_id=None,
            source_ts=None,
            payload={
                "service_state": "warming",
                "market_state": "morning",
                "candidates": [],
            },
        )

    previous = builder.build(now=first_time + timedelta(minutes=1))
    assert previous["snapshot_id"] == first_id
    assert previous["candidates_source"] == "last_realtime_snapshot"
    assert [row["name"] for row in previous["candidates"]] == [
        "第一批1",
        "第一批2",
        "第一批3",
    ]
    assert previous["source_ts"] == first_time.isoformat()

    second_time = first_time + timedelta(minutes=5)
    second_id = store.record_batch(make_batch(second_time, "第二批"))
    with store.transaction() as connection:
        store.upsert_public_state(
            connection,
            state_version=2,
            snapshot_id=second_id,
            source_ts=second_time.isoformat(),
            payload={
                "service_state": "healthy",
                "market_state": "morning",
                "candidates": [
                    {
                        "rank": 1,
                        "code": "60001.SH",
                        "name": "第二批1",
                        "level": "强",
                        "is_formal": True,
                        "is_supplement": False,
                        "price": 11.0,
                        "change_pct": 1.0,
                        "sector_name": "测试板块",
                        "sector_type": "industry",
                        "total_score": 49.0,
                    }
                ],
            },
        )

    current = builder.build(now=second_time + timedelta(minutes=1))
    assert current["snapshot_id"] == second_id
    assert current["candidates_source"] == "current_public_state"
    assert current["candidates"][0]["name"] == "第二批1"
