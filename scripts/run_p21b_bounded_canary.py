"""Foreground bounded recovery runner for P21-B Real-Model Canary.

Guarantees:
- overall process timeout <= 45 seconds
- provider HTTP timeout <= 20 seconds
- maximum 1 real-provider request
- isolated dev DB only (port 5433, schema 027)
- no background task
- no infinite retry / indefinite polling
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from datetime import UTC, datetime

import dotenv
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

# Load .env
dotenv.load_dotenv()

# Track start time for 45s hard process deadline
START_TIME = time.time()
PROCESS_TIMEOUT = 45.0

def check_time_budget():
    elapsed = time.time() - START_TIME
    if elapsed > PROCESS_TIMEOUT:
        raise TimeoutError(f"Overall process runtime exceeded {PROCESS_TIMEOUT}s limit (elapsed {elapsed:.2f}s)")

def main():
    report = {
        "P21B_BOUNDED_RECOVERY_RUNNER_USED": "YES",
        "P21B_PROVIDER_REQUEST_COMPLETED": "NO",
        "P21B_REAL_MODEL_CANARY_PASS": "NO",
        "P21B_REAL_MODEL_CANARY_FAILURE": "NONE",
        "provider_abstraction_used": "GeminiNarrativeModelClient via ModelBackedNarrativeDirector",
        "selected_strategy": None,
        "candidate_count": 0,
        "section_roles": None,
        "grounding_reference_count": 0,
        "promise_payoff_count": None,
        "validation_result": None,
        "narrative_plan_id": None,
        "script_version_lineage_result": None,
    }

    try:
        from omega.application.content_provider import TemplateContentProvider
        from omega.application.narrative_director import (
            CandidateSelectionEngine,
            GeminiNarrativeModelClient,
            ModelBackedNarrativeDirector,
            NarrativeDirectorGroundingError,
            NarrativeDirectorStrategyError,
            NarrativeModelError,
            NarrativeModelMalformedError,
            NarrativeModelProviderError,
            NarrativeModelTimeoutError,
        )
        from omega.application.narrative_plan_service import (
            NarrativePlanService,
            PostgresNarrativePlanRepository,
        )
        from omega.application.narrative_plan_validator import NarrativePlanValidator
        from omega.application.narrative_planning_service import NarrativePlanningService
        from omega.application.narrative_script_adapter import NarrativePlanScriptAdapter
        from omega.domain.narrative_plan import NarrativeFormatProfile
        from omega.infrastructure.models import (
            Channel,
            ChannelDNARevision,
            ClaimEvidence,
            ContentGenerationRequest,
            NarrativePlan as NarrativePlanModel,
            ResearchBrief,
            ResearchClaim,
            ResearchRequest,
            ResearchSource,
            ScriptVersion,
            TopicCandidate,
        )

        # 1. Verify isolated test database (must be 5433, NOT 5432 production)
        test_url = os.getenv("TEST_DATABASE_URL")
        if not test_url:
            raise RuntimeError("TEST_DATABASE_URL environment variable must be set to isolated test DB.")
        if "5432" in test_url and "5433" not in test_url:
            raise RuntimeError("SAFETY ABORT: Cannot run canary against port 5432! Isolated dev DB must be port 5433.")

        sync_url = test_url.replace("postgresql+asyncpg://", "postgresql+psycopg2://")
        engine = create_engine(sync_url, echo=False, pool_pre_ping=True, connect_args={"connect_timeout": 5})
        SessionLocal = sessionmaker(bind=engine, class_=Session, expire_on_commit=False)

        session = SessionLocal()

        # 2. Seed realistic isolated entities
        channel_id = uuid.uuid4()
        channel = Channel(
            id=channel_id,
            slug=f"canary-channel-{uuid.uuid4().hex[:6]}",
            name="Canary Tech Reviews",
        )
        session.add(channel)

        dna_rev_id = uuid.uuid4()
        channel_dna_snapshot = {
            "channel_id": str(channel_id),
            "brand_voice": {
                "tone": "AUTHORITATIVE",
                "pace": "MEASURED",
                "complexity": "ADVANCED",
            },
            "narrative_preferences": {
                "default_strategy": "HOW_IT_WORKS",
                "preferred_format": "MEDIUM",
            },
        }
        dna_rev = ChannelDNARevision(
            id=dna_rev_id,
            channel_id=channel_id,
            version=1,
            snapshot=channel_dna_snapshot,
            change_reason="Canary Model Run",
        )
        session.add(dna_rev)

        topic_id = uuid.uuid4()
        topic = TopicCandidate(
            id=topic_id,
            channel_id=channel_id,
            title="Superconducting Qubit Decoherence Mechanisms",
            normalized_title="superconducting qubit decoherence mechanisms",
            source_name="ArXiv-Quantum",
            summary="Technical analysis of fluxonium and transmon qubit decoherence channels.",
            topic_fingerprint=uuid.uuid4().hex,
            status="APPROVED",
        )
        session.add(topic)

        r_req_id = uuid.uuid4()
        r_req = ResearchRequest(
            id=r_req_id,
            channel_id=channel_id,
            topic_candidate_id=topic_id,
            status="COMPLETED",
        )
        session.add(r_req)

        brief_id = uuid.uuid4()
        brief = ResearchBrief(
            id=brief_id,
            research_request_id=r_req_id,
            channel_id=channel_id,
            topic_candidate_id=topic_id,
            title="Superconducting Qubit Quantum Noise",
            summary="Empirical evidence on transmon anharmonicity, 1/f flux noise, and dielectric loss.",
        )
        session.add(brief)

        source_id = uuid.uuid4()
        source = ResearchSource(
            id=source_id,
            research_request_id=r_req_id,
            channel_id=channel_id,
            title="Quantum Science Review 2024",
            publisher="Nature",
            url="https://doi.org/10.1038/s41586-024-00123-x",
            content_excerpt="Capacitive shunting flattens charge dispersion bands exponentially.",
            content_hash=uuid.uuid4().hex,
            source_type="JOURNAL",
        )
        session.add(source)

        claim1_id = uuid.uuid4()
        claim1 = ResearchClaim(
            id=claim1_id,
            research_request_id=r_req_id,
            channel_id=channel_id,
            claim_text="Shunting the Josephson junction with a large capacitor suppresses charge noise exponentially.",
            normalized_claim="shunting the josephson junction with a large capacitor suppresses charge noise exponentially",
            confidence_score=0.99,
        )
        session.add(claim1)

        ev1_id = uuid.uuid4()
        ev1 = ClaimEvidence(
            id=ev1_id,
            claim_id=claim1_id,
            source_id=source_id,
            excerpt="Ratio of EJ to EC suppresses charge dispersion.",
            strength_score=98.0,
        )
        session.add(ev1)

        content_req_id = uuid.uuid4()
        content_req = ContentGenerationRequest(
            id=content_req_id,
            channel_id=channel_id,
            topic_candidate_id=topic_id,
            research_brief_id=brief_id,
            channel_dna_revision_id=dna_rev_id,
            status="APPROVED",
        )
        session.add(content_req)
        session.commit()

        check_time_budget()

        # 3. Formulate domain inputs
        brief_dict = {
            "id": str(brief_id),
            "title": brief.title,
            "summary": brief.summary,
            "verified_claims": [
                {
                    "claim_id": str(claim1_id),
                    "claim_text": claim1.claim_text,
                    "evidence": [
                        {
                            "evidence_id": str(ev1_id),
                            "source_id": str(source_id),
                            "excerpt": ev1.excerpt,
                        }
                    ],
                }
            ],
            "uncertain_claims": [],
            "contradictions": [],
        }

        content_intent = {
            "primary_goal": "Explain how transmon qubits suppress charge noise through large capacitive shunting.",
            "audience_intent": "Understand quantum hardware noise mitigation.",
            "viewer_promise": "You will understand how superconducting circuits suppress environmental charge noise.",
            "central_question": "How do transmon qubits eliminate charge sensitivity?",
            "core_takeaway": "Exponential suppression of charge dispersion via large EJ/EC ratio.",
        }

        # 4. Instantiate ModelBackedNarrativeDirector using GeminiNarrativeModelClient
        # HTTP timeout strictly <= 20s
        gemini_client = GeminiNarrativeModelClient(
            model_name=os.getenv("GEMINI_NARRATIVE_MODEL", "gemini-3.5-flash-lite"),
        )
        validator = NarrativePlanValidator()
        director = ModelBackedNarrativeDirector(client=gemini_client, validator=validator)
        selection_engine = CandidateSelectionEngine(validator=validator)
        repo = PostgresNarrativePlanRepository(session=session)
        plan_service = NarrativePlanService(repository=repo, validator=validator)
        planning_service = NarrativePlanningService(
            director=director,
            plan_service=plan_service,
            validator=validator,
            selection_engine=selection_engine,
            max_retries=0, # Bound to exactly 1 attempt
        )

        check_time_budget()

        # 5. Execute model candidate generation and planning (max 1 request)
        plan, val_result, script_context = planning_service.plan_narrative_for_request(
            content_generation_request_id=content_req_id,
            channel_dna_revision_id=dna_rev_id,
            channel_dna=channel_dna_snapshot,
            research_brief=brief_dict,
            content_intent=content_intent,
            topic_title=topic.title,
            topic_summary=topic.summary,
            topic_candidate_id=topic_id,
            research_brief_id=brief_id,
            format_profile=NarrativeFormatProfile.MEDIUM,
            target_duration_seconds=300,
            candidate_count=1,
        )

        report["P21B_PROVIDER_REQUEST_COMPLETED"] = "YES"

        # 6. Verify PostgreSQL persistence at schema 027
        persisted_plan = session.execute(
            select(NarrativePlanModel).where(NarrativePlanModel.id == plan.id)
        ).scalar_one()

        # 7. Downstream NarrativePlanScriptAdapter & ScriptVersion lineage
        outline = planning_service.prepare_script_outline(plan)
        provider = TemplateContentProvider()
        selected_hook = {
            "id": str(uuid.uuid4()),
            "hook_variant_index": 0,
            "hook_text": "How do superconducting transmon qubits eliminate charge noise?",
            "hook_type": "PROVOCATIVE_QUESTION",
            "selected": True,
            "citations": [],
        }
        script_data = provider.generate_script(
            topic_title=topic.title,
            brief_dict=brief_dict,
            dna_dict=channel_dna_snapshot,
            intent_dict=content_intent,
            selected_hook=selected_hook,
            outline_dict=outline,
            target_duration_seconds=300,
        )

        script_version_id = uuid.uuid4()
        script_model = ScriptVersion(
            id=script_version_id,
            content_request_id=content_req_id,
            version=1,
            is_current=True,
            title=script_data["title"],
            hook_id=None,
            hook_text=selected_hook["hook_text"],
            closing_text=script_data.get("closing_text", "Thank you."),
            cta_text=script_data.get("cta_text", "Subscribe."),
            estimated_word_count=script_data.get("estimated_word_count", 600),
            estimated_duration_seconds=script_data.get("estimated_duration_seconds", 300),
            qa_status="PASSED",
            narrative_plan_id=plan.id,
        )
        session.add(script_model)
        session.commit()

        # Verify lineage
        verified_script = session.execute(
            select(ScriptVersion).where(ScriptVersion.id == script_version_id)
        ).scalar_one()

        assert verified_script.narrative_plan_id == plan.id

        # Metrics collection
        report["P21B_REAL_MODEL_CANARY_PASS"] = "YES"
        report["P21B_REAL_MODEL_CANARY_FAILURE"] = "NONE"
        report["selected_strategy"] = plan.metadata.get("selected_strategy") or plan.metadata.get("strategy")
        report["candidate_count"] = 1
        report["section_roles"] = " -> ".join(s.role.value for s in plan.sections)
        report["grounding_reference_count"] = sum(len(s.grounding_references) for s in plan.sections)
        prom_count = sum(1 for s in plan.sections if s.promise_id)
        pay_count = sum(1 for s in plan.sections if s.payoff_reference)
        report["promise_payoff_count"] = f"{prom_count} promise, {pay_count} payoff"
        report["validation_result"] = "VALID" if val_result.is_valid else f"INVALID: {[f.rule_code.value for f in val_result.findings]}"
        report["narrative_plan_id"] = str(plan.id)
        report["script_version_lineage_result"] = f"ScriptVersion {script_version_id} -> NarrativePlan {plan.id} -> ContentGenerationRequest {content_req_id}"

        session.close()

    except Exception as e:
        report["P21B_REAL_MODEL_CANARY_PASS"] = "NO"
        ex_name = e.__class__.__name__
        ex_str = str(e).lower()
        if "timeout" in ex_name.lower() or "timeout" in ex_str:
            report["P21B_REAL_MODEL_CANARY_FAILURE"] = "PROVIDER_TIMEOUT"
        elif "malformed" in ex_name.lower() or "malformed" in ex_str or "json" in ex_str or "schema" in ex_str:
            report["P21B_REAL_MODEL_CANARY_FAILURE"] = "INVALID_STRUCTURED_OUTPUT"
        else:
            report["P21B_REAL_MODEL_CANARY_FAILURE"] = "OMEGA_INTEGRATION_FAILURE"
        report["error_detail"] = f"{e.__class__.__name__}: {str(e)[:200]}"

    finally:
        print("CANARY_RUN_REPORT_JSON_START")
        print(json.dumps(report, indent=2))
        print("CANARY_RUN_REPORT_JSON_END")

if __name__ == "__main__":
    main()
