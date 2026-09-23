"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import {
  createContentSelectionRun,
  finalizeContentSelectionRun,
  listContentSelectionRuns,
  type ContentSelectionRun,
  type TopicCandidate,
} from "@/lib/api";
import {
  canFinalizeSelection,
  isCanonicalSelectionCandidateEligible,
  isValidSelectionCount,
  reuseSubmissionKey,
  summarizeHistoricalPerformanceEvidence,
} from "@/lib/content-workflow";
import { Alert, EmptyState, FormField, LoadingState, PageSection, StatusBadge } from "@/components/ui";
import { TechnicalDetails } from "@/components/workflow/WorkflowPrimitives";

export function CanonicalSelection({ channelId, candidates, archived }: { channelId: string; candidates: TopicCandidate[]; archived: boolean }) {
  const [chosenIds, setChosenIds] = useState<string[]>([]);
  const [runs, setRuns] = useState<ContentSelectionRun[]>([]);
  const [run, setRun] = useState<ContentSelectionRun | null>(null);
  const [winnerId, setWinnerId] = useState("");
  const [actor, setActor] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const pendingSubmission = useRef<{ key: string; candidateIds: string[] } | null>(null);

  const refreshRuns = useCallback(async () => {
    setLoading(true);
    try {
      setRuns(await listContentSelectionRuns(channelId));
      setError(null);
    } catch (cause: unknown) {
      setError(cause instanceof Error ? cause.message : "Unable to load canonical selection runs.");
    } finally {
      setLoading(false);
    }
  }, [channelId]);

  useEffect(() => { void refreshRuns(); }, [refreshRuns]);
  const eligible = candidates.filter(isCanonicalSelectionCandidateEligible);
  const recommended = run?.decisions.find((decision) => decision.candidate_id === run.recommended_candidate_id);
  const selected = run?.decisions.find((decision) => decision.candidate_id === run.selected_candidate_id);

  async function createRun() {
    if (archived || !isValidSelectionCount(chosenIds.length)) return;
    setBusy(true);
    setError(null);
    try {
      if (!pendingSubmission.current) pendingSubmission.current = { key: reuseSubmissionKey(null, () => crypto.randomUUID()), candidateIds: [...chosenIds] };
      const result = await createContentSelectionRun(channelId, {
        candidate_ids: pendingSubmission.current.candidateIds,
        idempotency_key: pendingSubmission.current.key,
      });
      pendingSubmission.current = null;
      setRun(result);
      setWinnerId(result.recommended_candidate_id);
      setChosenIds([]);
      await refreshRuns();
    } catch (cause: unknown) {
      setError(cause instanceof Error ? cause.message : "Unable to create selection run. Retry uses the same request key.");
    } finally {
      setBusy(false);
    }
  }

  async function finalize() {
    if (!run || archived || !canFinalizeSelection(run, winnerId, actor, reason)) return;
    setBusy(true);
    setError(null);
    try {
      const result = await finalizeContentSelectionRun(channelId, run.id, {
        selected_candidate_id: winnerId === run.recommended_candidate_id ? null : winnerId,
        actor: actor.trim(),
        override_reason: winnerId === run.recommended_candidate_id ? null : reason.trim(),
      });
      setRun(result);
      await refreshRuns();
    } catch (cause: unknown) {
      setError(cause instanceof Error ? cause.message : "Unable to finalize selection.");
    } finally {
      setBusy(false);
    }
  }

  return <div className="ui-page-stack">
    {error && <Alert tone="danger" title="Selection action failed">{error}</Alert>}
    <PageSection title="Create canonical selection run" description="Choose 1–50 eligible candidates. The persisted backend ranking is authoritative.">
      {archived && <Alert tone="warning">This archived channel is read-only.</Alert>}
      {eligible.length === 0 ? <EmptyState title="No eligible candidates" description="Discover or evaluate candidates first." /> : <div className="workflow-card-grid">
        {eligible.map((candidate) => <label key={candidate.id} className="workflow-card">
          <input type="checkbox" checked={chosenIds.includes(candidate.id)} disabled={archived || busy || (chosenIds.length >= 50 && !chosenIds.includes(candidate.id))}
            onChange={(event) => {
              pendingSubmission.current = null;
              setChosenIds((ids) => event.target.checked ? [...ids, candidate.id] : ids.filter((id) => id !== candidate.id));
            }} /> {candidate.title} <StatusBadge>{candidate.status}</StatusBadge>
        </label>)}
      </div>}
      <p className="small muted">{chosenIds.length} of 50 selected.</p>
      <button type="button" className="btn btn-primary" disabled={archived || busy || !isValidSelectionCount(chosenIds.length)} onClick={() => void createRun()}>
        {busy ? "Working…" : "Create selection run"}
      </button>
    </PageSection>

    <PageSection title="Selection runs" description="Open a persisted run to inspect its ranked decisions or finalize it.">
      {loading ? <LoadingState title="Loading selection runs" /> : runs.length === 0 ? <EmptyState title="No selection runs yet" /> :
        <div className="ui-inline-actions">{runs.map((item) => <button type="button" className="btn btn-secondary btn-sm" key={item.id}
          onClick={() => { setRun(item); setWinnerId(item.selected_candidate_id || item.recommended_candidate_id); setReason(""); }}>
          {item.decisions.find((decision) => decision.candidate_id === item.recommended_candidate_id)?.candidate_title_snapshot || "Selection run"} · {item.status} · {new Date(item.created_at).toLocaleDateString()}
        </button>)}</div>}
    </PageSection>

    {run && <PageSection title={`Ranked decisions · ${run.status}`} description={`Selection policy v${run.policy_version}; ${run.considered_count} considered.`}>
      <p>Recommended: <strong>{recommended?.candidate_title_snapshot || run.recommended_candidate_id}</strong> · rank {recommended?.rank ?? "—"} · score {recommended?.final_score.toFixed(1) ?? "—"}</p>
      <div className="workflow-card-grid">{[...run.decisions].sort((a, b) => a.rank - b.rank).map((decision) => {
        const summary = summarizeHistoricalPerformanceEvidence(decision.evidence_snapshot);
        return <article className="workflow-card" key={decision.id}>
          <h3>#{decision.rank} {decision.candidate_title_snapshot}</h3>
          <p>Final score: {decision.final_score.toFixed(1)} · {decision.duplicate_status}</p>
          <dl className="workflow-data-list">
            {Object.entries(decision.score_breakdown).map(([name, score]) => <div className="workflow-data-row" key={name}><dt>{name.replaceAll("_", " ")}</dt><dd>{score.toFixed(1)}</dd></div>)}
          </dl>
          <p>Historical performance: {summary.score?.toFixed(1) ?? "—"} · {summary.label} · {summary.matches} matches / {summary.corpus} performance records · policy v{summary.policyVersion ?? "—"}</p>
          <TechnicalDetails data={{
            corpus_checksum: decision.evidence_snapshot.historical_performance_corpus_checksum,
            policy_checksum: decision.evidence_snapshot.historical_performance_policy_checksum,
            learning_evidence_ids: decision.evidence_snapshot.learning_evidence_ids,
            analytics_evidence_ids: decision.evidence_snapshot.analytics_evidence_ids,
            matched_evidence: decision.evidence_snapshot.matched_evidence,
            active_knowledge_authority: decision.evidence_snapshot.active_knowledge_authority,
            evidence_snapshot: decision.evidence_snapshot,
          }} />
        </article>;
      })}</div>
      {run.status === "READY" && <div className="workflow-dialog-form">
        <FormField id="selection-actor" label="Operator actor" required><input className="input" value={actor} onChange={(event) => setActor(event.target.value)} maxLength={100} /></FormField>
        <FormField id="selection-winner" label="Select considered candidate" required><select className="select" value={winnerId} onChange={(event) => setWinnerId(event.target.value)}>
          {run.decisions.map((decision) => <option key={decision.id} value={decision.candidate_id}>{decision.candidate_title_snapshot}{decision.candidate_id === run.recommended_candidate_id ? " (recommended · POLICY)" : " (OVERRIDE)"}</option>)}
        </select></FormField>
        {winnerId !== run.recommended_candidate_id && <FormField id="selection-reason" label="Override reason" required><textarea className="input" value={reason} onChange={(event) => setReason(event.target.value)} maxLength={2000} /></FormField>}
        <button type="button" className="btn btn-primary" disabled={busy || archived || !canFinalizeSelection(run, winnerId, actor, reason)} onClick={() => void finalize()}>
          {winnerId === run.recommended_candidate_id ? "Accept POLICY recommendation" : "Finalize explicit OVERRIDE"}
        </button>
      </div>}
      {run.status === "SELECTED" && <div className="ui-inline-actions">
        <p>Selected: <strong>{selected?.candidate_title_snapshot || run.selected_candidate_id}</strong> · {run.selection_mode} · by {run.selected_by} · {run.selection_reason}</p>
        {!archived && <Link className="btn btn-primary" href={`/channels/${channelId}/campaigns/new?selection_run_id=${encodeURIComponent(run.id)}`}>Add to campaign</Link>}
      </div>}
      <TechnicalDetails data={{ run_id: run.id, policy_checksum: run.policy_checksum, candidate_set_checksum: run.candidate_set_checksum, channel_dna_revision_id: run.channel_dna_revision_id }} />
    </PageSection>}
  </div>;
}
