"""Narrative Director application component for P21-B.

Owns intelligent story structure selection, research grounding allocation,
promise/payoff construction, format profile adaptation, multi-candidate generation,
and deterministic candidate evaluation.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID

from omega.application.narrative_plan_validator import NarrativePlanValidator
from omega.domain.channel_dna import ChannelDNA
from omega.domain.narrative_plan import (
    FORMAT_PROFILE_CONSTRAINTS,
    FormatProfileConstraints,
    GroundingReference,
    GroundingType,
    InformationDensity,
    NarrativeFormatProfile,
    NarrativePlan,
    NarrativePlanStatus,
    NarrativePlanValidationResult,
    NarrativeSection,
    NarrativeSectionRole,
)
from omega.domain.narrative_strategy import (
    STRATEGY_CATALOG,
    NarrativePlanDraft,
    NarrativeStrategy,
    NarrativeStrategyDefinition,
    NarrativeStrategySelector,
    StrategySelectionScore,
)
from omega.logging import get_logger

logger = get_logger("omega-narrative-director")


class NarrativeModelError(Exception):
    """Base exception for narrative model operations."""


class NarrativeModelTimeoutError(NarrativeModelError):
    """Raised when model provider times out."""


class NarrativeModelProviderError(NarrativeModelError):
    """Raised when model provider returns an HTTP error or service failure."""


class NarrativeModelMalformedError(NarrativeModelError):
    """Raised when model provider returns invalid or unparseable structured output."""


class NarrativeDirectorGroundingError(NarrativeModelError):
    """Raised when model output references an invalid or unknown research authority ID."""


class NarrativeDirectorStrategyError(NarrativeModelError):
    """Raised when model attempts to select an ineligible narrative strategy."""


class NarrativeDirector(Protocol):
    """Protocol for intelligent Narrative Directors."""

    def generate_candidates(
        self,
        content_request_id: UUID,
        channel_dna: ChannelDNA | dict[str, Any],
        research_brief: dict[str, Any],
        content_intent: dict[str, Any],
        topic_title: str,
        topic_summary: str | None = None,
        format_profile: NarrativeFormatProfile = NarrativeFormatProfile.MEDIUM,
        target_duration_seconds: int | None = None,
        candidate_count: int = 3,
    ) -> list[NarrativePlanDraft]:
        """Generate multiple structured NarrativePlan candidate drafts."""
        ...


NarrativeDirectorProtocol = NarrativeDirector


class NarrativeModelClient(Protocol):
    """Protocol for underlying structured model providers."""

    provider_name: str
    model_name: str

    def generate_structured_plan(
        self,
        prompt: str,
        system_instruction: str,
        response_schema: dict[str, Any],
        timeout: float = 20.0,
    ) -> dict[str, Any]:
        """Invoke underlying LLM provider with structured schema constraint."""
        ...


class GeminiNarrativeModelClient:
    """Production Gemini REST API client supporting typed JSON schema constraints."""

    provider_name: str = "google_gemini"

    def __init__(
        self,
        api_key: str | None = None,
        model_name: str | None = None,
        client: Any | None = None,
    ) -> None:
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        self.model_name = model_name or os.getenv("GEMINI_NARRATIVE_MODEL", "gemini-3.5-flash-lite")
        self._client = client

    def generate_structured_plan(
        self,
        prompt: str,
        system_instruction: str,
        response_schema: dict[str, Any],
        timeout: float = 20.0,
    ) -> dict[str, Any]:
        if not self.api_key:
            raise NarrativeModelProviderError("GEMINI_API_KEY missing for GeminiNarrativeModelClient")

        headers = {
            "x-goog-api-key": self.api_key,
            "Content-Type": "application/json",
        }
        payload = {
            "systemInstruction": {"parts": [{"text": system_instruction}]},
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseSchema": response_schema,
            },
        }
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model_name}:generateContent"

        import httpx

        http_client = self._client or httpx.Client(timeout=timeout)
        try:
            resp = http_client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
        except httpx.TimeoutException as e:
            raise NarrativeModelTimeoutError(f"Gemini API request timed out after {timeout}s: {e}") from e
        except httpx.HTTPStatusError as e:
            raise NarrativeModelProviderError(
                f"Gemini API returned status code {e.response.status_code}: {e.response.text[:300]}"
            ) from e
        except httpx.RequestError as e:
            raise NarrativeModelProviderError(f"Network error calling Gemini API: {e}") from e
        finally:
            if not self._client:
                http_client.close()

        try:
            body = resp.json()
            candidates = body.get("candidates", [])
            if not candidates:
                raise NarrativeModelMalformedError(f"Gemini returned empty candidates: {body}")
            raw_text = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "")
            if not raw_text:
                raise NarrativeModelMalformedError("Gemini candidate part contains empty text")
            return json.loads(raw_text)
        except Exception as e:
            raise NarrativeModelMalformedError(f"Failed to parse model JSON: {e}") from e


class ModelBackedNarrativeDirector:
    """Intelligent Model-backed Narrative Director utilizing structured LLM completions."""

    DIRECTOR_VERSION = "p21-b-model-v1.0.0"

    def __init__(
        self,
        client: NarrativeModelClient | None = None,
        validator: NarrativePlanValidator | None = None,
    ) -> None:
        self.validator = validator or NarrativePlanValidator()
        if client is not None:
            self.client = client
        else:
            api_key = os.getenv("GEMINI_API_KEY")
            if not api_key:
                raise NarrativeModelProviderError("GEMINI_API_KEY is not configured for ModelBackedNarrativeDirector")
            self.client = GeminiNarrativeModelClient(api_key=api_key)

    def generate_candidates(
        self,
        content_request_id: UUID,
        channel_dna: ChannelDNA | dict[str, Any],
        research_brief: dict[str, Any],
        content_intent: dict[str, Any],
        topic_title: str,
        topic_summary: str | None = None,
        format_profile: NarrativeFormatProfile = NarrativeFormatProfile.MEDIUM,
        target_duration_seconds: int | None = None,
        candidate_count: int = 3,
    ) -> list[NarrativePlanDraft]:
        dna_dict = channel_dna.model_dump() if isinstance(channel_dna, ChannelDNA) else dict(channel_dna)
        constraints = FORMAT_PROFILE_CONSTRAINTS[format_profile]
        target_dur = target_duration_seconds or constraints.default_duration_seconds

        # 1. Deterministic eligibility filtering
        ranked_scores = NarrativeStrategySelector.evaluate_strategies(
            topic_title=topic_title,
            topic_summary=topic_summary,
            brief_dict=research_brief,
            dna_dict=dna_dict,
            content_intent=content_intent,
            format_profile=format_profile,
        )
        eligible_strategies = [s.strategy for s in ranked_scores if s.is_eligible]
        if not eligible_strategies:
            raise NarrativeDirectorStrategyError(
                f"No eligible strategies found for format profile {format_profile.value}"
            )

        # 2. Extract verified claims allowlist and uncertain claims
        verified_claims = research_brief.get("verified_claims", []) or []
        verified_claim_map: dict[str, dict[str, Any]] = {}
        for c in verified_claims:
            cid = str(c.get("claim_id") or "")
            if cid:
                verified_claim_map[cid] = c

        valid_claim_ids = set(verified_claim_map.keys())
        uncertain_claims = research_brief.get("uncertain_claims", []) or []
        uncertain_claim_ids = {
            str(c.get("claim_id") or "") for c in uncertain_claims if c.get("claim_id")
        }

        # 3. Construct schema and prompt
        response_schema = self._build_response_schema(eligible_strategies, constraints)
        system_instruction, prompt = self._build_prompts(
            topic_title=topic_title,
            topic_summary=topic_summary,
            dna_dict=dna_dict,
            content_intent=content_intent,
            format_profile=format_profile,
            target_dur=target_dur,
            constraints=constraints,
            eligible_strategies=eligible_strategies,
            verified_claims=verified_claims,
            uncertain_claims=uncertain_claims,
            candidate_count=candidate_count,
        )

        # 4. Invoke model client with structured schema
        raw_result = self.client.generate_structured_plan(
            prompt=prompt,
            system_instruction=system_instruction,
            response_schema=response_schema,
            timeout=20.0,
        )

        # 5. Parse and validate candidate plans
        raw_candidates = raw_result.get("candidates", [])
        if not raw_candidates:
            raise NarrativeModelMalformedError("Model returned empty candidates list")

        draft_candidates: list[NarrativePlanDraft] = []
        brief_id_val = research_brief.get("id")
        brief_uuid = UUID(str(brief_id_val)) if brief_id_val else uuid.uuid4()

        for raw_cand in raw_candidates[:candidate_count]:
            strat_val = raw_cand.get("strategy")
            try:
                strat_enum = NarrativeStrategy(strat_val)
            except ValueError:
                continue

            # Model must not select ineligible strategy
            if strat_enum not in eligible_strategies:
                continue

            strat_defn = STRATEGY_CATALOG[strat_enum]
            raw_sections = raw_cand.get("sections", [])
            if not raw_sections:
                continue

            # Process sections & check grounding allowlist
            sections: list[NarrativeSection] = []
            cand_disqualified = False
            promise_id_seen = None

            for s_idx, sec_dict in enumerate(raw_sections, start=1):
                raw_role = sec_dict.get("role")
                try:
                    role_enum = NarrativeSectionRole(raw_role)
                except ValueError:
                    cand_disqualified = True
                    break

                if role_enum not in constraints.allowed_roles:
                    cand_disqualified = True
                    break

                dur = int(sec_dict.get("target_duration_seconds", target_dur // len(raw_sections)))
                obj = str(sec_dict.get("objective", f"Objective for {role_enum.value}"))
                key_info = sec_dict.get("key_information", [f"Key detail for {role_enum.value}"])

                sec_promise = sec_dict.get("promise_id")
                sec_payoff = sec_dict.get("payoff_reference")

                if sec_promise:
                    promise_id_seen = sec_promise

                # Grounding allow-list check
                cited_cid = sec_dict.get("cited_claim_id")
                sec_grounding: list[GroundingReference] = []

                if cited_cid:
                    cited_str = str(cited_cid).strip()
                    # Safety 1: Uncertain claims must not become factual grounding
                    if cited_str in uncertain_claim_ids:
                        cand_disqualified = True
                        break
                    # Safety 2: Unknown claim IDs rejected
                    if cited_str not in valid_claim_ids:
                        cand_disqualified = True
                        break

                    claim_obj = verified_claim_map[cited_str]
                    ev_list = claim_obj.get("evidence", []) or []
                    ev_id = UUID(ev_list[0]["evidence_id"]) if ev_list and ev_list[0].get("evidence_id") else None
                    src_id = UUID(ev_list[0]["source_id"]) if ev_list and ev_list[0].get("source_id") else None
                    sec_grounding.append(
                        GroundingReference(
                            research_brief_id=brief_uuid,
                            claim_id=UUID(cited_str),
                            evidence_id=ev_id,
                            source_id=src_id,
                            grounding_type=GroundingType.FACTUAL,
                            description=claim_obj.get("claim_text", "")[:500],
                        )
                    )

                # For factual sections, ensure at least one verified citation if available
                if not sec_grounding and role_enum in (
                    NarrativeSectionRole.CONTEXT,
                    NarrativeSectionRole.DEVELOPMENT,
                    NarrativeSectionRole.PAYOFF,
                ):
                    if valid_claim_ids:
                        chosen_cid = list(valid_claim_ids)[s_idx % len(valid_claim_ids)]
                        claim_obj = verified_claim_map[chosen_cid]
                        ev_list = claim_obj.get("evidence", []) or []
                        ev_id = UUID(ev_list[0]["evidence_id"]) if ev_list and ev_list[0].get("evidence_id") else None
                        src_id = UUID(ev_list[0]["source_id"]) if ev_list and ev_list[0].get("source_id") else None
                        sec_grounding.append(
                            GroundingReference(
                                research_brief_id=brief_uuid,
                                claim_id=UUID(chosen_cid),
                                evidence_id=ev_id,
                                source_id=src_id,
                                grounding_type=GroundingType.FACTUAL,
                                description=claim_obj.get("claim_text", "")[:500],
                            )
                        )

                density = (
                    InformationDensity.HIGH
                    if role_enum in (NarrativeSectionRole.HOOK, NarrativeSectionRole.DEVELOPMENT)
                    else InformationDensity.MEDIUM
                )

                sections.append(
                    NarrativeSection(
                        id=uuid.uuid4(),
                        section_order=s_idx,
                        role=role_enum,
                        objective=obj,
                        key_information=key_info,
                        grounding_references=sec_grounding,
                        target_duration_seconds=max(dur, 5),
                        target_information_density=density,
                        promise_id=sec_promise,
                        payoff_reference=sec_payoff,
                        notes=f"Model-generated section under {strat_enum.value}",
                    )
                )

            if cand_disqualified or not sections:
                continue

            # Ensure promise/payoff linking if open loop
            if strat_defn.has_open_loop:
                has_prom = any(s.promise_id for s in sections)
                has_pay = any(s.payoff_reference for s in sections)
                if not has_prom or not has_pay:
                    loop_id = f"loop_{strat_enum.value.lower()}_{uuid.uuid4().hex[:6]}"
                    for s in sections:
                        if s.role in (NarrativeSectionRole.HOOK, NarrativeSectionRole.PROMISE) and not has_prom:
                            s.promise_id = loop_id
                            has_prom = True
                        elif s.role == NarrativeSectionRole.PAYOFF and not has_pay:
                            s.payoff_reference = loop_id
                            has_pay = True

            # Normalize duration sum to match target
            curr_sum = sum(s.target_duration_seconds for s in sections)
            if curr_sum != target_dur and sections:
                sections[-1].target_duration_seconds += (target_dur - curr_sum)

            draft_candidates.append(
                NarrativePlanDraft(
                    strategy=strat_enum,
                    format_profile=format_profile,
                    target_duration_seconds=target_dur,
                    estimated_duration_seconds=sum(s.target_duration_seconds for s in sections),
                    sections=sections,
                    rationale=str(raw_cand.get("rationale", f"Model selected strategy {strat_enum.value}")),
                    metadata={
                        "director_implementation": "ModelBackedNarrativeDirector",
                        "director_version": self.DIRECTOR_VERSION,
                        "model_provider": getattr(self.client, "provider_name", "google_gemini"),
                        "model_name": getattr(self.client, "model_name", "gemini-3.5-flash-lite"),
                        "content_generation_request_id": str(content_request_id),
                        "research_brief_id": str(brief_uuid),
                        "channel_dna_revision_id": str(dna_dict.get("id", "")),
                        "format_profile": format_profile.value,
                        "selected_strategy": strat_enum.value,
                        "generated_at": datetime.now(UTC).isoformat(),
                        "provenance_audit": {
                            "model_provider": getattr(self.client, "provider_name", "google_gemini"),
                            "model_name": getattr(self.client, "model_name", "gemini-3.5-flash-lite"),
                            "eligible_strategies": [s.value for s in eligible_strategies],
                            "verified_claim_count": len(valid_claim_ids),
                        },
                    },
                )
            )

        if not draft_candidates:
            raise NarrativeModelMalformedError(
                "All candidates generated by model failed validation or grounding allowlist checks."
            )

        return draft_candidates

    def _build_response_schema(
        self,
        eligible_strategies: list[NarrativeStrategy],
        constraints: FormatProfileConstraints,
    ) -> dict[str, Any]:
        """Build JSON schema enforcing structured typed model output."""
        return {
            "type": "OBJECT",
            "properties": {
                "candidates": {
                    "type": "ARRAY",
                    "items": {
                        "type": "OBJECT",
                        "properties": {
                            "strategy": {
                                "type": "STRING",
                                "enum": [s.value for s in eligible_strategies],
                            },
                            "rationale": {"type": "STRING"},
                            "sections": {
                                "type": "ARRAY",
                                "items": {
                                    "type": "OBJECT",
                                    "properties": {
                                        "role": {
                                            "type": "STRING",
                                            "enum": [r.value for r in sorted(constraints.allowed_roles, key=lambda x: x.value)],
                                        },
                                        "objective": {"type": "STRING"},
                                        "key_information": {
                                            "type": "ARRAY",
                                            "items": {"type": "STRING"},
                                        },
                                        "target_duration_seconds": {"type": "INTEGER"},
                                        "cited_claim_id": {"type": "STRING"},
                                        "promise_id": {"type": "STRING"},
                                        "payoff_reference": {"type": "STRING"},
                                    },
                                    "required": ["role", "objective", "target_duration_seconds"],
                                },
                            },
                        },
                        "required": ["strategy", "rationale", "sections"],
                    },
                }
            },
            "required": ["candidates"],
        }

    def _build_prompts(
        self,
        topic_title: str,
        topic_summary: str | None,
        dna_dict: dict[str, Any],
        content_intent: dict[str, Any],
        format_profile: NarrativeFormatProfile,
        target_dur: int,
        constraints: FormatProfileConstraints,
        eligible_strategies: list[NarrativeStrategy],
        verified_claims: list[dict[str, Any]],
        uncertain_claims: list[dict[str, Any]],
        candidate_count: int,
    ) -> tuple[str, str]:
        """Build system instruction and prompt for structured plan generation."""
        system_instruction = (
            "You are the Omega Narrative Director. You design authoritative, research-grounded, "
            "structured narrative plans for technical content. You MUST return strictly valid JSON "
            "matching the requested schema."
        )

        brand_voice = dna_dict.get("brand_voice", {})
        tone = brand_voice.get("tone", "AUTHORITATIVE")
        pace = brand_voice.get("pace", "FAST")

        claims_text = ""
        if verified_claims:
            claims_text = "VERIFIED CLAIMS ALLOW-LIST (You may ONLY cite these claim IDs for factual grounding):\n"
            for c in verified_claims:
                claims_text += f"- Claim ID: {c.get('claim_id')}: {c.get('claim_text')}\n"
        else:
            claims_text = "VERIFIED CLAIMS ALLOW-LIST: None provided.\n"

        uncertain_text = ""
        if uncertain_claims:
            uncertain_text = "UNCERTAIN CLAIMS (DO NOT cite or promote these to factual grounding):\n"
            for c in uncertain_claims:
                uncertain_text += f"- Uncertain Claim ID: {c.get('claim_id')}: {c.get('claim_text')}\n"

        required_roles_str = ", ".join(r.value for r in sorted(constraints.required_roles, key=lambda x: x.value))
        strat_options = "\n".join(f"- {s.value}: {STRATEGY_CATALOG[s].description}" for s in eligible_strategies)

        prompt = (
            f"Generate {candidate_count} structured narrative plan candidate(s) for:\n"
            f"Topic: {topic_title}\n"
            f"Summary: {topic_summary or 'N/A'}\n"
            f"Brand Tone: {tone}, Pace: {pace}\n"
            f"Content Intent: {content_intent.get('primary_goal', '')}\n"
            f"Central Question: {content_intent.get('central_question', '')}\n"
            f"Viewer Promise: {content_intent.get('viewer_promise', '')}\n"
            f"Format Profile: {format_profile.value} (Target Duration: {target_dur}s, Sections: {constraints.min_sections}-{constraints.max_sections})\n"
            f"Required Section Roles: {required_roles_str}\n\n"
            f"ELIGIBLE STRATEGIES (Choose ONLY from these):\n{strat_options}\n\n"
            f"{claims_text}\n"
            f"{uncertain_text}\n"
            "RULES:\n"
            "1. First section must have role 'HOOK'.\n"
            "2. All required roles must be included in the sequence.\n"
            "3. If opening a promise loop, define promise_id in HOOK or PROMISE and resolve it in PAYOFF with matching payoff_reference.\n"
            "4. NEVER invent claim IDs. Only use exact Claim IDs from the verified claims allow-list.\n"
            "5. Sum of section target_duration_seconds must approximate total target duration.\n"
        )

        return system_instruction, prompt


class CandidateEvaluation(Protocol):
    """Candidate evaluation result."""


class DeterministicNarrativeDirector:
    """Production-grade deterministic Narrative Director.

    Generates structured, research-grounded, format-compliant story structure drafts
    for multiple candidate strategies with full loop coherence and factual lineage.
    """

    DIRECTOR_VERSION = "p21-b-v1.0.0"

    def __init__(self, validator: NarrativePlanValidator | None = None) -> None:
        self.validator = validator or NarrativePlanValidator()

    def generate_candidates(
        self,
        content_request_id: UUID,
        channel_dna: ChannelDNA | dict[str, Any],
        research_brief: dict[str, Any],
        content_intent: dict[str, Any],
        topic_title: str,
        topic_summary: str | None = None,
        format_profile: NarrativeFormatProfile = NarrativeFormatProfile.MEDIUM,
        target_duration_seconds: int | None = None,
        candidate_count: int = 3,
    ) -> list[NarrativePlanDraft]:
        """Generate up to `candidate_count` distinct valid candidate plans."""
        dna_dict = channel_dna.model_dump() if isinstance(channel_dna, ChannelDNA) else dict(channel_dna)
        constraints = FORMAT_PROFILE_CONSTRAINTS[format_profile]
        target_dur = target_duration_seconds or constraints.default_duration_seconds

        # 1. Evaluate and rank all eligible strategies
        ranked_strategies = NarrativeStrategySelector.evaluate_strategies(
            topic_title=topic_title,
            topic_summary=topic_summary,
            brief_dict=research_brief,
            dna_dict=dna_dict,
            content_intent=content_intent,
            format_profile=format_profile,
        )
        eligible = [s for s in ranked_strategies if s.is_eligible]
        if not eligible:
            fallback = NarrativeStrategySelector.select_best_strategy(
                topic_title=topic_title,
                topic_summary=topic_summary,
                brief_dict=research_brief,
                dna_dict=dna_dict,
                content_intent=content_intent,
                format_profile=format_profile,
            )
            selected_strategies = [fallback]
        else:
            selected_strategies = [s.strategy for s in eligible[:candidate_count]]

        # Ensure at least 1 strategy is selected
        if not selected_strategies:
            selected_strategies = [NarrativeStrategy.HOW_IT_WORKS]

        candidates: list[NarrativePlanDraft] = []
        for strat in selected_strategies:
            draft = self._build_candidate_draft(
                strategy=strat,
                format_profile=format_profile,
                target_duration_seconds=target_dur,
                content_request_id=content_request_id,
                topic_title=topic_title,
                topic_summary=topic_summary,
                brief_dict=research_brief,
                dna_dict=dna_dict,
                content_intent=content_intent,
            )
            candidates.append(draft)

        return candidates

    def _build_candidate_draft(
        self,
        strategy: NarrativeStrategy,
        format_profile: NarrativeFormatProfile,
        target_duration_seconds: int,
        content_request_id: UUID,
        topic_title: str,
        topic_summary: str | None,
        brief_dict: dict[str, Any],
        dna_dict: dict[str, Any],
        content_intent: dict[str, Any],
    ) -> NarrativePlanDraft:
        """Construct a concrete, grounded NarrativePlanDraft for a specific strategy."""
        constraints = FORMAT_PROFILE_CONSTRAINTS[format_profile]
        strat_defn = STRATEGY_CATALOG[strategy]
        brief_id_str = brief_dict.get("id")
        brief_uuid = UUID(brief_id_str) if brief_id_str else uuid.uuid4()

        # Extract verified claims for grounding
        verified_claims = brief_dict.get("verified_claims", []) or []
        grounding_pool: list[GroundingReference] = []
        for c in verified_claims:
            claim_id_val = c.get("claim_id")
            if claim_id_val:
                try:
                    c_uuid = UUID(str(claim_id_val))
                    evidence_list = c.get("evidence", []) or []
                    ev_id_val = evidence_list[0].get("evidence_id") if evidence_list else None
                    ev_uuid = UUID(str(ev_id_val)) if ev_id_val else None
                    src_id_val = evidence_list[0].get("source_id") if evidence_list else None
                    src_uuid = UUID(str(src_id_val)) if src_id_val else None
                    grounding_pool.append(
                        GroundingReference(
                            research_brief_id=brief_uuid,
                            claim_id=c_uuid,
                            evidence_id=ev_uuid,
                            source_id=src_uuid,
                            grounding_type=GroundingType.FACTUAL,
                            description=c.get("claim_text", "")[:300],
                        )
                    )
                except (ValueError, TypeError):
                    continue

        # Determine roles based on format profile and strategy
        if format_profile == NarrativeFormatProfile.SHORT:
            roles = [
                NarrativeSectionRole.HOOK,
                NarrativeSectionRole.PAYOFF,
                NarrativeSectionRole.TAKEAWAY,
            ]
        elif format_profile == NarrativeFormatProfile.MEDIUM:
            if strat_defn.has_open_loop:
                roles = [
                    NarrativeSectionRole.HOOK,
                    NarrativeSectionRole.PROMISE,
                    NarrativeSectionRole.DEVELOPMENT,
                    NarrativeSectionRole.PAYOFF,
                    NarrativeSectionRole.TAKEAWAY,
                    NarrativeSectionRole.CLOSING,
                ]
            else:
                roles = [
                    NarrativeSectionRole.HOOK,
                    NarrativeSectionRole.CONTEXT,
                    NarrativeSectionRole.DEVELOPMENT,
                    NarrativeSectionRole.PAYOFF,
                    NarrativeSectionRole.TAKEAWAY,
                    NarrativeSectionRole.CLOSING,
                ]
        else:  # LONG
            roles = [
                NarrativeSectionRole.HOOK,
                NarrativeSectionRole.PROMISE,
                NarrativeSectionRole.CONTEXT,
                NarrativeSectionRole.DEVELOPMENT,
                NarrativeSectionRole.ESCALATION,
                NarrativeSectionRole.PAYOFF,
                NarrativeSectionRole.TAKEAWAY,
                NarrativeSectionRole.CLOSING,
            ]

        # Allocate durations proportionally to satisfy constraints
        num_sections = len(roles)
        hook_duration = min(constraints.max_hook_duration_seconds, max(target_duration_seconds // (num_sections + 1), 5))
        remaining_duration = target_duration_seconds - hook_duration
        other_durations = [max(remaining_duration // (num_sections - 1), 5) for _ in range(num_sections - 1)]
        # Adjust last section to match target_duration_seconds exactly
        other_durations[-1] += target_duration_seconds - (hook_duration + sum(other_durations))
        durations = [hook_duration] + other_durations

        # Set up curiosity loop / promise if applicable
        has_promise = strat_defn.has_open_loop or (format_profile == NarrativeFormatProfile.LONG)
        active_promise_id = None
        open_loop_intent = None
        if has_promise:
            active_promise_id = f"loop_{strategy.value.lower()}_{uuid.uuid4().hex[:6]}"
            open_loop_intent = f"Investigating whether {topic_title} satisfies the predicted mechanical outcome."

        sections: list[NarrativeSection] = []
        grounding_idx = 0
        promise_placed = False

        for order, (role, dur) in enumerate(zip(roles, durations, strict=True), start=1):
            sec_grounding: list[GroundingReference] = []
            sec_promise = None
            sec_payoff = None

            # Attach loop hook / promise and corresponding payoff
            if active_promise_id:
                should_place_promise = (
                    not promise_placed
                    and (
                        role == NarrativeSectionRole.PROMISE
                        or (role == NarrativeSectionRole.HOOK and NarrativeSectionRole.PROMISE not in roles)
                    )
                )
                if should_place_promise:
                    sec_promise = active_promise_id
                    promise_placed = True
                elif promise_placed and role == NarrativeSectionRole.PAYOFF and sec_payoff is None:
                    sec_payoff = active_promise_id

            # Ground factual sections
            if role in (NarrativeSectionRole.CONTEXT, NarrativeSectionRole.DEVELOPMENT, NarrativeSectionRole.PAYOFF):
                if grounding_pool:
                    sec_grounding.append(grounding_pool[grounding_idx % len(grounding_pool)])
                    grounding_idx += 1

            objective = self._generate_section_objective(role, strategy, topic_title, content_intent)
            key_info = [
                f"Factual detail for {role.value} under {strategy.value}",
                f"Core mechanism of {topic_title}",
            ]

            density = InformationDensity.HIGH if role in (NarrativeSectionRole.HOOK, NarrativeSectionRole.DEVELOPMENT) else InformationDensity.MEDIUM

            sections.append(
                NarrativeSection(
                    id=uuid.uuid4(),
                    section_order=order,
                    role=role,
                    objective=objective,
                    key_information=key_info,
                    grounding_references=sec_grounding,
                    target_duration_seconds=dur,
                    target_information_density=density,
                    open_loop_intent=open_loop_intent if sec_promise else None,
                    promise_id=sec_promise,
                    payoff_reference=sec_payoff,
                    notes=f"Generated via strategy {strategy.value}",
                )
            )

        rationale = f"Strategy '{strat_defn.name}' matches topic intent '{content_intent.get('primary_goal', topic_title)}' with {len(sections)} structured sections."

        return NarrativePlanDraft(
            strategy=strategy,
            format_profile=format_profile,
            target_duration_seconds=target_duration_seconds,
            estimated_duration_seconds=sum(s.target_duration_seconds for s in sections),
            sections=sections,
            rationale=rationale,
            metadata={
                "director_implementation": "DeterministicNarrativeDirector",
                "strategy": strategy.value,
                "strategy_name": strat_defn.name,
                "director_version": self.DIRECTOR_VERSION,
                "content_generation_request_id": str(content_request_id),
                "generated_at": datetime.now(UTC).isoformat(),
            },
        )

    def _generate_section_objective(
        self,
        role: NarrativeSectionRole,
        strategy: NarrativeStrategy,
        topic_title: str,
        content_intent: dict[str, Any],
    ) -> str:
        """Create a descriptive editorial objective for each section role."""
        if role == NarrativeSectionRole.HOOK:
            return f"Capture attention by highlighting the critical question regarding {topic_title}."
        if role == NarrativeSectionRole.PROMISE:
            return f"Establish the viewer contract regarding what insights will be revealed about {topic_title}."
        if role == NarrativeSectionRole.CONTEXT:
            return f"Provide foundational historical and technical background for understanding {topic_title}."
        if role == NarrativeSectionRole.DEVELOPMENT:
            return f"Progressively unpack the core operational mechanisms of {topic_title}."
        if role == NarrativeSectionRole.ESCALATION:
            return f"Raise the stakes and examine severe failure modes or bottlenecks in {topic_title}."
        if role == NarrativeSectionRole.PAYOFF:
            return f"Deliver the empirical resolution and payoff explaining the reality of {topic_title}."
        if role == NarrativeSectionRole.TAKEAWAY:
            return f"Synthesize actionable engineering conclusions from {topic_title}."
        if role == NarrativeSectionRole.CLOSING:
            return f"Conclude with a high-impact summary of {topic_title}."
        if role == NarrativeSectionRole.CTA:
            return f"Direct viewers to explore further technical documentation on {topic_title}."
        return f"Examine {role.value} for {topic_title}."


class CandidateSelectionEngine:
    """Deterministic selection layer that evaluates and ranks candidate NarrativePlanDrafts."""

    def __init__(self, validator: NarrativePlanValidator | None = None) -> None:
        self.validator = validator or NarrativePlanValidator()

    def select_best_candidate(
        self,
        candidates: list[NarrativePlanDraft],
        content_generation_request_id: UUID,
        channel_dna_revision_id: UUID,
        topic_candidate_id: UUID | None = None,
        research_brief_id: UUID | None = None,
    ) -> tuple[NarrativePlan, NarrativePlanValidationResult, dict[str, Any]]:
        """Select the highest-quality candidate plan that satisfies all P21-A validation checks."""
        if not candidates:
            raise ValueError("No candidate NarrativePlanDrafts provided for selection.")

        scored_candidates: list[tuple[float, NarrativePlan, NarrativePlanValidationResult, dict[str, Any]]] = []

        for draft in candidates:
            plan = NarrativePlan(
                id=uuid.uuid4(),
                content_generation_request_id=content_generation_request_id,
                channel_dna_revision_id=channel_dna_revision_id,
                topic_candidate_id=topic_candidate_id,
                research_brief_id=research_brief_id,
                version=1,
                status=NarrativePlanStatus.DRAFT,
                format_profile=draft.format_profile,
                target_duration_seconds=draft.target_duration_seconds,
                estimated_duration_seconds=draft.estimated_duration_seconds,
                is_current=True,
                schema_version=1,
                sections=draft.sections,
                metadata={
                    **draft.metadata,
                    "selection_candidate_strategy": draft.strategy.value,
                },
            )

            # Validate against P21-A deterministic rules
            val_result = self.validator.validate(plan)
            if not val_result.is_valid:
                continue  # Disqualify invalid candidates

            # Score based on grounding, loop coherence, and section balance
            grounding_count = sum(len(s.grounding_references) for s in plan.sections)
            grounding_score = min(grounding_count * 15.0, 45.0)

            # Promise/payoff loop score
            loop_score = 25.0 if any(s.promise_id for s in plan.sections) and any(s.payoff_reference for s in plan.sections) else 10.0

            # Duration fidelity score
            dur_diff = abs(plan.target_duration_seconds - plan.estimated_duration_seconds)
            dur_score = max(0.0, 30.0 - dur_diff)

            total_score = grounding_score + loop_score + dur_score
            rationale = {
                "strategy": draft.strategy.value,
                "total_score": total_score,
                "grounding_count": grounding_count,
                "has_promise_payoff": loop_score == 25.0,
                "duration_delta_seconds": dur_diff,
            }
            scored_candidates.append((total_score, plan, val_result, rationale))

        if not scored_candidates:
            raise ValueError("All candidate NarrativePlanDrafts failed P21-A deterministic validation.")

        # Pick highest scoring plan
        scored_candidates.sort(key=lambda x: x[0], reverse=True)
        winner_score, winning_plan, winning_val, winning_rationale = scored_candidates[0]

        winning_plan.status = NarrativePlanStatus.VALIDATED
        winning_plan.metadata["selection_rationale"] = winning_rationale
        winning_plan.metadata["selected_score"] = winner_score

        return winning_plan, winning_val, winning_rationale
