"""P0 regressions: pure planning, mocked acquisition and canonical QA only."""

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from omega.application.beat_asset_executor import resolve_provider_asset
from omega.application.content_provider import TemplateContentProvider
from omega.application.content_qa import run_content_qa_checks
from omega.application.creative_semantic_qa import semantic_production_findings
from omega.application.mechanism_diagram import resolve_mechanism_diagram_spec
from omega.application.production_qa import ProductionQAEngine
from omega.application.script_meta_guard import script_meta_evidence
from omega.application.semantic_asset_query import (
    contains_phrase,
    derive_semantic_asset_query,
    validate_provider_semantics,
    validate_semantic_query,
)
from omega.application.storyboard_engine import StoryboardEngine, StoryboardScene, VisualStrategy
from omega.application.template_payload_resolver import (
    TemplatePayloadResolver,
    can_resolve_diagram_payload,
)
from omega.application.visual_asset_engine import (
    ResolvedVisualAsset,
    VisualAssetCandidate,
    VisualAssetEngine,
    VisualAssetRequest,
)
from omega.application.visual_asset_orchestrator import VisualAssetOrchestrator
from omega.application.visual_direction import VisualAssetKind, VisualDirector, VisualTemplateId
from omega.domain.production import LicenseStatus, ProductionQASeverity, ProductionQAStatus

TOPIC = "Why Concrete Cracks: 5 Mechanisms Every Civil Engineer Should Understand"


@pytest.mark.parametrize(
    "text,term",
    [
        ("engineer", "engine"),
        ("rapid", "api"),
        ("capital", "api"),
        ("networking", "network"),
        ("rediscover", "redis"),
        ("serverless", "server"),
        ("manuscript", "script"),
        ("decode", "code"),
        ("asynchronous", "async"),
    ],
)
def test_short_keyword_boundaries(text, term):
    assert not contains_phrase(text, term)
    query = derive_semantic_asset_query(text)
    assert not {"software", "computer", "datacenter", "network"}.intersection(query.split())


@pytest.mark.parametrize(
    "text,term",
    [
        ("(API),", "api"),
        ("DATA-CENTER", "data center"),
        ("ＰＹＴＨＯＮ", "python"),
        ("Érosion—béton", "érosion béton"),
    ],
)
def test_punctuation_unicode_and_phrases(text, term):
    assert contains_phrase(text, term)


@pytest.mark.parametrize(
    "text",
    [
        "Capture attention by highlighting the critical question",
        "Progressively unpack the core mechanism",
        "Deliver the empirical resolution",
        "Conclude with a high impact takeaway",
        "Closing",
        "Rapid loss of surface water before concrete sets",
        "Civil engineer",
    ],
)
def test_topic_controls_query_instead_of_rhetoric(text):
    query = derive_semantic_asset_query(text, fallback_topic=text, subject_text=TOPIC)
    assert "concrete" in query and "cracks" in query
    assert not {
        "server",
        "datacenter",
        "computer",
        "software",
        "network",
        "technology",
    }.intersection(query.split())
    assert validate_semantic_query(query, TOPIC + " " + text).valid


def test_other_domains_and_real_software_are_preserved():
    assert "coral" in derive_semantic_asset_query(
        "Closing", subject_text="Coral bleaching and warm seawater"
    )
    assert "galaxy" in derive_semantic_asset_query(
        "Capture attention", subject_text="Galaxy formation"
    )
    assert "python" in derive_semantic_asset_query("Python event loop and coroutines")
    assert "network" in derive_semantic_asset_query("API requests and network switches")


@pytest.mark.parametrize(
    "query,code",
    [
        ("computer technology server datacenter infrastructure", "INVALID_DOMAIN_DRIFT"),
        ("Closing", "RHETORICAL_ONLY_QUERY"),
        ("dancing music performers", "INSUFFICIENT_SUBJECT_GROUNDING"),
        ("concrete cracks", "VALID"),
    ],
)
def test_query_boundary_evidence(query, code):
    decision = validate_semantic_query(query, TOPIC)
    assert decision.code == code
    assert decision.source_anchors


def test_weak_grounding_has_no_invented_software_default():
    assert derive_semantic_asset_query("Closing", default_technical="server technology") == ""
    assert (
        validate_semantic_query("concrete cracks", "Closing").code
        == "INSUFFICIENT_SUBJECT_GROUNDING"
    )


def candidate(title, asset_id="asset"):
    return VisualAssetCandidate(
        provider_id=asset_id,
        kind=VisualAssetKind.IMAGE,
        provider="mock",
        source_url="https://example.test/asset.jpg",
        source_page_url=None,
        mime_type="image/jpeg",
        width=1920,
        height=1080,
        duration_seconds=None,
        license_status=LicenseStatus.LICENSED,
        license_name="Licensed",
        license_url="https://example.test/license",
        attribution_text="Owner",
        metadata={"title": title},
    )


def request(query="concrete cracks"):
    return VisualAssetRequest(
        scene_index=1,
        kind=VisualAssetKind.IMAGE,
        query=query,
        purpose="Subject evidence",
        required=True,
        source_text=TOPIC,
    )


def test_candidate_selector_rejects_mismatch_and_uses_available_alternative():
    bad, good = (
        candidate("Datacenter server racks", "bad"),
        candidate("Concrete shrinkage cracks", "good"),
    )
    assert VisualAssetEngine().select_candidate(request(), [bad]) is None
    assert VisualAssetEngine().select_candidate(request(), [bad, good]) == good


def test_missing_description_is_not_fabricated_and_creator_is_not_subject():
    decision = validate_provider_semantics(
        source_text=TOPIC,
        query="concrete cracks",
        metadata={"creator": "Server Photographer", "search_query": "concrete cracks"},
    )
    assert decision.valid and not decision.evidence


def test_unrelated_nonsoftware_description_and_source_page_are_rejected():
    assert not validate_provider_semantics(
        source_text="Coral reef bleaching",
        query="coral reef",
        metadata={"title": "Industrial power plant machinery"},
    ).valid
    assert not validate_provider_semantics(
        source_text=TOPIC,
        query="concrete cracks",
        metadata={},
        source_page_url="https://example.test/photo/data-center-server-room-123/",
    ).valid


@pytest.mark.asyncio
async def test_query_rejected_before_any_provider_search():
    provider = AsyncMock()
    provider.provider_name = "mock"
    orchestrator = VisualAssetOrchestrator(VisualAssetEngine(), [provider])
    result = await resolve_provider_asset(
        resolver=orchestrator, request=request("server datacenter")
    )
    assert result.fallback_reason_code == "QUERY_SEMANTIC_REJECTED"
    assert result.resolved_asset is None
    provider.search.assert_not_awaited()


@pytest.mark.asyncio
async def test_post_acquisition_mismatch_falls_back_before_materialization():
    asset = ResolvedVisualAsset(
        asset_id="bad",
        kind=VisualAssetKind.IMAGE,
        provider="mock",
        source_url="https://example.test/file.jpg",
        source_page_url="https://example.test/server-racks/",
        local_path=Path("not-read.jpg"),
        mime_type="image/jpeg",
        width=1920,
        height=1080,
        duration_seconds=None,
        content_sha256="a" * 64,
        license_status=LicenseStatus.LICENSED,
        license_name="Licensed",
        license_url="https://example.test/license",
        attribution_text="Owner",
        query="concrete cracks",
        metadata={"title": "Software datacenter"},
    )
    resolver = AsyncMock()
    resolver.resolve.return_value = asset
    result = await resolve_provider_asset(resolver=resolver, request=request())
    assert result.fallback_reason_code == "PROVIDER_SEMANTIC_MISMATCH"
    assert result.resolved_asset is None and result.bound_visual_asset is None


@pytest.mark.parametrize(
    "text",
    [
        "Regarding -> Why Concrete Cracks -> Mechanisms Every Civil Engineer Should Understand",
        "Regarding concrete cracks, practitioners must evaluate the operational process and foundational mechanisms.",
        "Research confirms: Most HCC shows evidence of drying shrinkage",
        "Overview causes Introduction",
    ],
)
def test_generic_diagram_rejected_without_fabricating_nodes(text):
    assert not can_resolve_diagram_payload(text)
    nodes, edges = TemplatePayloadResolver()._extract_diagram(text)
    assert nodes == [] and edges == []
    scene = StoryboardScene(
        sequence_index=1,
        section_id=TOPIC,
        purpose="Explain",
        source_statement_references=[1],
        narration_excerpt=text,
        estimated_duration_seconds=5,
        visual_strategy=VisualStrategy.DIAGRAM,
        visual_brief="Diagram",
    )
    direction = VisualDirector().resolve(scene)
    assert direction.template_id == VisualTemplateId.KINETIC_TEXT


@pytest.mark.parametrize(
    "text",
    [
        "Rapid water loss causes concrete surface cracks.",
        "Because water heats slowly, coastal climates remain moderate.",
        "The Manufacturer ships products to the Distribution Center, which sends them to the Retailer.",
    ],
)
def test_valid_relationship_preserved_and_source_supported(text):
    spec = resolve_mechanism_diagram_spec(text)
    assert spec and len(spec.nodes) >= 2 and spec.edges
    assert all(node.lower() in text.lower() for node in spec.nodes)
    assert can_resolve_diagram_payload(text)


def test_script_depth_cannot_be_satisfied_by_role_placeholders():
    with pytest.raises(ValueError, match="INSUFFICIENT_GROUNDED_SCRIPT_CONTENT"):
        TemplateContentProvider().generate_script(
            TOPIC,
            {},
            {},
            {},
            {"text": "Why does concrete crack?"},
            {
                "sections": [
                    {
                        "title": "Hook",
                        "objective": "Capture attention",
                        "key_points": [
                            "Factual detail for HOOK under QUESTION_ANSWER",
                            f"Core mechanism of {TOPIC}",
                        ],
                    }
                ]
            },
            480,
        )


def test_script_uses_grounded_material_without_duration_padding():
    claim = {
        "claim_id": "claim",
        "text": "Rapid water loss causes concrete surface cracks.",
        "citations": [{"evidence_id": "e", "source_id": "s"}],
    }
    outline = {
        "sections": [{"title": "Concrete shrinkage", "key_points": [], "claim_refs": ["claim"]}]
    }
    provider = TemplateContentProvider()
    outputs = [
        provider.generate_script(
            TOPIC,
            {"id": "brief", "verified_claims": [claim]},
            {},
            {},
            {"text": "Why does concrete crack?"},
            outline,
            n,
        )
        for n in (480, 1320)
    ]
    assert outputs[0]["estimated_word_count"] == outputs[1]["estimated_word_count"]
    statement = outputs[0]["sections"][0]["statements"][0]
    assert statement["statement_text"] == claim["text"]
    assert statement["citations"][0]["claim_id"] == "claim"
    assert not script_meta_evidence(outputs[0])


def test_final_meta_guard_detects_family_and_repeated_paragraph_structure():
    text = "Our chapter explores the primary principles for disciplined analysis of the topic."
    script = {
        "hook_text": "Question?",
        "sections": [{"statements": [{"statement_text": text} for _ in range(3)]}],
    }
    status, findings = run_content_qa_checks(script, 480, {}, {})
    assert any(
        f["rule_code"] == "SCRIPT_META_CONTENT" and f["severity"] == "BLOCKING" for f in findings
    )
    assert script_meta_evidence(script)


def canonical_qa(script, scenes):
    return ProductionQAEngine().evaluate(
        request_data={"script_version_id": "script", "channel_dna_revision_id": "dna"},
        script_version_data={"id": "script", **script},
        content_request_data={"channel_dna_revision_id": "dna"},
        assets_data=[],
        requirements_data=[],
        narration_segments=[],
        subtitle_cues=[],
        media_probe_summary=None,
        artifact_file_path=None,
        expected_hash=None,
        scenes_data=scenes,
    )


def test_canonical_provider_semantics_block_even_with_alternating_strategies():
    scenes = [
        {
            "sequence_index": i,
            "effective_strategy": "BROLL" if i % 2 else "TITLE_MOTION",
            "visual_origin": "PROVIDER",
            "query": "computer technology server datacenter infrastructure",
            "provider_asset_id": "same",
            "source_page_url": "https://example.test/server-racks/",
            "narration_text": "Rapid water loss causes concrete cracking.",
        }
        for i in range(1, 17)
    ]
    status, findings = canonical_qa({"title": TOPIC}, scenes)
    codes = {f.rule_code.value for f in findings}
    assert status == ProductionQAStatus.BLOCKED
    assert {
        "PROVIDER_QUERY_SEMANTIC_DRIFT",
        "PROVIDER_ASSET_SEMANTIC_MISMATCH",
        "PROVIDER_QUERY_EXCESSIVE_REPETITION",
    } <= codes
    assert "VISUAL_REPETITION" not in codes
    assert all(
        f.severity == ProductionQASeverity.BLOCKING
        for f in findings
        if f.rule_code.value.startswith("PROVIDER_")
    )


def test_query_family_detects_forensic_34_beat_pattern():
    queries = ["server datacenter", "computer technology", "software review", "network cables"]
    snapshot = {
        "scenes": [
            {"sequence_index": i, "narration_text": "Concrete shrinkage"} for i in range(1, 18)
        ],
        "visual_beats": [
            {
                "parent_scene_index": i // 2 + 1,
                "visual_origin": "PROVIDER",
                "query": queries[i % 4],
                "provider_asset_id": str(i),
            }
            for i in range(34)
        ],
    }
    findings = semantic_production_findings({"title": TOPIC}, snapshot=snapshot)
    assert any(f.rule_code.value == "PROVIDER_QUERY_EXCESSIVE_REPETITION" for f in findings)


def test_canonical_diagram_and_script_meta_gates_are_blocking():
    script = {
        "title": TOPIC,
        "sections": [{"narration_text": "This section examines foundational principles."}],
    }
    scenes = [
        {
            "sequence_index": 1,
            "template_id": "FLOW_DIAGRAM",
            "narration_text": "Regarding Why Concrete Cracks Mechanisms Every Civil Engineer Should Understand",
        }
    ]
    status, findings = canonical_qa(script, scenes)
    assert status == ProductionQAStatus.BLOCKED
    assert {"DIAGRAM_SEMANTIC_INVALID", "SCRIPT_META_CONTENT"} <= {
        f.rule_code.value for f in findings
    }


def test_safe_provider_and_valid_diagram_do_not_get_semantic_findings():
    snapshot = {
        "scenes": [
            {"sequence_index": 1, "narration_text": "Rapid water loss causes concrete cracks."}
        ],
        "visual_beats": [
            {
                "parent_scene_index": 1,
                "visual_origin": "PROVIDER",
                "query": "concrete cracks",
                "provider_metadata": {"title": "Concrete cracks"},
            },
            {"parent_scene_index": 1, "visual_origin": "TEMPLATE", "template_id": "FLOW_DIAGRAM"},
        ],
    }
    assert not semantic_production_findings({"title": TOPIC}, snapshot=snapshot)


def test_storyboard_carries_canonical_title_through_rhetorical_section():
    script = {
        "title": TOPIC,
        "estimated_duration_seconds": 20,
        "sections": [
            {
                "heading": "Deliver the empirical resolution",
                "statements": [
                    {
                        "statement_order": 1,
                        "statement_text": "Rapid water loss from the surface of concrete forms shrinkage cracks.",
                    }
                ],
            }
        ],
    }
    scene = StoryboardEngine().generate_storyboard(script).scenes[0]
    assert scene.subject_text == TOPIC
    assert "concrete" in scene.asset_query_hint


def test_forensic_eight_query_34_beat_distribution_is_blocked():
    queries = [
        ("computer technology server datacenter infrastructure", 22),
        ("software verification testing security code review", 4),
        ("network switch fiber optic cables server data", 2),
        ("Conclude with a high technology", 2),
        ("Capture attention by highlighting technology", 1),
        ("Progressively unpack the core technology", 1),
        ("Deliver the empirical resolution technology", 1),
        ("Closing technology", 1),
    ]
    beats = []
    for query, count in queries:
        for _ in range(count):
            beats.append({"parent_scene_index": len(beats) % 16 + 1,
                          "visual_origin": "PROVIDER", "query": query})
    snapshot = {"scenes": [{"sequence_index": i, "narration_text": "Concrete surface cracking"} for i in range(1, 17)],
                "visual_beats": beats}
    findings = semantic_production_findings({"title": TOPIC}, snapshot=snapshot)
    assert sum(f.rule_code.value == "PROVIDER_QUERY_SEMANTIC_DRIFT" for f in findings) == 34
    assert any(f.rule_code.value == "PROVIDER_QUERY_EXCESSIVE_REPETITION" for f in findings)


def test_unknown_provider_descriptors_do_not_reject_synonyms():
    assert validate_provider_semantics(source_text="Galaxy formation", query="galaxy formation",
                                      metadata={"title": "Stellar nebula"}).valid


@pytest.mark.asyncio
async def test_multibeat_truncation_receipt_reaches_parent_truth(tmp_path):
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from omega.application.beat_asset_executor import BeatAssetExecutionResult, ExecutedBeatAsset
    from omega.application.beat_asset_policy import BeatAssetAction
    from omega.application.beat_visual_renderer import BeatClipMetadata
    from omega.application.canonical_beat_preparation import CanonicalBeatPreparationService
    from omega.application.editorial_beat import BeatMotionIntent
    from omega.application.visual_production_v2_service import VisualProductionV2Service

    text = "Water evaporates from the surface of concrete before curing completes. Concrete shrinks as its internal moisture leaves the material."
    script = {"title": TOPIC, "sections": [{"heading": "Concrete moisture", "statements": [{"statement_order": 1, "statement_text": text, "statement_type": "INTERPRETIVE"}]}]}
    scene = StoryboardScene(sequence_index=1, section_id="Concrete moisture", subject_text=TOPIC,
                            purpose="Explain", source_statement_references=[1], narration_excerpt=text,
                            estimated_duration_seconds=12, visual_strategy=VisualStrategy.KINETIC_TEXT,
                            visual_brief="Material explanation")
    prep = CanonicalBeatPreparationService.prepare_from_script_dict(script_dict=script, scene=scene,
        scene_duration_ms=12000, visual_asset_mode="PEXELS")
    assert prep.eligible and len(prep.render_plan.units) >= 2
    execution = BeatAssetExecutionResult(parent_scene_index=1, assets=tuple(
        ExecutedBeatAsset(parent_scene_index=1, beat_index=u.materialized_index, action=BeatAssetAction.LOCAL_TEMPLATE)
        for u in prep.render_plan.units))
    receipt = {"role": "BODY", "source_text": text, "rendered_text": "Water evaporates…", "text_truncated": True}
    metadata = tuple(BeatClipMetadata(beat_index=u.materialized_index, template_id=VisualTemplateId.KINETIC_TEXT,
        camera_motion_intent=BeatMotionIntent.STATIC, video_sha256="a" * 64,
        text_fitting=(receipt,) if u.materialized_index == 0 else ()) for u in prep.render_plan.units)
    preparation = MagicMock()
    preparation.prepare_from_script_dict.return_value = prep
    executor, renderer, assembler = AsyncMock(), AsyncMock(), AsyncMock()
    executor.execute_plan.return_value = execution
    renderer.render_plan.return_value = SimpleNamespace(clips=(), beat_metadata=metadata)
    assembler.assemble.return_value = SimpleNamespace(parent_scene_index=1, expected_duration_ms=12000, content_sha256="b" * 64)
    service = VisualProductionV2Service(None, tmp_path, beat_preparation_service=preparation,
        beat_asset_executor=executor, beat_visual_renderer=renderer, beat_clip_assembler=assembler)
    result = await service._render_parent_visual(script_dict=script, scene=scene, duration_seconds=12,
        canonical_visual_mode="PEXELS", work_dir=tmp_path, browser=MagicMock(), fps=30,
        style_profile=None, narration_enabled=False)
    assert result["text_truncated"] is True
    assert result["text_fitting"] == (receipt,)
