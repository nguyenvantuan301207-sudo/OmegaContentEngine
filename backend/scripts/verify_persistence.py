"""Persistence Verification Script for OMEGA-007 Production Engine.

Stage 1 (--populate): Creates Channel, Script, ProductionRequest, renders v1 and v2, records hashes & URIs.
Stage 2 (--verify): Verifies database records, disk files, hashes, ffprobe, streaming, and current flags after restart.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from pathlib import Path

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from omega.application.media_probe import MediaProbe
from omega.application.media_storage import LocalMediaStorageProvider, compute_sha256
from omega.infrastructure.models import (
    MediaArtifact,
    ProductionRequest,
    RenderPlan,
)

METADATA_FILE = Path("/app/data/persistence_test_meta.json")
DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql+asyncpg://postgres:postgres@omega-postgres:5432/omega"
)
BASE_API = "http://127.0.0.1:8000"


async def populate():
    """Populate channel, topic, script, render v1 and v2, and record state."""
    async with httpx.AsyncClient(base_url=BASE_API, timeout=60.0) as client:
        # 1. Channel
        slug = f"persist-{uuid.uuid4().hex[:6]}"
        c_res = await client.post(
            "/api/v1/channels",
            json={
                "name": "Persistence Channel",
                "slug": slug,
                "platform": "YOUTUBE",
                "dna": {"brand_voice": {"tone": ["AUTHORITATIVE"]}},
            },
        )
        assert c_res.status_code == 201, f"Channel create failed: {c_res.text}"
        channel_id = c_res.json()["id"]
        await client.post(f"/api/v1/channels/{channel_id}/activate")

        # 2. Topic
        cand_res = await client.post(
            f"/api/v1/channels/{channel_id}/topics/candidates",
            json={"title": "Quantum Persistence Test", "summary": "Persistence test"},
        )
        cand_id = cand_res.json()["id"]
        await client.post(
            f"/api/v1/channels/{channel_id}/topics/candidates/{cand_id}/evaluate",
            json={"mode": "INTERACTIVE"},
        )
        await client.post(f"/api/v1/channels/{channel_id}/topics/candidates/{cand_id}/select")

        # 3. Research Brief
        r_res = await client.post(
            f"/api/v1/channels/{channel_id}/research",
            json={"topic_candidate_id": cand_id},
        )
        r_req_id = r_res.json()["id"]
        s_res = await client.post(
            f"/api/v1/channels/{channel_id}/research/{r_req_id}/sources",
            json={
                "source_type": "MANUAL",
                "url": "https://example.com/persist",
                "title": "Quantum Storage",
                "publisher": "Quantum Labs",
                "content_excerpt": "Quantum storage retains information indefinitely.",
                "primary_source_status": "CONFIRMED",
            },
        )
        src_id = s_res.json()["id"]
        cl_res = await client.post(
            f"/api/v1/channels/{channel_id}/research/{r_req_id}/claims",
            json={"claim_text": "Quantum storage retains information indefinitely."},
        )
        claim_id = cl_res.json()["id"]
        await client.post(
            f"/api/v1/channels/{channel_id}/research/{r_req_id}/claims/{claim_id}/evidence",
            json={
                "source_id": src_id,
                "support_direction": "SUPPORTS",
                "excerpt": "Quantum storage retains information indefinitely.",
                "strength_score": 90.0,
            },
        )
        run_res = await client.post(f"/api/v1/channels/{channel_id}/research/{r_req_id}/run")
        brief_id = run_res.json()["id"]

        # 4. Content Request & Script
        cnt_res = await client.post(
            f"/api/v1/channels/{channel_id}/content",
            json={"topic_candidate_id": cand_id, "research_brief_id": brief_id},
        )
        cnt_id = cnt_res.json()["id"]
        gen_res = await client.post(
            f"/api/v1/channels/{channel_id}/content/{cnt_id}/generate",
            json={"idempotency_key": f"gen_{uuid.uuid4().hex}"},
        )
        script_id = gen_res.json()["id"]

        # 5. Production Request & Prepare
        prod_res = await client.post(
            f"/api/v1/channels/{channel_id}/production",
            json={"script_version_id": script_id},
        )
        assert prod_res.status_code == 201
        prod_req_id = prod_res.json()["id"]

        prep_res = await client.post(
            f"/api/v1/channels/{channel_id}/production/{prod_req_id}/prepare"
        )
        assert prep_res.status_code == 200

        # 6. Render v1
        r1 = await client.post(
            f"/api/v1/channels/{channel_id}/production/{prod_req_id}/render",
            json={"idempotency_key": f"key_v1_{uuid.uuid4().hex}"},
        )
        assert r1.status_code == 200, f"Render v1 failed: {r1.text}"

        # 7. Rerender v2
        r2 = await client.post(
            f"/api/v1/channels/{channel_id}/production/{prod_req_id}/rerender",
            json={"idempotency_key": f"key_v2_{uuid.uuid4().hex}"},
        )
        assert r2.status_code == 200, f"Rerender v2 failed: {r2.text}"

        # 8. List Artifacts and Record State
        arts_res = await client.get(
            f"/api/v1/channels/{channel_id}/production/{prod_req_id}/artifacts"
        )
        artifacts = arts_res.json()
        assert len(artifacts) == 2, f"Expected 2 artifacts, got {len(artifacts)}"

        storage = LocalMediaStorageProvider()
        meta_payload = {
            "channel_id": channel_id,
            "production_request_id": prod_req_id,
            "artifacts": [],
        }

        for art in artifacts:
            resolved_path = storage.resolve_stored_uri(
                uuid.UUID(channel_id), uuid.UUID(prod_req_id), art["storage_uri"]
            )
            sha256_disk = compute_sha256(resolved_path)
            meta_payload["artifacts"].append(
                {
                    "id": art["id"],
                    "version": art["version"],
                    "is_current": art["is_current"],
                    "storage_uri": art["storage_uri"],
                    "disk_path": str(resolved_path),
                    "db_content_hash": art["content_hash"],
                    "calculated_hash": sha256_disk,
                    "file_size_bytes": art["file_size_bytes"],
                }
            )

        METADATA_FILE.write_text(json.dumps(meta_payload, indent=2), encoding="utf-8")
        print(f"STAGE 1 COMPLETE: Saved metadata to {METADATA_FILE}")
        print(json.dumps(meta_payload, indent=2))


async def verify():
    """Verify state from recorded metadata after container restart."""
    assert METADATA_FILE.exists(), f"Metadata file {METADATA_FILE} not found!"
    meta = json.loads(METADATA_FILE.read_text(encoding="utf-8"))

    channel_id = meta["channel_id"]
    prod_req_id = meta["production_request_id"]
    print(f"VERIFYING PERSISTENCE FOR CHANNEL {channel_id}, REQUEST {prod_req_id}")

    # 1. Verify Database Records
    engine = create_async_engine(DATABASE_URL)
    session_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with session_factory() as session:
        # Check ProductionRequest
        req_res = await session.execute(
            select(ProductionRequest).where(
                ProductionRequest.id == uuid.UUID(prod_req_id),
                ProductionRequest.channel_id == uuid.UUID(channel_id),
            )
        )
        req = req_res.scalar_one_or_none()
        assert req is not None, "ProductionRequest missing from DB!"
        print("✔ ProductionRequest DB record verified")

        # Check RenderPlans (v1 and v2)
        plans_res = await session.execute(
            select(RenderPlan).where(RenderPlan.production_request_id == uuid.UUID(prod_req_id))
        )
        plans = plans_res.scalars().all()
        assert len(plans) == 2, f"Expected 2 RenderPlans in DB, found {len(plans)}"
        print("✔ RenderPlans v1 and v2 DB records verified")

        # Check MediaArtifacts (v1 and v2)
        arts_res = await session.execute(
            select(MediaArtifact)
            .where(MediaArtifact.production_request_id == uuid.UUID(prod_req_id))
            .order_by(MediaArtifact.version.asc())
        )
        db_artifacts = arts_res.scalars().all()
        assert len(db_artifacts) == 2, f"Expected 2 MediaArtifacts, found {len(db_artifacts)}"

        current_count = sum(1 for a in db_artifacts if a.is_current and a.artifact_type == "VIDEO")
        assert current_count == 1, (
            f"Expected exactly 1 current VIDEO artifact, found {current_count}"
        )
        print("✔ Exactly 1 current VIDEO MediaArtifact verified in DB")

    await engine.dispose()

    # 2. Verify Physical Media Files on Disk
    probe = MediaProbe()

    for expected_art in meta["artifacts"]:
        disk_path = Path(expected_art["disk_path"])
        assert disk_path.exists(), f"Physical file {disk_path} missing from disk!"
        assert disk_path.stat().st_size == expected_art["file_size_bytes"], "File size mismatch!"

        # Hash Check
        current_sha256 = compute_sha256(disk_path)
        assert current_sha256 == expected_art["calculated_hash"], "SHA-256 hash mismatch!"
        assert current_sha256 == expected_art["db_content_hash"], "DB content hash mismatch!"

        # ffprobe Validation
        probe_summary = await probe.probe_file(disk_path)
        assert probe_summary["has_video"] is True, "ffprobe reported no video stream!"
        assert probe_summary["has_audio"] is True, "ffprobe reported no audio stream!"
        assert probe_summary["width"] == 1920, f"Expected width 1920, got {probe_summary['width']}"
        assert probe_summary["height"] == 1080, (
            f"Expected height 1080, got {probe_summary['height']}"
        )

        print(
            f"✔ Artifact v{expected_art['version']} (current={expected_art['is_current']}): File exists, SHA-256 matches, ffprobe valid ({probe_summary['duration_ms']}ms)"
        )

    # 3. Verify Media API Streaming & Range 206
    async with httpx.AsyncClient(base_url=BASE_API, timeout=30.0) as client:
        for art in meta["artifacts"]:
            art_id = art["id"]
            # Full 200 OK
            stream_res = await client.get(
                f"/api/v1/channels/{channel_id}/production/{prod_req_id}/artifacts/{art_id}/media"
            )
            assert stream_res.status_code == 200, f"Streaming failed: {stream_res.status_code}"
            assert stream_res.headers.get("content-type") == "video/mp4"
            assert stream_res.headers.get("accept-ranges") == "bytes"

            # Partial 206 Range
            range_res = await client.get(
                f"/api/v1/channels/{channel_id}/production/{prod_req_id}/artifacts/{art_id}/media",
                headers={"Range": "bytes=0-99"},
            )
            assert range_res.status_code == 206, f"Range request failed: {range_res.status_code}"
            assert len(range_res.content) == 100
            print(f"✔ Artifact {art_id} HTTP 200 and HTTP 206 Range streaming verified")

    print("\n====================================================")
    print("ALL PERSISTENCE VERIFICATION GATES PASSED CLEANLY!")
    print("====================================================")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--populate", action="store_true")
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()

    if args.populate:
        asyncio.run(populate())
    elif args.verify:
        asyncio.run(verify())
    else:
        print("Usage: python scripts/verify_persistence.py [--populate | --verify]")
        sys.exit(1)
