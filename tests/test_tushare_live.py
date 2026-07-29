from __future__ import annotations

import pytest

from stock_watcher.providers.tushare.m0 import M0Verdict, run_capability_m0


@pytest.mark.live_tushare
@pytest.mark.parametrize("profile", ["super", "fast"])
def test_live_tushare_capability_m0(profile: str) -> None:
    report = run_capability_m0(profile)
    if (
        len(report.observations) == 1
        and report.observations[0].safe_reason == "credential_missing"
    ):
        pytest.skip("Human Owner has not stored this profile credential")
    assert report.verdict() in {
        M0Verdict.PASS,
        M0Verdict.PASS_WITH_LIMITS,
        M0Verdict.FAIL,
    }
    assert not report.raw_payload_persisted
    assert not report.credential_persisted
