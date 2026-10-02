"""Full isolated M4 rehearsal and M4-to-M7 integration chain.
Runs on isolated PostgreSQL database sim_rotation using synthetic keys.
"""

import asyncio
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from uuid import UUID, uuid4
from cryptography.fernet import Fernet
import sqlalchemy as sa
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from omega.application.disposition_service import (
    DirtyDataDispositionService,
    DispositionStatus,
)
from omega.application.vault_key_rotation import VaultKeyRotationService
from omega.domain.mission import MissionState
from omega.domain.publisher import PlatformAccountStatus, PublishIntentState
from omega.domain.task import TaskState
from omega.infrastructure.models import (
    Base,
    Channel,
    ChannelDNARevision,
    ContentGenerationRequest,
    CredentialVault,
    DecisionLog,
    MediaArtifact,
    Mission,
    MissionExecution,
    PlatformAccount,
    ProductionRequest,
    PublishIntent,
    PublishIntentTransition,
    ResearchBrief,
    ResearchRequest,
    ScriptVersion,
    Task,
    TopicCandidate,
)
from omega.infrastructure.vault import CredentialVaultService

ISOLATED_DB_URL = "postgresql+asyncpg://omega:omega_isolated_pw@p20c-postgres:5432/sim_rotation"


async def run_chain():
    print("=" * 80)
    print("STARTING FULL ISOLATED M4 REHEARSAL AND M4-TO-M7 CHAIN")
    print("=" * 80)

    engine = create_async_engine(ISOLATED_DB_URL, echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    # 1. Setup isolated tables
    async with engine.begin() as conn:
        await conn.execute(
            sa.text("TRUNCATE TABLE publish_intent_transitions, decision_logs, publish_intents, media_artifacts, production_requests, script_versions, content_generation_requests, research_briefs, research_requests, topic_candidates, channel_dna_revisions, tasks, mission_executions, missions, credential_vault, platform_accounts, channels CASCADE;")
        )
    print("Database tables truncated.")

    # 2. Setup synthetic keys
    synth_k1 = Fernet.generate_key().decode()
    synth_k2 = Fernet.generate_key().decode()
    synth_k3 = Fernet.generate_key().decode()
    f1, f2 = Fernet(synth_k1.encode()), Fernet(synth_k2.encode())

    # 3. Seed exact population:
    # 20 representative legitimate rows (10 v1, 10 v2) representing the 1630 population
    # 125 exact dirty rows matching manifest structure (39 corrupt, 2 v999, 84 ephemeral)
    manifest_items = []
    legit_vault_ids = []
    now = datetime.now(timezone.utc)

    channels = []
    accounts = []
    creds = []
    missions = []
    tasks = []
    intents = []

    # Legitimate rows
    for i in range(10):
        cid, aid, vid = uuid4(), uuid4(), uuid4()
        legit_vault_ids.append(vid)
        channels.append(Channel(id=cid, name=f"Legit Ch v1 {i}", slug=f"legit-v1-{i}-{uuid4().hex[:6]}", platform="YOUTUBE", primary_language="en", target_region="US", timezone="UTC"))
        accounts.append(PlatformAccount(id=aid, channel_id=cid, platform="YOUTUBE", account_display_name=f"Legit v1 {i}", external_account_id=f"ext_legit_v1_{i}", status=PlatformAccountStatus.ACTIVE.value))
        creds.append(CredentialVault(id=vid, platform_account_id=aid, encrypted_access_token=f1.encrypt(b"token_v1").decode(), encrypted_refresh_token=f1.encrypt(b"ref_v1").decode(), access_token_expires_at=now+timedelta(hours=2), key_version=1, updated_at=now))

    for i in range(10):
        cid, aid, vid = uuid4(), uuid4(), uuid4()
        legit_vault_ids.append(vid)
        channels.append(Channel(id=cid, name=f"Legit Ch v2 {i}", slug=f"legit-v2-{i}-{uuid4().hex[:6]}", platform="YOUTUBE", primary_language="en", target_region="US", timezone="UTC"))
        accounts.append(PlatformAccount(id=aid, channel_id=cid, platform="YOUTUBE", account_display_name=f"Legit v2 {i}", external_account_id=f"ext_legit_v2_{i}", status=PlatformAccountStatus.ACTIVE.value))
        creds.append(CredentialVault(id=vid, platform_account_id=aid, encrypted_access_token=f2.encrypt(b"token_v2").decode(), encrypted_refresh_token=f2.encrypt(b"ref_v2").decode(), access_token_expires_at=now+timedelta(hours=2), key_version=2, updated_at=now))

    # Shared artifact ancestry
    dna_rev = ChannelDNARevision(id=uuid4(), channel_id=channels[0].id, version=1, snapshot={"name": "DNA"}, change_reason="Initial")
    topic = TopicCandidate(id=uuid4(), channel_id=channels[0].id, title="Test Topic", normalized_title="test topic", source_name="Manual Entry", summary="Test topic summary", topic_fingerprint=uuid4().hex, status="APPROVED")
    r_req = ResearchRequest(id=uuid4(), channel_id=channels[0].id, topic_candidate_id=topic.id, status="COMPLETED")
    brief = ResearchBrief(id=uuid4(), research_request_id=r_req.id, channel_id=channels[0].id, topic_candidate_id=topic.id, title="Brief Title", summary="Brief summary")
    content_req = ContentGenerationRequest(id=uuid4(), channel_id=channels[0].id, topic_candidate_id=topic.id, research_brief_id=brief.id, channel_dna_revision_id=dna_rev.id, status="APPROVED")
    script_ver = ScriptVersion(id=uuid4(), content_request_id=content_req.id, version=1, title="Script v1", hook_text="Hook", closing_text="Close", cta_text="CTA")
    prod_req = ProductionRequest(id=uuid4(), channel_id=channels[0].id, script_version_id=script_ver.id, content_request_id=content_req.id, channel_dna_revision_id=dna_rev.id, status="APPROVED")
    shared_artifact = MediaArtifact(
        id=uuid4(),
        production_request_id=prod_req.id,
        artifact_type="VIDEO",
        version=1,
        is_current=True,
        storage_uri="videos/test_render.mp4",
        file_size_bytes=2048,
        content_hash="a" * 64,
        mime_type="video/mp4",
    )

    # 39 CORRUPT_CIPHERTEXT (38 APPROVED, 1 DRAFT)
    for i in range(39):
        cid, aid, vid, mid, tid, iid = uuid4(), uuid4(), uuid4(), uuid4(), uuid4(), uuid4()
        st = "DRAFT" if i == 0 else "APPROVED"
        channels.append(Channel(id=cid, name=f"Corrupt Ch {i}", slug=f"corrupt-{i}-{uuid4().hex[:6]}", platform="YOUTUBE", primary_language="en", target_region="US", timezone="UTC"))
        accounts.append(PlatformAccount(id=aid, channel_id=cid, platform="YOUTUBE", account_display_name=f"Corrupt Acc {i}", external_account_id=f"ext_corrupt_{i}", status=PlatformAccountStatus.ACTIVE.value))
        creds.append(CredentialVault(id=vid, platform_account_id=aid, encrypted_access_token="corrupt_ciphertext_bytes", encrypted_refresh_token="corrupt_ciphertext_bytes", access_token_expires_at=now+timedelta(hours=2), key_version=1 if i < 20 else 2, updated_at=now))
        missions.append(Mission(id=mid, title=f"Corrupt Mission {i}", objective="Test", state=MissionState.RUNNING.value))
        tasks.append(Task(id=tid, mission_id=mid, task_type="PUBLISH", title=f"Corrupt Task {i}", state=TaskState.READY.value))
        intents.append(PublishIntent(
            id=iid,
            mission_id=mid,
            task_id=tid,
            channel_id=cid,
            platform_account_id=aid,
            media_artifact_id=shared_artifact.id,
            media_artifact_checksum=shared_artifact.content_hash,
            state=st,
            title=f"Corrupt Intent {i}",
            description="Test",
            made_for_kids=False,
            intent_checksum="0"*64,
        ))
        manifest_items.append({
            "credential_vault_id": str(vid),
            "platform_account_id": str(aid),
            "key_version": 1 if i < 20 else 2,
            "anomaly_class": "CORRUPT_CIPHERTEXT",
            "decryptability_status": "UNDECRYPTABLE_CORRUPT",
            "platform_account_status": "ACTIVE",
            "reference_classification": "ACTIVE_OPERATIONAL_REFERENCE_EXISTS",
            "recommended_disposition": "REVOKE_AND_CANCEL",
            "publish_intents": [{"intent_id": str(iid), "state": st}],
            "tasks": [{"task_id": str(tid), "state": "READY"}],
            "missions": [{"mission_id": str(mid), "state": "RUNNING"}],
            "excluded_from_rotation": True,
            "human_authorization_required": True,
        })

    # 2 MISLABELLED v999 (APPROVED)
    for i in range(2):
        cid, aid, vid, mid, tid, iid = uuid4(), uuid4(), uuid4(), uuid4(), uuid4(), uuid4()
        channels.append(Channel(id=cid, name=f"v999 Ch {i}", slug=f"v999-{i}-{uuid4().hex[:6]}", platform="YOUTUBE", primary_language="en", target_region="US", timezone="UTC"))
        accounts.append(PlatformAccount(id=aid, channel_id=cid, platform="YOUTUBE", account_display_name=f"v999 Acc {i}", external_account_id=f"ext_v999_{i}", status=PlatformAccountStatus.ACTIVE.value))
        creds.append(CredentialVault(id=vid, platform_account_id=aid, encrypted_access_token=f1.encrypt(b"token_v999").decode(), encrypted_refresh_token=f1.encrypt(b"ref_v999").decode(), access_token_expires_at=now+timedelta(hours=2), key_version=999, updated_at=now))
        missions.append(Mission(id=mid, title=f"v999 Mission {i}", objective="Test", state=MissionState.RUNNING.value))
        tasks.append(Task(id=tid, mission_id=mid, task_type="PUBLISH", title=f"v999 Task {i}", state=TaskState.READY.value))
        intents.append(PublishIntent(
            id=iid,
            mission_id=mid,
            task_id=tid,
            channel_id=cid,
            platform_account_id=aid,
            media_artifact_id=shared_artifact.id,
            media_artifact_checksum=shared_artifact.content_hash,
            state="APPROVED",
            title=f"v999 Intent {i}",
            description="Test",
            made_for_kids=False,
            intent_checksum="0"*64,
        ))
        manifest_items.append({
            "credential_vault_id": str(vid),
            "platform_account_id": str(aid),
            "key_version": 999,
            "anomaly_class": "MISLABELLED_KEY_VERSION_TEST_ARTIFACT",
            "decryptability_status": "LEGITIMATE_TEST_FIXTURE",
            "platform_account_status": "ACTIVE",
            "reference_classification": "ACTIVE_OPERATIONAL_REFERENCE_EXISTS",
            "recommended_disposition": "REVOKE_AND_CANCEL",
            "publish_intents": [{"intent_id": str(iid), "state": "APPROVED"}],
            "tasks": [{"task_id": str(tid), "state": "READY"}],
            "missions": [{"mission_id": str(mid), "state": "RUNNING"}],
            "excluded_from_rotation": True,
            "human_authorization_required": True,
        })

    # 84 UNKNOWN_EPHEMERAL_KEY (zero references)
    for i in range(84):
        cid, aid, vid = uuid4(), uuid4(), uuid4()
        channels.append(Channel(id=cid, name=f"Eph Ch {i}", slug=f"eph-{i}-{uuid4().hex[:6]}", platform="YOUTUBE", primary_language="en", target_region="US", timezone="UTC"))
        accounts.append(PlatformAccount(id=aid, channel_id=cid, platform="YOUTUBE", account_display_name=f"Account {uuid4().hex[:8]}", external_account_id=f"ext_eph_{i}", status=PlatformAccountStatus.ACTIVE.value))
        creds.append(CredentialVault(id=vid, platform_account_id=aid, encrypted_access_token="eph_ciphertext", encrypted_refresh_token="eph_ciphertext", access_token_expires_at=now+timedelta(hours=2), key_version=1 if i < 40 else 2, updated_at=now))
        manifest_items.append({
            "credential_vault_id": str(vid),
            "platform_account_id": str(aid),
            "key_version": 1 if i < 40 else 2,
            "anomaly_class": "UNKNOWN_EPHEMERAL_KEY",
            "decryptability_status": "UNDECRYPTABLE_UNKNOWN_KEY",
            "platform_account_status": "ACTIVE",
            "reference_classification": "ZERO_OPERATIONAL_REFERENCES",
            "recommended_disposition": "REVOKE_AND_RETAIN",
            "publish_intents": [],
            "tasks": [],
            "missions": [],
            "excluded_from_rotation": True,
            "human_authorization_required": True,
        })

    async with session_factory() as session:
        session.add_all(channels); await session.flush()
        session.add_all(accounts); await session.flush()
        session.add_all(creds); await session.flush()
        session.add_all(missions); await session.flush()
        session.add_all(tasks); await session.flush()
        session.add_all([dna_rev, topic, r_req, brief, content_req, script_ver, prod_req, shared_artifact]); await session.flush()
        session.add_all(intents); await session.commit()

    print(f"Seeded 20 legitimate credentials and 125 dirty items (Total = 145).")

    # Save synthetic manifest
    manifest_path = Path("/tmp/sim_dirty_manifest.json")
    manifest_bytes = json.dumps(manifest_items, indent=2).encode("utf-8")
    manifest_path.write_bytes(manifest_bytes)
    import hashlib
    manifest_hash = hashlib.sha256(manifest_bytes).hexdigest()
    print(f"Saved manifest to {manifest_path} with hash {manifest_hash}")

    # =========================================================================
    # PHASE M4: DISPOSITION
    # =========================================================================
    disp_svc = DirtyDataDispositionService(session_factory=session_factory)

    # M4.1: Dry-Run
    print("\n--- [M4.1] RUNNING DISPOSITION DRY-RUN ---")
    rep_dry = await disp_svc.execute_disposition(
        manifest_path=manifest_path,
        expected_sha256=manifest_hash,
        execute=False,
    )
    assert rep_dry.status == DispositionStatus.PLANNED
    assert rep_dry.accounts_targeted == 125
    assert rep_dry.intents_targeted == 41
    print("M4.1 Dry-Run passed: Status PLANNED, zero mutations committed.")

    # M4.2: Execution
    print("\n--- [M4.2] RUNNING ATOMIC DISPOSITION EXECUTE ---")
    rep_exec = await disp_svc.execute_disposition(
        manifest_path=manifest_path,
        expected_sha256=manifest_hash,
        execute=True,
    )
    assert rep_exec.status == DispositionStatus.EXECUTED
    assert rep_exec.accounts_revoked == 125
    assert rep_exec.intents_cancelled == 41
    assert rep_exec.tasks_cancelled == 41
    assert rep_exec.missions_cancelled == 41
    assert rep_exec.audit_records_emitted == 82
    print("M4.2 Execute passed: Status EXECUTED, 125 accounts revoked, 41 workflows cancelled.")

    # M4.3: State Verification
    async with session_factory() as session:
        # Check all 125 accounts are REVOKED
        res_pa = await session.execute(
            select(func.count(PlatformAccount.id)).where(PlatformAccount.status == PlatformAccountStatus.REVOKED.value)
        )
        assert res_pa.scalar_one() == 125, "Expected exactly 125 accounts in REVOKED state"

        # Check all 20 legitimate accounts are still ACTIVE
        res_pa_legit = await session.execute(
            select(func.count(PlatformAccount.id)).where(PlatformAccount.status == PlatformAccountStatus.ACTIVE.value)
        )
        assert res_pa_legit.scalar_one() == 20, "Expected all 20 legitimate accounts to remain ACTIVE"

        # Check all 41 intents are CANCELLED
        res_pi = await session.execute(
            select(func.count(PublishIntent.id)).where(PublishIntent.state == PublishIntentState.CANCELLED.value)
        )
        assert res_pi.scalar_one() == 41, "Expected all 41 intents CANCELLED"

        # Check all 145 CredentialVault rows are still present (ZERO DELETES)
        res_cv = await session.execute(select(func.count(CredentialVault.id)))
        assert res_cv.scalar_one() == 145, "Expected all 145 CredentialVault rows retained"

        # Check v999 key_version is still 999
        res_v999 = await session.execute(
            select(func.count(CredentialVault.id)).where(CredentialVault.key_version == 999)
        )
        assert res_v999.scalar_one() == 2, "Expected 2 rows with key_version=999 unchanged"

    print("M4.3 State Verification passed: 100% of dirty records in terminal state, legitimate untouched.")
    print(">>> FULL_M4_ISOLATED_REHEARSAL_PASS = YES")

    # =========================================================================
    # PHASE M5-M7: ROTATION CHAIN INTEGRATION
    # =========================================================================
    print("\n--- [M5-M7] RUNNING ROTATION INTEGRATION WITH EXCLUSION CONTRACT ---")

    # M5: Keyring configuration
    keyring = {1: synth_k1, 2: synth_k2, 3: synth_k3}
    vault = CredentialVaultService(keyring=keyring, active_version=3)
    rot_service = VaultKeyRotationService(session_factory=session_factory, vault=vault)

    # Excluded vault IDs
    excluded_ids = {UUID(item["credential_vault_id"]) for item in manifest_items}
    assert len(excluded_ids) == 125

    # M6: Rotate candidates with exclusions applied
    rep_rot = await rot_service.rotate_keys(
        target_version=3,
        execute=True,
        excluded_vault_ids=excluded_ids,
    )
    print(f"Rotation Report: evaluated={rep_rot.total_evaluated}, rotated={rep_rot.rotated_count}, failed={rep_rot.failed_count}")
    assert rep_rot.total_evaluated == 20, f"Expected 20 evaluated, got {rep_rot.total_evaluated}"
    assert rep_rot.rotated_count == 20, f"Expected 20 rotated, got {rep_rot.rotated_count}"
    assert rep_rot.failed_count == 0, f"Expected 0 failed, got {rep_rot.failed_count}"

    # M7: Verify rotated candidates
    rep_ver = await rot_service.verify_keys(
        target_version=3,
        excluded_vault_ids=excluded_ids,
    )
    print(f"Verification Report: evaluated={rep_ver.total_evaluated}, verified={rep_ver.already_current_count}, failed={rep_ver.failed_count}")
    assert rep_ver.total_evaluated == 20
    assert rep_ver.already_current_count == 20
    assert rep_ver.failed_count == 0

    # Ensure dirty rows were NOT rotated
    async with session_factory() as session:
        res_v3 = await session.execute(
            select(func.count(CredentialVault.id)).where(CredentialVault.key_version == 3)
        )
        assert res_v3.scalar_one() == 20, "Expected exactly 20 rows rotated to v3"

        res_dirty_unrotated = await session.execute(
            select(func.count(CredentialVault.id)).where(CredentialVault.id.in_(list(excluded_ids)))
        )
        assert res_dirty_unrotated.scalar_one() == 125, "Expected all 125 dirty rows retained"

        # Check v999 was not rotated
        res_v999_post = await session.execute(
            select(func.count(CredentialVault.id)).where(CredentialVault.key_version == 999)
        )
        assert res_v999_post.scalar_one() == 2, "Expected 2 v999 rows untouched"

    print("M5-M7 Integration passed: Legitimate rows rotated to v3, dirty rows completely untouched.")
    print(">>> M4_TO_M7_ISOLATED_CHAIN_PASS = YES")

    # M4.4: Idempotent Rerun
    print("\n--- [M4.4] IDEMPOTENT RERUN VERIFICATION ---")
    rep_rerun = await disp_svc.execute_disposition(
        manifest_path=manifest_path,
        expected_sha256=manifest_hash,
        execute=True,
    )
    assert rep_rerun.status == DispositionStatus.ALREADY_COMPLETED
    print("M4.4 Idempotent rerun passed: Status ALREADY_COMPLETED, zero mutations.")

    await engine.dispose()
    print("\n" + "=" * 80)
    print("FULL ISOLATED M4 AND M4-TO-M7 REHEARSAL SUCCESSFUL")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(run_chain())
