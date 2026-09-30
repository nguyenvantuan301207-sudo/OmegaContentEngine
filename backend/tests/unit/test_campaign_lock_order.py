"""Static lock-order regressions for Campaign runtime authority paths."""

from __future__ import annotations

import inspect

from omega.application import campaign_admission_service as runtime


def _source(function) -> str:
    return inspect.getsource(function)


def _assert_advisory_before_campaign_row(function) -> None:
    source = _source(function)
    advisory = source.index("await _lock_channel(")
    campaign_row = source.index("await _locked_campaign(")
    assert advisory < campaign_row


def test_start_path_uses_canonical_lock_order() -> None:
    _assert_advisory_before_campaign_row(runtime.start_campaign)


def test_reconciliation_acquires_canonical_locks_before_finalization() -> None:
    source = _source(runtime.reconcile_campaign)
    advisory = source.index("await _lock_channel(")
    campaign_row = source.index("await _locked_campaign(")
    finalization = source.index("await finalize_campaign(")
    assert advisory < campaign_row < finalization
    assert source.rfind("await _lock_channel(") == advisory


def test_direct_finalization_never_inverts_into_advisory_locking() -> None:
    source = _source(runtime.finalize_campaign)
    assert ".with_for_update()" in source
    assert "_lock_channel" not in source


def test_pause_resume_and_cancel_use_canonical_lock_order() -> None:
    for function in (
        runtime.pause_campaign,
        runtime.resume_campaign,
        runtime.cancel_campaign,
    ):
        _assert_advisory_before_campaign_row(function)
