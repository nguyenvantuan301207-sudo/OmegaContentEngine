"""Unit tests for content campaign domain models, validation, and checksums."""

from __future__ import annotations

import datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from omega.domain.content import ContentType
from omega.domain.content_campaign import (
    ContentCampaignCreate,
    ContentCampaignItemInput,
    compute_campaign_plan_checksum,
    normalize_planned_release_at,
)


def test_normalize_planned_release_at():
    assert normalize_planned_release_at(None) is None

    # Naive datetime must raise ValueError
    naive_dt = datetime.datetime(2026, 9, 18, 12, 0, 0)
    with pytest.raises(ValueError, match="timezone-aware"):
        normalize_planned_release_at(naive_dt)

    # Offset datetime converted to UTC
    tz_plus_7 = datetime.timezone(datetime.timedelta(hours=7))
    dt_with_tz = datetime.datetime(2026, 9, 18, 19, 0, 0, tzinfo=tz_plus_7)
    normalized = normalize_planned_release_at(dt_with_tz)
    assert normalized == "2026-09-18T12:00:00+00:00"

    # Equivalent UTC string input
    normalized_str = normalize_planned_release_at("2026-09-18T19:00:00+07:00")
    assert normalized_str == "2026-09-18T12:00:00+00:00"


def test_compute_campaign_plan_checksum_determinism_and_equivalence():
    channel_id = uuid4()
    dna_id = uuid4()
    run1 = uuid4()
    run2 = uuid4()
    dec1 = uuid4()
    dec2 = uuid4()
    cand1 = uuid4()
    cand2 = uuid4()

    tz_plus_7 = datetime.timezone(datetime.timedelta(hours=7))

    items_a = [
        {
            "position": 1,
            "selection_run_id": run1,
            "selection_decision_id": dec1,
            "topic_candidate_id": cand1,
            "target_content_type": "YOUTUBE_LONGFORM",
            "planned_release_at": datetime.datetime(2026, 9, 18, 19, 0, 0, tzinfo=tz_plus_7),
        },
        {
            "position": 2,
            "selection_run_id": run2,
            "selection_decision_id": dec2,
            "topic_candidate_id": cand2,
            "target_content_type": "YOUTUBE_SHORT",
            "planned_release_at": "2026-09-19T12:00:00+00:00",
        },
    ]

    items_b = [
        {
            "position": 1,
            "selection_run_id": run1,
            "selection_decision_id": dec1,
            "topic_candidate_id": cand1,
            "target_content_type": "YOUTUBE_LONGFORM",
            "planned_release_at": datetime.datetime(2026, 9, 18, 12, 0, 0, tzinfo=datetime.UTC),
        },
        {
            "position": 2,
            "selection_run_id": run2,
            "selection_decision_id": dec2,
            "topic_candidate_id": cand2,
            "target_content_type": "YOUTUBE_SHORT",
            "planned_release_at": datetime.datetime(2026, 9, 19, 12, 0, 0, tzinfo=datetime.UTC),
        },
    ]

    sum_a = compute_campaign_plan_checksum(
        channel_id=channel_id,
        channel_dna_revision_id=dna_id,
        title="  Campaign Alpha  ",
        objective="  Test Objective  ",
        priority=1,
        items=items_a,
    )

    sum_b = compute_campaign_plan_checksum(
        channel_id=channel_id,
        channel_dna_revision_id=dna_id,
        title="Campaign Alpha",
        objective="Test Objective",
        priority=1,
        items=items_b,
    )

    # Identical after whitespace stripping and UTC timezone normalization
    assert sum_a == sum_b
    assert len(sum_a) == 64


def test_compute_campaign_plan_checksum_order_sensitivity():
    channel_id = uuid4()
    dna_id = uuid4()
    run1 = uuid4()
    run2 = uuid4()
    dec1 = uuid4()
    dec2 = uuid4()
    cand1 = uuid4()
    cand2 = uuid4()

    items_forward = [
        {
            "position": 1,
            "selection_run_id": run1,
            "selection_decision_id": dec1,
            "topic_candidate_id": cand1,
            "target_content_type": "YOUTUBE_LONGFORM",
            "planned_release_at": None,
        },
        {
            "position": 2,
            "selection_run_id": run2,
            "selection_decision_id": dec2,
            "topic_candidate_id": cand2,
            "target_content_type": "YOUTUBE_SHORT",
            "planned_release_at": None,
        },
    ]

    items_reversed = [
        {
            "position": 1,
            "selection_run_id": run2,
            "selection_decision_id": dec2,
            "topic_candidate_id": cand2,
            "target_content_type": "YOUTUBE_SHORT",
            "planned_release_at": None,
        },
        {
            "position": 2,
            "selection_run_id": run1,
            "selection_decision_id": dec1,
            "topic_candidate_id": cand1,
            "target_content_type": "YOUTUBE_LONGFORM",
            "planned_release_at": None,
        },
    ]

    sum1 = compute_campaign_plan_checksum(
        channel_id=channel_id,
        channel_dna_revision_id=dna_id,
        title="Campaign",
        objective=None,
        priority=1,
        items=items_forward,
    )
    sum2 = compute_campaign_plan_checksum(
        channel_id=channel_id,
        channel_dna_revision_id=dna_id,
        title="Campaign",
        objective=None,
        priority=1,
        items=items_reversed,
    )

    assert sum1 != sum2


def test_campaign_create_item_count_bounds():
    run_id = uuid4()
    single_item = ContentCampaignItemInput(
        selection_run_id=run_id,
        target_content_type=ContentType.YOUTUBE_LONGFORM,
    )

    # 0 items is rejected
    with pytest.raises(ValidationError):
        ContentCampaignCreate(
            title="Empty Campaign",
            idempotency_key="key-0",
            created_by="tester",
            items=[],
        )

    # 1 item is valid
    valid_one = ContentCampaignCreate(
        title="One Item Campaign",
        idempotency_key="key-1",
        created_by="tester",
        items=[single_item],
    )
    assert len(valid_one.items) == 1

    # 50 items is valid
    fifty_items = [
        ContentCampaignItemInput(
            selection_run_id=uuid4(),
            target_content_type=ContentType.YOUTUBE_SHORT,
        )
        for _ in range(50)
    ]
    valid_fifty = ContentCampaignCreate(
        title="Fifty Items",
        idempotency_key="key-50",
        created_by="tester",
        items=fifty_items,
    )
    assert len(valid_fifty.items) == 50

    # 51 items is rejected
    fifty_one_items = [
        ContentCampaignItemInput(
            selection_run_id=uuid4(),
            target_content_type=ContentType.YOUTUBE_SHORT,
        )
        for _ in range(51)
    ]
    with pytest.raises(ValidationError):
        ContentCampaignCreate(
            title="Fifty One Items",
            idempotency_key="key-51",
            created_by="tester",
            items=fifty_one_items,
        )


def test_campaign_create_duplicate_selection_run_in_request():
    dup_run_id = uuid4()
    items = [
        ContentCampaignItemInput(
            selection_run_id=dup_run_id,
            target_content_type=ContentType.YOUTUBE_LONGFORM,
        ),
        ContentCampaignItemInput(
            selection_run_id=dup_run_id,
            target_content_type=ContentType.YOUTUBE_SHORT,
        ),
    ]
    with pytest.raises(ValidationError, match="selection_run_id cannot appear multiple times"):
        ContentCampaignCreate(
            title="Duplicate Run Campaign",
            idempotency_key="key-dup",
            created_by="tester",
            items=items,
        )


def test_campaign_create_planned_release_order_validation():
    t1 = datetime.datetime(2026, 9, 18, 10, 0, 0, tzinfo=datetime.UTC)
    t2 = datetime.datetime(2026, 9, 18, 11, 0, 0, tzinfo=datetime.UTC)
    t3 = datetime.datetime(2026, 9, 18, 12, 0, 0, tzinfo=datetime.UTC)

    # Strictly increasing is valid
    valid_items = [
        ContentCampaignItemInput(
            selection_run_id=uuid4(),
            target_content_type=ContentType.YOUTUBE_LONGFORM,
            planned_release_at=t1,
        ),
        ContentCampaignItemInput(
            selection_run_id=uuid4(),
            target_content_type=ContentType.YOUTUBE_SHORT,
            planned_release_at=t2,
        ),
        ContentCampaignItemInput(
            selection_run_id=uuid4(),
            target_content_type=ContentType.YOUTUBE_LONGFORM,
            planned_release_at=t3,
        ),
    ]
    campaign = ContentCampaignCreate(
        title="Valid Schedule",
        idempotency_key="sched-1",
        created_by="tester",
        items=valid_items,
    )
    assert len(campaign.items) == 3

    # Decreasing release time is rejected
    invalid_decreasing = [
        ContentCampaignItemInput(
            selection_run_id=uuid4(),
            target_content_type=ContentType.YOUTUBE_LONGFORM,
            planned_release_at=t2,
        ),
        ContentCampaignItemInput(
            selection_run_id=uuid4(),
            target_content_type=ContentType.YOUTUBE_SHORT,
            planned_release_at=t1,
        ),
    ]
    with pytest.raises(ValidationError, match="planned_release_at must be strictly increasing"):
        ContentCampaignCreate(
            title="Invalid Decreasing",
            idempotency_key="sched-dec",
            created_by="tester",
            items=invalid_decreasing,
        )

    # Equal release time is rejected (must be strictly increasing)
    invalid_equal = [
        ContentCampaignItemInput(
            selection_run_id=uuid4(),
            target_content_type=ContentType.YOUTUBE_LONGFORM,
            planned_release_at=t1,
        ),
        ContentCampaignItemInput(
            selection_run_id=uuid4(),
            target_content_type=ContentType.YOUTUBE_SHORT,
            planned_release_at=t1,
        ),
    ]
    with pytest.raises(ValidationError, match="planned_release_at must be strictly increasing"):
        ContentCampaignCreate(
            title="Invalid Equal",
            idempotency_key="sched-eq",
            created_by="tester",
            items=invalid_equal,
        )

    # Partial scheduling (some None, some scheduled) strictly increasing for scheduled items
    valid_mixed_schedule = [
        ContentCampaignItemInput(
            selection_run_id=uuid4(),
            target_content_type=ContentType.YOUTUBE_LONGFORM,
            planned_release_at=t1,
        ),
        ContentCampaignItemInput(
            selection_run_id=uuid4(),
            target_content_type=ContentType.YOUTUBE_SHORT,
            planned_release_at=None,
        ),
        ContentCampaignItemInput(
            selection_run_id=uuid4(),
            target_content_type=ContentType.YOUTUBE_LONGFORM,
            planned_release_at=t2,
        ),
    ]
    campaign_mixed = ContentCampaignCreate(
        title="Valid Mixed Schedule",
        idempotency_key="sched-mix",
        created_by="tester",
        items=valid_mixed_schedule,
    )
    assert len(campaign_mixed.items) == 3


def test_campaign_create_naive_datetime_rejected():
    naive_dt = datetime.datetime(2026, 9, 18, 10, 0, 0)
    with pytest.raises(ValidationError, match="timezone-aware"):
        ContentCampaignItemInput(
            selection_run_id=uuid4(),
            target_content_type=ContentType.YOUTUBE_LONGFORM,
            planned_release_at=naive_dt,
        )


def test_compute_campaign_plan_checksum_three_item_permutation():
    channel_id = uuid4()
    dna_id = uuid4()
    run_a, run_b, run_c = uuid4(), uuid4(), uuid4()
    dec_a, dec_b, dec_c = uuid4(), uuid4(), uuid4()
    cand_a, cand_b, cand_c = uuid4(), uuid4(), uuid4()

    item_a = {
        "position": 1,
        "selection_run_id": run_a,
        "selection_decision_id": dec_a,
        "topic_candidate_id": cand_a,
        "target_content_type": "YOUTUBE_LONGFORM",
        "planned_release_at": None,
    }
    item_b = {
        "position": 2,
        "selection_run_id": run_b,
        "selection_decision_id": dec_b,
        "topic_candidate_id": cand_b,
        "target_content_type": "YOUTUBE_SHORT",
        "planned_release_at": None,
    }
    item_c = {
        "position": 3,
        "selection_run_id": run_c,
        "selection_decision_id": dec_c,
        "topic_candidate_id": cand_c,
        "target_content_type": "YOUTUBE_LONGFORM",
        "planned_release_at": None,
    }

    # Order [A, B, C]
    sum_abc = compute_campaign_plan_checksum(
        channel_id=channel_id,
        channel_dna_revision_id=dna_id,
        title="Three Item Campaign",
        objective=None,
        priority=1,
        items=[item_a, item_b, item_c],
    )

    # Order [C, A, B] with positions reindexed 1, 2, 3
    item_c_as_1 = {**item_c, "position": 1}
    item_a_as_2 = {**item_a, "position": 2}
    item_b_as_3 = {**item_b, "position": 3}

    sum_cab = compute_campaign_plan_checksum(
        channel_id=channel_id,
        channel_dna_revision_id=dna_id,
        title="Three Item Campaign",
        objective=None,
        priority=1,
        items=[item_c_as_1, item_a_as_2, item_b_as_3],
    )

    assert sum_abc != sum_cab


def test_recompute_persisted_campaign_plan_checksum_reproducibility():
    from omega.domain.content_campaign import recompute_persisted_campaign_plan_checksum

    class MockItem:
        def __init__(self, position, run_id, dec_id, cand_id, content_type, rel_at):
            self.position = position
            self.selection_run_id = run_id
            self.selection_decision_id = dec_id
            self.topic_candidate_id = cand_id
            self.target_content_type = content_type
            self.planned_release_at = rel_at

    class MockCampaign:
        def __init__(self, ch_id, dna_id, title, objective, priority, items, checksum):
            self.channel_id = ch_id
            self.channel_dna_revision_id = dna_id
            self.title = title
            self.objective = objective
            self.priority = priority
            self.items = items
            self.plan_checksum = checksum

    channel_id = uuid4()
    dna_id = uuid4()
    run1, run2 = uuid4(), uuid4()
    dec1, dec2 = uuid4(), uuid4()
    cand1, cand2 = uuid4(), uuid4()

    items_raw = [
        {
            "position": 1,
            "selection_run_id": run1,
            "selection_decision_id": dec1,
            "topic_candidate_id": cand1,
            "target_content_type": "YOUTUBE_LONGFORM",
            "planned_release_at": "2026-09-20T10:00:00+00:00",
        },
        {
            "position": 2,
            "selection_run_id": run2,
            "selection_decision_id": dec2,
            "topic_candidate_id": cand2,
            "target_content_type": "YOUTUBE_SHORT",
            "planned_release_at": "2026-09-21T10:00:00+00:00",
        },
    ]

    expected_sum = compute_campaign_plan_checksum(
        channel_id=channel_id,
        channel_dna_revision_id=dna_id,
        title="Persisted Campaign",
        objective="Reproducibility check",
        priority=2,
        items=items_raw,
    )

    # Simulated persisted campaign where items might be out of order in memory
    mock_items = [
        MockItem(2, run2, dec2, cand2, "YOUTUBE_SHORT", datetime.datetime(2026, 9, 21, 10, 0, 0, tzinfo=datetime.UTC)),
        MockItem(1, run1, dec1, cand1, "YOUTUBE_LONGFORM", datetime.datetime(2026, 9, 20, 10, 0, 0, tzinfo=datetime.UTC)),
    ]
    mock_campaign = MockCampaign(
        ch_id=channel_id,
        dna_id=dna_id,
        title="Persisted Campaign",
        objective="Reproducibility check",
        priority=2,
        items=mock_items,
        checksum=expected_sum,
    )

    recomputed = recompute_persisted_campaign_plan_checksum(mock_campaign)
    assert recomputed == expected_sum
